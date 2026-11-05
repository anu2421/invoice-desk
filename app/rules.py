"""Deterministic AP rules. The LLM never decides; it only reads. Every check here returns
findings with an effect:

    pass    - checked, fine
    info    - worth noting, doesn't change the outcome
    review  - a person must look before money moves
    reject  - cannot be booked as-is; goes back to the vendor

Decision = reject if any reject, else review if any review, else approve.
"""
import re
from datetime import date, timedelta

from . import config, db
from .extract import similarity
from .gst import STATE_CODES, state_of, validate_gstin

R = config.RULES
FIELD_NAMES = {"vendor_name": "Vendor name", "vendor_gstin": "Vendor GSTIN", "buyer_gstin": "Billed-to GSTIN",
               "invoice_number": "Invoice number", "invoice_date": "Invoice date", "po_number": "PO number",
               "subtotal": "Taxable value", "total": "Total", "bank_account": "Bank account", "line_items": "Line items"}


def F(code, title, effect, detail="", evidence=None, vendor_ask=None, owner=None, priority=None):
    return dict(code=code, title=title, effect=effect, detail=detail, evidence=evidence or {},
                vendor_ask=vendor_ask, owner=owner, priority=priority)


def inr(x):
    if x is None:
        return "—"
    neg = x < 0
    whole, frac = f"{abs(x):.2f}".split(".")
    if len(whole) > 3:
        head, tail, groups = whole[:-3], whole[-3:], []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + "₹" + whole + "." + frac


