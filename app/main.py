"""HTTP API + static UI."""
import json
import threading
import time
from collections import Counter

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from . import config, db, pipeline, rules

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024
ALLOWED = (".pdf", ".png", ".jpg", ".jpeg")


@app.get("/")
def index():
    return send_from_directory(config.STATIC_DIR, "index.html")


@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(config.STATIC_DIR, p)


@app.get("/api/health")
def health():
    ex = config.active_extractor()
    model = {"gemini": config.GEMINI_MODEL, "anthropic": config.ANTHROPIC_MODEL}.get(ex)
    return jsonify(ok=True, extractor=ex, model=model)


def manifest():
    return json.loads((config.SAMPLES_DIR / "manifest.json").read_text())


@app.get("/api/samples")
def samples():
    return jsonify(manifest())


@app.get("/api/samples/<name>")
def sample_file(name):
    if name not in {m["file"] for m in manifest()}:
        abort(404)
    return send_file(config.SAMPLES_DIR / name, mimetype="application/pdf", download_name=name)


@app.post("/api/runs")
def create_run():
    if "file" in request.files:
        up = request.files["file"]
        name = up.filename or "upload.pdf"
        if not name.lower().endswith(ALLOWED):
            return jsonify(error="Upload a PDF, PNG or JPG invoice."), 400
        data = up.read()
        if not data:
            return jsonify(error="The file is empty."), 400
    else:
        body = request.get_json(silent=True) or {}
        name = body.get("sample")
        if name not in {m["file"] for m in manifest()}:
            return jsonify(error="Unknown sample."), 400
        data = (config.SAMPLES_DIR / name).read_bytes()
    return jsonify(id=pipeline.start(data, name))


_batch_lock = threading.Lock()


@app.post("/api/runs/batch")
def batch():
    """Run every sample in order (sequentially, since each run can change the ledger)."""
    if not _batch_lock.acquire(blocking=False):
        return jsonify(error="A batch is already running."), 409

    def go():
        try:
            for m in [m for m in manifest() if m.get("batch", True)]:
                rid = pipeline.start((config.SAMPLES_DIR / m["file"]).read_bytes(), m["file"])
                while (db.get_run(rid) or {}).get("status") == "running":
                    time.sleep(0.2)
        finally:
            _batch_lock.release()

    threading.Thread(target=go, daemon=True).start()
    return jsonify(started=len([m for m in manifest() if m.get("batch", True)]))


@app.get("/api/runs")
def runs():
    rows = db.list_runs()
    for r in rows:
        f = r.pop("findings") or []
        r["exceptions"] = [x["title"] for x in f if x["effect"] in ("review", "reject")]
    return jsonify(rows)


@app.get("/api/runs/<rid>")
def run(rid):
    r = db.get_run(rid)
    if not r:
        abort(404)
    r.pop("stored_path", None)
    return jsonify(r)


@app.get("/api/runs/<rid>/file")
def run_file(rid):
    r = db.get_run(rid)
    if not r:
        abort(404)
    return send_file(r["stored_path"], mimetype="application/pdf")


@app.get("/api/runs/<rid>/page/<int:n>.png")
def run_page(rid, n):
    """First pages rendered to PNG, so the invoice shows the same in every browser."""
    import io

    import pypdfium2 as pdfium
    r = db.get_run(rid)
    if not r:
        abort(404)
    pdf = pdfium.PdfDocument(r["stored_path"])
    if n >= len(pdf):
        abort(404)
    buf = io.BytesIO()
    pdf[n].render(scale=1.6).to_pil().save(buf, "PNG", optimize=True)
    buf.seek(0)
    resp = send_file(buf, mimetype="image/png")
    resp.headers["Cache-Control"] = "max-age=3600"
    return resp


@app.post("/api/runs/<rid>/review")
def review(rid):
    body = request.get_json(silent=True) or {}
    try:
        r = pipeline.review_action(rid, body.get("action"), (body.get("note") or "").strip())
    except ValueError as e:
        return jsonify(error=str(e)), 400
    r.pop("stored_path", None)
    return jsonify(r)


