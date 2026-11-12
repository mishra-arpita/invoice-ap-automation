"""
Storage: reference data (CSV "spreadsheets") + a small SQLite ledger of processed invoices.

* data/vendors.csv          - approved vendor master
* data/purchase_orders.csv  - PO register
* data/invoice_history.csv  - invoices processed BEFORE this tool existed (seeded into SQLite)
* data/ap.db (SQLite)       - every invoice this app processes: decision, status, full trace

The ledger answers two questions the rules need:
  1. Have we seen this invoice before?          (duplicate check)
  2. How much has already been billed on a PO?  (split invoices / over-billing)
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime

import pandas as pd

from core import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source              TEXT NOT NULL,   -- 'history' (seeded) or 'run' (processed by this app)
    processed_at        TEXT NOT NULL,
    file_name           TEXT,
    extraction_method   TEXT,            -- 'ai' or 'rules'
    vendor_id           TEXT,
    vendor_name         TEXT,
    invoice_number      TEXT,
    invoice_number_norm TEXT,            -- normalised for duplicate detection
    invoice_date        TEXT,
    po_number           TEXT,
    total               REAL,
    decision            TEXT,            -- what the automated process decided
    status              TEXT,            -- current status (a reviewer can change it)
    headline            TEXT,
    result_json         TEXT,            -- full stage-by-stage trace
    reviewer_note       TEXT,
    reviewed_at         TEXT
);
"""


def normalize_invoice_number(value) -> str | None:
    """'INV 2026/0042', 'inv-2026-0042', 'INV20260042' -> 'INV20260042'.
    Vendors often re-send the same invoice with slightly different formatting."""
    if not value:
        return None
    return re.sub(r"[^A-Z0-9]", "", str(value).upper()) or None


def _connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ setup
def init_db() -> None:
    """Create the table and seed historical invoices (only if the DB is empty)."""
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn, conn:
        conn.executescript(SCHEMA)
        if conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0:
            history = pd.read_csv(config.DATA_DIR / "invoice_history.csv", dtype=str)
            for row in history.to_dict("records"):
                conn.execute(
                    """INSERT INTO invoices (source, processed_at, file_name, extraction_method, vendor_id,
                           vendor_name, invoice_number, invoice_number_norm, invoice_date, po_number, total,
                           decision, status, headline)
                       VALUES ('history', ?, '(historical record)', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row["processed_on"] + " 10:00:00", row["vendor_id"], row["vendor_name"],
                     row["invoice_number"], normalize_invoice_number(row["invoice_number"]),
                     row["invoice_date"], row["po_number"], float(row["total"]),
                     row["status"], row["status"], "Processed before this tool (seeded history)"),
                )


def reset() -> None:
    """Wipe all runs and re-seed history - used by the 'Reset demo data' button."""
    with closing(_connect()) as conn, conn:
        conn.execute("DROP TABLE IF EXISTS invoices")
    init_db()


# ------------------------------------------------------------------ reference data
def load_vendors() -> pd.DataFrame:
    return pd.read_csv(config.DATA_DIR / "vendors.csv", dtype=str).fillna("")


def load_purchase_orders() -> pd.DataFrame:
    df = pd.read_csv(config.DATA_DIR / "purchase_orders.csv", dtype={"po_amount": float})
    return df.fillna("")


def po_register() -> pd.DataFrame:
    """PO register with live 'billed to date' and 'remaining' columns."""
    pos = load_purchase_orders()
    pos["billed_to_date"] = pos["po_number"].map(billed_to_date)
    pos["remaining"] = (pos["po_amount"] - pos["billed_to_date"]).round(2)
    return pos


# ------------------------------------------------------------------ ledger queries used by the rules
def billed_to_date(po_number: str) -> float:
    """Sum of APPROVED invoices already booked against this PO."""
    with closing(_connect()) as conn:
        value = conn.execute(
            "SELECT COALESCE(SUM(total), 0) FROM invoices WHERE po_number = ? AND status = 'APPROVED'",
            (po_number,),
        ).fetchone()[0]
    return round(float(value), 2)


def find_same_invoice_number(vendor_id: str, invoice_number_norm: str) -> list[dict]:
    """Previously processed (not rejected) invoices from this vendor with the same number."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM invoices WHERE vendor_id = ? AND invoice_number_norm = ?
               AND status != 'REJECTED' ORDER BY id""",
            (vendor_id, invoice_number_norm),
        ).fetchall()
    return [dict(r) for r in rows]


def find_same_amount_and_date(vendor_id: str, total: float, invoice_date: str) -> list[dict]:
    """Same vendor + same amount + same date = probably the same invoice with a new number."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            """SELECT * FROM invoices WHERE vendor_id = ? AND ABS(total - ?) < 0.01
               AND invoice_date = ? AND status != 'REJECTED' ORDER BY id""",
            (vendor_id, total, invoice_date),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ saving / reading runs
def save_run(result: dict) -> int:
    """Store one processed invoice and return its id."""
    fields = result["fields"]
    vendor = result.get("vendor") or {}
    decision = result["decision"]
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            """INSERT INTO invoices (source, processed_at, file_name, extraction_method, vendor_id,
                   vendor_name, invoice_number, invoice_number_norm, invoice_date, po_number, total,
                   decision, status, headline, result_json)
               VALUES ('run', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (result["processed_at"], result["file_name"], result["extraction"]["method"],
             vendor.get("vendor_id"), vendor.get("vendor_name") or fields.get("vendor_name"),
             fields.get("invoice_number"), normalize_invoice_number(fields.get("invoice_number")),
             fields.get("invoice_date"), result.get("po_number"), fields.get("total"),
             decision["decision"], decision["decision"], decision["headline"], json.dumps(result)),
        )
        return cursor.lastrowid


def list_invoices(include_history: bool = False) -> pd.DataFrame:
    query = "SELECT * FROM invoices"
    if not include_history:
        query += " WHERE source = 'run'"
    with closing(_connect()) as conn:
        return pd.read_sql_query(query + " ORDER BY id DESC", conn)


def get_invoice(invoice_id: int) -> dict | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not row:
        return None
    record = dict(row)
    record["result"] = json.loads(record["result_json"]) if record.get("result_json") else None
    return record


def record_review(invoice_id: int, new_status: str, note: str) -> None:
    """Human-in-the-loop: an AP reviewer approves or rejects an invoice that was held."""
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE invoices SET status = ?, reviewer_note = ?, reviewed_at = ? WHERE id = ?",
            (new_status, note.strip() or None, _now(), invoice_id),
        )
