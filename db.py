"""SQLite storage: reference data (vendors, POs), the AP ledger, and run history."""
import csv
import json
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS vendors (
  vendor_id TEXT PRIMARY KEY, name TEXT, gstin TEXT, state TEXT, status TEXT,
  bank_name TEXT, bank_account TEXT, ifsc TEXT, payment_terms_days INTEGER, email TEXT, address TEXT);
CREATE TABLE IF NOT EXISTS purchase_orders (
  po_number TEXT PRIMARY KEY, vendor_id TEXT, po_date TEXT, status TEXT, description TEXT, total_value REAL);
CREATE TABLE IF NOT EXISTS po_lines (
  po_number TEXT, line_no INTEGER, description TEXT, hsn TEXT, qty REAL, unit_price REAL, uom TEXT);
CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id TEXT, invoice_number TEXT, invoice_date TEXT,
  po_number TEXT, subtotal REAL, tax REAL, total REAL, status TEXT, run_id TEXT, file_hash TEXT,
  line_sig TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS vendor_rules (
  vendor_id TEXT PRIMARY KEY, price_tolerance_pct REAL, reason TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, created_at TEXT, filename TEXT, stored_path TEXT, file_hash TEXT,
  status TEXT, decision TEXT, final_decision TEXT, vendor_name TEXT, vendor_id TEXT,
  invoice_number TEXT, total REAL, extractor TEXT, duration_ms INTEGER,
  stages TEXT, extracted TEXT, findings TEXT, output TEXT, review TEXT, error TEXT,
  parent_id TEXT);
"""

JSON_COLS = ("stages", "extracted", "findings", "output", "review")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(runs)")}
        if "parent_id" not in cols:  # databases created before invoice splitting existed
            _conn.execute("ALTER TABLE runs ADD COLUMN parent_id TEXT")
        if not _conn.execute("SELECT 1 FROM vendors LIMIT 1").fetchone():
            load_reference(reset_runs=False)
    return _conn


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    with _lock:
        c = conn()
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def load_reference(reset_runs: bool = True):
    """(Re)load vendor master, POs and the seeded ledger from CSV. Optionally wipe run history."""
    with _lock:
        c = conn()
        for t in ("vendors", "purchase_orders", "po_lines", "ledger", "vendor_rules"):
            c.execute(f"DELETE FROM {t}")
        if reset_runs:
            c.execute("DELETE FROM runs")
        ref = config.REF_DIR
        with open(ref / "vendors.csv") as f:
            for r in csv.DictReader(f):
                c.execute("INSERT INTO vendors VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                    r["vendor_id"], r["name"], r["gstin"], r["state"], r["status"], r["bank_name"],
                    r["bank_account"], r["ifsc"], int(r["payment_terms_days"]), r["email"], r["address"]))
        with open(ref / "purchase_orders.csv") as f:
            for r in csv.DictReader(f):
                c.execute("INSERT INTO purchase_orders VALUES (?,?,?,?,?,?)", (
                    r["po_number"], r["vendor_id"], r["po_date"], r["status"], r["description"], float(r["total_value"])))
        with open(ref / "po_lines.csv") as f:
            for r in csv.DictReader(f):
                c.execute("INSERT INTO po_lines VALUES (?,?,?,?,?,?,?)", (
                    r["po_number"], int(r["line_no"]), r["description"], r["hsn"], float(r["qty"]),
                    float(r["unit_price"]), r["uom"]))
        with open(ref / "invoice_ledger.csv") as f:
            for r in csv.DictReader(f):
                c.execute("INSERT INTO ledger (vendor_id, invoice_number, invoice_date, po_number, subtotal, tax, total,"
                          " status, run_id, file_hash, line_sig, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                              r["vendor_id"], r["invoice_number"], r["invoice_date"], r["po_number"],
                              float(r["subtotal"]), float(r["tax"]), float(r["total"]), r["status"],
                              None, None, None, "seed"))
        c.commit()


def company() -> dict:
    return json.loads((config.REF_DIR / "company.json").read_text())


# ------------------------------------------------------------------ runs
def create_run(run_id, filename, stored_path, file_hash, stages, parent_id=None):
    ex("INSERT INTO runs (id, created_at, filename, stored_path, file_hash, status, stages, findings, parent_id) "
       "VALUES (?,?,?,?,?,?,?,?,?)",
       (run_id, now_iso(), filename, str(stored_path), file_hash, "running", json.dumps(stages), "[]", parent_id))


def update_run(run_id, **fields):
    if not fields:
        return
    cols, vals = [], []
    for k, v in fields.items():
        cols.append(f"{k}=?")
        vals.append(json.dumps(v) if k in JSON_COLS else v)
    vals.append(run_id)
    ex(f"UPDATE runs SET {', '.join(cols)} WHERE id=?", vals)


def _decode(row):
    if row is None:
        return None
    for k in JSON_COLS:
        if row.get(k):
            row[k] = json.loads(row[k])
    return row


def get_run(run_id):
    return _decode(q1("SELECT * FROM runs WHERE id=?", (run_id,)))


def list_runs(limit=500):
    rows = q("SELECT id, created_at, filename, status, decision, final_decision, vendor_name, vendor_id, "
             "invoice_number, total, extractor, duration_ms, findings, review, output, parent_id FROM runs ORDER BY created_at DESC, rowid DESC LIMIT ?",
             (limit,))
    return [_decode(r) for r in rows]
