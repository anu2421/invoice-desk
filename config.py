"""All tunable rules and settings live here so they can be shown on the Rules page."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF_DIR = ROOT / "data" / "reference"
SAMPLES_DIR = ROOT / "samples"
STATIC_DIR = ROOT / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data" / "runtime"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "invoice_desk.sqlite3"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
# Tried in order when the main model is overloaded (503/429), retired (404) or too slow.
GEMINI_FALLBACKS = [m.strip() for m in os.environ.get(
    "GEMINI_FALLBACKS", "gemini-3.1-flash-lite,gemini-3.8-flash").split(",") if m.strip()]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
# auto | gemini | anthropic | rules
EXTRACTOR = os.environ.get("EXTRACTOR", "auto").lower()

# Presentation pacing: minimum time a stage stays "running" in the UI so a person can follow it.
# Real compute time is measured separately and shown as-is.
STAGE_MIN_MS = int(os.environ.get("STAGE_MIN_MS", "550"))

RULES = {
    "amount_tolerance_pct": 2.0,       # cumulative billing may exceed PO value by at most this
    "price_tolerance_pct": 2.0,        # unit price may exceed PO rate by at most this
    "arithmetic_tolerance_inr": 1.0,   # rounding slack for line/total arithmetic
    "duplicate_window_days": 120,      # look-back for fuzzy duplicates
    "max_invoice_age_days": 180,       # older invoices need a human look
    "min_field_confidence": 0.80,      # below this, a key field is flagged for review
    "po_inference_min_score": 0.60,    # minimum score to suggest a PO when none is quoted
    "manual_minutes_per_invoice": 12,  # baseline for the time-saved estimate on the dashboard
}


def active_extractor() -> str:
    if EXTRACTOR in ("gemini", "anthropic", "rules"):
        return EXTRACTOR
    if GEMINI_API_KEY:
        return "gemini"
    if ANTHROPIC_API_KEY:
        return "anthropic"
    return "rules"
