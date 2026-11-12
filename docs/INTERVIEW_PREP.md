# Interview prep: understand and explain this project

This guide is for the person presenting InvoiceFlow. Read it top to bottom once (about 45 minutes), run the demo twice, and you'll be able to answer almost any question about it.

---

## 1. The 60-second pitch

> "Accounts payable teams receive hundreds of vendor invoices as PDFs. For each one, someone finds the purchase order, checks the numbers, makes sure it isn't a duplicate, and decides whether to pay. It's repetitive, and mistakes are expensive.
>
> InvoiceFlow automates that. You give it an invoice PDF and it runs 8 stages: read the document, extract the fields, validate them, verify the vendor, match the PO, check for duplicates, check the amount against the PO's remaining balance, and decide. The output is **Approved**, **Needs review** or **Rejected**, with the reasons and the next step.
>
> AI (Gemini) reads the invoice, because layouts vary and some are scans. The decision is made by clear rules, because paying money must be predictable and explainable. Anything uncertain goes to a human review queue instead of being guessed. A dashboard shows the history, the exception reasons and the review queue."

---

## 2. Key terms (AP basics in 2 minutes)

| Term | Meaning |
|---|---|
| **AP (Accounts Payable)** | The finance team that pays the company's bills to vendors. |
| **Vendor** | A supplier who sends us an invoice. |
| **Vendor master** | The list of approved vendors we're allowed to pay. |
| **PO (Purchase Order)** | Our approval to buy something, created *before* the purchase, with a max amount. |
| **Invoice** | The vendor's bill, sent *after* delivering. It should reference our PO. |
| **2-way match** | Invoice ↔ PO: does the bill match what we approved? (This project does this.) |
| **3-way match** | Invoice ↔ PO ↔ goods receipt: did we actually receive the goods? (Future step.) |
| **Tolerance** | A small allowed difference (2% here) so tiny variances don't need a human. |
| **Split billing / partial invoicing** | One PO billed through several invoices over time. |
| **Duplicate payment** | Paying the same invoice twice, one of the most common AP losses. |
| **Payment terms (Net 30)** | Pay within 30 days of the invoice date. |

---

## 3. Learn the code in one hour (reading order)

Read in this order. Each file is short and commented.

1. **`core/config.py`** (2 min): all thresholds. Tolerance 2%, approval limit $25k, fuzzy-match 85%, the Gemini model and key.
2. **`data/*.csv`** (3 min): the vendor master, PO register and invoice history. This is the "procurement system".
3. **`core/pipeline.py`** (10 min): **start here for the logic.**
   - `process_invoice()` runs the 8 stages in order and calls `on_start` and `on_done` so the UI can update live.
   - `stage_read()` checks for a text layer.
   - `stage_extract()` picks AI or rules and cross-checks AI values against the PDF text.
   - `decide()` applies **reject > review > approve** and builds the headline, reasons and next steps.
4. **`core/rules.py`** (15 min): the business rules.
   - `Finding` = one check result. `StageResult` = one stage (card).
   - `check_invoice_data()` covers required fields, future dates and arithmetic.
   - `check_vendor()` / `match_vendor()` do fuzzy name matching with `difflib.SequenceMatcher`, after removing "Inc", "LLC", "Ltd"...
   - `check_purchase_order()` handles an explicit PO (exists? same vendor? open?) **or** infers one (exactly one open PO that fits).
   - `check_duplicates()` handles exact duplicates (normalised number) and possible duplicates (same amount + date).
   - `check_amount()` compares against the **remaining** PO balance, applies the tolerance and the approval limit.
5. **`core/extract.py`** (10 min):
   - `read_pdf()` uses pdfplumber.
   - `extract_with_ai()` sends the PDF bytes to Gemini and asks for JSON.
   - `extract_with_rules()` does regex label matching: "Invoice No / Bill # / Invoice Number", "PO Number / Your Ref / Purchase Order"...
   - `extract_fields()` chooses AI or rules and falls back on error.
6. **`core/db.py`** (5 min):
   - SQLite table `invoices`.
   - `billed_to_date()` and `find_same_invoice_number()` are what the rules use.
   - `record_review()` is the human approval.
7. **`views/*.py` + `core/ui.py`** (10 min): the Streamlit pages. `views/process.py` shows how the live run view works: placeholders that are updated by `on_start` and `on_done`.
8. **`tests/test_pipeline.py`** (5 min): every scenario is tested. Run `pytest -q`.

---