@app.get("/api/stats")
def stats():
    # a split PDF isn't an invoice itself; its parts are counted instead
    rows = [r for r in db.list_runs(5000) if r["status"] == "done" and r["decision"] != "split"]
    n = len(rows)
    auto = sum(r["decision"] == "approved" for r in rows)
    final = Counter(r["final_decision"] for r in rows)
    exc = Counter()
    for r in rows:
        for x in r["findings"] or []:
            if x["effect"] in ("review", "reject"):
                exc[x["title"].split(" (")[0] if x["code"] not in ("DUPLICATE_LIKELY", "PO_INFERRED", "PRICE_VARIANCE", "PO_OVERBILLED")
                    else {"DUPLICATE_LIKELY": "Likely duplicate", "PO_INFERRED": "PO inferred, needs confirming",
                          "PRICE_VARIANCE": "Unit price above PO", "PO_OVERBILLED": "PO over-billed"}[x["code"]]] += 1
    rerouted = sum(1 for r in rows if (r.get("output") or {}).get("kind") == "rerouted" and not r.get("review"))
    durations = [r["duration_ms"] for r in rows if r["duration_ms"]]
    held_value = sum((r["total"] or 0) for r in rows if r["final_decision"] == "review")
    caught = sum((r["total"] or 0) for r in rows if r["decision"] in ("review", "rejected"))
    return jsonify(
        runs=n, auto_approved=auto, touchless_rate=round(auto / n, 3) if n else None,
        final=dict(final), in_queue=final.get("review", 0), rerouted=rerouted,
        returned=final.get("rejected", 0) - rerouted,
        avg_seconds=round(sum(durations) / len(durations) / 1000, 1) if durations else None,
        value_processed=round(sum((r["total"] or 0) for r in rows), 2),
        value_held=round(held_value, 2), value_stopped=round(caught, 2),
        minutes_saved=round(auto * config.RULES["manual_minutes_per_invoice"]
                            + (n - auto) * config.RULES["manual_minutes_per_invoice"] * 0.5),
        exceptions=exc.most_common(), extractor=config.active_extractor())


@app.get("/api/reference")
def reference():
    pos = db.q("SELECT * FROM purchase_orders ORDER BY po_number")
    for p in pos:
        p["lines"] = db.q("SELECT * FROM po_lines WHERE po_number=? ORDER BY line_no", (p["po_number"],))
        p["billed"] = db.q1("SELECT COALESCE(SUM(subtotal),0) s FROM ledger WHERE po_number=? AND status='posted'",
                            (p["po_number"],))["s"]
    return jsonify(company=db.company(), vendors=db.q("SELECT * FROM vendors ORDER BY vendor_id"), pos=pos,
                   ledger=db.q("SELECT * FROM ledger ORDER BY COALESCE(invoice_date,'') DESC, id DESC"))


@app.get("/api/insights")
def insights():
    suggestions, all_stats = rules.insights()
    return jsonify(suggestions=suggestions, rules=all_stats,
                   vendor_rules=db.q("SELECT r.*, v.name FROM vendor_rules r JOIN vendors v USING (vendor_id)"))


@app.post("/api/insights/apply")
def apply_insight():
    body = request.get_json(silent=True) or {}
    vid, pct = body.get("vendor_id"), body.get("pct")
    if not vid or not isinstance(pct, (int, float)) or not (0 < pct <= 10):
        return jsonify(error="Need a vendor and a tolerance between 0 and 10%."), 400
    match = [x for x in rules.insights()[0] if x["vendor_id"] == vid and x.get("kind") == "price_tolerance"]
    if not match:
        return jsonify(error="No reviewer evidence for this change."), 400
    m = match[0]
    reason = f"learned from {m['approved']} reviewer approvals on {', '.join(m['runs'])}"
    rules.apply_price_tolerance(vid, float(pct), reason)
    return jsonify(ok=True, reason=reason)


@app.get("/api/rules")
def rules_cfg():
    return jsonify(rules=config.RULES, extractor=config.active_extractor(),
                   vendor_rules=db.q("SELECT r.*, v.name FROM vendor_rules r JOIN vendors v USING (vendor_id)"),
                   stages=[{"key": k, "name": n, "purpose": p} for k, n, p in pipeline.STAGES])


@app.post("/api/reset")
def reset():
    db.load_reference(reset_runs=True)
    for p in config.UPLOAD_DIR.glob("*"):
        p.unlink(missing_ok=True)
    return jsonify(ok=True)


db.conn()  # create tables and load reference data on start

if __name__ == "__main__":
    import os
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), threaded=True, debug=False)
