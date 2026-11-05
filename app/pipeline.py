"""Runs one invoice through the stages, recording each stage's status, timing, summary and
details so the UI can show it live and the dashboard can show it later."""
import hashlib
import threading
import time
import traceback
import uuid
from datetime import date, timedelta
from pathlib import Path

from . import config, db, extract, rules

STAGES = [
    ("receive", "Receive file", "Fingerprint the upload and convert photos to PDF"),
    ("read", "Read document", "Use the text layer, or OCR if it's a scan"),
    ("extract", "Extract fields", "Pull out vendor, numbers, lines and tax"),
    ("validate", "Validate invoice", "Mandatory fields, arithmetic, GSTIN, GST type"),
    ("vendor", "Match vendor", "Vendor master, status, bank details"),
    ("duplicates", "Check for duplicates", "Exact, reformatted and near-identical invoices"),
    ("po", "Match purchase order", "Find the PO, compare lines and cumulative billing"),
    ("decide", "Decide and route", "Apply the decision rule and prepare the output"),
]


class Run:
    def __init__(self, run_id):
        self.id = run_id
        self.stages = [dict(key=k, name=n, purpose=p, status="pending", started_at=None, duration_ms=None,
                            compute_ms=None, summary="", details={}, findings=[]) for k, n, p in STAGES]
        self.findings = []

    def stage(self, key):
        return next(s for s in self.stages if s["key"] == key)

    def save(self, **extra):
        db.update_run(self.id, stages=self.stages, findings=self.findings, **extra)


def new_run_id():
    return "R-" + uuid.uuid4().hex[:6].upper()


def start(file_bytes: bytes, filename: str) -> str:
    run_id = new_run_id()
    ext = Path(filename).suffix.lower() or ".pdf"
    stored = config.UPLOAD_DIR / f"{run_id}{ext}"
    stored.write_bytes(file_bytes)
    h = hashlib.sha256(file_bytes).hexdigest()
    run = Run(run_id)
    db.create_run(run_id, filename, stored, h, run.stages)
    threading.Thread(target=execute, args=(run, stored, filename, h), daemon=True).start()
    return run_id


def _stage(run, key, fn):
    """Run one stage: mark running, execute, record findings, pace for the live view."""
    s = run.stage(key)
    s["status"], s["started_at"] = "running", db.now_iso()
    run.save()
    t0 = time.perf_counter()
    try:
        summary, details, findings = fn()
        s["status"] = "done"
    except Exception as e:
        summary, details, findings = f"Stage failed: {e}", {"traceback": traceback.format_exc(limit=3)}, [
            rules.F("SYSTEM_ERROR", f"{s['name']} failed", "review",
                    f"{type(e).__name__}: {e}. Nothing was auto-approved; a person needs to process this one.",
                    owner="AP clerk")]
        s["status"] = "error"
    compute = (time.perf_counter() - t0) * 1000
    remaining = config.STAGE_MIN_MS / 1000 - (time.perf_counter() - t0)
    if remaining > 0:
        time.sleep(remaining)
    for x in findings:
        x["stage"] = key
    worst = max((rules.EFFECT_RANK[x["effect"]] for x in findings), default=0)
    if s["status"] == "done" and worst >= 2:
        s["status"] = "flag" if worst == 2 else "block"
    s.update(summary=summary, details=details, findings=findings, compute_ms=round(compute, 1),
             duration_ms=round((time.perf_counter() - t0) * 1000))
    run.findings.extend(findings)
    run.save()
    return s["status"] != "error"


def _skip(run, keys, why):
    for k in keys:
        s = run.stage(k)
        s.update(status="skipped", summary=why)
    run.save()