## 4. Design decisions and why

| Decision | Why |
|---|---|
| **AI extracts, rules decide** | Extraction from messy layouts is a language task, so AI is good at it. The pay/don't-pay decision must be deterministic, explainable and auditable. An LLM could give different answers for the same invoice, and you can't audit a "vibe". |
| **The PDF goes to Gemini directly, not text** | Gemini can then also read **scanned** invoices (images). One code path handles both. |
| **The prompt says "return null, never guess"** | A wrong number is worse than a missing number. Missing → review is safe; a hallucinated total could get paid. |
| **AI output is cross-checked against the PDF text** | Trust but verify. If the AI's invoice number or total doesn't appear in the document, the invoice goes to review. The arithmetic check (lines + tax = total) also catches misreads. |
| **The rules parser is a fallback** | The process must never stop. No key, no internet, quota exceeded → it still runs. It also means the demo can't break because of an API. |
| **Three outcomes (approve / review / reject)** | Most real exceptions are judgement calls, such as over-billing that procurement may approve. Rejecting them would be wrong, and approving them would be risky. "Review" puts a human in the loop exactly where judgement is needed. |
| **Reject only for clear-cut cases** | An unknown or blocked vendor (fraud risk) and an exact duplicate (already paid). Everything else is review. |
| **Compare against the *remaining* PO balance** | Vendors split POs across invoices. Checking each invoice against the full PO would miss cumulative over-billing. |
| **PO inference only with exactly one candidate** | Saves manual work on the common "no PO quoted" case, but never guesses between two POs. |
| **Normalise invoice numbers** | Re-sent duplicates often differ only in formatting (`INV 2026/0042` vs `INV-2026-0042`). |
| **2% tolerance** | Small differences (rounding, freight, a small price change) aren't worth a human's time. It's in config, so the AP manager decides. |
| **$25k approval limit** | High-value invoices should always have human sign-off, even when everything matches. It's a common finance control. |
| **All thresholds in `config.py`** | Policy changes without code changes. |
| **Every run stores its full trace** | An audit trail: you can always answer "why did we pay this?" months later. |
| **Streamlit + SQLite + CSV** | The fastest way to build a working, demo-able POC in a few days. It's pure Python with no separate frontend. The CSVs stand in for the ERP. |

---

## 5. The edge cases: what to say about each

**1. Invoice with no PO number** (`03_no_po_reference_nimbus`)
- *Why it's realistic:* many vendors don't quote the PO, especially for subscriptions and services.
- *What the process does:* it looks at the vendor's **open** POs and keeps those whose remaining balance (+ tolerance) can cover the invoice. Exactly one → it uses that PO, with a note. Zero or 2+ → review.
- *Interesting detail:* Nimbus also has last year's PO (PO-0998), which is **closed**, so it is ignored.

**2. Split billing that over-bills the PO** (`04_split_po_overbilled_greenleaf`)
- *Why it's realistic:* facility and service vendors bill monthly against one PO.
- *What the process does:* billed-to-date ($6,000 from the ledger) + this invoice ($5,000) = $11,000 against a $10,000 PO → 10% over, beyond 2% → review, with the next step "PO amendment or credit note".
- *Key point:* this invoice alone ($5,000 against $10,000) looks fine. You only catch it by remembering earlier invoices.

**3. Disguised duplicate** (`05_duplicate_resubmitted_acme`)
- *Why it's realistic:* vendors re-send invoices as "reminders", sometimes re-typed.
- *What the process does:* it normalises the number (removes spaces, dashes and slashes and uppercases it), finds the paid `INV-2026-0042` → **reject**.
- *Also:* a second rule flags "same vendor + same amount + same date, different number" as a *possible* duplicate → review.

**4. Scanned invoice** (`06_scanned_brightpath`)
- *Why it's realistic:* the brief says some invoices are scanned images.
- *What the process does:* no text layer + no AI → it does **not** guess; it stops and routes to a person ("key in manually"). With AI on, Gemini reads the image.
- *Key point:* graceful degradation. The process knows what it doesn't know.

The supporting cases (not the main edge cases):
- `02`: a different layout, 1.5% over, within tolerance → approved with a note.
- `07`: unknown vendor quoting **another vendor's** PO → rejected, and also flagged as possible fraud.
- `08`: missing invoice number and date → review.

---

## 6. Likely interview questions with answers

**Q: Why not just let the LLM make the decision?**
Paying money needs consistency and an audit trail. The same invoice must always get the same answer, and finance must be able to explain every payment. Rules give that. AI is used where it adds value: reading unstructured documents.

