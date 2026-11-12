"""
Business rules and settings live in ONE place.

An AP manager can tune how strict the process is (tolerance, approval limit, ...)
here, without touching any of the logic in rules.py.
"""
import os
from pathlib import Path

# ---------------------------------------------------------------- paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SAMPLES_DIR = BASE_DIR / "sample_invoices"
DB_PATH = DATA_DIR / "ap.db"  # SQLite file, created automatically on first start

# ---------------------------------------------------------------- business rules
COMPANY_NAME = "Orbit Retail Pvt Ltd"  # the (fictional) company receiving the invoices
CURRENCY = "USD"

# An invoice may exceed the remaining PO balance by up to this % of the PO amount
# (covers rounding, freight, small price changes). Anything more needs a human.
PRICE_TOLERANCE_PCT = 2.0

# Invoices above this amount always need a human approver, even if everything matches.
AUTO_APPROVE_LIMIT = 25_000.00

# How similar (0-1) the vendor name on the invoice must be to a name in the vendor master.
VENDOR_MATCH_THRESHOLD = 0.85

# Line items + tax may differ from the stated total by at most this much ($) - rounding.
ARITHMETIC_TOLERANCE = 1.00

# If a PDF has fewer characters of text than this, we treat it as a scanned image.
MIN_TEXT_CHARS = 30

# ---------------------------------------------------------------- UI
# Small pause between stages in the live run view so the audience can follow along.
STAGE_DELAY_SECONDS = 0.6

# ---------------------------------------------------------------- AI (Google Gemini)
# "gemini-flash-latest" is Google's alias that always points to the current Flash model.
DEFAULT_GEMINI_MODEL = "gemini-flash-latest"
AI_TIMEOUT_SECONDS = 60


def _secret(name: str):
    """Read a setting from an environment variable, else from .streamlit/secrets.toml."""
    value = os.getenv(name)
    if value:
        return value.strip()
    try:
        import streamlit as st

        value = st.secrets.get(name)
        return str(value).strip() if value else None
    except Exception:  # no secrets file / not running inside Streamlit
        return None


def gemini_api_key():
    key = _secret("GEMINI_API_KEY")
    if not key or key == "paste-your-key-here":
        return None
    return key


def gemini_model() -> str:
    return _secret("GEMINI_MODEL") or DEFAULT_GEMINI_MODEL
