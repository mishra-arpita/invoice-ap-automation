"""
Step 1 + 2 of the process: read the PDF and pull out the invoice fields.

Two ways to extract fields:
  * AI (Google Gemini) - we send the PDF itself to the model and ask for JSON.
    Handles any layout, and can even read scanned (image-only) invoices.
  * Rules (regex)     - looks for common labels ("Invoice No", "Bill #", "PO Number",
    "Your Ref", "Total Due" ...). Works offline, no API key needed.

If AI is switched on but fails (no internet, quota, bad key), we automatically fall
back to the rules parser so the process never stops.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

from core import config

FIELD_NAMES = [
    "vendor_name", "invoice_number", "invoice_date", "po_number",
    "currency", "subtotal", "tax", "total",
]


def empty_fields() -> dict:
    fields = {name: None for name in FIELD_NAMES}
    fields["line_items"] = []
    return fields


@dataclass
class ExtractionResult:
    fields: dict
    method: str                      # "ai" or "rules"
    model: str | None = None         # Gemini model used (AI only)
    notes: list[str] = field(default_factory=list)


# =============================================================== reading the PDF
def read_pdf(pdf_bytes: bytes) -> tuple[str, int]:
    """Return (all text in the PDF, number of pages). Raises if it's not a valid PDF."""
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        pages = len(pdf.pages)
        text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    return text.strip(), pages


# =============================================================== small cleaners
def parse_amount(value) -> float | None:
    """'$2,420.28' / 'USD 2420.28' / 2420.28  ->  2420.28"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    cleaned = re.sub(r"[^\d.\-]", "", str(value))
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return None


DATE_FORMATS = [
    "%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y",
    "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d.%m.%Y",
]


def parse_date(value) -> str | None:
    """Return the date as YYYY-MM-DD, or None if we can't read it.
    Assumption: numeric dates like 05/11/2026 are day/month/year."""
    if not value:
        return None
    text = str(value).strip().replace(".,", ",")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def normalize_po(value) -> str | None:
    """'PO 1002' / 'po#1002' / 'PO-1002'  ->  'PO-1002'"""
    if not value:
        return None
    match = re.search(r"P\.?O\.?\s*[-#:]?\s*(\d{3,})", str(value), re.IGNORECASE)
    return f"PO-{match.group(1)}" if match else str(value).strip().upper()


def clean_fields(raw: dict) -> dict:
    """Make AI or regex output consistent: numbers as floats, dates as ISO, PO as PO-1234."""
    fields = empty_fields()
    raw = raw or {}
    for key in ("vendor_name", "invoice_number"):
        value = raw.get(key)
        fields[key] = str(value).strip() if value not in (None, "", "null") else None
    fields["invoice_date"] = parse_date(raw.get("invoice_date"))
    fields["po_number"] = normalize_po(raw.get("po_number"))
    fields["currency"] = str(raw["currency"]).upper() if raw.get("currency") else None
    for key in ("subtotal", "tax", "total"):
        fields[key] = parse_amount(raw.get(key))
    for item in raw.get("line_items") or []:
        fields["line_items"].append({
            "description": str(item.get("description") or "").strip(),
            "quantity": parse_amount(item.get("quantity")),
            "unit_price": parse_amount(item.get("unit_price")),
            "amount": parse_amount(item.get("amount")),
        })
    return fields


# =============================================================== AI extraction (Gemini)
AI_PROMPT = """You are an accounts-payable assistant. Extract data from the attached vendor invoice.

Return ONLY a JSON object with exactly these keys:
{
  "vendor_name": string,        // the company that ISSUED the invoice (not the bill-to customer)
  "invoice_number": string,
  "invoice_date": "YYYY-MM-DD",
  "po_number": string,          // buyer's purchase order ref (may be labelled PO, Purchase Order, Your Ref)
  "currency": string,           // e.g. "USD"
  "line_items": [{"description": string, "quantity": number, "unit_price": number, "amount": number}],
  "subtotal": number,
  "tax": number,                // separately stated tax amount
  "total": number               // final amount payable
}

Rules:
- Use null for anything that is not clearly printed on the invoice. NEVER guess or invent values.
- If tax is included in the prices and not shown as a separate amount, set "tax" to null.
- Numbers must be plain numbers (no currency symbols, no thousands separators).
"""


def extract_with_ai(pdf_bytes: bytes, api_key: str, model: str) -> dict:
    """Send the PDF to Gemini and get back the invoice fields as JSON."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=config.AI_TIMEOUT_SECONDS * 1000),
    )
    response = client.models.generate_content(
        model=model,
        contents=[
            types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
            AI_PROMPT,
        ],
        config=types.GenerateContentConfig(
            temperature=0,                       # same input -> same output
            response_mime_type="application/json",
        ),
    )
    data = json.loads(response.text)
    if isinstance(data, list):  # some models wrap the object in a list
        data = data[0] if data else {}
    return clean_fields(data)