# ============================================================ 1. validate the document itself
def validate_invoice(f: dict, company: dict, read_info: dict):
    out = []
    title = (f.get("document_title") or "").upper()
    if any(w in title for w in ("PROFORMA", "QUOTATION", "ESTIMATE")):
        out.append(F("NOT_TAX_INVOICE", f"Document is a {title.title()}, not a tax invoice", "reject",
                     "A proforma or quotation can't be booked or used to claim input tax credit.",
                     vendor_ask="Please send the final GST tax invoice for this supply."))

    missing = [k for k in ("invoice_number", "invoice_date", "total") if not f.get(k)]
    names = {"invoice_number": "invoice number", "invoice_date": "invoice date", "total": "invoice total"}
    scanned = read_info.get("method") in ("OCR", "image only")
    if missing and scanned and "PROFORMA" not in title:
        # On a scan, "not read" isn't the same as "not on the invoice". Don't bounce it to the vendor.
        out.append(F("UNREADABLE_FIELDS", "Couldn't read " + ", ".join(names[m] for m in missing) + " from the scan", "review",
                     "The field may be on the document but illegible in the scan. A person checks the image and keys it in, "
                     "rather than sending the vendor a rejection for something they may have included.",
                     evidence={"missing": missing}, owner="AP clerk"))
    elif missing:
        out.append(F("MISSING_FIELDS", "Missing " + ", ".join(names[m] for m in missing), "reject",
                     "These are mandatory on a GST tax invoice, and the ledger can't key or de-duplicate without them.",
                     evidence={"missing": missing},
                     vendor_ask="The invoice is missing: " + ", ".join(names[m] for m in missing) + "."))
    else:
        out.append(F("MANDATORY_FIELDS", "Invoice number, date and total present", "pass"))

    low = [k for k, v in (f.get("confidence") or {}).items() if f.get(k) and v < R["min_field_confidence"]]
    if low:
        out.append(F("LOW_CONFIDENCE", "Some fields were read with low confidence", "review",
                     f"{', '.join(FIELD_NAMES.get(k, k) for k in low)} below {R['min_field_confidence']:.0%}"
                     + (" (OCR scan, corrected using the GSTIN format)" if f.get("repaired_fields") else ""),
                     evidence={"fields": low, "repaired": f.get("repaired_fields")}, owner="AP clerk", priority="low"))

    # arithmetic
    lines = f.get("line_items") or []
    tol = R["arithmetic_tolerance_inr"]
    bad = [l for l in lines if l.get("qty") and l.get("unit_price") and l.get("amount")
           and abs(l["qty"] * l["unit_price"] - l["amount"]) > tol]
    if bad:
        out.append(F("LINE_ARITHMETIC", "Line amounts don't equal qty × rate", "review",
                     "; ".join(f"{l['description']}: {l['qty']:g} × {inr(l['unit_price'])} ≠ {inr(l['amount'])}" for l in bad)))
    sub = f.get("subtotal")
    if lines and sub is not None:
        s = round(sum(l.get("amount") or 0 for l in lines), 2)
        if abs(s - sub) > tol:
            out.append(F("SUBTOTAL_MISMATCH", "Line items don't add up to the subtotal", "review",
                         f"Lines sum to {inr(s)}, invoice says {inr(sub)}", evidence={"lines_sum": s, "subtotal": sub}))
    taxes = f.get("taxes") or []
    tax_total = round(sum(t.get("amount") or 0 for t in taxes), 2)
    f["tax_total"] = tax_total
    if sub is not None and f.get("total") is not None:
        if abs(sub + tax_total - f["total"]) > tol:
            out.append(F("TOTAL_MISMATCH", "Subtotal plus tax doesn't equal the total", "review",
                         f"{inr(sub)} + {inr(tax_total)} = {inr(sub + tax_total)}, invoice total is {inr(f['total'])}"))
        else:
            out.append(F("ARITHMETIC", "Lines, tax and total add up", "pass",
                         f"{inr(sub)} + {inr(tax_total)} GST = {inr(f['total'])}"))
    for t in taxes:
        if sub and t.get("rate") and t.get("amount") and abs(sub * t["rate"] / 100 - t["amount"]) > tol:
            out.append(F("TAX_ARITHMETIC", f"{t['type']} amount doesn't match its rate", "review",
                         f"{t['rate']:g}% of {inr(sub)} is {inr(sub * t['rate'] / 100)}, invoice shows {inr(t['amount'])}"))

    # GST identity
    ok, why = validate_gstin(f.get("vendor_gstin"))
    if ok:
        out.append(F("VENDOR_GSTIN", "Vendor GSTIN is valid", "pass", f"{f['vendor_gstin']} · checksum ok"))
    else:
        out.append(F("VENDOR_GSTIN", "Vendor GSTIN is invalid", "reject", why,
                     evidence={"gstin": f.get("vendor_gstin")},
                     vendor_ask=f"The GSTIN on the invoice ({f.get('vendor_gstin') or 'none'}) is not valid. Please reissue with your registered GSTIN."))
    if f.get("buyer_gstin") and f["buyer_gstin"] != company["gstin"]:
        out.append(F("BUYER_GSTIN", "Invoice is addressed to a different GSTIN", "reject",
                     f"Billed to {f['buyer_gstin']}, our GSTIN is {company['gstin']}",
                     vendor_ask=f"Please bill to our GSTIN {company['gstin']}."))
    elif f.get("buyer_gstin"):
        out.append(F("BUYER_GSTIN", "Billed to our GSTIN", "pass", company["gstin"]))

    # place of supply: intra-state = CGST+SGST, inter-state = IGST
    vs, bs = state_of(f.get("vendor_gstin")), company["state_code"]
    kinds = {t["type"] for t in taxes}
    if vs and taxes:
        intra = vs == bs
        expected = {"CGST", "SGST"} if intra else {"IGST"}
        place = f"{STATE_CODES.get(vs, vs)} → {STATE_CODES.get(bs, bs)}"
        if kinds != expected and not (intra and kinds == {"CGST", "UTGST"}):
            out.append(F("TAX_TYPE", "Wrong GST type for the place of supply", "reject",
                         f"{place} is {'intra' if intra else 'inter'}-state, so it should be "
                         f"{' + '.join(sorted(expected))}. Invoice charges {' + '.join(sorted(kinds))}. "
                         "The amount is right but we couldn't claim the input tax credit.",
                         evidence={"vendor_state": vs, "buyer_state": bs, "charged": sorted(kinds)},
                         vendor_ask=f"Supply is {'within' if intra else 'across'} state lines ({place}), so GST should be charged as "
                                    f"{' + '.join(sorted(expected))}. Please issue a corrected invoice."))
        else:
            out.append(F("TAX_TYPE", f"GST type correct ({' + '.join(sorted(kinds))})", "pass", place))

    d = f.get("invoice_date")
    if d:
        dd = date.fromisoformat(d)
        if dd > date.today() + timedelta(days=1):
            out.append(F("FUTURE_DATE", "Invoice is dated in the future", "review", d))
        elif (date.today() - dd).days > R["max_invoice_age_days"]:
            out.append(F("STALE_INVOICE", f"Invoice is more than {R['max_invoice_age_days']} days old", "review", d))
    return out