**Q: What if Gemini extracts a wrong number?**
There are three safety nets:
1. The AI-extracted total and invoice # must appear in the PDF text, or the invoice goes to review.
2. Line items + tax must add up to the total.
3. The total must fit the PO balance.

A misread value almost always fails one of these.

**Q: What happens if the API is down?**
It falls back to the rules parser automatically and shows a note. The test `test_ai_failure_falls_back_to_rules` covers this.

**Q: How does fuzzy vendor matching work? Couldn't it match the wrong vendor?**
It lowercases names, removes punctuation and suffixes like Inc / LLC / Ltd, and compares them with `SequenceMatcher` against each vendor name and alias. It needs ≥85% similarity. "Quantum Print Solutions" scores 42% against the closest vendor, so it is rejected. The threshold is configurable. In production I'd also match on tax ID and bank account.

**Q: Why is a duplicate rejected but over-billing only sent to review?**
A duplicate is certain: we already paid it, and paying again is always wrong. Over-billing might be legitimate (the scope grew and procurement will amend the PO), so a human decides.

**Q: How did you pick 2% tolerance and $25,000?**
They're common, reasonable defaults for a mid-size company. The point is that they live in config, so the AP manager sets the policy. I'd tune them by looking at historical exception rates.

**Q: How would this scale to hundreds of invoices a month?**
Each invoice takes about a second with rules, and a few seconds with AI. Next steps:
- read the AP mailbox automatically;
- process invoices in a background queue;
- move to Postgres;
- connect to the ERP for live POs and vendors.

The rule logic doesn't change.

**Q: How do you measure if it's working?**
The dashboard shows the auto-approval rate, the review queue size and the top exception reasons. In production I'd also track how often reviewers overturn the process's decision; that is the false-positive rate.

**Q: What's the hardest part of this problem?**
Matching when information is incomplete: no PO, split invoices, near-duplicates. That's why my edge cases focus there.

**Q: What would you build next?**
- 3-way match with goods receipts.
- Line-level matching.
- Email intake.
- An auto-drafted email to the vendor when information is missing.
- Multi-currency support.
- Approval routing by amount and department.

**Q: What are the limitations?**
- Header-level matching only.
- One currency.
- CSV instead of an ERP.
- The rules parser only knows common label names; truly unusual layouts need the AI path.
- A single user, no login.

**Q: Did you use AI tools to build this?**
Yes. Be honest. The brief encourages it ("Use AI tools — and be deliberate about it"). Say which ones you used, and show you understand every part. That's what this guide is for.

**Q: Why Streamlit?**
It's pure Python, so the logic and the UI are in one language. It has built-in components for the live updates and the dashboard, and free one-click hosting for a live link. For a POC, speed and reliability matter more than a custom frontend.

---

## 7. Make it your own (do at least 2–3 of these)

The interviewer will ask about choices, so it should feel like *your* project. Each of these is small:

- **Rename the company and vendors.** Edit `data/*.csv`, `COMPANY_NAME` in `config.py`, and the names in `scripts/generate_sample_invoices.py`, then regenerate the PDFs (`python scripts/generate_sample_invoices.py`). Update `EXPECTED` in the tests if names change.
- **Switch to INR and GST.** Change the currency text and tax labels. A good talking point for Indian companies.
- **Add a rule.** For example: "invoice date older than 180 days → review", "vendor is on a watch-list → review", or "line item quantity × unit price ≠ amount → review". Add it in `rules.py` and add a test.
- **Add an edge case of your own.** Create a new sample PDF (copy one of the `layout_*` calls) and add it to `SAMPLES` in `views/process.py` and to `EXPECTED` in the tests.
- **Tune the thresholds** in `config.py` and be ready to explain why.
- **Draft a vendor email** for "Needs review" cases (e.g. missing invoice number), using Gemini or a template.

---

## 8. If you only remember five things

1. **8 stages:** read → extract → validate → vendor → PO → duplicates → amount → decide.
2. **AI reads, rules decide.** It never guesses; uncertainty → human review.
3. **Reject** = unknown or blocked vendor, or duplicate. **Review** = everything that needs judgement.
4. **Edge cases:** no PO (inferred), split billing (remaining balance), disguised duplicate (normalisation), scanned (no guessing).
5. **Safety nets:** AI fallback to rules, AI values checked against the PDF, arithmetic check, full audit trail, config-driven policy.
