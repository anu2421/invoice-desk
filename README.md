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
4. Open the URL and check the header says *Reading with gemini-3.8-flash*.

Free-tier caveats you should know before the demo:
- The service sleeps after 15 minutes idle; the first request then takes about 30–50 seconds. **Open the link 2 minutes before any demo.**
- The disk resets when the service restarts, so the run history and ledger go back to the seed data. Use **Run all test invoices** on the Runs page to refill the dashboard in about 45 seconds.

Railway, Fly.io or Hugging Face Spaces (Docker) all work with the same Dockerfile.

## Configuration

| Variable | Default | What it does |
|---|---|---|
| `GEMINI_API_KEY` | none | Use Gemini to read invoices |
| `GEMINI_MODEL` | `gemini-3.5-flash-lite` | Main Gemini model (falls back to gemini-3.1-flash-lite, then gemini-3.8-flash, if busy) |
| `ANTHROPIC_API_KEY` | none | Use Claude to read invoices |
| `ANTHROPIC_MODEL` | `claude-haiku-5-5` | Claude model |
| `EXTRACTOR` | `auto` | `auto`, `gemini`, `anthropic`, or `rules` (force offline) |
| `STAGE_MIN_MS` | `550` | Minimum time each stage stays visible in the live view (pacing only; real processing time is measured and shown separately) |
| `DATA_DIR` | `data/runtime` | Where SQLite and uploads live |

Rule thresholds (tolerances, windows) are in `app/config.py` and shown on the *How it decides* page.

## Test invoices

`samples/` has 14 invoices from 5 fictional vendors in 3 different layouts (clean, branded, dot-matrix style), plus one image-only scan. Regenerate them with `python scripts/generate_samples.py`.

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

| 12–14 | Learning from reviewers | On hold, then Approved | Sharma bills boxes 3.5% over PO (an agreed surcharge). After reviewers approve 12 and 13, a 4% vendor tolerance is suggested; once applied, 14 goes straight through. Not part of "Run all"; run them one by one. |

**Real-world documents.** It was also tested on real Myntra and Zomato downloads (not included, since they hold personal details). Those showed two things synthetic data didn't:
- One PDF often holds several documents from different sellers (product invoice, platform-fee invoice, delivery bill of supply), sometimes with a repeated page. They're split into one run each, and repeated pages are dropped.
- Consumer bills are addressed to a person ("Customer type: Unregistered"). Those are routed to employee expense claims, not paid as supplier invoices.
- They also exposed a bug: the GST-type rule assumed the buyer's state. It now uses the place of supply printed on the invoice.

The GSTIN checksum validates all 5 real GSTINs on those invoices.

Re-uploading an approved invoice is rejected as an exact duplicate. A low-resolution phone photo (PNG) of invoice 11 is fully read through two OCR passes and held, because one GSTIN needed an OCR repair. `tests/test_scenarios.py` checks all of these.

## Problems it's built around

| What goes wrong in AP | Evidence | What this does |
|---|---|---|
| Exceptions eat time | Top AP challenge for 53% of AP leaders; exception rate 22% typical vs 9% best-in-class ([Ardent Partners 2025](https://www.datocms-assets.com/80283/1744404602-ardent-partners-ap-metrics-that-matter-in-2025-pagero-final.pdf)) | Every hold names its reason and owner; holds reviewers keep approving become a suggested rule change |
| Slow approvals | 41% name it a top challenge; 9.2-day average cycle (Ardent 2025) | Seconds to a decision; held invoices show payment due date and days left |
| Vendor impersonation / fraud | 79% of organisations faced payments fraud; BEC top route at 63%; vendor impersonation 45% ([AFP 2025](https://www.financialprofessionals.org/about/learn-more/press-releases/Details/survey-79-percent-of-organizations-were-victims-of-attempted-or-actual-payments-fraud-activity-in-2024)) | Bank changes always held, never emailed back; lookalike vendors held; never relaxed by learning |
| Duplicate payments | Median 1.5% of disbursements (APQC, via [Docsumo](https://www.docsumo.com/blog/accounts-payable-statistics)) | File hash, exact and reformatted numbers, amount + PO within a date window |
| Messy documents | 77% still key invoices by hand (IFOL, via Docsumo) | Multi-invoice PDF splitting; personal bills routed to expenses |

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
