"""How it works - process map, rules, and the thinking behind them."""
import streamlit as st

from core import config

st.title("How it works")
st.caption("The process map, every rule it applies, and why it is built this way.")

st.subheader("Process map")
map_col, legend_col = st.columns([3, 2], gap="large")
map_col.graphviz_chart("""
digraph {
  rankdir=TB; bgcolor="transparent"; nodesep=0.35; ranksep=0.28;
  node [shape=box style="rounded,filled" fillcolor="#EEF2FD" color="#2F5BEA" fontname="Helvetica" fontsize=11];
  edge [color="#8A94A6" fontname="Helvetica" fontsize=9];

  pdf [label="Invoice PDF\\n(email / upload)" shape=note fillcolor="#FFFFFF"];
  read [label="1. Read document\\ntext layer?"];
  extract [label="2. Extract fields\\nAI (Gemini) or rules"];
  validate [label="3. Validate data\\nrequired fields, maths"];
  vendor [label="4. Verify vendor\\nvendor master"];
  po [label="5. Match PO\\nexplicit or inferred"];
  dup [label="6. Duplicate check\\ninvoice ledger"];
  amt [label="7. Amount vs PO\\nbalance + tolerance"];
  decide [label="8. Decide\\nreject > review > approve" shape=diamond fillcolor="#FFFFFF" ordering=out];
  ok [label="Approved\\nschedule payment" fillcolor="#E3F5E3" color="#0ca30c"];
  hold [label="Needs review\\nreview queue (human)" fillcolor="#FEF3D9" color="#fab219"];
  no [label="Rejected\\ndo not pay" fillcolor="#FAE1E1" color="#d03b3b"];

  pdf -> read -> extract -> validate -> vendor -> po -> dup -> amt -> decide;
  read -> decide [label=" scanned & no AI" style=dashed constraint=false];
  {rank=same; ok; hold; no;}
  decide -> ok; decide -> hold; decide -> no;
  ok -> hold -> no [style=invis];
  hold -> ok [label="reviewer approves" style=dashed];
  hold -> no [label="reviewer rejects" style=dashed];
}
""", width="content")
with legend_col:
    st.markdown("""
**Reading the map**

- Every invoice goes through all 8 stages in order. The live run view shows each one as it executes.
- Each stage produces *findings*: ✓ pass, ! note, ⚠️ needs review, ❌ reject.
- Stage 8 combines them: **any reject → Rejected**, else **any review → Needs review**, else **Approved**.
- Held invoices go to the **review queue** on the Dashboard, where a person approves or rejects them with a note.
- If a scanned invoice can't be read, the process stops early and hands it to a person instead of guessing.
""")

st.subheader("Rules applied to every invoice")
tol, limit = config.PRICE_TOLERANCE_PCT, config.AUTO_APPROVE_LIMIT
st.markdown(f"""
| # | Stage | Rule | If it fails |
|---|---|---|---|
| 1 | Read document | The PDF must have machine-readable text, or AI must be on to read the scan | ⚠️ Needs review (key in manually) |
| 2 | Extract fields | AI output must appear in the PDF text (anti-hallucination cross-check) | ⚠️ Needs review |
| 3 | Validate data | Vendor, invoice #, date and total must be present; date not in the future | ⚠️ Needs review |
| 3 | Validate data | Line items + tax must equal the total (±${config.ARITHMETIC_TOLERANCE:.2f}) | ⚠️ Needs review |
| 4 | Verify vendor | Vendor must be in the vendor master (≥ {config.VENDOR_MATCH_THRESHOLD:.0%} name similarity) and active | ❌ Rejected |
| 5 | Match PO | Quoted PO must exist, belong to this vendor, and be open | ⚠️ Needs review |
| 5 | Match PO | No PO quoted → infer it: exactly one open PO of this vendor must fit the amount | ⚠️ Needs review if 0 or 2+ fit |
| 6 | Duplicate check | Same vendor + same invoice # (ignoring spaces, dashes, slashes) | ❌ Rejected |
| 6 | Duplicate check | Same vendor + same amount + same date, different invoice # | ⚠️ Needs review |
| 7 | Amount vs PO | Invoice must fit the PO's **remaining** balance (PO − earlier approved invoices), ≤ {tol:g}% over is tolerated | ⚠️ Needs review |
| 7 | Amount vs PO | Invoices above ${limit:,.0f} always need a human approver | ⚠️ Needs review |

**Decision rule:** any ❌ reject finding → **Rejected**; otherwise any ⚠️ review finding → **Needs review**;
otherwise → **Approved**. Notes (e.g. "PO inferred", "within tolerance") are attached to approved invoices so a
person can see *why* it was still approved.
""")

st.subheader("Why AI here, rules there")
c1, c2 = st.columns(2)
with c1, st.container(border=True):
    st.markdown("**AI for reading** (stage 2)")
    st.markdown("Every vendor formats invoices differently, and some are scans. Reading messy documents is "
                "what language models are good at. Gemini receives the PDF itself and returns structured JSON. "
                "It is told to return *null* rather than guess.")
with c2, st.container(border=True):
    st.markdown("**Rules for deciding** (stages 3-8)")
    st.markdown("Paying money must be predictable and auditable. The same invoice always gets the same "
                "answer, every decision can be explained line by line, and an AP manager can change a threshold "
                "in one place (`core/config.py`). AI never decides whether to pay.")
st.markdown("**Safety nets:** if the AI call fails (no internet, quota, bad key), the process falls back to the "
            "offline rules parser automatically. AI-extracted numbers are cross-checked against the PDF text, and "
            "the arithmetic check catches misread amounts.")

st.subheader("Assumptions")
st.markdown("""
- PO amount = total approved spend **including tax**; matching is at invoice-total level (not line-by-line).
- One currency (USD). Numeric dates like 05/11/2026 are read as day/month/year.
- Only **approved** invoices count towards a PO's billed amount; anything not rejected counts for duplicate checks.
- Vendor master, PO register and invoice history are CSV files standing in for the ERP / procurement system.
""")
