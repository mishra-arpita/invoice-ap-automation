"""
Automated tests: every sample invoice must get the expected decision, and key rules
behave correctly. Run with:  pytest -q

Tests use the rules parser (no API key needed) and a throw-away database.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, db  # noqa: E402
from core.pipeline import process_invoice  # noqa: E402
from core.rules import RunContext, check_amount, check_duplicates, check_purchase_order, match_vendor  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    db.init_db()


def run_sample(name: str) -> dict:
    path = config.SAMPLES_DIR / name
    return process_invoice(path.read_bytes(), name, use_ai=False)


def all_text(result: dict) -> str:
    d = result["decision"]
    return " ".join(d["reasons"] + d["notes"])


# ------------------------------------------------------------------ every sample -> expected decision
EXPECTED = {
    "01_happy_path_acme.pdf": ("APPROVED", None),
    "02_close_match_brightpath.pdf": ("APPROVED", "within the 2% tolerance"),
    "03_no_po_reference_nimbus.pdf": ("APPROVED", "Inferred PO-1004"),
    "04_split_po_overbilled_greenleaf.pdf": ("NEEDS_REVIEW", "beyond the 2% tolerance"),
    "05_duplicate_resubmitted_acme.pdf": ("REJECTED", "Duplicate"),
    "06_scanned_brightpath.pdf": ("NEEDS_REVIEW", "scanned image"),
    "07_unknown_vendor.pdf": ("REJECTED", "not in the approved vendor master"),
    "08_missing_fields_greenleaf.pdf": ("NEEDS_REVIEW", "Missing required field(s): invoice number, invoice date"),
}


@pytest.mark.parametrize("sample, expected", EXPECTED.items())
def test_sample_decisions(sample, expected):
    decision, must_mention = expected
    result = run_sample(sample)
    assert result["decision"]["decision"] == decision, result["decision"]
    if must_mention:
        assert must_mention in all_text(result)


def test_every_sample_is_covered():
    assert {p.name for p in config.SAMPLES_DIR.glob("*.pdf")} == set(EXPECTED)


def test_reprocessing_an_approved_invoice_is_rejected_as_duplicate():
    assert run_sample("01_happy_path_acme.pdf")["decision"]["decision"] == "APPROVED"
    again = run_sample("01_happy_path_acme.pdf")
    assert again["decision"]["decision"] == "REJECTED"
    assert "Duplicate" in all_text(again)


def test_approved_invoice_counts_towards_po_balance():
    run_sample("01_happy_path_acme.pdf")
    assert db.billed_to_date("PO-1001") == pytest.approx(2420.28)


def test_reviewer_approval_updates_status():
    result = run_sample("04_split_po_overbilled_greenleaf.pdf")
    db.record_review(result["id"], "APPROVED", "PO amendment raised")
    assert db.get_invoice(result["id"])["status"] == "APPROVED"
    assert db.billed_to_date("PO-1003") == pytest.approx(11000.00)


# ------------------------------------------------------------------ individual rules
def test_invoice_number_normalisation():
    assert db.normalize_invoice_number("INV 2026/0042") == db.normalize_invoice_number("inv-2026-0042")


def test_vendor_fuzzy_match_handles_formatting():
    row, score = match_vendor("ACME OFFICE SUPPLIES, INC")
    assert row["vendor_id"] == "V001" and score >= config.VENDOR_MATCH_THRESHOLD


def test_po_inference_is_ambiguous_when_two_open_pos_fit():
    # Acme has two open POs (PO-1001 $2,420.28 and PO-1005 $39 remaining). $30 fits both.
    vendor, _ = match_vendor("Acme Office Supplies Inc.")
    ctx = RunContext()
    ctx.vendor = vendor
    ctx.fields.update({"total": 30.00, "po_number": None})
    result = check_purchase_order(ctx)
    assert result.findings[0].outcome == "review" and "ambiguous" in result.summary


def test_closed_po_is_not_accepted():
    vendor, _ = match_vendor("Nimbus Cloud Hosting Ltd")
    ctx = RunContext()
    ctx.vendor = vendor
    ctx.fields.update({"total": 3600.00, "po_number": "PO-0998"})
    assert "closed" in check_purchase_order(ctx).summary


def test_possible_duplicate_same_amount_and_date_different_number():
    vendor, _ = match_vendor("GreenLeaf Facility Management")
    ctx = RunContext()
    ctx.vendor = vendor
    ctx.fields.update({"invoice_number": "GL-2026-0917-A", "total": 6000.00, "invoice_date": "2026-10-05"})
    result = check_duplicates(ctx)
    assert any(f.rule == "Possible duplicate" and f.outcome == "review" for f in result.findings)


def test_invoices_above_approval_limit_need_review():
    ctx = RunContext()
    ctx.fields.update({"total": config.AUTO_APPROVE_LIMIT + 1})
    result = check_amount(ctx)
    assert any(f.rule == "Approval limit" and f.outcome == "review" for f in result.findings)


def test_ai_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "invalid-key-for-test")
    import core.extract as extract

    def boom(*args, **kwargs):
        raise ConnectionError("network unreachable")

    monkeypatch.setattr(extract, "extract_with_ai", boom)
    path = config.SAMPLES_DIR / "01_happy_path_acme.pdf"
    result = process_invoice(path.read_bytes(), path.name, use_ai=True)
    assert result["extraction"]["method"] == "rules"
    assert "fell back" in result["extraction"]["notes"][0]
    assert result["decision"]["decision"] == "APPROVED"


# ------------------------------------------------------------------ AI path (Gemini mocked - no key or network needed)
def _fake_ai(fields):
    from core.extract import clean_fields
    return lambda *args, **kwargs: clean_fields(fields)


def test_ai_reads_scanned_invoice(monkeypatch):
    """With AI on, the scanned invoice is read and approved instead of being routed to manual entry."""
    import core.extract as extract
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(extract, "extract_with_ai", _fake_ai({
        "vendor_name": "BrightPath IT Services LLC", "invoice_number": "BP-7840", "invoice_date": "2026-11-05",
        "po_number": "PO-1006", "currency": "USD", "subtotal": 1150, "tax": 92, "total": 1242,
        "line_items": [{"description": "Help-desk support retainer - November", "quantity": 1,
                        "unit_price": 1150, "amount": 1150}]}))
    path = config.SAMPLES_DIR / "06_scanned_brightpath.pdf"
    result = process_invoice(path.read_bytes(), path.name, use_ai=True)
    assert result["extraction"]["method"] == "ai"
    assert result["decision"]["decision"] == "APPROVED"


def test_ai_value_not_in_pdf_goes_to_review(monkeypatch):
    """Anti-hallucination: an AI-extracted total that isn't printed on the invoice must not be paid."""
    import core.extract as extract
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(extract, "extract_with_ai", _fake_ai({
        "vendor_name": "Acme Office Supplies Inc.", "invoice_number": "INV-2026-0107", "invoice_date": "2026-11-02",
        "po_number": "PO-1001", "currency": "USD", "subtotal": None, "tax": None, "total": 2402.28, "line_items": []}))
    path = config.SAMPLES_DIR / "01_happy_path_acme.pdf"
    result = process_invoice(path.read_bytes(), path.name, use_ai=True)
    assert result["decision"]["decision"] == "NEEDS_REVIEW"
    assert "not found in the PDF text" in all_text(result)
