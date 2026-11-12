"""
InvoiceFlow - AP invoice processing POC (AI Solutions Analyst case study, PS-1).

Run:  streamlit run app.py
"""
import streamlit as st

from core import config, db
from core.ui import inject_css

st.set_page_config(page_title="InvoiceFlow - AP automation", page_icon="🧾", layout="wide")
inject_css()
db.init_db()  # creates data/ap.db and seeds invoice history on first start

pages = st.navigation([
    st.Page("views/process.py", title="Process invoice", icon="⚡", default=True),
    st.Page("views/dashboard.py", title="Dashboard", icon="📊"),
    st.Page("views/reference.py", title="Vendors & POs", icon="🗂️"),
    st.Page("views/how_it_works.py", title="How it works", icon="🧭"),
])

with st.sidebar:
    st.markdown("### 🧾 InvoiceFlow")
    st.caption(f"Invoice PDF → reasoned decision, for the {config.COMPANY_NAME} AP team.")

    engine = st.radio(
        "Extraction engine",
        ["Auto (AI if key is set)", "Rules only"],
        key="engine",
        help="Auto uses Google Gemini to read the invoice when a GEMINI_API_KEY is configured. "
             "Rules only uses the offline regex parser.",
    )
    st.session_state["use_ai"] = engine.startswith("Auto")

    if config.gemini_api_key():
        st.success(f"Gemini key found · model `{config.gemini_model()}`", icon="🟢")
    else:
        st.info("No Gemini key - using the offline rules parser. See README to enable AI.", icon="⚪")

    st.divider()
    with st.popover("Reset demo data", width="stretch"):
        st.write("Deletes all processed invoices and restores the original invoice history. "
                 "Use this before a demo so every sample behaves as documented.")
        if st.button("Yes, reset", type="primary"):
            db.reset()
            st.session_state.pop("last_result", None)
            st.toast("Demo data reset.", icon="♻️")

pages.run()