# ============================================================ 2. vendor
def match_vendor(f: dict):
    out, vendor = [], None
    g = f.get("vendor_gstin")
    if g:
        vendor = db.q1("SELECT * FROM vendors WHERE gstin=?", (g,))
        if vendor:
            out.append(F("VENDOR_MATCH", f"Matched vendor {vendor['vendor_id']} by GSTIN", "pass", vendor["name"]))
    if not vendor and f.get("vendor_name"):
        best = max(db.q("SELECT * FROM vendors"), key=lambda v: similarity(v["name"], f["vendor_name"]))
        score = similarity(best["name"], f["vendor_name"])
        if score >= 0.85:
            vendor = best
            out.append(F("VENDOR_GSTIN_DIFFERS", f"Vendor matched by name only ({score:.0%})", "review",
                         f"Name matches {best['name']} but the GSTIN on the invoice ({g}) is not the one on file ({best['gstin']}).",
                         owner="Vendor master team", priority="high"))
    if not vendor:
        out.append(F("UNKNOWN_VENDOR", "Vendor is not in the vendor master", "reject",
                     f"No approved vendor matches \"{f.get('vendor_name')}\" or GSTIN {g}. "
                     "Unknown vendors are never paid from an invoice alone; they go through onboarding first.",
                     vendor_ask="We don't have you registered as a supplier. Please complete our vendor onboarding "
                                "(company details, GST certificate, cancelled cheque) before we can process invoices.",
                     owner="Procurement"))
        return vendor, out
    if vendor["status"] != "approved":
        out.append(F("VENDOR_BLOCKED", f"Vendor status is {vendor['status']}", "reject",
                     "Payments to this vendor are on hold in the vendor master.", owner="Procurement"))

    acct = f.get("bank_account")
    if acct and acct != vendor["bank_account"]:
        out.append(F("BANK_CHANGED", "Bank account differs from the vendor master", "review",
                     f"Invoice asks for payment to …{acct[-4:]} ({f.get('ifsc') or 'no IFSC'}); the verified account on file is "
                     f"…{vendor['bank_account'][-4:]} ({vendor['ifsc']}). "
                     + ("The invoice explicitly says the bank details changed. " if f.get("bank_change_notice") else "")
                     + "This is the most common invoice-fraud pattern: confirm by calling the vendor on the number already on file, "
                       "never using contact details from this invoice.",
                     evidence={"invoice_account": acct, "invoice_ifsc": f.get("ifsc"),
                               "master_account": vendor["bank_account"], "master_ifsc": vendor["ifsc"]},
                     owner="Treasury / vendor master", priority="high"))
    elif acct:
        out.append(F("BANK_MATCH", "Bank account matches the vendor master", "pass", f"…{acct[-4:]}"))
    return vendor, out


# ============================================================ 3. duplicates
def group_key(n: str):
    """BSE/2526/0042 and BSE-2526-42 both become ('BSE', '2526', '42')."""
    return tuple(re.sub(r"^0+(?=\d)", "", p) for p in re.findall(r"[A-Z]+|\d+", (n or "").upper()))


def line_sig(lines):
    return "|".join(sorted(f"{round(l.get('qty') or 0, 2)}x{round(l.get('unit_price') or 0, 2)}" for l in lines or []))