def execute(run: Run, path: Path, filename: str, file_hash: str):
    t_start = time.perf_counter()
    company = db.company()
    ctx = {"path": path}

    # ---------------------------------------------------------------- receive
    def receive():
        p = ctx["path"]
        size = p.stat().st_size
        head = p.read_bytes()[:5]
        details = {"filename": filename, "size_kb": round(size / 1024, 1), "sha256": file_hash[:16] + "…"}
        if head != b"%PDF-":
            from PIL import Image
            try:
                img = Image.open(p).convert("RGB")
            except Exception:
                raise ValueError("File is neither a PDF nor an image")
            pdf_path = p.with_suffix(".pdf")
            img.save(pdf_path, "PDF", resolution=200)
            ctx["path"] = pdf_path
            db.update_run(run.id, stored_path=str(pdf_path))
            details["converted"] = f"{img.width}×{img.height} image converted to PDF"
            return "Image received and converted to PDF", details, []
        return f"PDF received · {details['size_kb']} KB", details, []

    if not _stage(run, "receive", receive):
        _skip(run, [k for k, *_ in STAGES[1:-1]], "Skipped: file could not be received")
        return finish(run, t_start, None, None, None, company)

    # ---------------------------------------------------------------- read
    def read():
        info = extract.read_document(ctx["path"])
        ctx["read"] = info
        if info.get("error") and not info.get("text"):
            if info.get("pages") and config.active_extractor() != "rules":
                # Image-only PDF and no local OCR: the LLM can still read the page itself.
                return ("No text layer and local OCR unavailable; the LLM will read the page image",
                        {"method": "image only", "pages": info["pages"]},
                        [rules.F("NO_LOCAL_OCR", "Read by the LLM only, no parser cross-check", "info", info["error"])])
            raise ValueError(info["error"])
        d = {"method": info["method"], "pages": info["pages"], "characters": info["chars"],
             "text_preview": info["text"][:1800]}
        if info.get("ocr_confidence") is not None:
            d["ocr_confidence"] = info["ocr_confidence"]
        if info["method"] == "OCR":
            return (f"Scanned document: no text layer, read with OCR "
                    f"({info.get('ocr_confidence', 0):.0%} mean word confidence)"), d, [
                rules.F("SCANNED", "Scanned document, read with OCR", "info",
                        "OCR text is less reliable, so field confidence is lowered.")]
        return f"Text layer found · {info['pages']} page(s), {info['chars']} characters", d, []

    if not _stage(run, "read", read):
        _skip(run, [k for k, *_ in STAGES[2:-1]], "Skipped: document could not be read")
        return finish(run, t_start, None, None, None, company)

    # ---------------------------------------------------------------- extract
    def do_extract():
        info = ctx["read"]
        if not info.get("text") and config.active_extractor() == "rules":
            raise ValueError("No text to extract from")
        parsed = extract.rules_extract(info.get("text") or "", ocr=info["method"] == "OCR",
                                       ocr_confidence=info.get("ocr_confidence"))
        findings, details = [], {}
        if info.get("alt_text"):
            alt = extract.rules_extract(info["alt_text"], ocr=True, ocr_confidence=info.get("ocr_confidence"))
            filled = extract.merge_passes(parsed, alt)
            if filled:
                details["second_ocr_pass"] = f"Filled {', '.join(filled)} from a second OCR pass"
        provider = config.active_extractor()
        fields, used = parsed, "rules parser"
        if provider in ("gemini", "anthropic"):
            try:
                t = time.perf_counter()
                llm = extract.llm_extract(ctx["path"], provider, scanned=info.get("method") != "text layer")
                details["llm_ms"] = round((time.perf_counter() - t) * 1000)
                fields, used = llm, f"{provider} ({llm.pop('_model', '')})"
                diffs = extract.cross_check(llm, parsed)
                details["cross_check"] = diffs or "LLM and parser agree on every money field both could read"
                if diffs:
                    findings.append(rules.F("EXTRACTOR_DISAGREE", "LLM and parser disagree on " +
                                            ", ".join(d["field"] for d in diffs), "review",
                                            "; ".join(f"{d['field']}: LLM read {d['llm']}, parser read {d['parser']}" for d in diffs)
                                            + ". When two independent reads of a money field differ, a person decides.",
                                            evidence={"diffs": diffs}, owner="AP clerk"))
            except Exception as e:
                findings.append(rules.F("LLM_FALLBACK", "LLM unavailable, used the rules parser", "info",
                                        f"{type(e).__name__}: {str(e)[:160]}"))
                used = "rules parser (LLM fallback)"
        ctx["fields"] = fields
        details["extractor"] = used
        details["fields"] = {k: fields.get(k) for k in (
            "document_title", "vendor_name", "vendor_gstin", "buyer_gstin", "invoice_number", "invoice_date",
            "po_number", "subtotal", "total", "bank_account", "ifsc")}
        details["line_items"] = fields.get("line_items")
        details["taxes"] = fields.get("taxes")
        details["confidence"] = fields.get("confidence")
        details["repaired"] = fields.get("repaired_fields")
        details["repairs"] = fields.get("repairs") or parsed.get("repairs")
        missing = [k for k, v in details["fields"].items() if v in (None, "")]
        db.update_run(run.id, extractor=used, extracted=details, vendor_name=fields.get("vendor_name"),
                      invoice_number=fields.get("invoice_number"), total=fields.get("total"))
        s = f"{len([v for v in details['fields'].values() if v not in (None, '')])} fields and " \
            f"{len(fields.get('line_items') or [])} line items via {used}"
        if missing:
            s += f" · not found: {', '.join(missing)}"
        return s, details, findings

    if not _stage(run, "extract", do_extract):
        _skip(run, [k for k, *_ in STAGES[3:-1]], "Skipped: nothing to check without extracted fields")
        return finish(run, t_start, None, None, None, company)
    f = ctx["fields"]

    # ---------------------------------------------------------------- validate
    def validate():
        out = rules.validate_invoice(f, company, ctx["read"])
        bad = [x for x in out if x["effect"] in ("review", "reject")]
        return (f"{sum(x['effect'] == 'pass' for x in out)} checks passed"
                + (f", {len(bad)} issue(s)" if bad else "")), {}, out

    _stage(run, "validate", validate)

    # ---------------------------------------------------------------- vendor
    def vendor():
        v, out = rules.match_vendor(f)
        ctx["vendor"] = v
        if v:
            db.update_run(run.id, vendor_id=v["vendor_id"], vendor_name=v["name"])
            return f"{v['vendor_id']} · {v['name']} · {v['status']}", {"vendor": v}, out
        return "No matching vendor in the master", {}, out

    _stage(run, "vendor", vendor)
    v = ctx.get("vendor")

    # ---------------------------------------------------------------- duplicates
    def dups():
        out = rules.check_duplicates(f, v, file_hash)
        top = next((x for x in out if x["effect"] != "pass"), out[0] if out else None)
        return (top["title"] if top else "Checked"), {}, out

    _stage(run, "duplicates", dups)

    # ---------------------------------------------------------------- po
    def po():
        p, out = rules.match_po(f, v)
        ctx["po"] = p
        if not p:
            return (out[0]["title"] if out else "No PO"), {}, out
        c = p["consumption"]
        return (f"{p['po_number']}{' (inferred)' if p['inferred'] else ''} · {c['pct_after']:g}% of PO used after this invoice",
                {"po": {k: p[k] for k in ("po_number", "vendor_id", "po_date", "status", "description", "total_value", "inferred")},
                 "lines": p["lines"], "consumption": c}, out)

    _stage(run, "po", po)
    return finish(run, t_start, f, v, ctx.get("po"), company)


