"""
The process, end to end:

  PDF -> 1 Read -> 2 Extract -> 3 Validate -> 4 Vendor -> 5 PO match -> 6 Duplicates -> 7 Amount -> 8 Decision

process_invoice() runs the stages in order. After each stage it calls `on_done(stage)`
so the UI can show progress live. Tests call it without callbacks.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Callable

from core import config, db
from core.extract import extract_fields, read_pdf
from core.rules import (Finding, RunContext, StageResult, check_amount, check_duplicates,
                        check_invoice_data, check_purchase_order, check_vendor, money, skipped, stage)


# =============================================================== 1. read document
def stage_read(ctx: RunContext, pdf_bytes: bytes) -> StageResult:
    title = "Read document"
    try:
        text, pages = read_pdf(pdf_bytes)
    except Exception as exc:
        ctx.halt_reason = "the file could not be opened as a PDF."
        return stage("read", title, [Finding(
            "Readable document", "review", f"This file could not be opened as a PDF ({type(exc).__name__}).",
            "Ask the vendor to re-send the invoice as a PDF.")])

    ctx.text = text
    has_text = len(text) >= config.MIN_TEXT_CHARS
    details = {"Pages": pages, "Characters of text": len(text),
               "Text layer": "yes" if has_text else "no - image only (scanned)"}
    if has_text:
        return stage("read", title, [Finding(
            "Readable document", "pass", f"Read {pages} page(s), {len(text):,} characters of machine-readable text.")],
            details=details)
    if ctx.use_ai:
        return stage("read", title, [Finding(
            "Readable document", "warn", "No machine-readable text (looks like a scanned image) - AI will read the page image.")],
            details=details)
    # Rule: if we cannot read it, we do NOT guess - a person keys it in.
    ctx.halt_reason = "the invoice is a scanned image and could not be read."
    return stage("read", title, [Finding(
        "Readable document", "review",
        "No machine-readable text - this looks like a scanned image and AI extraction is off, so nothing can be read reliably.",
        "Key the invoice in manually, or turn on AI extraction (Gemini) to read scanned invoices.")], details=details)


# =============================================================== 2. extract fields
FIELD_LABELS = {
    "vendor_name": "Vendor", "invoice_number": "Invoice #", "invoice_date": "Invoice date",
    "po_number": "PO number", "subtotal": "Subtotal", "tax": "Tax", "total": "Total", "currency": "Currency",
}


def stage_extract(ctx: RunContext, pdf_bytes: bytes) -> StageResult:
    title = "Extract fields"
    result = extract_fields(pdf_bytes, ctx.text, ctx.use_ai)
    ctx.fields, ctx.extraction = result.fields, result
    f = result.fields
    findings = [Finding("Extraction", "warn", note) for note in result.notes]

    # AI fell back to rules on a scanned invoice -> nothing could be read.
    if result.method == "rules" and len(ctx.text) < config.MIN_TEXT_CHARS:
        ctx.halt_reason = "the scanned invoice could not be read."
        findings.append(Finding(
            "Extraction", "review", "Scanned invoice and AI extraction is unavailable - nothing could be extracted.",
            "Key the invoice in manually."))

    # Trust, but verify: values the AI returns must actually appear in the PDF text.
    if result.method == "ai" and len(ctx.text) >= config.MIN_TEXT_CHARS:
        not_found = []
        if f["invoice_number"] and db.normalize_invoice_number(f["invoice_number"]) not in db.normalize_invoice_number(ctx.text):
            not_found.append(f"invoice number '{f['invoice_number']}'")
        if f["total"] is not None and f"{f['total']:,.2f}" not in ctx.text and f"{f['total']:.2f}" not in ctx.text:
            not_found.append(f"total {money(f['total'])}")
        if not_found:
            findings.append(Finding(
                "AI grounding", "review", f"AI-extracted {' and '.join(not_found)} not found in the PDF text.",
                "Check the extracted values against the PDF before paying."))
        else:
            findings.append(Finding("AI grounding", "pass", "Cross-check passed: AI-extracted invoice # and total appear in the PDF text."))

    method = f"AI - Gemini ({result.model})" if result.method == "ai" else "Rules parser (regex)"
    key_fields = ["vendor_name", "invoice_number", "invoice_date", "po_number", "total"]
    found = sum(1 for k in key_fields if f.get(k) not in (None, ""))
    summary = f"{found}/{len(key_fields)} key fields found using {method}."
    if any(x.outcome in ("warn", "review") for x in findings):
        summary += " " + next(x.message for x in findings if x.outcome in ("review", "warn"))

    details = {
        "Method": method,
        "Extracted fields": [{"Field": label, "Value": "-" if f.get(k) in (None, "") else
                              (money(f[k]) if k in ("subtotal", "tax", "total") else str(f[k]))}
                             for k, label in FIELD_LABELS.items()],
        "Line items": [{"Description": i["description"], "Qty": i["quantity"],
                        "Unit price": money(i["unit_price"]), "Amount": money(i["amount"])} for i in f["line_items"]],
    }
    status_findings = findings or [Finding("Extraction", "pass", summary)]
    return stage("extract", title, status_findings, summary=summary, details=details)


# =============================================================== 8. decision
DECISION_LABELS = {"APPROVED": "Approved", "NEEDS_REVIEW": "Needs review", "REJECTED": "Rejected"}


def decide(ctx: RunContext, stages: list[StageResult]) -> dict:
    """Combine all findings. Any REJECT beats any REVIEW, which beats APPROVE."""
    findings = [f for s in stages for f in s.findings]
    rejects = [f for f in findings if f.outcome == "reject"]
    reviews = [f for f in findings if f.outcome == "review"]
    warns = [f for f in findings if f.outcome == "warn"]

    f = ctx.fields
    invoice = f"Invoice {f['invoice_number']}" if f.get("invoice_number") else "Invoice (no number)"
    vendor = (ctx.vendor or {}).get("vendor_name") or f.get("vendor_name") or "an unknown vendor"
    amount = money(f["total"]) if f.get("total") is not None else "an unknown amount"

    if rejects:
        decision = "REJECTED"
        headline = f"{invoice} from {vendor} for {amount} was rejected: {rejects[0].message}"
    elif reviews:
        decision = "NEEDS_REVIEW"
        if ctx.halt_reason:  # we could not even read it
            headline = f"Could not be processed automatically - {ctx.halt_reason} On hold for a person to handle."
        else:
            issues = "1 issue needs" if len(reviews) == 1 else f"{len(reviews)} issues need"
            headline = f"{invoice} from {vendor} for {amount} is on hold - {issues} a person to decide."
    else:
        decision = "APPROVED"
        po_text = f"{ctx.po['po_number']}{' (inferred)' if ctx.po_inferred else ''}" if ctx.po else "its PO"
        headline = f"{invoice} from {vendor} for {amount} matched {po_text} and passed every check - approved for payment."

    actions = []
    # A rejected invoice is not paid, so only the reject actions apply ("do not pay ...").
    for x in (rejects if rejects else reviews):
        if x.action and x.action not in actions:
            actions.append(x.action)
    if decision == "APPROVED":
        terms = (ctx.vendor or {}).get("payment_terms", "")
        actions = [f"Schedule payment{f' ({terms})' if terms else ''}."]

    return {
        "decision": decision,
        "label": DECISION_LABELS[decision],
        "headline": headline,
        "reasons": [x.message for x in rejects + reviews],
        "notes": [x.message for x in warns],
        "next_actions": actions,
        "rule": "Any REJECT finding -> Rejected; else any REVIEW finding -> Needs review; else Approved.",
    }


# =============================================================== run everything
STAGES = [
    ("read", "Read document"),
    ("extract", "Extract fields"),
    ("validate", "Validate invoice data"),
    ("vendor", "Verify vendor"),
    ("po", "Match purchase order"),
    ("duplicate", "Duplicate check"),
    ("amount", "Amount vs PO"),
    ("decision", "Decision"),
]


def process_invoice(pdf_bytes: bytes, file_name: str, use_ai: bool = True,
                    on_start: Callable[[str], None] | None = None,
                    on_done: Callable[[StageResult], None] | None = None) -> dict:
    """Run one invoice through every stage, save it, and return the full result."""
    ctx = RunContext(use_ai=use_ai and bool(config.gemini_api_key()))
    steps = {
        "read": lambda: stage_read(ctx, pdf_bytes),
        "extract": lambda: stage_extract(ctx, pdf_bytes),
        "validate": lambda: check_invoice_data(ctx),
        "vendor": lambda: check_vendor(ctx),
        "po": lambda: check_purchase_order(ctx),
        "duplicate": lambda: check_duplicates(ctx),
        "amount": lambda: check_amount(ctx),
    }

    stages: list[StageResult] = []
    for key, title in STAGES[:-1]:
        if on_start:
            on_start(key)
        result = skipped(key, title, ctx.halt_reason) if ctx.halt_reason else steps[key]()
        stages.append(result)
        if on_done:
            on_done(result)

    if on_start:
        on_start("decision")
    decision = decide(ctx, stages)
    colour = {"APPROVED": "pass", "NEEDS_REVIEW": "warn", "REJECTED": "fail"}[decision["decision"]]
    decision_stage = StageResult("decision", "Decision", colour, decision["headline"])
    stages.append(decision_stage)

    extraction = ctx.extraction
    result = {
        "file_name": file_name,
        "processed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "extraction": {
            "method": getattr(extraction, "method", None),
            "model": getattr(extraction, "model", None),
            "notes": getattr(extraction, "notes", []),
        },
        "fields": ctx.fields,
        "vendor": {k: ctx.vendor[k] for k in ("vendor_id", "vendor_name", "status", "payment_terms")} if ctx.vendor else None,
        "po_number": ctx.po["po_number"] if ctx.po else ctx.fields.get("po_number"),
        "po_inferred": ctx.po_inferred,
        "stages": [asdict(s) for s in stages],
        "decision": decision,
    }
    result["id"] = db.save_run(result)
    if on_done:
        on_done(decision_stage)
    return result
