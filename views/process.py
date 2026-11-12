"""Process an invoice - the LIVE RUN VIEW."""
import json
import time
from dataclasses import asdict

import streamlit as st

from core import config
from core.pipeline import STAGES, process_invoice
from core.ui import pdf_preview, render_decision, render_stage, waiting_card

SAMPLES = {
    "01_happy_path_acme.pdf": "Happy path - explicit PO, amounts match exactly → expect APPROVED",
    "02_close_match_brightpath.pdf": "Different layout ('Bill #', 'Your Ref'); total 1.5% above PO → APPROVED with tolerance note",
    "03_no_po_reference_nimbus.pdf": "Edge case: no PO number on the invoice → PO is inferred → APPROVED with note",
    "04_split_po_overbilled_greenleaf.pdf": "Edge case: 2nd invoice on a PO that's already 60% billed → over-billing → NEEDS REVIEW",
    "05_duplicate_resubmitted_acme.pdf": "Edge case: already-paid invoice re-sent with the number formatted differently → REJECTED",
    "06_scanned_brightpath.pdf": "Edge case: scanned image, no text layer → rules: NEEDS REVIEW · AI: reads the scan",
    "07_unknown_vendor.pdf": "Vendor not in vendor master, quoting another vendor's PO → REJECTED",
    "08_missing_fields_greenleaf.pdf": "No invoice number or date on the invoice → NEEDS REVIEW",
}

st.title("Process an invoice")
st.caption("Pick a sample or upload a vendor invoice PDF. Each stage runs live and explains itself; "
           "the decision and its reasons appear at the end.")

# ------------------------------------------------------------------ input
with st.container(border=True):
    source = st.segmented_control("Input", ["Sample invoice", "Upload a PDF"], default="Sample invoice",
                                  label_visibility="collapsed")
    pdf_bytes, file_name = None, None
    if source == "Upload a PDF":
        uploaded = st.file_uploader("Vendor invoice (PDF)", type=["pdf"])
        if uploaded:
            pdf_bytes, file_name = uploaded.getvalue(), uploaded.name
        run = st.button("▶ Run process", type="primary", disabled=pdf_bytes is None)
    else:
        pick, go = st.columns([5, 1], vertical_alignment="bottom")
        file_name = pick.selectbox("Sample invoice", list(SAMPLES), format_func=lambda n: n.replace(".pdf", ""))
        pdf_bytes = (config.SAMPLES_DIR / file_name).read_bytes()
        run = go.button("▶ Run process", type="primary", width="stretch")
        st.caption(f"**What this demonstrates:** {SAMPLES[file_name]}")
    engine = "AI (Gemini) if a key is set" if st.session_state.get("use_ai", True) else "Rules parser only"
    st.caption(f"Extraction engine: {engine} - change it in the sidebar.")

# ------------------------------------------------------------------ layout
left, right = st.columns([2, 3], gap="large")
with left:
    st.subheader("Invoice")
    if pdf_bytes:
        image = pdf_preview(pdf_bytes)
        if image is not None:
            st.image(image, width="stretch")
        else:
            st.warning("Preview not available for this file.")
    else:
        st.info("Upload a PDF to see it here.")

with right:
    st.subheader("Live run")
    decision_slot = st.empty()   # the decision appears at the top when the run finishes
    output_slot = st.container()
    slots = {key: st.empty() for key, _ in STAGES}

numbers = {key: i for i, (key, _) in enumerate(STAGES, start=1)}
titles = dict(STAGES)


def show_result(result: dict) -> None:
    for s in result["stages"]:
        render_stage(slots[s["key"]], numbers[s["key"]], s["title"], s["status"], s["summary"], s)
    with decision_slot.container():
        render_decision(result["decision"])
    with output_slot:
        c1, c2 = st.columns(2, vertical_alignment="center")
        c1.download_button("⬇ Download decision (JSON)", json.dumps(result, indent=2),
                           file_name=f"decision_{result['id']}.json", mime="application/json", width="stretch")
        c2.page_link("views/dashboard.py", label="Open in dashboard →", icon="📊")


if run and pdf_bytes:
    # 1. draw every stage as "pending"
    for key, title in STAGES:
        render_stage(slots[key], numbers[key], title, "pending")
    decision_slot.markdown(waiting_card(), unsafe_allow_html=True)

    # 2. the pipeline calls these as it moves through the stages
    def on_start(key):
        render_stage(slots[key], numbers[key], titles[key], "running")
        time.sleep(config.STAGE_DELAY_SECONDS)

    def on_done(stage):
        s = asdict(stage)
        render_stage(slots[s["key"]], numbers[s["key"]], s["title"], s["status"], s["summary"], s)

    result = process_invoice(pdf_bytes, file_name, st.session_state.get("use_ai", True), on_start, on_done)
    st.session_state["last_result"] = result
    show_result(result)

elif st.session_state.get("last_result", {}).get("file_name") == file_name:
    show_result(st.session_state["last_result"])  # keep showing the last run after any click
else:
    for key, title in STAGES:
        render_stage(slots[key], numbers[key], title, "pending")
    decision_slot.markdown(waiting_card("Press ▶ Run process to start."), unsafe_allow_html=True)