def check_duplicates(f: dict, vendor: dict | None, file_hash: str):
    out = []
    hit = db.q1("SELECT * FROM ledger WHERE file_hash=? AND status IN ('posted','held')", (file_hash,))
    if hit:
        out.append(F("DUPLICATE_FILE", "This exact file was already processed", "reject",
                     f"Same file as {hit['invoice_number']} ({hit['status']}).", evidence=hit,
                     vendor_ask=f"We've already received invoice {hit['invoice_number']}; no need to resend."))
        return out
    if not vendor or not f.get("invoice_number"):
        out.append(F("DUPLICATE_CHECK", "Duplicate check limited", "info",
                     "Needs a known vendor and an invoice number to check the ledger."))
        return out
    prior = db.q("SELECT * FROM ledger WHERE vendor_id=? AND status IN ('posted','held')", (vendor["vendor_id"],))
    num = f["invoice_number"].strip().upper()
    for p in prior:
        if p["invoice_number"].strip().upper() == num:
            out.append(F("DUPLICATE_EXACT", f"Invoice {num} is already in the ledger", "reject",
                         f"{p['invoice_number']} dated {p['invoice_date']} for {inr(p['total'])} is {p['status']}.",
                         evidence=p, vendor_ask=f"Invoice {num} was already received and is {p['status']}; we won't process it twice."))
            return out
    gk, total, d = group_key(num), f.get("total") or 0, f.get("invoice_date")
    for p in prior:
        reasons = []
        if group_key(p["invoice_number"]) == gk:
            reasons.append(f"same invoice number once formatting is ignored ({p['invoice_number']} vs {num})")
        if total and abs(p["total"] - total) <= max(1, total * 0.005):
            reasons.append(f"same total {inr(total)}")
        if p["po_number"] and p["po_number"] == f.get("po_number"):
            reasons.append(f"same PO {p['po_number']}")
        gap = None
        if d and p["invoice_date"]:
            gap = abs((date.fromisoformat(d) - date.fromisoformat(p["invoice_date"])).days)
        within = gap is None or gap <= R["duplicate_window_days"]
        strong = bool(reasons) and reasons[0].startswith("same invoice number")
        # Recurring bills (monthly retainers) share amount and PO by design, so amount + PO only
        # counts as a duplicate signal when the dates are also close together.
        weak_but_close = (not strong and len(reasons) >= 2 and "same total" in " ".join(reasons)
                          and gap is not None and gap <= 10)
        if within and (strong or weak_but_close):
            if not strong:
                reasons.append(f"dated {gap} days apart")
            score = min(0.97, 0.45 + 0.15 * len(reasons) + (0.1 if strong else 0))
            out.append(F("DUPLICATE_LIKELY", f"Likely duplicate of {p['invoice_number']} ({score:.0%} match)", "review",
                         "Matches on " + ", ".join(reasons) + f". The earlier invoice is {p['status']} "
                         f"(dated {p['invoice_date']}). Resubmissions with a new number format or date are the usual way "
                         "duplicate payments happen.",
                         evidence={"earlier": p, "score": score, "reasons": reasons}, owner="AP clerk", priority="high",
                         vendor_ask=f"This looks like a resubmission of invoice {p['invoice_number']} dated {p['invoice_date']}, "
                                    f"which we have already {'paid' if p['status'] == 'posted' else 'received'}. "
                                    "Please confirm whether it is a duplicate or a separate supply."))
            return out
    out.append(F("NO_DUPLICATE", "No duplicate found", "pass",
                 f"Checked {len(prior)} earlier invoices from this vendor (exact, reformatted number, amount)."))
    return out


# ============================================================ 4. purchase order
def _po_lines(po):
    return db.q("SELECT * FROM po_lines WHERE po_number=? ORDER BY line_no", (po,))


def score_po(f, po):
    lines = f.get("line_items") or []
    pl = _po_lines(po["po_number"])
    if not lines or not pl:
        return 0.0, {}
    sims = [max(similarity(l["description"], p["description"]) for p in pl) for l in lines]
    billed = db.q1("SELECT COALESCE(SUM(subtotal),0) s FROM ledger WHERE po_number=? AND status='posted'",
                   (po["po_number"],))["s"]
    remaining = po["total_value"] - billed
    sub = f.get("subtotal") or 0
    fit = max(0.0, 1 - abs(remaining - sub) / max(remaining, sub, 1))
    score = round(0.75 * (sum(sims) / len(sims)) + 0.25 * fit, 3)
    return score, {"description_match": round(sum(sims) / len(sims), 3), "amount_fit": round(fit, 3), "remaining": remaining}


