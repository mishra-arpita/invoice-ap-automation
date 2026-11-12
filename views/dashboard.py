"""Dashboard - history, status and outputs across all runs, plus the human review queue."""
import json
from collections import Counter

import altair as alt
import pandas as pd
import streamlit as st

from core import db
from core.ui import DECISION_STYLE, render_decision, render_stage_trace

st.title("Dashboard")

include_history = st.toggle("Include historical invoices (processed before this tool)", value=False)
df = db.list_invoices(include_history=include_history)
runs = df[df["source"] == "run"]

if df.empty:
    st.info("No invoices processed yet. Go to **Process invoice** and run a sample.")
    st.page_link("views/process.py", label="Process an invoice →", icon="⚡")
    st.stop()

# ------------------------------------------------------------------ KPI tiles
processed = len(runs)
auto_approved = int((runs["decision"] == "APPROVED").sum())
waiting = int((df["status"] == "NEEDS_REVIEW").sum())
rejected = int((runs["status"] == "REJECTED").sum())
approved_value = float(runs.loc[runs["status"] == "APPROVED", "total"].fillna(0).sum())

k = st.columns(5)
k[0].metric("Invoices processed", processed, border=True)
k[1].metric("Auto-approved", f"{auto_approved}" + (f" ({auto_approved / processed:.0%})" if processed else ""),
            border=True, help="Approved by the process with no human involved.")
k[2].metric("⚠️ Waiting for review", waiting, border=True)
k[3].metric("❌ Rejected", rejected, border=True)
k[4].metric("Approved value", f"${approved_value:,.2f}", border=True,
            help="Total of invoices currently approved for payment (automatically or by a reviewer).")

# ------------------------------------------------------------------ exception reasons + review queue
chart_col, queue_col = st.columns([3, 2], gap="large")

with chart_col:
    st.subheader("Why invoices were held or rejected")
    reasons = Counter()
    for raw in runs["result_json"].dropna():
        for stage in json.loads(raw)["stages"]:
            for f in stage.get("findings", []):
                if f["outcome"] in ("review", "reject"):
                    reasons[f["rule"]] += 1
    if reasons:
        reason_df = pd.DataFrame(reasons.items(), columns=["Rule", "Invoices"]).sort_values("Invoices", ascending=False)
        base = alt.Chart(reason_df).encode(
            y=alt.Y("Rule:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220, ticks=False, domain=False)),
            x=alt.X("Invoices:Q", title=None, axis=alt.Axis(tickMinStep=1, format="d", grid=True)),
            tooltip=[alt.Tooltip("Rule:N"), alt.Tooltip("Invoices:Q", title="Invoices affected")],
        )
        bars = base.mark_bar(color="#2a78d6", cornerRadiusEnd=4, size=18)
        labels = base.mark_text(align="left", dx=6).encode(text="Invoices:Q")
        st.altair_chart((bars + labels).properties(height=max(120, 42 * len(reason_df))), width="stretch")
        st.caption("Each bar = number of processed invoices where this rule raised a review or reject finding.")
    else:
        st.caption("No exceptions yet - every processed invoice was approved.")

with queue_col:
    st.subheader("Review queue")
    queue = df[df["status"] == "NEEDS_REVIEW"]
    if queue.empty:
        st.success("Nothing waiting for review.", icon="✅")
    for _, row in queue.iterrows():
        with st.container(border=True):
            st.markdown(f"**{row['vendor_name'] or 'Unknown vendor'}** · {row['invoice_number'] or '(no number)'} · "
                        f"{'$%s' % format(row['total'], ',.2f') if pd.notna(row['total']) else '-'}")
            st.caption(row["headline"])
            note = st.text_input("Reviewer note", key=f"note_{row['id']}", placeholder="e.g. PO amendment approved")
            a, r = st.columns(2)
            if a.button("✅ Approve", key=f"approve_{row['id']}", width="stretch"):
                db.record_review(int(row["id"]), "APPROVED", note)
                st.rerun()
            if r.button("❌ Reject", key=f"reject_{row['id']}", width="stretch"):
                db.record_review(int(row["id"]), "REJECTED", note)
                st.rerun()

# ------------------------------------------------------------------ history table
st.subheader("Run history")
f1, f2 = st.columns([2, 3], vertical_alignment="bottom")
status_filter = f1.pills("Status", list(DECISION_STYLE), selection_mode="multi", default=list(DECISION_STYLE),
                         format_func=lambda s: DECISION_STYLE[s]["badge"])
search = f2.text_input("Search vendor, invoice # or PO", placeholder="e.g. Acme, PO-1003")

view = df[df["status"].isin(status_filter or [])]
if search:
    s = search.lower()
    view = view[view[["vendor_name", "invoice_number", "po_number", "file_name"]]
                .fillna("").apply(lambda col: col.str.lower().str.contains(s, regex=False)).any(axis=1)]


def status_text(decision, status, reviewed_at):
    """'✅ Approved', or '✅ Approved · 👤 was Needs review' when a reviewer changed it."""
    text = DECISION_STYLE.get(status, {}).get("badge", status)
    if pd.notna(reviewed_at):
        text += f" · 👤 was {DECISION_STYLE.get(decision, {}).get('label', decision)}"
    return text


table = pd.DataFrame({
    "ID": view["id"],
    "Time": pd.to_datetime(view["processed_at"]).dt.strftime("%d %b %H:%M"),
    "File": view["file_name"].str.replace(".pdf", "", regex=False),
    "Vendor": view["vendor_name"],
    "Invoice #": view["invoice_number"],
    "PO": view["po_number"],
    "Total": view["total"],
    "Status": [status_text(d, s_, r) for d, s_, r in zip(view["decision"], view["status"], view["reviewed_at"])],
    "Engine": view["extraction_method"].map({"ai": "AI", "rules": "Rules"}).fillna("-"),
})
selection = st.dataframe(
    table, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="history",
    column_config={"Total": st.column_config.NumberColumn(format="dollar"),
                   "ID": st.column_config.NumberColumn(width="small"),
                   "Engine": st.column_config.TextColumn(width="small"),
                   "Status": st.column_config.TextColumn(help="👤 = a human reviewer changed the process decision")},
)
st.caption("Select a row to see the full stage-by-stage trace.")

# ------------------------------------------------------------------ run detail
rows = selection.selection.rows if selection else []
if rows:
    record = db.get_invoice(int(table.iloc[rows[0]]["ID"]))
    st.divider()
    st.subheader(f"Run #{record['id']} · {record['file_name']}")
    if record["reviewed_at"]:
        st.info(f"👤 Reviewer set status to **{DECISION_STYLE[record['status']]['label']}** on "
                f"{record['reviewed_at']}" + (f" - note: “{record['reviewer_note']}”" if record["reviewer_note"] else ""))
    result = record["result"]
    if result is None:
        st.write(f"Historical record: {record['vendor_name']} invoice {record['invoice_number']} for "
                 f"${record['total']:,.2f} on {record['po_number']} - status {record['status']}. "
                 "No stage trace, because it was processed before this tool existed.")
    else:
        render_decision(result["decision"])
        engine = "AI - Gemini" if result["extraction"]["method"] == "ai" else "Rules parser"
        st.caption(f"Processed {result['processed_at']} · extraction: {engine}")
        render_stage_trace(result["stages"])
        st.download_button("⬇ Download decision (JSON)", json.dumps(result, indent=2),
                           file_name=f"decision_{record['id']}.json", mime="application/json")
