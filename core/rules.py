"""
The business rules - the "AP clerk's checklist" written as code.

Every check returns a StageResult made of Findings. Each Finding has an OUTCOME:
    pass   - all good
    warn   - fine, but worth a note on the decision (e.g. "PO was inferred")
    review - a human must look at this before paying       -> NEEDS REVIEW
    reject - this must not be paid                          -> REJECTED

The final decision (pipeline.decide) is simply:  any reject > any review > approve.
Rules are deterministic on purpose: the same invoice always gets the same answer,
and every decision can be explained line by line.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from core import config, db
from core.extract import empty_fields


# =============================================================== data structures
@dataclass
class Finding:
    rule: str
    outcome: str          # pass | warn | review | reject
    message: str
    action: str = ""      # what a person should do next (for review / reject)


@dataclass
class StageResult:
    key: str
    title: str
    status: str = "pass"  # pass | warn | fail | skip  (drives the colour in the UI)
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    details: dict = field(default_factory=dict)


@dataclass
class RunContext:
    """Everything we learn about one invoice as it moves through the stages."""
    use_ai: bool = False
    text: str = ""
    fields: dict = field(default_factory=empty_fields)
    extraction: object = None
    vendor: dict | None = None     # matched row from the vendor master
    po: dict | None = None         # matched PO (+ billed_to_date, remaining)
    po_inferred: bool = False
    halt_reason: str = ""          # set when it makes no sense to continue


def stage(key: str, title: str, findings: list[Finding], summary: str = "", details: dict | None = None) -> StageResult:
    """Build a StageResult; its colour comes from the worst finding."""
    outcomes = {f.outcome for f in findings}
    if outcomes & {"reject", "review"}:
        status = "fail"
    elif "warn" in outcomes:
        status = "warn"
    else:
        status = "pass"
    if not summary:
        worst = [f for f in findings if f.outcome in ("reject", "review")] or \
                [f for f in findings if f.outcome == "warn"] or findings
        summary = worst[0].message if worst else ""
    return StageResult(key, title, status, summary, findings, details or {})


def skipped(key: str, title: str, reason: str) -> StageResult:
    return StageResult(key, title, "skip", f"Skipped - {reason}")


def money(value) -> str:
    return "-" if value is None else f"${value:,.2f}"


# =============================================================== 3. validate invoice data
REQUIRED_FIELDS = {
    "vendor_name": "vendor name",
    "invoice_number": "invoice number",
    "invoice_date": "invoice date",
    "total": "total amount",
}


def check_invoice_data(ctx: RunContext) -> StageResult:
    f = ctx.fields
    findings = []

    # Rule: the fields we need to pay an invoice must be present.
    missing = [label for key, label in REQUIRED_FIELDS.items() if f.get(key) in (None, "")]
    if missing:
        findings.append(Finding(
            "Required fields", "review",
            f"Missing required field(s): {', '.join(missing)}.",
            "Request a corrected invoice from the vendor with the missing details."))
    else:
        findings.append(Finding("Required fields", "pass", "All required fields present (vendor, invoice #, date, total)."))

    # Rule: an invoice cannot be dated in the future.
    if f.get("invoice_date") and f["invoice_date"] > date.today().isoformat():
        findings.append(Finding(
            "Invoice date", "review", f"Invoice is dated in the future ({f['invoice_date']}).",
            "Confirm the invoice date with the vendor."))

    # Rule: line items (+ tax) must add up to the total. Catches vendor errors AND extraction errors.
    items, total, tax, subtotal = f["line_items"], f["total"], f["tax"], f["subtotal"]
    lines_sum = round(sum(i["amount"] or 0 for i in items), 2) if items else None
    if total is not None:
        if not items:
            findings.append(Finding("Arithmetic", "warn", "No itemised lines found (bundled invoice) - line arithmetic could not be verified."))
        else:
            base = subtotal if subtotal is not None else lines_sum
            expected = round(base + (tax or 0), 2)
            if subtotal is not None and abs(lines_sum - subtotal) > config.ARITHMETIC_TOLERANCE:
                findings.append(Finding(
                    "Arithmetic", "review",
                    f"Line items add up to {money(lines_sum)} but the subtotal says {money(subtotal)}.",
                    "Ask the vendor to correct the invoice calculation."))
            elif abs(expected - total) > config.ARITHMETIC_TOLERANCE:
                findings.append(Finding(
                    "Arithmetic", "review",
                    f"Line items + tax = {money(expected)}, but the invoice total says {money(total)}.",
                    "Ask the vendor to correct the invoice calculation."))
            else:
                tax_text = f"tax {money(tax)}" if tax is not None else "tax included in prices"
                findings.append(Finding("Arithmetic", "pass", f"Arithmetic checks out: {money(base)} + {tax_text} = {money(total)}."))

    details = {
        "Line items found": len(items),
        "Sum of line items": money(lines_sum),
        "Subtotal on invoice": money(subtotal),
        "Tax on invoice": money(tax) if tax is not None else "not stated separately",
        "Total on invoice": money(total),
    }
    return stage("validate", "Validate invoice data", findings, details=details)


# =============================================================== 4. verify vendor
COMPANY_SUFFIXES = {"inc", "llc", "ltd", "limited", "co", "corp", "corporation", "company", "pvt", "plc", "the", "gmbh"}


def _normalize_company(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    return " ".join(w for w in words if w not in COMPANY_SUFFIXES)


def match_vendor(name: str) -> tuple[dict | None, float]:
    """Find the closest vendor-master entry (by name or alias). Returns (row, similarity 0-1)."""
    best_row, best_score = None, 0.0
    target = _normalize_company(name)
    for row in db.load_vendors().to_dict("records"):
        for candidate in [row["vendor_name"], *row["aliases"].split("|")]:
            if not candidate:
                continue
            score = SequenceMatcher(None, target, _normalize_company(candidate)).ratio()
            if score > best_score:
                best_row, best_score = row, score
    return best_row, best_score


def check_vendor(ctx: RunContext) -> StageResult:
    name = ctx.fields.get("vendor_name")
    if not name:
        return stage("vendor", "Verify vendor", [Finding(
            "Approved vendor", "review", "Vendor name could not be found on the invoice.",
            "Identify the vendor manually before processing.")])

    row, score = match_vendor(name)
    details = {"Name on invoice": name, "Closest vendor-master match": row["vendor_name"] if row else "-",
               "Name similarity": f"{score:.0%}", "Required similarity": f"{config.VENDOR_MATCH_THRESHOLD:.0%}"}

    # Rule: only pay vendors that are in the approved vendor master ...
    if score < config.VENDOR_MATCH_THRESHOLD:
        return stage("vendor", "Verify vendor", [Finding(
            "Approved vendor", "reject",
            f"'{name}' is not in the approved vendor master (closest: {row['vendor_name']}, {score:.0%} similar).",
            "Do not pay. Send to procurement to verify and onboard this vendor first.")], details=details)

    # ... and whose status is active.
    details.update({"Vendor ID": row["vendor_id"], "Status": row["status"], "Payment terms": row["payment_terms"]})
    if row["status"] != "active":
        return stage("vendor", "Verify vendor", [Finding(
            "Approved vendor", "reject",
            f"{row['vendor_name']} is '{row['status']}' in the vendor master ({row['notes']}).",
            "Do not pay until procurement re-activates this vendor.")], details=details)

    ctx.vendor = row
    return stage("vendor", "Verify vendor", [Finding(
        "Approved vendor", "pass",
        f"Matched to {row['vendor_name']} ({row['vendor_id']}) - {score:.0%} name match, status active.")], details=details)


# =============================================================== 5. match purchase order
def _with_balance(po: dict) -> dict:
    po = dict(po)
    po["billed_to_date"] = db.billed_to_date(po["po_number"])
    po["remaining"] = round(po["po_amount"] - po["billed_to_date"], 2)
    return po


def _po_table(pos: list[dict]) -> list[dict]:
    return [{"PO": p["po_number"], "Description": p["description"], "PO amount": money(p["po_amount"]),
             "Billed to date": money(p["billed_to_date"]), "Remaining": money(p["remaining"]),
             "Status": p["status"]} for p in pos]


def check_purchase_order(ctx: RunContext) -> StageResult:
    title = "Match purchase order"
    f, vendor = ctx.fields, ctx.vendor
    all_pos = db.load_purchase_orders().to_dict("records")
    po_number = f.get("po_number")

    # ---- Case A: the invoice quotes a PO number explicitly
    if po_number:
        po = next((p for p in all_pos if p["po_number"] == po_number), None)
        if po is None:
            return stage("po", title, [Finding(
                "PO match", "review", f"{po_number} (quoted on the invoice) does not exist in the PO register.",
                "Ask the vendor / requester for the correct PO number.")])
        po = _with_balance(po)
        details = {"PO details": _po_table([po])}
        owner = db.load_vendors().set_index("vendor_id").loc[po["vendor_id"], "vendor_name"]
        if vendor is None or po["vendor_id"] != vendor["vendor_id"]:
            issuer = vendor["vendor_name"] if vendor else f"'{f.get('vendor_name')}'"
            return stage("po", title, [Finding(
                "PO match", "review", f"{po_number} belongs to {owner}, not to {issuer}.",
                "Possible mis-billing or fraud - verify with procurement before paying.")], details=details)
        if po["status"] != "open":
            return stage("po", title, [Finding(
                "PO match", "review", f"{po_number} is {po['status']} - no further invoices should be billed to it.",
                "Confirm with procurement whether this PO should be re-opened.")], details=details)
        ctx.po = po
        return stage("po", title, [Finding(
            "PO match", "pass", f"Invoice quotes {po_number} - found in the PO register: '{po['description']}', "
                                f"{money(po['po_amount'])}, open.")], details=details)

    # ---- Case B: no PO on the invoice -> try to infer it
    if vendor is None:
        return skipped("po", title, "no PO quoted and the vendor is not recognised, so there is nothing to match.")
    total = f.get("total")
    if total is None:
        return stage("po", title, [Finding(
            "PO match", "review", "No PO number on the invoice and no total to infer one from.",
            "Ask the vendor for the PO reference.")])

    vendor_pos = [_with_balance(p) for p in all_pos if p["vendor_id"] == vendor["vendor_id"]]
    open_pos = [p for p in vendor_pos if p["status"] == "open"]
    candidates = [p for p in open_pos
                  if total <= p["remaining"] + p["po_amount"] * config.PRICE_TOLERANCE_PCT / 100]
    details = {"POs considered for this vendor": _po_table(vendor_pos),
               "Inference rule": "open PO, same vendor, remaining balance (+ tolerance) covers the invoice total"}

    if len(candidates) == 1:
        ctx.po, ctx.po_inferred = candidates[0], True
        return stage("po", title, [Finding(
            "PO match", "warn",
            f"No PO number on the invoice. Inferred {candidates[0]['po_number']} - the only open PO for "
            f"{vendor['vendor_name']} with enough remaining balance ({money(candidates[0]['remaining'])}).")],
            details=details)
    if not candidates:
        return stage("po", title, [Finding(
            "PO match", "review",
            f"No PO number on the invoice, and no open PO for {vendor['vendor_name']} can cover {money(total)}.",
            "Ask the requester to raise a PO, or ask the vendor for the PO reference.")], details=details)
    names = ", ".join(p["po_number"] for p in candidates)
    return stage("po", title, [Finding(
        "PO match", "review",
        f"No PO number on the invoice and {len(candidates)} open POs could match ({names}) - ambiguous.",
        "Ask the vendor which PO this invoice is for.")], details=details)


# =============================================================== 6. duplicate check
def check_duplicates(ctx: RunContext) -> StageResult:
    title = "Duplicate check"
    f, vendor = ctx.fields, ctx.vendor
    if vendor is None:
        return skipped("duplicate", title, "vendor not recognised, so there is no history to compare against.")

    findings = []
    number = f.get("invoice_number")
    norm = db.normalize_invoice_number(number)

    # Rule 1: same vendor + same invoice number (after normalising formatting) = duplicate -> reject.
    if norm:
        same = db.find_same_invoice_number(vendor["vendor_id"], norm)
        if same:
            prev = same[0]
            reformatted = " (same number, formatted differently)" if prev["invoice_number"] != number else ""
            findings.append(Finding(
                "Duplicate invoice", "reject",
                f"Duplicate: invoice '{number}' matches '{prev['invoice_number']}'{reformatted}, already processed "
                f"on {prev['processed_at'][:10]} for {money(prev['total'])} (status {prev['status']}).",
                "Do not pay again. Tell the vendor this invoice was already processed."))
    else:
        findings.append(Finding("Duplicate invoice", "warn", "Invoice number missing - exact duplicate check not possible."))

    # Rule 2: same vendor + same amount + same date but a different number = possible duplicate -> review.
    if f.get("total") is not None and f.get("invoice_date"):
        lookalikes = [r for r in db.find_same_amount_and_date(vendor["vendor_id"], f["total"], f["invoice_date"])
                      if r["invoice_number_norm"] != norm]
        if lookalikes:
            other = lookalikes[0]
            findings.append(Finding(
                "Possible duplicate", "review",
                f"Possible duplicate: invoice '{other['invoice_number']}' has the same amount ({money(f['total'])}) "
                f"and date ({f['invoice_date']}) under a different number.",
                "Compare both invoices before paying."))

    if not findings:
        findings.append(Finding("Duplicate invoice", "pass",
                                "No earlier invoice from this vendor with the same number, or the same amount and date."))
    details = {"Invoice number (as printed)": number or "-", "Normalised for comparison": norm or "-",
               "Searched": f"all non-rejected invoices from {vendor['vendor_name']}"}
    return stage("duplicate", title, findings, details=details)


# =============================================================== 7. amount vs PO
def check_amount(ctx: RunContext) -> StageResult:
    title = "Amount vs PO"
    total = ctx.fields.get("total")
    if total is None:
        return skipped("amount", title, "no total amount found on the invoice.")

    findings, details = [], {}

    # Rule: big invoices always need a human approver.
    if total > config.AUTO_APPROVE_LIMIT:
        findings.append(Finding(
            "Approval limit", "review",
            f"{money(total)} is above the auto-approval limit of {money(config.AUTO_APPROVE_LIMIT)}.",
            "Route to the finance manager for approval."))

    po = ctx.po
    if po:
        amount, billed = po["po_amount"], po["billed_to_date"]
        remaining = round(amount - billed, 2)
        over = round(total - remaining, 2)
        tolerance = round(amount * config.PRICE_TOLERANCE_PCT / 100, 2)
        details = {
            "PO": po["po_number"], "PO amount": money(amount),
            "Already billed (approved invoices)": money(billed),
            "Remaining before this invoice": money(remaining), "This invoice": money(total),
            "Remaining after this invoice": money(remaining - total),
            "Tolerance": f"{config.PRICE_TOLERANCE_PCT}% of PO = {money(tolerance)}",
        }
        # Rule: invoice must fit in what's left on the PO (allowing a small tolerance).
        if over <= 0.005:
            message = f"{money(total)} fits within the remaining PO balance of {money(remaining)}."
            if billed > 0:
                message += (f" Split billing: {money(billed)} was already billed on {po['po_number']}; "
                            f"{money(billed + total)} of {money(amount)} used after this invoice.")
            elif total < amount - 0.005:
                message += f" Partial billing - {money(remaining - total)} stays open on the PO."
            findings.append(Finding("PO balance", "pass", message))
        elif over <= tolerance:
            findings.append(Finding(
                "PO balance", "warn",
                f"{money(total)} is {money(over)} ({over / amount:.1%}) above the remaining PO balance of "
                f"{money(remaining)} - within the {config.PRICE_TOLERANCE_PCT:g}% tolerance, accepted."))
        else:
            earlier = f" {money(billed)} was already billed by earlier invoices." if billed > 0 else ""
            findings.append(Finding(
                "PO balance", "review",
                f"This invoice would bring {po['po_number']} to {money(billed + total)} billed against a "
                f"{money(amount)} PO - {money(over)} ({over / amount:.1%}) over, beyond the "
                f"{config.PRICE_TOLERANCE_PCT:g}% tolerance.{earlier}",
                "Hold payment. Ask procurement for a PO amendment, or ask the vendor for a credit note."))
    elif not findings:
        return skipped("amount", title, "no matched PO to compare the amount against.")

    return stage("amount", title, findings, details=details)
