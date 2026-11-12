# Demo script: 5-minute video + live interview

The guide asks for:
- **Video:** at most 5 minutes. Show the happy path, then at least one edge case, and narrate what is happening and why.
- **Interview:** run the process live (the happy path + your edge cases), then talk through your decisions. There are no slides.

Speak simply. Say what the process is doing and **why it made that decision**. The words in *italics* below are a guide, not a script to read word for word.

---

## Before you record (or before the interview)

- [ ] Start the app (`streamlit run app.py`), or open your Streamlit Cloud link (open it early to wake it up).
- [ ] Sidebar → **Reset demo data** → *Yes, reset*. Every sample now behaves as documented.
- [ ] Sidebar → choose **Rules only** for the main demo. It is fast and needs no internet. If you have a Gemini key, keep **Auto** ready for the scanned-invoice demo.
- [ ] Browser zoom at 90%, so the stage list and the decision fit on screen.
- [ ] Close other tabs and notifications.
- [ ] Have `sample_invoices/` open in a file window in case you want to show a PDF.

---

## 5-minute video

### 0:00 – 0:30 · The problem and what you built
*"AP teams get hundreds of vendor invoices as PDFs. Someone opens each one, finds the PO in a spreadsheet, checks that the numbers match, and decides whether to pay. It's slow, and mistakes like paying a duplicate are expensive. I built InvoiceFlow. It takes an invoice PDF and gives a clear decision, approved, needs review or rejected, with every step visible."*

### 0:30 – 1:45 · Happy path: `01_happy_path_acme`
- Select it. Point at the invoice preview on the left.
- Press **▶ Run process** and narrate while the stages light up:
  - *"First it reads the PDF and checks there's real text in it."*
  - *"Then it extracts the fields: vendor, invoice number, date, PO number and total. With an API key this uses Gemini; here I'm using the offline rules parser."*
  - *"It validates the data: all required fields are present, and line items plus tax add up to the total."*
  - *"It checks the vendor is in our approved vendor master and is active."*
  - *"It finds PO-1001 in the PO register, checks it belongs to this vendor and is still open."*
  - *"Duplicate check: have we seen this invoice before? No."*
  - *"The amount fits the remaining PO balance, so it's approved, and the next step is to schedule payment on Net 30 terms."*
- Open one **Show details** strip (e.g. stage 7) to show the reasoning is visible.

### 1:45 – 2:35 · Edge case: `04_split_po_overbilled_greenleaf`
- Run it. *"This vendor bills one PO across several invoices. The PO is $10,000. They've already billed $6,000, and this invoice is $5,000. On its own, $5,000 looks fine against a $10,000 PO. But together it's $11,000, which is 10% over and beyond our 2% tolerance. So the process doesn't pay it; it holds it for a person and says exactly why and what to do."*
- Open stage 7's details to show the billed-to-date maths.

### 2:35 – 3:15 · Edge case: `05_duplicate_resubmitted_acme`
- Run it. *"The vendor re-sent an invoice we already paid, as a 'payment reminder'. The number is written differently, with spaces and a slash instead of dashes. A simple text match would miss it and we'd pay twice. The process normalises invoice numbers before comparing, so it catches it and rejects it."*

### 3:15 – 3:45 · Edge case: `03_no_po_reference_nimbus`
- Run it. *"This invoice has no PO number at all, which is very common. Instead of rejecting it, the process looks at this vendor's open POs. Exactly one has enough balance, so it infers PO-1004 and approves it with a note. If two POs could fit, it would ask a person instead of guessing."*

### 3:45 – 4:25 · Dashboard
- Open **📊 Dashboard**.
  - *"Here's the history across all runs: how many were auto-approved, waiting for review, or rejected, and the total value approved."*
  - *"This chart shows which rules cause the most exceptions. That tells the AP manager where the problems are."*
  - *"Here's the review queue. The GreenLeaf invoice is waiting. Say procurement increased the PO: I add a note and approve."* Type a note and click **Approve**.
  - *"Its status changes, and it's marked as changed by a human, so there's a full audit trail."* Click a row in **Run history** to show the replayed trace.

