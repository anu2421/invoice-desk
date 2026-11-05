# Invoice Desk

Takes a vendor invoice (PDF, scanned PDF or photo) and produces a reasoned decision: **approve for payment**, **hold for review** or **reject and return to the vendor**. Each step of the reasoning is visible.

Built for the Zamp AI Solutions Analyst case study, PS-1 (invoice processing).

```
Receive → Read → Extract → Validate → Match vendor → Check duplicates → Match PO → Decide and route
```

**Design principle:** the LLM reads; code decides. Extraction is the only step that uses a model. Matching, tolerances, duplicate logic, GST rules and the decision itself are deterministic, so every outcome traces to a named check and the same invoice always gets the same answer.

---

## Run it locally (5 minutes)

You need Python 3.11+ and Tesseract (for scanned invoices).

```bash
# Tesseract
#   macOS:   brew install tesseract
#   Ubuntu:  sudo apt install tesseract-ocr
#   Windows: install from https://github.com/UB-Mannheim/tesseract/wiki and add it to PATH

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export GEMINI_API_KEY=your-key     # optional; Windows: set GEMINI_API_KEY=your-key
python -m app.main                 # http://localhost:8000
```

Without an API key it runs fully offline on the rules parser. Every test invoice still gets the right decision.

**Get a free Gemini key:** go to https://aistudio.google.com/apikey and choose *Create API key*. The free tier is plenty for this.
Claude works too: set `ANTHROPIC_API_KEY` instead (and `ANTHROPIC_MODEL` if you want a different model).

Check that every scenario still behaves:

```bash
python tests/test_scenarios.py     # runs all 11 invoices + a re-upload, prints PASS/FAIL
```

## Deploy (Render, free)

1. Push this folder to a new GitHub repo.
2. On https://render.com choose **New → Blueprint** and pick the repo. It reads `render.yaml` and builds the Dockerfile (with Tesseract).
3. When asked, paste your `GEMINI_API_KEY`.
4. Open the URL and check the header says *Reading with gemini-2.5-flash*.

Free-tier caveats you should know before the demo:
- The service sleeps after 15 minutes idle; the first request then takes about 30–50 seconds. **Open the link 2 minutes before any demo.**
- The disk resets when the service restarts, so the run history and ledger go back to the seed data. Use **Run all test invoices** on the Runs page to refill the dashboard in about 45 seconds.

Railway, Fly.io or Hugging Face Spaces (Docker) all work with the same Dockerfile.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | none | Use Gemini to read invoices |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Gemini model |
| `ANTHROPIC_API_KEY` | none | Use Claude to read invoices |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Claude model |
| `EXTRACTOR` | `auto` | `auto`, `gemini`, `anthropic`, or `rules` (force offline) |
| `STAGE_MIN_MS` | `550` | Minimum time each stage stays visible in the live view (pacing only; real processing time is measured and shown separately) |
| `DATA_DIR` | `data/runtime` | Where SQLite and uploads live |

Rule thresholds (tolerances, windows) are in `app/config.py` and shown on the *How it decides* page.

## Test invoices

`samples/` has 11 invoices from 5 fictional vendors in 3 different layouts (clean, branded, dot-matrix style), plus one image-only scan. Regenerate them with `python scripts/generate_samples.py`.

| # | Scenario | Decision | What it tests |
|---|---|---|---|
| 01 | Happy path | Approved | Clean PDF, exact PO match, inter-state IGST |
| 02 | Split PO, third of three | Approved | PO consumed across invoices; the last one fits exactly |
| 03 | Split PO, over-billing | On hold | Fine on its own; cumulative billing exceeds the PO |
| 04 | Disguised duplicate | On hold | `BSE-2526-42` is `BSE/2526/0042` (already paid) with a new date |
| 05 | Scanned, no PO number | On hold | OCR, GSTIN repair via check digit, PO inferred from line items |
| 06 | Total matches, price doesn't | On hold | Total within 0.3%, but 5 fewer chairs at 14% more each |
| 07 | Bank details changed | On hold (high) | Classic payment-fraud pattern; never verified from the invoice |
| 08 | Unknown vendor | Rejected | Not in master, GSTIN check digit wrong, PO doesn't exist |
| 09 | Proforma, missing fields | Rejected | Not a tax invoice; no number or date |
| 10 | Wrong GST type | Rejected | Both parties in Haryana but IGST charged: right amount, wrong tax head |
| 11 | Variance inside tolerance | Approved | Stretch film +1% vs PO; noted but not stopped |

Re-uploading an approved invoice is rejected as an exact duplicate. A low-resolution phone photo (PNG) of invoice 11 is fully read through two OCR passes and held, because one GSTIN needed an OCR repair. `tests/test_scenarios.py` checks all of these.

## How it works

| File | What's in it |
|---|---|
| `app/extract.py` | Text layer or OCR; LLM extraction to a fixed JSON schema; regex parser; GSTIN/IFSC OCR repair; cross-check |
| `app/rules.py` | Every check, its effect (pass / info / review / reject) and the decision rule |
| `app/gst.py` | GSTIN checksum (mod-36), state codes |
| `app/pipeline.py` | Runs stages in a background thread, records timing and findings, builds the output, handles reviewer overrides |
| `app/main.py` | Flask API and static UI |
| `static/` | The UI: plain HTML/CSS/JS, no build step |
| `data/reference/` | Vendor master, POs, PO lines, ledger history (CSV), loaded into SQLite |

**Decision rule:** any *reject* finding means the invoice is rejected (with a vendor email drafted). Otherwise any *review* finding means it's held, routed to the owner of the most serious issue. Otherwise it's approved and posted. If any stage throws an error, the invoice is held, never approved.

**When an LLM key is set**, the LLM reads the PDF and the regex parser reads it independently. If they disagree on a money field (invoice number, total, subtotal, GSTIN, bank account, PO), the invoice is held. If the LLM call fails, it falls back to the parser and notes that on the run.

## Known limitations

- Two-way match only (invoice ↔ PO). There's no goods-receipt data, so quantity received isn't checked.
- One buyer entity and INR only.
- The regex parser is tuned to common Indian invoice labels; truly unusual layouts need the LLM.
- PO consumption is tracked at value level; line-level quantity consumption across invoices isn't.
- Single-process SQLite, which is fine for a demo but not for concurrent production use.
