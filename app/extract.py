"""Reading and field extraction.

Two extractors produce the same normalised schema:
  * an LLM (Gemini or Claude) that reads the PDF directly, including scans
  * a deterministic parser (regex over the text layer, or over OCR text for scans)

When the LLM runs, the deterministic parser still runs alongside it and the money-critical
fields are cross-checked. If the two disagree, the invoice goes to a human rather than
trusting either one.
"""
import base64
import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher

import pdfplumber
import requests

from . import config
from .gst import CHARSET, checksum_char

KEY_FIELDS = ["vendor_name", "vendor_gstin", "invoice_number", "invoice_date", "po_number",
              "subtotal", "total", "buyer_gstin", "bank_account"]


# ============================================================ normalisers
def parse_amount(v):
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    s = re.sub(r"(?i)(rs\.?|inr|₹|/-)", "", str(v)).replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return round(float(m.group()), 2) if m else None


MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def parse_date(v):
    """Accepts 14-Oct-2026, 30 Sep 2026, 02/10/2026 (dd/mm/yyyy, Indian convention), 2026-10-02."""
    if not v:
        return None
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[\-/ .]([A-Za-z]{3,9})[\-/ .,]*(\d{4})", s)
        if m:
            d, mo, y = int(m.group(1)), MONTHS.get(m.group(2)[:3].lower()), int(m.group(3))
        else:
            m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
            if not m:
                return None
            d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d).isoformat()
    except (TypeError, ValueError):
        return None