### 4:25 – 5:00 · Design choices
*"Two key choices. First, AI reads, rules decide. Reading messy invoices is where AI helps. Whether to pay must be predictable and explainable, so that's done with clear rules, and every threshold is in one config file. Second, the process never guesses. Missing data, unreadable scans or ambiguous POs go to a person. And if the AI is unavailable, it falls back to the rules parser automatically. Next, I'd add line-level 3-way matching and read invoices straight from the AP mailbox."*

> If you have a Gemini key, swap the 3:15 segment for `06_scanned_brightpath`. Run it on **Rules only** first (it goes to review because it can't be read), then on **Auto**, where Gemini reads the scan.

---

## Live interview: run order (about 10 minutes)

1. **Reset demo data.**
2. `01_happy_path_acme` → ✅ Approved. Walk through all 8 stages.
3. `02_close_match_brightpath` → ✅ Approved with a note. *"A different vendor layout, and the total is 1.5% over. That's within tolerance, so we don't bother a human for $76."*
4. `04_split_po_overbilled_greenleaf` → ⚠️ Needs review (split billing).
5. `05_duplicate_resubmitted_acme` → ❌ Rejected (disguised duplicate).
6. `03_no_po_reference_nimbus` → ✅ Approved, PO inferred.
7. `06_scanned_brightpath` → Rules only: ⚠️ Needs review. With a key, on Auto: Gemini reads it.
8. **Bonus:** run `01_happy_path_acme` **again** → ❌ Rejected as a duplicate of the invoice you approved 2 minutes ago.
9. **Dashboard:** KPIs, exception chart, approve the GreenLeaf invoice from the review queue with a note, open a run's trace.
10. **Vendors & POs:** show that PO-1003's "billed to date" went up after the approval. The ledger is live.
11. **How it works:** use the process map and rules table to answer "why" questions.

If they ask you to change a rule live (local run only):
1. Open `core/config.py` and change `PRICE_TOLERANCE_PCT = 2.0` to `15.0`.
2. Restart the app: press `Ctrl+C` in the terminal, then run `streamlit run app.py` again.
3. **Reset demo data** and re-run `04`. It is now approved, because 10% over is within a 15% tolerance.

This shows the policy is configurable without touching the logic. Change it back afterwards.

### If something goes wrong live
- **The AI call fails or is slow:** the run still finishes; it falls back to rules and shows a note. Or switch the sidebar to **Rules only**.
- **A sample gives an unexpected result:** you probably forgot to reset. Click **Reset demo data** and run it again.
- **The cloud app is asleep or broken:** run it locally (`streamlit run app.py`). Keep a terminal ready.

---

## Email templates

**Day 1: choosing the problem**

> **Subject:** ASA Case Study — [Your Name] — PS-1
>
> Hi [Coordinator name],
>
> I'd like to take up **PS-1: Invoice processing, from PDF to decision**. I'll build a working process that takes an invoice PDF, extracts the key fields, matches it against a PO register and vendor master, and produces an approve / review / reject decision with its reasoning visible.
>
> Thanks,
> [Your Name]

**Day 7: submission**

> **Subject:** ASA Case Study — [Your Name] — PS-1 — Submission
>
> Hi [Coordinator name],
>
> Please find my submission for PS-1 (Invoice processing):
>
> - **Live process:** [https://your-app.streamlit.app]
> - **Demo video (5 min):** [Loom / Drive link]
> - **Code:** [GitHub link] (optional)
>
> The process extracts invoice data with Gemini (with an offline rules fallback), checks the vendor, PO match, duplicates and PO balance, and decides Approved / Needs review / Rejected with reasons. It has a live run view and a dashboard with a review queue. The edge cases I designed: an invoice with no PO number (inferred), split billing that exceeds the PO, a re-sent duplicate with a reformatted invoice number, and a scanned invoice.
>
> Thanks,
> [Your Name]