# ==================================================================== decide + output
def finish(run, t_start, f, vendor, po, company):
    def decide():
        if f is None:
            d = {"decision": "review", "headline": "Hold for review: could not process automatically",
                 "priority": "normal", "reasons": [{"code": x["code"], "title": x["title"], "effect": x["effect"]}
                                                   for x in run.findings if x["effect"] in ("review", "reject")],
                 "checks_passed": 0, "checks_total": len(run.findings)}
        else:
            d = rules.decide(run.findings)
        output = build_output(run, d, f, vendor, po, company)
        ctx_out.update(decision=d, output=output)
        return d["headline"], {"decision": d, "output": output}, []

    ctx_out = {}
    _stage(run, "decide", decide)
    d = ctx_out.get("decision") or {"decision": "review", "headline": "Hold for review: system error"}
    # Report real processing time (sum of stage compute), not the presentation pacing.
    processing = sum(s["compute_ms"] or 0 for s in run.stages)
    run.save(status="done", decision=d["decision"], final_decision=d["decision"],
             output=ctx_out.get("output"), duration_ms=round(processing))


def build_output(run, d, f, vendor, po, company):
    f = f or {}
    r = db.get_run(run.id)
    base = {"decision": d["decision"], "headline": d["headline"], "priority": d.get("priority")}
    if d["decision"] == "approved":
        terms = vendor["payment_terms_days"] if vendor else 30
        due = (date.fromisoformat(f["invoice_date"]) + timedelta(days=terms)).isoformat() if f.get("invoice_date") else None
        posting = {"vendor": f"{vendor['vendor_id']} · {vendor['name']}", "invoice_number": f.get("invoice_number"),
                   "invoice_date": f.get("invoice_date"), "po_number": po["po_number"] if po else None,
                   "taxable_value": f.get("subtotal"), "gst": f.get("tax_total"), "total_payable": f.get("total"),
                   "payment_terms": f"Net {terms}", "due_date": due,
                   "pay_to": f"{vendor['bank_name']} …{vendor['bank_account'][-4:]} (verified account on file)",
                   "itc_claimable": f.get("tax_total")}
        ledger_insert(vendor, f, po, "posted", run.id, r["file_hash"])
        notes = [x["title"] for x in run.findings if x["effect"] == "info"]
        return {**base, "kind": "posting", "posting": posting, "notes": notes,
                "next": (f"Posted to the AP ledger. Scheduled for payment on "
                         f"{date.fromisoformat(due).day} {date.fromisoformat(due).strftime('%b %Y')}.") if due else "Posted to the AP ledger."}

    reasons = [x for x in run.findings if x["effect"] in ("review", "reject")]
    reasons.sort(key=rules.reason_order)
    asks = [x["vendor_ask"] for x in reasons if x.get("vendor_ask")]
    fraud = any(x["code"] == "BANK_CHANGED" for x in reasons)

    if d["decision"] == "review":
        owner = next((x["owner"] for x in reasons if x.get("owner")), "AP clerk")
        if vendor:
            ledger_insert(vendor, f, po, "held", run.id, r["file_hash"])
        out = {**base, "kind": "review", "queue": owner,
               "checklist": [f"{x['title']}: {x['detail']}" if x.get("detail") else x["title"] for x in reasons],
               "next": f"Held in the {owner} queue. Not paid until someone approves it."}
        if fraud:
            out["warning"] = ("Don't contact the vendor using details on this invoice. Call the number in the vendor master "
                              "to confirm the bank change, then update the master before approving.")
        elif asks:
            out["vendor_email"] = vendor_email(f, vendor, po, asks, company, returned=False)
        return out

    return {**base, "kind": "returned",
            "vendor_email": vendor_email(f, vendor, po, asks or [x["title"] for x in reasons], company, returned=True),
            "next": "Not booked. Email drafted to the vendor for a corrected invoice."}


