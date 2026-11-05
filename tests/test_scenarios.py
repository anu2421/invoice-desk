"""Runs every sample through the real pipeline against a fresh database and checks the
decision matches the scenario's expected outcome. Run:  python tests/test_scenarios.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["STAGE_MIN_MS"] = "0"
os.environ.setdefault("EXTRACTOR", "rules")
sys.path.insert(0, str(ROOT))

from app import db, pipeline  # noqa: E402


def run(name):
    rid = pipeline.start((ROOT / "samples" / name).read_bytes(), name)
    while db.get_run(rid)["status"] == "running":
        time.sleep(0.05)
    return db.get_run(rid)


def main():
    db.conn()
    manifest = json.loads((ROOT / "samples" / "manifest.json").read_text())
    failed = 0
    for m in [m for m in manifest if m.get("batch", True)]:
        r = run(m["file"])
        ok = r["decision"] == m["expect"]
        failed += not ok
        reasons = [f"{x['effect']}:{x['code']}" for x in r["findings"] if x["effect"] in ("review", "reject")]
        print(f"{'PASS' if ok else 'FAIL'}  {m['file']:<44} {r['decision']:<9} (expected {m['expect']})  {reasons}  {r['duration_ms']}ms")
    # resubmitting an approved invoice must be caught
    r = run("01_kaveri_stationery_clean.pdf")
    ok = r["decision"] == "rejected" and any(x["code"].startswith("DUPLICATE") for x in r["findings"])
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  re-upload of 01 is rejected as duplicate -> {r['decision']}")
    # a low-resolution phone photo of invoice 11 must be read fully and held (OCR-repaired GSTIN), not rejected.
    # Fresh ledger first: 11 was approved above, so its photo would (correctly) be rejected as a duplicate.
    db.load_reference(reset_runs=True)
    import io
    import pypdfium2 as pdfium
    buf = io.BytesIO()
    pdfium.PdfDocument(str(ROOT / "samples" / "11_sharma_bubblewrap_within_tolerance.pdf"))[0].render(scale=1.5).to_pil().save(buf, "PNG")
    rid = pipeline.start(buf.getvalue(), "photo.png")
    while db.get_run(rid)["status"] == "running":
        time.sleep(0.05)
    r = db.get_run(rid)
    fl = r["extracted"]["fields"]
    ok = r["decision"] == "review" and fl["invoice_number"] == "SPI/2026/0661" and fl["invoice_date"] == "2026-10-24" \
        and fl["vendor_gstin"] == "27AADFS3390Q1ZJ" and fl["total"] == 115781.6
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  phone photo of 11 is fully read and held for OCR repair -> {r['decision']}")
    # learning from reviewers: approve 12 and 13, accept the suggested tolerance, 14 then goes straight through
    db.load_reference(reset_runs=True)
    from app import rules as R
    for n in ("12_sharma_boxes_oct_surcharge.pdf", "13_sharma_boxes_nov_surcharge.pdf"):
        r = run(n)
        assert r["decision"] == "review", (n, r["decision"])
        pipeline.review_action(r["id"], "approve", "Freight surcharge agreed with procurement")
    sugg = [x for x in R.insights()[0] if x.get("kind") == "price_tolerance"]
    ok = bool(sugg) and sugg[0]["vendor_id"] == "V102" and sugg[0]["proposed"] == 4.0
    if ok:
        R.apply_price_tolerance("V102", sugg[0]["proposed"], "test")
        r = run("14_sharma_boxes_dec_surcharge.pdf")
        ok = r["decision"] == "approved"
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  learning loop: 2 approvals -> 4% tolerance suggested -> invoice 14 auto-approved")

    # a PDF holding two invoices is split into two runs
    import pypdfium2 as pdfium
    a = pdfium.PdfDocument(str(ROOT / "samples" / "01_kaveri_stationery_clean.pdf"))
    b = pdfium.PdfDocument(str(ROOT / "samples" / "06_kaveri_chairs_price_creep.pdf"))
    merged = pdfium.PdfDocument.new()
    merged.import_pages(a)
    merged.import_pages(b)
    merged.import_pages(b)  # a repeated page, as marketplace downloads often have
    buf = io.BytesIO()
    merged.save(buf)
    db.load_reference(reset_runs=True)
    rid = pipeline.start(buf.getvalue(), "two_invoices.pdf")
    while db.get_run(rid)["status"] == "running":
        time.sleep(0.05)
    parent = db.get_run(rid)
    kids = parent["output"]["children"] if parent["decision"] == "split" else []
    for k in kids:
        while db.get_run(k["id"])["status"] == "running":
            time.sleep(0.05)
    decisions = [db.get_run(k["id"])["decision"] for k in kids]
    ok = decisions == ["approved", "review"] and kids[1]["dropped"] == [3]
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  2 invoices + a repeated page in one PDF -> split into {len(kids)} runs {decisions}")
    print("ALL PASSED" if not failed else f"{failed} FAILED")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