def match_po(f: dict, vendor: dict | None):
    out, po, inferred = [], None, False
    if not vendor:
        out.append(F("PO_SKIPPED", "PO match skipped", "info", "Vendor is unknown, so there's no PO to match against."))
        return None, out
    pon = f.get("po_number")
    if pon:
        po = db.q1("SELECT * FROM purchase_orders WHERE po_number=?", (pon,))
        if not po:
            out.append(F("PO_NOT_FOUND", f"{pon} doesn't exist", "review",
                         "The PO quoted on the invoice isn't in the procurement system.", owner="Procurement"))
            return None, out
        if po["vendor_id"] != vendor["vendor_id"]:
            out.append(F("PO_OTHER_VENDOR", f"{pon} belongs to a different vendor", "review",
                         f"PO is raised on {po['vendor_id']}, invoice is from {vendor['vendor_id']}.", owner="Procurement"))
            return None, out
        out.append(F("PO_FOUND", f"Found {pon}", "pass", po["description"]))
    else:
        cands = db.q("SELECT * FROM purchase_orders WHERE vendor_id=? AND status='open'", (vendor["vendor_id"],))
        scored = sorted(((score_po(f, c), c) for c in cands), key=lambda x: -x[0][0])
        ranking = [{"po": c["po_number"], "score": s, **detail} for (s, detail), c in scored]
        if scored and scored[0][0][0] >= R["po_inference_min_score"] and \
                (len(scored) == 1 or scored[0][0][0] - scored[1][0][0] >= 0.1):
            po, inferred = scored[0][1], True
            out.append(F("PO_INFERRED", f"No PO quoted; best match is {po['po_number']} ({scored[0][0][0]:.0%})", "review",
                         f"Compared against {len(cands)} open POs for this vendor on line descriptions and remaining value. "
                         "An inferred PO is a suggestion, so a person confirms it before payment.",
                         evidence={"ranking": ranking}, owner="AP clerk"))
        else:
            out.append(F("PO_MISSING", "No PO quoted and no confident match", "review",
                         "Couldn't tie this invoice to an open PO.", evidence={"ranking": ranking},
                         vendor_ask="Please quote our purchase order number on the invoice.", owner="Procurement"))
            return None, out

    if po["status"] != "open":
        out.append(F("PO_CLOSED", f"{po['po_number']} is {po['status']}", "review", owner="Procurement"))
    if f.get("invoice_date") and f["invoice_date"] < po["po_date"]:
        out.append(F("INVOICE_BEFORE_PO", "Invoice is dated before the PO", "review",
                     f"Invoice {f['invoice_date']}, PO raised {po['po_date']}."))

    # line-level match
    pl = _po_lines(po["po_number"])
    rows, price_issues, unmatched = [], [], []
    for l in f.get("line_items") or []:
        best = max(pl, key=lambda p: similarity(l["description"], p["description"])) if pl else None
        sim = similarity(l["description"], best["description"]) if best else 0
        if not best or sim < 0.55:
            unmatched.append(l["description"])
            rows.append({"invoice": l, "po": None, "similarity": sim})
            continue
        var = (l["unit_price"] - best["unit_price"]) / best["unit_price"] * 100 if best["unit_price"] else 0
        row = {"invoice": l, "po": best, "similarity": sim, "price_var_pct": round(var, 2),
               "qty_var": round((l["qty"] or 0) - best["qty"], 2)}
        rows.append(row)
        if var > R["price_tolerance_pct"]:
            price_issues.append(row)
    if unmatched:
        out.append(F("LINE_NOT_ON_PO", "Invoice lines not on the PO", "review", "; ".join(unmatched), owner="Procurement"))
    for r in price_issues:
        out.append(F("PRICE_VARIANCE", f"Unit price {r['price_var_pct']:+.1f}% above PO for {r['po']['description']}", "review",
                     f"PO rate {inr(r['po']['unit_price'])} × {r['po']['qty']:g}; invoiced {inr(r['invoice']['unit_price'])} × "
                     f"{r['invoice']['qty']:g}. Tolerance is {R['price_tolerance_pct']:g}%."
                     + (f" Quantity is {abs(r['qty_var']):g} {'fewer' if r['qty_var'] < 0 else 'more'} than ordered, so the line "
                        f"total is {inr(r['invoice']['amount'])} against {inr(r['po']['qty'] * r['po']['unit_price'])} on the PO "
                        f"({(r['invoice']['amount'] / (r['po']['qty'] * r['po']['unit_price']) - 1) * 100:+.1f}%). A check on totals alone would have passed it."
                        if r.get('qty_var') else ""),
                     evidence=r, owner="Procurement",
                     vendor_ask=f"{r['po']['description']} is billed at {inr(r['invoice']['unit_price'])} against our PO rate of "
                                f"{inr(r['po']['unit_price'])}. Please confirm or reissue at the PO rate."))
    small = [r for r in rows if r.get("po") and 0 < abs(r.get("price_var_pct", 0)) <= R["price_tolerance_pct"]]
    for r in small:
        out.append(F("PRICE_WITHIN_TOL", f"{r['po']['description']}: {r['price_var_pct']:+.1f}% vs PO, within tolerance", "info",
                     f"Tolerance is {R['price_tolerance_pct']:g}%."))
    if rows and not unmatched and not price_issues:
        out.append(F("LINES_MATCH", f"All {len(rows)} lines match PO rates", "pass"))

    # cumulative consumption of the PO (split invoicing)
    prior = db.q("SELECT invoice_number, invoice_date, subtotal, status FROM ledger WHERE po_number=? AND status='posted' "
                 "ORDER BY invoice_date", (po["po_number"],))
    held = db.q("SELECT invoice_number, subtotal FROM ledger WHERE po_number=? AND status='held'", (po["po_number"],))
    billed = round(sum(p["subtotal"] for p in prior), 2)
    sub = f.get("subtotal") or 0
    after = billed + sub
    limit = po["total_value"] * (1 + R["amount_tolerance_pct"] / 100)
    consumption = {"po_value": po["total_value"], "billed_before": billed, "this_invoice": sub, "after": after,
                   "prior_invoices": prior, "held": held, "pct_after": round(after / po["total_value"] * 100, 1)}
    if after > limit:
        over = after - po["total_value"]
        out.append(F("PO_OVERBILLED", f"PO would be over-billed by {inr(over)} ({over / po['total_value']:.1%})", "review",
                     f"{po['po_number']} is worth {inr(po['total_value'])}. {len(prior)} earlier invoice(s) already used "
                     f"{inr(billed)}; this one adds {inr(sub)}, taking it to {inr(after)}. Each invoice looks fine on its own; "
                     "the problem only shows up across invoices.",
                     evidence=consumption, owner="Procurement", priority="high",
                     vendor_ask=f"Our purchase order {po['po_number']} has {inr(max(po['total_value'] - billed, 0))} remaining, and this invoice is for "
                                f"{inr(sub)} before tax. Please hold billing until a PO amendment is issued, or reissue for the remaining amount."))
    else:
        out.append(F("PO_BALANCE", f"Within PO value ({consumption['pct_after']:g}% used after this invoice)", "pass",
                     f"{inr(billed)} billed before + {inr(sub)} now = {inr(after)} of {inr(po['total_value'])}", evidence=consumption))
    if held:
        out.append(F("PO_HELD_EXPOSURE", f"{len(held)} other invoice(s) on this PO are on hold", "info",
                     ", ".join(h["invoice_number"] for h in held)))
    po = dict(po, inferred=inferred, lines=rows, consumption=consumption)
    return po, out


# ============================================================ 5. decision
EFFECT_RANK = {"reject": 3, "review": 2, "info": 1, "pass": 0}
PRIORITY_RANK = {"high": 0, None: 1, "normal": 1, "low": 2}


def reason_order(x):
    return (-EFFECT_RANK[x["effect"]], PRIORITY_RANK.get(x.get("priority"), 1))


def decide(findings: list):
    worst = max((EFFECT_RANK[x["effect"]] for x in findings), default=0)
    decision = {3: "rejected", 2: "review"}.get(worst, "approved")
    reasons = sorted([x for x in findings if x["effect"] in ("reject", "review")], key=reason_order)
    priority = "high" if any(x.get("priority") == "high" for x in reasons) else "normal"
    if decision == "approved":
        headline = "Approved for payment"
    elif decision == "review":
        headline = "Hold for review: " + reasons[0]["title"]
    else:
        headline = "Reject and return to vendor: " + reasons[0]["title"]
    return {"decision": decision, "headline": headline, "priority": priority,
            "reasons": [{"code": r["code"], "title": r["title"], "effect": r["effect"]} for r in reasons],
            "checks_passed": sum(1 for x in findings if x["effect"] == "pass"),
            "checks_total": len(findings)}