def ledger_insert(vendor, f, po, status, run_id, file_hash):
    db.ex("INSERT INTO ledger (vendor_id, invoice_number, invoice_date, po_number, subtotal, tax, total, status, run_id,"
          " file_hash, line_sig, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          (vendor["vendor_id"] if vendor else None, f.get("invoice_number") or "(none)", f.get("invoice_date"),
           po["po_number"] if po else None, f.get("subtotal") or 0, f.get("tax_total") or 0, f.get("total") or 0,
           status, run_id, file_hash, rules.line_sig(f.get("line_items")), db.now_iso()))


def vendor_email(f, vendor, po, asks, company, returned: bool):
    name = vendor["name"] if vendor else (f.get("vendor_name") or "Vendor")
    num = f.get("invoice_number")
    ref = f"invoice {num}" if num else (f"your invoice dated {f['invoice_date']}" if f.get("invoice_date") else
                                       f"your invoice for {rules.inr(f.get('total'))}")
    to = vendor["email"] if vendor else "(contact on the invoice; vendor is not in our master)"
    subject = f"{ref[0].upper() + ref[1:]}: {'returned, corrected invoice needed' if returned else 'query before payment'}"
    po_txt = f" against {po['po_number']}" if po else (f" quoting {f['po_number']}" if f.get("po_number") else "")
    body = [f"Hello {name} team,", "",
            f"Thank you for {ref}{po_txt}. "
            + ("We're unable to process it in its current form:" if returned else
               "Before we can release payment we need to clear the following:"), ""]
    body += [f"{i}. {a}" for i, a in enumerate(dict.fromkeys(asks), 1)]
    body += ["", ("Please send a corrected invoice to ap@halden-retail.example and we'll process it on receipt."
                  if returned else "A quick reply to this email is enough; we'll update the invoice on our side."),
             "", "Regards,", "Accounts Payable", company["name"]]
    return {"to": to, "subject": subject, "body": "\n".join(body)}


# ==================================================================== human review
def review_action(run_id: str, action: str, note: str, reviewer: str = "AP reviewer"):
    r = db.get_run(run_id)
    if not r or r["final_decision"] != "review":
        raise ValueError("Only runs on hold can be reviewed")
    if action == "approve":
        db.ex("UPDATE ledger SET status='posted' WHERE run_id=?", (run_id,))
        final = "approved"
    elif action == "reject":
        db.ex("UPDATE ledger SET status='rejected' WHERE run_id=?", (run_id,))
        final = "rejected"
    else:
        raise ValueError("action must be approve or reject")
    db.update_run(run_id, final_decision=final,
                  review={"action": action, "note": note, "by": reviewer, "at": db.now_iso()})
    return db.get_run(run_id)
