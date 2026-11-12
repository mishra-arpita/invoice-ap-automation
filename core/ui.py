"""
Shared UI pieces: stage cards (live run view + dashboard replay) and the decision card.
Status colours always come with an icon + a text label, so colour never carries meaning alone.
"""
from __future__ import annotations

import html
import io

import streamlit as st

# good / warning / critical (status palette) + neutral and "in progress"
STATUS_STYLE = {
    "pending": {"icon": "", "label": "Pending", "color": "#898781"},
    "running": {"icon": "", "label": "Running", "color": "#2F5BEA"},
    "pass": {"icon": "✓", "label": "Pass", "color": "#0ca30c"},
    "warn": {"icon": "!", "label": "Note", "color": "#fab219"},
    "fail": {"icon": "✕", "label": "Issue", "color": "#d03b3b"},
    "skip": {"icon": "–", "label": "Skipped", "color": "#898781"},
}
DECISION_STYLE = {
    "APPROVED": {"icon": "✓", "label": "Approved", "color": "#0ca30c", "badge": "✅ Approved"},
    "NEEDS_REVIEW": {"icon": "!", "label": "Needs review", "color": "#fab219", "badge": "⚠️ Needs review"},
    "REJECTED": {"icon": "✕", "label": "Rejected", "color": "#d03b3b", "badge": "❌ Rejected"},
}
OUTCOME_MARK = {"pass": ":green[✓]", "warn": ":orange[!]", "review": ":orange[●]", "reject": ":red[✕]"}

