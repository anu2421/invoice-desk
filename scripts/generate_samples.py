"""Generate reference data (vendor master, POs, invoice ledger) and sample invoice PDFs.

Run:  python scripts/generate_samples.py
Everything here is fictional. GSTINs are made with a valid check digit (except the
one deliberately broken for the unknown-vendor scenario).
"""
import csv
import io
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from app.gst import make_gstin  # noqa: E402

REF = ROOT / "data" / "reference"
SAMPLES = ROOT / "samples"
REF.mkdir(parents=True, exist_ok=True)
SAMPLES.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------- reference data
COMPANY = {
    "name": "Halden Home Retail Pvt Ltd",
    "gstin": make_gstin("06", "AAHCH4821K"),
    "state_code": "06",
    "address": ["Plot 14, Sector 44", "Gurugram, Haryana 122003"],
}

VENDORS = [
    dict(vendor_id="V101", name="Kaveri Office Supplies Pvt Ltd", gstin=make_gstin("33", "AABCK7712M"),
         state="Tamil Nadu", status="approved", bank_name="Indian Bank", bank_account="6123004455",
         ifsc="IDIB000T012", payment_terms_days=30, email="accounts@kaverioffice.example",
         address="22 Anna Salai, Chennai 600002"),
    dict(vendor_id="V102", name="Sharma Packaging Industries", gstin=make_gstin("27", "AADFS3390Q"),
         state="Maharashtra", status="approved", bank_name="Bank of Maharashtra", bank_account="60298811402",
         ifsc="MAHB0000921", payment_terms_days=45, email="billing@sharmapack.example",
         address="Gala 7, MIDC Bhosari, Pune 411026"),
    dict(vendor_id="V103", name="Nimbus Cloud Services LLP", gstin=make_gstin("29", "AAJFN5521C"),
         state="Karnataka", status="approved", bank_name="ICICI Bank", bank_account="002405017733",
         ifsc="ICIC0000024", payment_terms_days=30, email="ar@nimbuscloud.example",
         address="4th Floor, 80 Feet Road, Koramangala, Bengaluru 560034"),
    dict(vendor_id="V104", name="BrightSpark Electricals", gstin=make_gstin("06", "ABRPB8813L"),
         state="Haryana", status="approved", bank_name="Axis Bank", bank_account="917020045523781",
         ifsc="UTIB0000412", payment_terms_days=15, email="brightspark.elec@example.com",
         address="Shop 3, Sadar Bazaar, Gurugram 122001"),
    dict(vendor_id="V105", name="Delta Freight Carriers", gstin=make_gstin("07", "AACCD6604R"),
         state="Delhi", status="blocked", bank_name="Punjab National Bank", bank_account="0151002100034567",
         ifsc="PUNB0015100", payment_terms_days=30, email="accounts@deltafreight.example",
         address="Transport Nagar, New Delhi 110033"),
]
V = {v["vendor_id"]: v for v in VENDORS}