def norm_text(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def similarity(a: str, b: str) -> float:
    a, b = norm_text(a), norm_text(b)
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jacc = len(ta & tb) / len(ta | tb)
    return round(max(SequenceMatcher(None, a, b).ratio(), jacc), 3)


# ============================================================ OCR-aware repair
DIGIT_FIX = {"O": "0", "D": "0", "Q": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}
ALPHA_FIX = {v: k for k, v in {"O": "0", "I": "1", "S": "5", "B": "8", "Z": "2", "G": "6"}.items()}


CONFUSABLE = [set("0OQD"), set("1IL|T!"), set("5S"), set("8B"), set("2Z"), set("6G")]


def _options(ch, kind, i):
    """Alternatives OCR might have confused this character with. Only characters that break
    the GSTIN shape are changed; the check character (last) may swap with a lookalike."""
    group = next((g for g in CONFUSABLE if ch in g), {ch})
    if kind == "Z":
        return ["Z"]
    fits = (kind == "D" and ch.isdigit()) or (kind == "A" and ch.isalpha()) or kind == "X"
    if fits and i != 14:
        return [ch]
    if kind == "D":
        opts = [c for c in group if c.isdigit()]
    elif kind == "A":
        opts = [c for c in group if c.isalpha()]
    else:
        opts = [c for c in group if c in CHARSET]
    if ch in opts:
        opts.remove(ch)
        opts.insert(0, ch)
    return opts or [ch]


def repair_gstin(raw: str | None, from_ocr: bool = True):
    """Fix OCR confusions (O/0/Q, I/1, S/5...) using the GSTIN's fixed shape
    (2 digits, 5 letters, 4 digits, letter, entity, 'Z', check) and its mod-36 check digit.
    Text that came from a real text layer is never altered: an invalid GSTIN there is a finding,
    not a typo to fix. Returns (gstin, was_repaired)."""
    if not raw:
        return None, False
    s = re.sub(r"[^0-9A-Z|!]", "", raw.upper())
    if len(s) != 15 or not from_ocr:
        return (s if len(s) == 15 else raw.upper()), False
    shape = "DDAAAAADDDDAXZX"
    options = [_options(ch, kind, i) for i, (ch, kind) in enumerate(zip(s, shape))]
    baseline = "".join(o[0] for o in options)
    best = None
    from itertools import product
    for cand in product(*options):
        c = "".join(cand)
        if all(x in CHARSET for x in c) and checksum_char(c[:14]) == c[14]:
            changes = sum(a != b for a, b in zip(c, s))
            if best is None or changes < best[0]:
                best = (changes, c)
    fixed = best[1] if best else baseline
    return fixed, fixed != s


def repair_ifsc(raw: str | None):
    if not raw:
        return None, False
    s = raw.upper()
    if len(s) == 11:
        fixed = "".join(ALPHA_FIX.get(c, c) for c in s[:4]) + "0" + s[5:]
        return fixed, fixed != s
    return s, False


# ============================================================ reading
def read_document(path):
    """Returns text plus how it was obtained. Scans have little or no text layer."""
    info = {"pages": 0, "method": None, "chars": 0, "text": ""}
    try:
        with pdfplumber.open(path) as pdf:
            info["pages"] = len(pdf.pages)
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception as e:  # not a PDF, encrypted, corrupt
        info["error"] = f"Could not open as PDF: {e}"
        return info
    if len(text.strip()) >= 80:
        info.update(method="text layer", text=text, chars=len(text))
        return info
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        images, parts, confs = [], [], []
        for i in range(min(len(pdf), 3)):
            page = pdf[i]
            # photos converted to PDF can be small; OCR needs roughly 2000px across for invoice-sized text
            scale = max(200 / 72, 1700 / page.get_width())
            img = page.render(scale=scale).to_pil().convert("L")
            images.append(img)
            text_i, conf_i = ocr_image(img, psm=6)
            parts.append(text_i)
            confs += conf_i
        text = "\n".join(parts)
        info.update(method="OCR", text=text, chars=len(text), _images=images,
                    ocr_confidence=round(sum(confs) / len(confs) / 100, 2) if confs else None)
    except Exception as e:
        info.update(method="none", error=f"No text layer and OCR is unavailable: {e}")
    return info


def ocr_image(img, psm=6):
    """One Tesseract pass that returns both the text (rebuilt line by line) and word confidences."""
    import pytesseract

    d = pytesseract.image_to_data(img, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    lines, order, confs = {}, [], []
    for i, word in enumerate(d["text"]):
        if not str(word).strip():
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
        c = float(d["conf"][i])
        if c >= 0:
            confs.append(c)
    return "\n".join(" ".join(lines[k]) for k in order), confs


def second_ocr_pass(info) -> str:
    """Column-aware pass (psm 4) for when the first pass missed fields. Only run when needed,
    since OCR is the slowest step on a small server."""
    return "\n".join(ocr_image(img, psm=4)[0] for img in info.get("_images") or [])


# ============================================================ rules-based extractor
LINE_RE = re.compile(
    r"^(?:\d{1,3}[.)]?\s+)?(?P<desc>[A-Za-z].+?)\s+(?:(?P<hsn>\d{4,8})\s+)?(?P<qty>\d[\d,]*(?:\.\d+)?)\s+"
    r"(?:[A-Za-z]{2,6}\s+)?(?P<rate>[\d,]+\.\d{2})\s+(?P<amt>[\d,]+\.\d{2})\s*$")
AMT = r"(?:Rs\.?|INR|₹)?\s*([\d,]+\.\d{2})"
DOC_NO = (r"(?:Invoice\s*(?:No\.?|Number|#)|Bill\s*of\s*Supply\s*(?:No\.?|Number)|Bill\s*No\.?|Inv\.?\s*No\.?)"
          r"\s*[:#.\-]?\s*([A-Z0-9][A-Z0-9/\-]{2,})")


def split_documents(path):
    """Find where one invoice ends and the next begins inside a single PDF (common with
    marketplace downloads: product invoice + platform-fee invoice + delivery bill of supply).
    Pages are grouped by the document number printed on them; a page without a number
    continues the previous document; an exact repeat of a page is dropped as a duplicate copy.
    Returns a list of groups: {"number", "pages": [0-based], "dropped": [0-based]}."""
    try:
        with pdfplumber.open(path) as pdf:
            texts = [(p.extract_text() or "") for p in pdf.pages]
    except Exception:
        return []
    groups, seen = [], set()
    for i, t in enumerate(texts):
        m = re.search(DOC_NO, t, re.I | re.M)
        num = m.group(1).strip().upper() if m else None
        key = re.sub(r"\s+", " ", t).strip()
        if groups and key and key in seen:
            groups[-1]["dropped"].append(i)
            continue
        seen.add(key)
        if num and (not groups or groups[-1]["number"] not in (None, num)):
            groups.append({"number": num, "pages": [i], "dropped": []})
        elif groups:
            groups[-1]["pages"].append(i)
            groups[-1]["number"] = groups[-1]["number"] or num
        else:
            groups.append({"number": num, "pages": [i], "dropped": []})
    return groups


def write_pages(src, pages, dest):
    import pypdfium2 as pdfium
    pdf, new = pdfium.PdfDocument(str(src)), pdfium.PdfDocument.new()
    new.import_pages(pdf, pages)
    new.save(str(dest))


def _find(pattern, text, flags=re.I | re.M):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def rules_extract(text: str, ocr: bool = False, ocr_confidence: float | None = None):
    f, conf = {}, {}
    # OCR'd fields inherit the engine's word confidence; repaired fields drop below the review bar
    base = round(min(0.95, (ocr_confidence or 0.8) * 0.97), 2) if ocr else 0.95
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    # vendor name: first line of the document, minus document-type words
    first = lines[0] if lines else ""
    f["vendor_name"] = re.sub(r"(?i)\b(tax invoice|proforma invoice|bill of supply|invoice|original for recipient)\b", "", first).strip(" -:|") or None
    if not f["vendor_name"]:
        # marketplace layouts name the seller under a label instead of at the top
        company = r"([A-Za-z][A-Za-z&. ]*?\b(?:Private Limited|Pvt\.? Ltd\.?|Limited|LLP))\b"
        for pat in (r"Legal\s*Entity\s*Name\s*:\s*([^\n]+)",
                    r"Bill(?:ed)?\s*From\s*:?[^\n]*\n[ \t]*" + company,
                    r"(?:Service\s*Provider|Sold\s*By|Seller)[^\n]*\n(?:[^\n]*\n)?[ \t]*" + company,
                    company):
            v = _find(pat, text, re.I | re.M)
            if v:
                f["vendor_name"] = v.strip()
                break
    f["document_title"] = _find(r"\b(PROFORMA INVOICE|TAX INVOICE|BILL OF SUPPLY|QUOTATION|CREDIT NOTE|DEBIT NOTE|INVOICE)\b", text)

    f["invoice_number"] = _find(DOC_NO, text)
    raw_date = _find(r"(?:Invoice\s*Date|(?<![A-Za-z])(?<!Due )(?<!PO )Date)\s*[:\-]?\s*"
                     r"(\d{1,2}[\-/ ][A-Za-z]{3,9}[\-/ ]\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})", text)
    f["invoice_date"] = parse_date(raw_date)
    po = _find(r"\b(PO[\-\s]?\d{4,6})\b", text, re.M)
    f["po_number"] = re.sub(r"PO[\-\s]?", "PO-", po.upper()) if po else None

    # GSTINs: the one after "Bill To / Buyer" belongs to us; the other is the vendor's
    found = [(m.start(), m.group(1)) for m in re.finditer(
        r"GSTIN\s*(?:Number|No\.?)?\s*[:\-]?\s*([0-9A-Z|!]{15})", text, re.I)]
    bill_pos = min([m.start() for m in re.finditer(r"(?i)bill(?:ed)?\s*to|buyer", text)] or [10 ** 9])
    if len(found) == 1:
        # one GSTIN on the page is the seller's; an individual buyer has none
        vendor_g, buyer_g = found[0][1], None
    else:
        vendor_g = next((g for p, g in found if p < bill_pos), found[0][1] if found and bill_pos == 10 ** 9 else None)
        buyer_g = next((g for p, g in found if p > bill_pos), None)
    repaired, repairs = [], {}
    for key, g in (("vendor_gstin", vendor_g), ("buyer_gstin", buyer_g)):
        fixed, did = repair_gstin(g, from_ocr=ocr)
        f[key] = fixed
        if did:
            repaired.append(key)
            repairs[key] = g

    items = []
    for l in lines:
        m = LINE_RE.match(l)
        if not m:
            continue
        qty, rate, amt = parse_amount(m["qty"]), parse_amount(m["rate"]), parse_amount(m["amt"])
        if qty and rate and amt and abs(qty * rate - amt) <= max(1.0, amt * 0.01):
            items.append(dict(description=m["desc"].strip(), hsn=m["hsn"], qty=qty, unit_price=rate, amount=amt))
    f["line_items"] = items

    f["subtotal"] = parse_amount(_find(r"(?:Sub\s*Total|Taxable\s*(?:Value|Amount))\s*:?\s*" + AMT, text))
    taxes = []
    for m in re.finditer(r"\b(CGST|SGST|IGST|UTGST)\s*@?\s*(\d{1,2}(?:\.\d+)?)\s*%\s*" + AMT, text, re.I):
        taxes.append(dict(type=m.group(1).upper(), rate=float(m.group(2)), amount=parse_amount(m.group(3))))
    f["taxes"] = taxes
    # total: the LAST amount on the first total line (tables put the final column last)
    total = None
    m = re.search(r"^[ \t]*(?:Grand\s*Total|Total\s*Amount|Amount\s*Payable|Net\s*Amount|Invoice\s*Total|Total\s*Value|Total)\b(.*)$",
                  text, re.I | re.M)
    if m:
        nums = re.findall(r"(?:Rs\.?|INR|₹)?\s*(\d[\d,]*\.\d{2,3})\b", m.group(1))
        total = parse_amount(nums[-1]) if nums else None
    if total is None:
        total = parse_amount(_find(r"Amount\s*of\s*INR\s*([\d,]+\.\d{2})", text))
    f["total"] = total

    f["bank_account"] = _find(r"(?:A/?C\.?\s*(?:No\.?|Number)?|Account\s*(?:No\.?|Number))\s*[:\-]?\s*(\d{9,18})", text)
    ifsc, did = repair_ifsc(_find(r"IFSC\s*(?:Code)?\s*[:\-]?\s*([A-Z0-9]{11})", text))
    f["ifsc"] = ifsc
    f["bank_change_notice"] = bool(re.search(r"(?i)(bank|account)\s+(details|account)?\s*(has|have)?\s*changed|new bank", text))
    f["currency"] = "INR"

    # who it's billed to, and where the supply is taxed
    buyer = None
    for m in re.finditer(r"(?:Customer\s*Name|Bill(?:ed)?\s*To(?:\s*/\s*Ship\s*to)?)\s*:?[ \t]*\n?[ \t]*([A-Z][A-Za-z.&' ]{2,60}?)(?:\s{2,}|\n|$)",
                         text, re.I | re.M):
        cand = m.group(1).strip()
        # skip column headers that sit where a name would be
        if not re.search(r"(?i)^(service provider|billed from|ship(ped)? (from|to)|seller|sold by)\b", cand):
            # two-column layouts glue the seller's name to the buyer's: "Flipkart India Private Limited Anukriti Sinha"
            tail = re.split(r"(?i)\b(?:private limited|pvt\.? ltd\.?|limited|llp)\s+", cand)
            buyer = tail[-1].strip() if len(tail) > 1 and tail[-1].strip() else cand
            break
    f["buyer_name"] = buyer
    f["buyer_registered"] = False if re.search(r"(?i)customer\s*type\s*:\s*unregistered|gstin\s*(?:number)?\s*:\s*unregistered", text) else None
    pos = _find(r"Place\s*of\s*Supply\s*:?\s*([A-Za-z][A-Za-z &]+?(?:\s*\(\s*\d{1,2}\s*\))?)(?:\s{2,}|\n|$)", text)
    f["place_of_supply"] = pos

    for k in KEY_FIELDS:
        conf[k] = base if f.get(k) else 0.0
    for k in repaired:
        conf[k] = 0.7
    conf["line_items"] = base if items else 0.0
    f["confidence"] = conf
    f["repaired_fields"] = repaired
    f["repairs"] = repairs
    return f


# ============================================================ LLM extractor
PROMPT = """You are an accounts-payable data extraction engine for an Indian company.
Read the attached vendor invoice and return ONLY a JSON object with exactly these keys:

{
 "document_title": string|null,          // e.g. "TAX INVOICE", "PROFORMA INVOICE", "BILL OF SUPPLY"
 "vendor_name": string|null,
 "vendor_gstin": string|null,             // 15 chars, the SELLER's GSTIN
 "buyer_name": string|null,               // the BILL-TO party's name, company or person
 "buyer_gstin": string|null,              // the BILL-TO party's GSTIN
 "buyer_registered": boolean|null,        // false if the buyer is shown as "Unregistered"
 "place_of_supply": string|null,          // state as printed, e.g. "Uttarakhand (05)"
 "invoice_number": string|null,           // exactly as printed
 "invoice_date": string|null,             // ISO yyyy-mm-dd. Numeric dates are dd/mm/yyyy.
 "po_number": string|null,                // only if printed. Never guess.
 "line_items": [{"description": string, "hsn": string|null, "qty": number, "unit_price": number, "amount": number}],
 "subtotal": number|null,                 // taxable value before GST
 "taxes": [{"type": "CGST"|"SGST"|"IGST"|"UTGST", "rate": number, "amount": number}],
 "total": number|null,                    // final amount payable
 "bank_account": string|null,
 "ifsc": string|null,
 "bank_change_notice": boolean,           // true if the invoice says bank details changed
 "confidence": {"vendor_name": 0-1, "vendor_gstin": 0-1, "invoice_number": 0-1, "invoice_date": 0-1,
                "po_number": 0-1, "subtotal": 0-1, "total": 0-1, "buyer_gstin": 0-1, "bank_account": 0-1,
                "line_items": 0-1}
}

Rules: if the file holds more than one invoice, read only the first one. Numbers as plain numbers without commas or currency symbols. If a field is not on the
document, use null and confidence 0. Do not infer or invent values. Confidence reflects how
clearly the value is printed (blurry scan, partially cut off, ambiguous = lower)."""


def _json_from(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    return json.loads(text[start:end + 1])


def _post(url, body, headers, name):
    """POST with one retry on rate limits / server errors, and a readable error message."""
    import time as _t
    for attempt in range(2):
        r = requests.post(url, json=body, headers=headers, timeout=60)
        if r.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            _t.sleep(2)
            continue
        break
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else str(msg)
        except Exception:
            msg = r.text[:200]
        raise RuntimeError(f"{name} API returned {r.status_code}: {msg}")
    return r


def _gemini(data: str):
    """Try the main model, then the fallbacks. Moves on when a model is overloaded, rate-limited,
    retired or slow, so one busy model doesn't push every invoice onto the parser."""
    import time as _t
    models = [config.GEMINI_MODEL] + [m for m in config.GEMINI_FALLBACKS if m != config.GEMINI_MODEL]
    base = {"temperature": 0, "responseMimeType": "application/json"}
    # Gemini 3 models think at "high" by default, which is slow and unnecessary for reading a
    # document. Ask for the lowest level; if a model rejects the setting, step back.
    gens = [{**base, "thinkingConfig": {"thinkingLevel": lvl}} for lvl in ("minimal", "low")] + [base]
    errors, started = [], _t.perf_counter()
    for model in models:
        if _t.perf_counter() - started > 30:
            break
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for gen in gens:
            body = {"contents": [{"parts": [{"text": PROMPT}, {"inline_data": {"mime_type": "application/pdf", "data": data}}]}],
                    "generationConfig": gen}
            try:
                r = requests.post(url, json=body, headers={"x-goog-api-key": config.GEMINI_API_KEY}, timeout=15)
            except requests.Timeout:
                errors.append(f"{model}: no reply in 15 s")
                break
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in parts if not p.get("thought")), model
            try:
                msg = r.json().get("error", {}).get("message", "")
            except Exception:
                msg = r.text[:120]
            if r.status_code == 400:
                continue  # setting not supported by this model; try the next thinking level
            errors.append(f"{model}: {r.status_code} {msg[:90]}")
            if r.status_code in (401, 403):
                raise RuntimeError("Gemini rejected the API key: " + msg[:150])
            break  # 404 / 429 / 5xx: try the next model
    raise RuntimeError("All Gemini models unavailable. " + " | ".join(errors))


def llm_extract(path, provider: str, scanned: bool = False) -> dict:
    data = base64.b64encode(open(path, "rb").read()).decode()
    if provider == "gemini":
        out, model = _gemini(data)
    elif provider == "anthropic":
        body = {"model": config.ANTHROPIC_MODEL, "max_tokens": 2500, "temperature": 0,
                "messages": [{"role": "user", "content": [
                    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": data}},
                    {"type": "text", "text": PROMPT}]}]}
        r = _post("https://api.anthropic.com/v1/messages", body, {
            "x-api-key": config.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}, "Claude")
        out = "".join(b.get("text", "") for b in r.json()["content"])
        model = config.ANTHROPIC_MODEL
    else:
        raise ValueError(provider)
    f = _json_from(out)
    # normalise whatever the model returned
    f["invoice_date"] = parse_date(f.get("invoice_date"))
    for k in ("subtotal", "total"):
        f[k] = parse_amount(f.get(k))
    f["line_items"] = [dict(description=str(li.get("description", "")).strip(), hsn=li.get("hsn"),
                            qty=parse_amount(li.get("qty")), unit_price=parse_amount(li.get("unit_price")),
                            amount=parse_amount(li.get("amount"))) for li in (f.get("line_items") or [])]
    f["taxes"] = [dict(type=str(t.get("type", "")).upper(), rate=parse_amount(t.get("rate")),
                       amount=parse_amount(t.get("amount"))) for t in (f.get("taxes") or [])]
    if f.get("po_number"):
        f["po_number"] = re.sub(r"(?i)^PO[\s\-]?", "PO-", str(f["po_number"]).strip())
    for k in ("vendor_gstin", "buyer_gstin"):
        if f.get(k):
            f[k], _ = repair_gstin(f[k], from_ocr=scanned)
    f["currency"] = "INR"
    f["confidence"] = {k: float(v) for k, v in (f.get("confidence") or {}).items() if isinstance(v, (int, float))}
    f["repaired_fields"] = []
    f["_model"] = model
    return f


def merge_passes(first: dict, second: dict) -> list:
    """Fill fields the first OCR pass missed from the second. Returns which fields were filled."""
    filled = []
    for k in KEY_FIELDS + ["document_title", "ifsc", "taxes"]:
        if first.get(k) in (None, "", []) and second.get(k) not in (None, "", []):
            first[k] = second[k]
            first["confidence"][k] = second["confidence"].get(k, 0.8)
            filled.append(k)
    if not first.get("line_items") and second.get("line_items"):
        first["line_items"] = second["line_items"]
        filled.append("line_items")
    for k, raw in (second.get("repairs") or {}).items():
        if k in filled:
            first.setdefault("repairs", {})[k] = raw
            first.setdefault("repaired_fields", []).append(k)
    return filled


def cross_check(primary: dict, secondary: dict) -> list:
    """Compare money-critical fields between the two extractors. Only flags real disagreements
    (both sides have a value and they differ)."""
    diffs = []
    for k in ("invoice_number", "total", "subtotal", "vendor_gstin", "bank_account", "po_number"):
        a, b = primary.get(k), secondary.get(k)
        if a in (None, "") or b in (None, ""):
            continue
        same = abs(float(a) - float(b)) <= 1 if isinstance(a, (int, float)) and isinstance(b, (int, float)) \
            else str(a).strip().upper() == str(b).strip().upper()
        if not same:
            diffs.append({"field": k, "llm": a, "parser": b})
    return diffs


def timestamp():
    return datetime.now().strftime("%H:%M:%S")
