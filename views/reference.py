"""Vendors & POs - the reference data the process checks every invoice against."""
import streamlit as st

from core import db
from core.ui import DECISION_STYLE

st.title("Vendors & purchase orders")
st.caption("This is the 'spreadsheet side' of the process. Every invoice is checked against these tables. "
           "Edit the CSV files in the `data/` folder to change them.")

st.subheader("Vendor master")
vendors = db.load_vendors()
vendors["aliases"] = vendors["aliases"].str.replace("|", ", ", regex=False)
vendors["status"] = vendors["status"].map({"active": "🟢 active", "blocked": "⛔ blocked"}).fillna(vendors["status"])
st.dataframe(vendors.rename(columns={"vendor_id": "ID", "vendor_name": "Vendor", "aliases": "Also known as",
                                     "status": "Status", "payment_terms": "Terms", "notes": "Notes"}),
             hide_index=True, width="stretch")

st.subheader("PO register (live balances)")
pos = db.po_register()
pos["used"] = (pos["billed_to_date"] / pos["po_amount"]).clip(upper=1.5)
st.dataframe(
    pos[["po_number", "vendor_id", "description", "po_amount", "billed_to_date", "remaining", "used", "status", "issue_date"]],
    hide_index=True, width="stretch",
    column_config={
        "po_number": "PO", "vendor_id": "Vendor", "description": "Description",
        "po_amount": st.column_config.NumberColumn("PO amount", format="dollar"),
        "billed_to_date": st.column_config.NumberColumn("Billed to date", format="dollar",
                                                        help="Sum of APPROVED invoices on this PO"),
        "remaining": st.column_config.NumberColumn("Remaining", format="dollar"),
        "used": st.column_config.ProgressColumn("Used", format="percent", min_value=0, max_value=1),
        "status": "Status", "issue_date": "Issued",
    },
)
st.caption("Billed to date updates as invoices are approved - automatically or by a reviewer.")

st.subheader("Invoice ledger")
ledger = db.list_invoices(include_history=True)
ledger["status"] = ledger["status"].map(lambda s: DECISION_STYLE.get(s, {}).get("badge", s))
ledger["source"] = ledger["source"].map({"history": "history (seeded)", "run": "processed here"})
st.dataframe(
    ledger[["invoice_number", "vendor_name", "invoice_date", "po_number", "total", "status", "source", "processed_at"]],
    hide_index=True, width="stretch",
    column_config={"invoice_number": "Invoice #", "vendor_name": "Vendor", "invoice_date": "Invoice date",
                   "po_number": "PO", "total": st.column_config.NumberColumn("Total", format="dollar"),
                   "status": "Status", "source": "Source", "processed_at": "Processed"},
)
st.caption("Used by the duplicate check (have we seen this invoice before?) and the PO balance check.")