# PO lines: (description, hsn, qty, unit_price, uom)
POS = {
    "PO-24033": ("V102", "2026-09-10", "open", "Packaging stock - Q3 replenishment", [
        ("Corrugated box 5-ply 18x12x12 in", "4819", 2000, 38.00, "Nos"),
        ("BOPP packing tape 48mm x 65m", "3919", 500, 42.00, "Rolls")]),
    "PO-24051": ("V101", "2026-09-22", "open", "Stationery - October", [
        ("A4 copier paper 75 GSM (ream)", "4802", 200, 245.00, "Reams"),
        ("Ball pens blue (box of 50)", "9608", 40, 310.00, "Boxes"),
        ("Box files laminated", "4820", 150, 85.00, "Nos")]),
    "PO-24058": ("V101", "2026-09-30", "open", "Office chairs - new floor", [
        ("Ergonomic mesh office chair", "9401", 40, 6800.00, "Nos")]),
    "PO-24060": ("V103", "2026-06-25", "open", "Managed hosting retainer Jul-Sep 2026", [
        ("Managed cloud hosting - monthly retainer", "998315", 3, 200000.00, "Month")]),
    "PO-24063": ("V101", "2026-10-05", "open", "Printer consumables", [
        ("Toner cartridge 88A compatible", "8443", 60, 1450.00, "Nos")]),
    "PO-24066": ("V105", "2026-08-18", "open", "Inbound freight - August", [
        ("Full truck load Delhi-Gurugram", "996511", 12, 9500.00, "Trips")]),
    "PO-24068": ("V102", "2026-10-01", "open", "Protective packaging", [
        ("Bubble wrap roll 1m x 100m", "3920", 100, 650.00, "Rolls"),
        ("Stretch film 23 micron", "3920", 80, 410.00, "Rolls")]),
    "PO-24072": ("V104", "2026-09-05", "open", "Store lighting upgrade", [
        ("LED panel light 2x2 ft 40W", "9405", 120, 1150.00, "Nos"),
        ("Copper wire 2.5 sq mm 90m coil", "8544", 30, 2400.00, "Coils")]),
    "PO-24075": ("V104", "2026-10-08", "open", "Electrical panel - warehouse", [
        ("MCB 32A double pole", "8536", 200, 340.00, "Nos"),
        ("Distribution board 8-way", "8537", 25, 1900.00, "Nos")]),
    "PO-24082": ("V102", "2026-10-02", "open", "Corrugated boxes - Q4 blanket order", [
        ("Corrugated box 3-ply 12x10x8 in", "4819", 10000, 26.00, "Nos")]),
    "PO-24079": ("V104", "2026-10-12", "open", "Ventilation - stockroom", [
        ("Exhaust fan 300mm", "8414", 60, 1850.00, "Nos")]),
}

# Invoices already in the AP ledger before the demo starts
LEDGER_SEED = [
    ("V103", "NCS/2026/0711", "2026-07-31", "PO-24060", 200000.00, 36000.00, "posted"),
    ("V103", "NCS/2026/0802", "2026-08-31", "PO-24060", 200000.00, 36000.00, "posted"),
    ("V104", "BSE/2526/0042", "2026-09-18", "PO-24072", 138000.00, 24840.00, "posted"),
    ("V101", "KOS/26-27/1180", "2026-08-14", "PO-23990", 41250.00, 7425.00, "posted"),
    ("V102", "SPI/2026/0558", "2026-08-20", "PO-23921", 88400.00, 15912.00, "posted"),
    ("V105", "DFC/0817", "2026-08-29", "PO-24066", 57000.00, 6840.00, "posted"),
]