# =============================================================== rules extraction (regex)
# Different vendors use different labels for the same thing - we list the synonyms.
LABELS = {
    "invoice_number": ["invoice number", "invoice no", "invoice #", "inv no", "bill no", "bill #", "bill number"],
    "invoice_date": ["invoice date", "date of issue", "bill date", "issue date", "date"],
    "po_number": ["po number", "po no", "po #", "purchase order", "your ref", "p.o."],
    "subtotal": ["subtotal", "sub-total", "sub total", "net amount"],
    "tax": ["sales tax", "tax", "vat", "gst"],
    "total": ["total due", "amount due", "balance due", "grand total", "total"],
}

AMOUNT = r"(?:USD|\$)?\s?([\d,]+\.\d{2})"
INVOICE_NO = r"([A-Z]{0,5}[ \-/]?\d[\w\-/]*)"
PO_REF = r"(P\.?O\.?[ \-#]?\d{3,})"
DATE = (
    r"(\d{4}-\d{2}-\d{2}"                       # 2026-11-03
    r"|[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}"       # Nov 02, 2026 / November 4, 2026
    r"|\d{1,2}[ \-][A-Z][a-z]{2,8}[ \-]\d{4}"    # 02-Nov-2026 / 2 Nov 2026
    r"|\d{1,2}[/.]\d{1,2}[/.]\d{4})"             # 05/11/2026
)
VALUE_PATTERNS = {
    "invoice_number": INVOICE_NO,
    "invoice_date": DATE,
    "po_number": PO_REF,
    "subtotal": AMOUNT,
    "tax": AMOUNT,
    "total": AMOUNT,
}
LINE_ITEM = re.compile(
    r"^(?P<description>.+?)\s+(?P<quantity>\d+(?:\.\d+)?)\s+\$?(?P<unit_price>[\d,]+\.\d{2})"
    r"\s+\$?(?P<amount>[\d,]+\.\d{2})$"
)


def _label_regex(label: str) -> str:
    # "date" must not match "Due Date"; "total" must not match "Subtotal".
    return r"(?<![A-Za-z])" + re.escape(label).replace(r"\ ", r"\s+")


def _find(lines: list[str], field_name: str):
    value_pattern = VALUE_PATTERNS[field_name]
    for label in LABELS[field_name]:
        pattern = re.compile(_label_regex(label) + r"\s*[:#.]?\s*.{0,25}?" + value_pattern, re.IGNORECASE)
        for line in lines:
            low = line.lower()
            if field_name == "tax" and ("total" in low or "incl" in low):
                continue  # "Total (incl. tax)" is the total, not the tax amount
            if field_name == "total" and "sub" in low:
                continue  # "Sub-total" is not the total
            if label == "date" and "due" in low:
                continue
            match = pattern.search(line)
            if match:
                return match.group(1).strip()
    return None


def _guess_vendor_name(lines: list[str]) -> str | None:
    """Vendors print their own name at the top. Take the first real line of text."""
    for line in lines[:5]:
        name = re.sub(r"\b(TAX\s+)?INVOICE\b", "", line, flags=re.IGNORECASE).strip(" -|:")
        if len(name) >= 3 and not re.match(r"^(page|date|bill to)\b", name, re.IGNORECASE):
            return name
    return None


def extract_with_rules(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    raw = {name: _find(lines, name) for name in VALUE_PATTERNS}
    raw["vendor_name"] = _guess_vendor_name(lines)

    # PO number not labelled? Look for anything shaped like "PO-1234" anywhere in the text.
    if not raw["po_number"]:
        anywhere = re.search(r"\bP\.?O\.?[ \-#]?\d{3,}\b", text, re.IGNORECASE)
        raw["po_number"] = anywhere.group(0) if anywhere else None

    raw["currency"] = "USD" if ("$" in text or "USD" in text) else None
    raw["line_items"] = [m.groupdict() for ln in lines if (m := LINE_ITEM.match(ln))]
    return clean_fields(raw)


# =============================================================== choose the method
def extract_fields(pdf_bytes: bytes, text: str, use_ai: bool) -> ExtractionResult:
    """Use AI if enabled (and a key exists); otherwise, or on any AI error, use rules."""
    notes = []
    api_key = config.gemini_api_key()
    if use_ai and api_key:
        model = config.gemini_model()
        try:
            return ExtractionResult(extract_with_ai(pdf_bytes, api_key, model), "ai", model)
        except Exception as exc:  # network down, quota, invalid key, bad JSON ...
            reason = getattr(exc, "message", None) or str(exc) or type(exc).__name__
            reason = str(reason).splitlines()[0][:140]
            notes.append(f"AI extraction failed ({reason}) - fell back to the rules parser.")
    elif use_ai and not api_key:
        notes.append("No GEMINI_API_KEY configured - used the rules parser.")
    return ExtractionResult(extract_with_rules(text), "rules", None, notes)