CSS = """
<style>
.block-container {padding-top: 2.2rem;}
.stage {display:flex; align-items:center; gap:12px; padding:8px 12px; margin:0 0 2px 0;
        border:1px solid rgba(128,128,128,.22); border-left:4px solid var(--c); border-radius:10px;
        background: color-mix(in srgb, var(--c) 6%, transparent);}
.stage.pending {opacity:.55; border-left-style:dashed; background:transparent;}
.stage .icon {flex:0 0 28px; height:28px; border-radius:50%; background:var(--c); color:#fff;
        display:flex; align-items:center; justify-content:center; font-weight:700; font-size:15px;}
.stage.warn .icon {color:#0b0b0b;}
.stage.pending .icon {background:transparent; border:2px dashed var(--c);}
.stage.running .icon {background:transparent; border:3px solid rgba(47,91,234,.25); border-top-color:var(--c);
        animation: spin .8s linear infinite;}
@keyframes spin {to {transform: rotate(360deg);}}
.stage .body {flex:1; min-width:0;}
.stage .title {font-weight:600; font-size:.95rem;}
.stage .num {opacity:.6; font-weight:500; margin-right:4px;}
.stage .summary {font-size:.86rem; opacity:.85; margin-top:1px;}
.stage .pill {flex:0 0 auto; font-size:.72rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase;
        padding:3px 9px; border-radius:999px; border:1px solid var(--c); color:inherit;}
.decision {border:1px solid var(--c); border-left:8px solid var(--c); border-radius:12px; padding:16px 20px;
        background: color-mix(in srgb, var(--c) 9%, transparent); margin:6px 0 12px 0;}
.decision .badge {display:inline-flex; align-items:center; gap:8px; font-weight:800; font-size:1.25rem;}
.decision .badge .dot {width:30px; height:30px; border-radius:50%; background:var(--c); color:#fff;
        display:inline-flex; align-items:center; justify-content:center; font-size:16px;}
.decision.NEEDS_REVIEW .badge .dot {color:#0b0b0b;}
.decision .headline {margin:8px 0 4px 0; font-size:1rem;}
.decision h5 {margin:12px 0 4px 0; font-size:.8rem; text-transform:uppercase; letter-spacing:.05em; opacity:.7;}
.decision ul {margin:0 0 0 1.1rem; padding:0;}
.decision li {margin:2px 0; font-size:.92rem;}
/* make the per-stage "Details" expander a slim strip attached under its card */
[data-testid="stExpander"] details {border:none !important; margin:-2px 0 4px 0;}
[data-testid="stExpander"] summary {padding:2px 0 2px 52px !important; min-height:0 !important;}
[data-testid="stExpander"] summary p {font-size:.78rem !important; opacity:.7;}
.waiting {border:1px dashed rgba(128,128,128,.4); border-radius:12px; padding:18px; text-align:center; opacity:.6;}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def _e(text) -> str:
    return html.escape(str(text))


# ------------------------------------------------------------------ stage card
def stage_card_html(number: int, title: str, state: str, summary: str = "") -> str:
    s = STATUS_STYLE[state]
    summary = summary or ("Working..." if state == "running" else "Waiting" if state == "pending" else "")
    return (f'<div class="stage {state}" style="--c:{s["color"]}"><div class="icon">{s["icon"]}</div>'
            f'<div class="body"><div class="title"><span class="num">{number}.</span>{_e(title)}</div>'
            f'<div class="summary">{_e(summary)}</div></div><div class="pill">{s["label"]}</div></div>')


def render_stage(slot, number: int, title: str, state: str, summary: str = "",
                 stage: dict | None = None, expanded: bool = False) -> None:
    """Draw one stage into `slot` (an st.empty() placeholder or a container)."""
    with slot.container():
        st.markdown(stage_card_html(number, title, state, summary), unsafe_allow_html=True)
        if stage and stage.get("key") != "decision" and (stage.get("details") or stage.get("findings")):
            with st.expander("Show details", expanded=expanded):
                render_stage_details(stage)


def render_stage_details(stage: dict) -> None:
    for f in stage.get("findings", []):
        line = f"{OUTCOME_MARK.get(f['outcome'], '')} **{f['rule']}** - {f['message']}"
        if f.get("action") and f["outcome"] in ("review", "reject"):
            line += f"  \n&nbsp;&nbsp;&nbsp;&nbsp;→ *Next step: {f['action']}*"
        st.markdown(line)
    scalars = {k: v for k, v in stage.get("details", {}).items() if not isinstance(v, list)}
    tables = {k: v for k, v in stage.get("details", {}).items() if isinstance(v, list)}
    if scalars:
        st.markdown("  \n".join(f"**{k}:** {v}" for k, v in scalars.items()))
    for name, rows in tables.items():
        if rows:
            st.caption(name)
            st.dataframe(rows, hide_index=True, width="stretch")


def render_stage_trace(stages: list[dict]) -> None:
    """Replay a stored run (used by the dashboard)."""
    for i, s in enumerate(stages, start=1):
        render_stage(st, i, s["title"], s["status"], s["summary"], s)


# ------------------------------------------------------------------ decision card
def decision_card_html(decision: dict) -> str:
    d = DECISION_STYLE[decision["decision"]]

    def section(title, items):
        if not items:
            return ""
        return f"<h5>{title}</h5><ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>"

    return (f'<div class="decision {decision["decision"]}" style="--c:{d["color"]}">'
            f'<div class="badge"><span class="dot">{d["icon"]}</span>{d["label"]}</div>'
            f'<div class="headline">{_e(decision["headline"])}</div>'
            + section("Why", decision.get("reasons"))
            + section("Notes", decision.get("notes"))
            + section("Next step", decision.get("next_actions"))
            + "</div>")


def render_decision(decision: dict) -> None:
    st.markdown(decision_card_html(decision), unsafe_allow_html=True)


def waiting_card(text: str = "The decision will appear here when the run finishes.") -> str:
    return f'<div class="waiting">{_e(text)}</div>'


# ------------------------------------------------------------------ PDF preview
@st.cache_data(show_spinner=False, max_entries=30)
def pdf_preview(pdf_bytes: bytes):
    """First page of the PDF as an image (or None if it can't be rendered)."""
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return pdf.pages[0].to_image(resolution=90).original.copy()
    except Exception:
        return None