def write_reference():
    (REF / "company.json").write_text(json.dumps(COMPANY, indent=2))
    with open(REF / "vendors.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(VENDORS[0].keys()))
        w.writeheader()
        w.writerows(VENDORS)
    with open(REF / "purchase_orders.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["po_number", "vendor_id", "po_date", "status", "description", "total_value"])
        for po, (vid, d, st, desc, lines) in POS.items():
            w.writerow([po, vid, d, st, desc, f"{sum(q * p for _, _, q, p, _ in lines):.2f}"])
    with open(REF / "po_lines.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["po_number", "line_no", "description", "hsn", "qty", "unit_price", "uom"])
        for po, (_, _, _, _, lines) in POS.items():
            for i, (desc, hsn, q, p, uom) in enumerate(lines, 1):
                w.writerow([po, i, desc, hsn, q, f"{p:.2f}", uom])
    with open(REF / "invoice_ledger.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["vendor_id", "invoice_number", "invoice_date", "po_number", "subtotal", "tax", "total", "status"])
        for vid, no, d, po, sub, tax, st in LEDGER_SEED:
            w.writerow([vid, no, d, po, f"{sub:.2f}", f"{tax:.2f}", f"{sub + tax:.2f}", st])


# --------------------------------------------------------------------------- formatting
def inr(x: float) -> str:
    """Indian digit grouping: 1,23,456.00"""
    neg = x < 0
    s = f"{abs(x):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return ("-" if neg else "") + whole + "." + frac


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_date(iso: str, style: str) -> str:
    y, m, d = iso.split("-")
    if style == "A":
        return f"{d}-{MONTHS[int(m) - 1]}-{y}"
    if style == "B":
        return f"{int(d)} {MONTHS[int(m) - 1]} {y}"
    return f"{d}/{m}/{y}"


def words(n: int) -> str:
    ones = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split()
    tens = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()

    def two(x):
        return ones[x] if x < 20 else tens[x // 10] + ("-" + ones[x % 10] if x % 10 else "")

    def three(x):
        return (ones[x // 100] + " hundred" + (" " + two(x % 100) if x % 100 else "")) if x >= 100 else two(x)

    parts = []
    for div, name in ((10000000, "crore"), (100000, "lakh"), (1000, "thousand")):
        if n >= div:
            parts.append(three(n // div) + " " + name)
            n %= div
    if n:
        parts.append(three(n))
    return ("Rupees " + " ".join(parts) + " only").capitalize()


# --------------------------------------------------------------------------- invoice model
def build(inv):
    lines = [dict(desc=d, hsn=h, qty=q, rate=r, uom=u, amount=round(q * r, 2)) for d, h, q, r, u in inv["lines"]]
    sub = round(sum(l["amount"] for l in lines), 2)
    taxes = []
    for kind, rate in inv["tax"]:
        taxes.append((kind, rate, round(sub * rate / 100, 2)))
    total = round(sub + sum(t[2] for t in taxes), 2)
    return lines, sub, taxes, total


def vendor_block(inv):
    if inv.get("vendor_id"):
        v = dict(V[inv["vendor_id"]])
    else:
        v = dict(inv["vendor_override"])
    v.update(inv.get("vendor_patch", {}))
    return v


# --------------------------------------------------------------------------- layouts
def layout_a(c, inv):
    """Classic: vendor top-left, document block top-right, ruled table."""
    v = vendor_block(inv)
    lines, sub, taxes, total = build(inv)
    W, H = A4
    c.setFont("Helvetica-Bold", 17)
    c.drawString(40, H - 60, v["name"])
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 76, v["address"])
    c.drawString(40, H - 89, f"GSTIN: {v['gstin']}    Email: {v['email']}")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(W - 40, H - 118, inv.get("title", "TAX INVOICE"))
    c.setFont("Helvetica", 9.5)
    y = H - 136
    if inv.get("number"):
        c.drawRightString(W - 40, y, f"Invoice No: {inv['number']}")
        y -= 14
    if inv.get("date"):
        c.drawRightString(W - 40, y, f"Invoice Date: {fmt_date(inv['date'], 'A')}")
        y -= 14
    if inv.get("po"):
        c.drawRightString(W - 40, y, f"PO Ref: {inv['po']}")
        y -= 14
    c.setStrokeColor(colors.HexColor("#999999"))
    c.line(40, H - 180, W - 40, H - 180)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawString(40, H - 198, "Bill To:")
    c.setFont("Helvetica", 9.5)
    c.drawString(40, H - 212, COMPANY["name"])
    c.drawString(40, H - 225, ", ".join(COMPANY["address"]))
    c.drawString(40, H - 238, f"GSTIN: {inv.get('bill_to_gstin', COMPANY['gstin'])}")
    c.drawString(330, H - 212, f"Place of supply: {inv.get('pos_text', 'Haryana (06)')}")

    y = H - 275
    c.setFillColor(colors.HexColor("#E8E8E8"))
    c.rect(40, y - 5, W - 80, 18, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 9)
    cols = [44, 64, 330, 380, 450, W - 44]
    for x, t in zip(cols[:5], ["#", "Description", "HSN", "Qty", "Rate"]):
        c.drawString(x, y, t)
    c.drawRightString(cols[5], y, "Amount")
    c.setFont("Helvetica", 9)
    y -= 22
    for i, l in enumerate(lines, 1):
        c.drawString(cols[0], y, str(i))
        c.drawString(cols[1], y, l["desc"])
        c.drawString(cols[2], y, l["hsn"])
        c.drawString(cols[3], y, f"{l['qty']}")
        c.drawString(cols[4], y, inr(l["rate"]))
        c.drawRightString(cols[5], y, inr(l["amount"]))
        y -= 18
    c.line(40, y + 6, W - 40, y + 6)
    y -= 14
    c.setFont("Helvetica", 9.5)
    c.drawString(360, y, "Taxable Value")
    c.drawRightString(W - 44, y, inr(sub))
    for kind, rate, amt in taxes:
        y -= 15
        c.drawString(360, y, f"{kind} @ {rate:g}%")
        c.drawRightString(W - 44, y, inr(amt))
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawString(360, y, "Total")
    c.drawRightString(W - 44, y, inr(total))
    y -= 22
    c.setFont("Helvetica-Oblique", 8.5)
    c.drawString(40, y, words(int(round(total))))
    y -= 40
    c.setFont("Helvetica-Bold", 9)
    c.drawString(40, y, "Bank details")
    c.setFont("Helvetica", 9)
    c.drawString(40, y - 14, f"{v['bank_name']}    A/c No: {v['bank_account']}    IFSC: {v['ifsc']}")
    if inv.get("note"):
        c.drawString(40, y - 34, inv["note"])
    c.setFont("Helvetica", 7.5)
    c.setFillColor(colors.HexColor("#666666"))
    c.drawString(40, 50, f"Payment terms: Net {v.get('payment_terms_days', 30)} days. This is a computer generated invoice.")


def layout_b(c, inv):
    """Modern: coloured header band, two-column detail grid, zebra table."""
    v = vendor_block(inv)
    lines, sub, taxes, total = build(inv)
    W, H = A4
    band = colors.HexColor(inv.get("band", "#23395B"))
    c.setFillColor(band)
    c.rect(0, H - 95, W, 95, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 19)
    c.drawString(36, H - 48, v["name"])
    c.setFont("Helvetica", 9)
    c.drawString(36, H - 66, v["address"])
    c.drawString(36, H - 80, f"GSTIN {v['gstin']}")
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 22)
    c.setFillColor(band)
    c.drawString(36, H - 140, "Invoice")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9.5)
    left = [f"Invoice #: {inv['number']}", f"Date: {fmt_date(inv['date'], 'B')}"]
    if inv.get("po"):
        left.append(f"Purchase Order: {inv['po']}")
    for i, t in enumerate(left):
        c.drawString(36, H - 165 - i * 15, t)
    right = ["Billed To", COMPANY["name"], COMPANY["address"][1], f"GSTIN {inv.get('bill_to_gstin', COMPANY['gstin'])}"]
    for i, t in enumerate(right):
        c.setFont("Helvetica-Bold" if i == 0 else "Helvetica", 9.5)
        c.drawString(330, H - 158 - i * 14, t)

    y = H - 250
    c.setFont("Helvetica-Bold", 9)
    c.setFillColor(band)
    c.rect(36, y - 6, W - 72, 20, stroke=0, fill=1)
    c.setFillColor(colors.white)
    cols = [42, 330, 390, 455, W - 42]
    for x, t in zip(cols[:4], ["Description", "SAC/HSN", "Qty", "Rate"]):
        c.drawString(x, y, t)
    c.drawRightString(cols[4], y, "Amount")
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 9)
    y -= 24
    for i, l in enumerate(lines):
        if i % 2:
            c.setFillColor(colors.HexColor("#F1F3F7"))
            c.rect(36, y - 6, W - 72, 20, stroke=0, fill=1)
            c.setFillColor(colors.black)
        c.drawString(cols[0], y, l["desc"])
        c.drawString(cols[1], y, l["hsn"])
        c.drawString(cols[2], y, f"{l['qty']}")
        c.drawString(cols[3], y, inr(l["rate"]))
        c.drawRightString(cols[4], y, inr(l["amount"]))
        y -= 20
    y -= 16
    c.drawString(370, y, "Sub Total")
    c.drawRightString(W - 42, y, inr(sub))
    for kind, rate, amt in taxes:
        y -= 15
        c.drawString(370, y, f"{kind} {rate:g}%")
        c.drawRightString(W - 42, y, inr(amt))
    y -= 22
    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(370, y, "Amount Payable")
    c.drawRightString(W - 42, y, inr(total))
    c.setFont("Helvetica", 8.5)
    c.drawString(36, 110, "Remit to")
    c.drawString(36, 96, f"{v['bank_name']}, Account Number: {v['bank_account']}, IFSC {v['ifsc']}")
    c.setFillColor(colors.HexColor("#777777"))
    c.drawString(36, 60, "Thank you for your business.")


def layout_c(c, inv):
    """Plain: monospaced, dot-matrix style, everything left-aligned."""
    v = vendor_block(inv)
    lines, sub, taxes, total = build(inv)
    W, H = A4
    c.setFont("Courier-Bold", 14)
    c.drawString(40, H - 55, v["name"].upper())
    c.setFont("Courier", 9)
    c.drawString(40, H - 70, v["address"])
    c.drawString(40, H - 82, f"GSTIN : {v['gstin']}   PH : 98110 44120")
    c.drawString(40, H - 100, "-" * 92)
    c.setFont("Courier-Bold", 11)
    c.drawString(250, H - 116, "TAX INVOICE")
    c.setFont("Courier", 9)
    rows = [f"BILL NO.       : {inv['number']}", f"DATE           : {fmt_date(inv['date'], 'C')}"]
    if inv.get("po"):
        rows.append(f"YOUR ORDER NO. : {inv['po']}")
    for i, t in enumerate(rows):
        c.drawString(40, H - 136 - i * 12, t)
    c.drawString(40, H - 180, f"BUYER : {COMPANY['name'].upper()}")
    c.drawString(40, H - 192, f"        {COMPANY['address'][0]}, {COMPANY['address'][1]}")
    c.drawString(40, H - 204, f"BUYER GSTIN : {inv.get('bill_to_gstin', COMPANY['gstin'])}")
    c.drawString(40, H - 220, "-" * 92)
    y = H - 234
    c.drawString(40, y, "SN  PARTICULARS                         HSN       QTY       RATE        AMOUNT")
    c.drawString(40, y - 10, "-" * 92)
    y -= 24
    for i, l in enumerate(lines, 1):
        c.drawString(40, y, f"{i:<3} {l['desc']:<35} {l['hsn']:<8} {l['qty']:>5} {inr(l['rate']):>10} {inr(l['amount']):>13}")
        y -= 12
    c.drawString(40, y, "-" * 92)
    y -= 14
    c.drawString(300, y, f"TAXABLE AMOUNT   {inr(sub):>15}")
    for kind, rate, amt in taxes:
        y -= 12
        c.drawString(300, y, f"{kind} {rate:g}%{'':<9} {inr(amt):>15}"[:33] if False else f"{kind} {rate:g}%".ljust(17) + f"{inr(amt):>15}")
    y -= 14
    c.setFont("Courier-Bold", 9)
    c.drawString(300, y, f"NET AMOUNT       {inr(total):>15}")
    c.setFont("Courier", 9)
    y -= 16
    c.drawString(40, y, words(int(round(total))).upper())
    y -= 30
    if inv.get("note"):
        c.setFont("Courier-Bold", 9)
        for t in inv["note"].split("\n"):
            c.drawString(40, y, t)
            y -= 12
        c.setFont("Courier", 9)
        y -= 4
    c.drawString(40, y, f"BANK : {v['bank_name'].upper()}  A/C NO {v['bank_account']}  IFSC {v['ifsc']}")
    c.drawString(40, 70, "E.&O.E.                                                   FOR " + v["name"].upper())
    c.drawString(40, 46, "                                                          AUTHORISED SIGNATORY")


LAYOUTS = {"A": layout_a, "B": layout_b, "C": layout_c}


def render(inv) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f"Invoice {inv.get('number') or ''}")
    LAYOUTS[inv["layout"]](c, inv)
    c.showPage()
    c.save()
    return buf.getvalue()


def scan(pdf_bytes: bytes) -> bytes:
    """Rasterise, tilt and add noise so it behaves like a phone/office scan (no text layer)."""
    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter

    pdf = pdfium.PdfDocument(pdf_bytes)
    img = pdf[0].render(scale=200 / 72).to_pil().convert("L")
    rnd = random.Random(7)
    img = img.rotate(0.9, resample=Image.BICUBIC, expand=False, fillcolor=255)
    px = img.load()
    w, h = img.size
    for _ in range(int(w * h * 0.004)):
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice([120, 170, 200])
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    img = img.point(lambda p: min(255, int(p * 0.93 + 14)))  # greyish paper
    out = io.BytesIO()
    img.save(out, "PDF", resolution=200)
    return out.getvalue()


# --------------------------------------------------------------------------- scenarios
IGST = [("IGST", 18)]
CGST_SGST = [("CGST", 9), ("SGST", 9)]
P = {k: v[4] for k, v in POS.items()}

INVOICES = [
    dict(file="01_kaveri_stationery_clean.pdf", layout="A", vendor_id="V101", number="KOS/26-27/1307",
         date="2026-10-14", po="PO-24051", lines=P["PO-24051"], tax=IGST, pos_text="Haryana (06)",
         scenario="Happy path", expect="approved",
         story="Clean text PDF. Every line matches PO-24051 exactly, inter-state IGST is correct."),
    dict(file="02_nimbus_hosting_sep.pdf", layout="B", vendor_id="V103", number="NCS/2026/0930",
         date="2026-09-30", po="PO-24060", band="#3B2C5A",
         lines=[("Managed cloud hosting - monthly retainer (Sep 2026)", "998315", 1, 200000.00, "Month")],
         tax=IGST, scenario="Split PO - third of three", expect="approved",
         story="PO-24060 covers 3 months. Jul and Aug are already billed. Sep uses the last of the PO, so it fits."),
    dict(file="03_nimbus_hosting_advance_overbill.pdf", layout="B", vendor_id="V103", number="NCS/2026/1031",
         date="2026-10-31", po="PO-24060", band="#3B2C5A",
         lines=[("Managed cloud hosting - Oct-Nov 2026 (advance)", "998315", 2, 200000.00, "Month")],
         tax=IGST, scenario="Split PO - over-billing", expect="review",
         story="Looks fine on its own, but the PO was for 3 months. Cumulative billing goes past the PO value."),
    dict(file="04_brightspark_resubmitted.pdf", layout="C", vendor_id="V104", number="BSE-2526-42",
         date="2026-10-02", po="PO-24072",
         lines=[("LED panel light 2x2 ft 40W", "9405", 120, 1150.00, "Nos")], tax=CGST_SGST,
         scenario="Disguised duplicate", expect="review",
         story="Same invoice as BSE/2526/0042 (already paid) with the number reformatted and a new date."),
    dict(file="05_sharma_scanned_no_po.pdf", layout="A", vendor_id="V102", number="SPI/2026/0647",
         date="2026-10-09", po=None, lines=P["PO-24033"], tax=IGST, scanned=True,
         scenario="Scanned, no PO reference", expect="review",
         story="Image-only scan with no PO number. The process has to OCR it and infer which open PO it belongs to."),
    dict(file="06_kaveri_chairs_price_creep.pdf", layout="A", vendor_id="V101", number="KOS/26-27/1342",
         date="2026-10-21", po="PO-24058",
         lines=[("Ergonomic mesh office chair", "9401", 35, 7750.00, "Nos")], tax=IGST,
         scenario="Total matches, unit price doesn't", expect="review",
         story="Total is within 0.3% of the PO, but 5 fewer chairs at a 14% higher unit price."),
    dict(file="07_brightspark_new_bank_details.pdf", layout="C", vendor_id="V104", number="BSE/2526/0057",
         date="2026-10-16", po="PO-24075", lines=P["PO-24075"], tax=CGST_SGST,
         vendor_patch=dict(bank_name="HDFC Bank", bank_account="50200087765412", ifsc="HDFC0001234"),
         note="NOTE: OUR BANK ACCOUNT HAS CHANGED. KINDLY UPDATE YOUR RECORDS\nAND REMIT THIS AND ALL FUTURE PAYMENTS TO THE ACCOUNT BELOW.",
         scenario="Bank details changed", expect="review",
         story="Everything matches except the bank account, which differs from the vendor master. Classic payment-fraud pattern."),
    dict(file="08_quickfix_unknown_vendor.pdf", layout="B", vendor_id=None, number="QFS/1029",
         date="2026-10-29", po="PO-24080", band="#1F5F5B",
         vendor_override=dict(name="QuickFix Facility Services", address="Office 12, Andheri East, Mumbai 400069",
                              gstin="27AAKFQ1209E1ZX", bank_name="Yes Bank", bank_account="012345678901",
                              ifsc="YESB0000123", email="quickfix.billing@example.com", payment_terms_days=7),
         lines=[("Deep cleaning - warehouse (one-time)", "998533", 1, 85000.00, "Job")], tax=IGST,
         scenario="Unknown vendor", expect="rejected",
         story="Vendor isn't in the master, the GSTIN check digit is wrong and the PO doesn't exist."),
    dict(file="09_kaveri_toner_proforma.pdf", layout="A", vendor_id="V101", number=None, date=None,
         po="PO-24063", title="PROFORMA INVOICE", lines=P["PO-24063"], tax=IGST,
         scenario="Missing invoice number and date", expect="rejected",
         story="A proforma sent instead of a tax invoice: no invoice number, no date. It can't be booked."),
    dict(file="10_brightspark_wrong_tax_type.pdf", layout="C", vendor_id="V104", number="BSE/2526/0061",
         date="2026-10-23", po="PO-24079", lines=P["PO-24079"], tax=IGST,
         scenario="Wrong GST type", expect="rejected",
         story="Vendor and buyer are both in Haryana, so it should be CGST + SGST. IGST was charged. Total is right, tax head is wrong."),
    dict(file="11_sharma_bubblewrap_within_tolerance.pdf", layout="B", vendor_id="V102", number="SPI/2026/0661",
         date="2026-10-24", po="PO-24068", band="#7A3B14",
         lines=[("Bubble wrap roll 1m x 100m", "3920", 100, 650.00, "Rolls"),
                ("Stretch film 23 micron", "3920", 80, 414.00, "Rolls")], tax=IGST,
         scenario="Small variance within tolerance", expect="approved",
         story="Stretch film is 1% above the PO rate. That's inside the 2% price tolerance, so it auto-approves with a note."),
    # ---- learning from reviewers: run these one at a time, approving 12 and 13 in between
    dict(file="12_sharma_boxes_oct_surcharge.pdf", layout="B", vendor_id="V102", number="SPI/2026/0672",
         date="2026-10-06", po="PO-24082", band="#7A3B14",
         lines=[("Corrugated box 3-ply 12x10x8 in", "4819", 3000, 26.90, "Nos")], tax=IGST,
         scenario="Learning loop 1: price +3.5%", expect="review", batch=False, group="learning",
         story="Box price is 3.5% over PO (a freight surcharge procurement agreed verbally). Held. Approve it with a note."),
    dict(file="13_sharma_boxes_nov_surcharge.pdf", layout="B", vendor_id="V102", number="SPI/2026/0689",
         date="2026-10-20", po="PO-24082", band="#7A3B14",
         lines=[("Corrugated box 3-ply 12x10x8 in", "4819", 2500, 26.85, "Nos")], tax=IGST,
         scenario="Learning loop 2: same surcharge again", expect="review", batch=False, group="learning",
         story="Same surcharge, held again. Approve it too, then check the Runs page: a rule change is suggested."),
    dict(file="14_sharma_boxes_dec_surcharge.pdf", layout="B", vendor_id="V102", number="SPI/2026/0701",
         date="2026-11-03", po="PO-24082", band="#7A3B14",
         lines=[("Corrugated box 3-ply 12x10x8 in", "4819", 3200, 26.95, "Nos")], tax=IGST,
         scenario="Learning loop 3: after applying the rule", expect="review", batch=False, group="learning",
         story="Once the suggested tolerance is applied, this one goes straight through. Without it, it's held like the others."),
]


def main():
    write_reference()
    manifest = []
    for inv in INVOICES:
        pdf = render(inv)
        if inv.get("scanned"):
            pdf = scan(pdf)
        (SAMPLES / inv["file"]).write_bytes(pdf)
        manifest.append({**{k: inv[k] for k in ("file", "scenario", "expect", "story")},
                         "batch": inv.get("batch", True), "group": inv.get("group", "core")})
        print(f"wrote {inv['file']}")
    (SAMPLES / "manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
