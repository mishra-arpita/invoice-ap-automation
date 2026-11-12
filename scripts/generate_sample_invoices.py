"""
Generates the realistic test invoices in sample_invoices/.

Three vendors use three DIFFERENT layouts (different labels, tax shown separately vs
included, PO quoted vs missing) - just like real AP inboxes. One invoice is a scanned
image with no text layer at all.

Run:  python scripts/generate_sample_invoices.py
"""
import io
import random
import sys
from pathlib import Path

from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "sample_invoices"
BILL_TO = ["Orbit Retail Pvt Ltd", "Accounts Payable", "42 Lake View Road", "Pune 411001, India"]


def money(x):
    return f"{x:,.2f}"


def line_total(items):
    return round(sum(q * p for _, q, p in items), 2)


# ======================================================================= Layout A
# Classic layout: coloured header band, "Invoice No / Invoice Date / PO Number", tax separate.
def layout_a(path, vendor, address, colour, meta, items, tax_rate, banner=None, footer=""):
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(False)
    pdf.set_fill_color(*colour)
    pdf.rect(0, 0, 210, 32, style="F")
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_xy(14, 10)
    pdf.cell(120, 10, vendor)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(140, 10)
    pdf.cell(56, 10, "INVOICE", align="R")

    pdf.set_text_color(90, 90, 90)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(14, 36)
    for line in address:
        pdf.cell(0, 4.5, line, new_x="LMARGIN", new_y="NEXT")
        pdf.set_x(14)

    if banner:
        pdf.set_text_color(200, 30, 30)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_xy(14, 52)
        pdf.cell(0, 6, banner)

    # Bill-to (left) and invoice meta (right)
    pdf.set_text_color(30, 30, 30)
    y = 62
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(14, y)
    pdf.cell(80, 6, "Bill To:")
    pdf.set_font("Helvetica", "", 10)
    for i, line in enumerate(BILL_TO):
        pdf.set_xy(14, y + 6 + i * 5)
        pdf.cell(80, 5, line)
    for i, (label, value) in enumerate(meta):
        pdf.set_xy(118, y + i * 6)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(36, 6, f"{label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(42, 6, value, align="R")

    # Line items
    y = 100
    pdf.set_fill_color(240, 242, 247)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(14, y)
    for text, w, align in [("Description", 96, "L"), ("Qty", 18, "R"), ("Unit Price", 32, "R"), ("Amount", 36, "R")]:
        pdf.cell(w, 8, text, fill=True, align=align)
    pdf.set_font("Helvetica", "", 10)
    for i, (desc, qty, price) in enumerate(items):
        pdf.set_xy(14, y + 10 + i * 8)
        pdf.cell(96, 7, desc)
        pdf.cell(18, 7, str(qty), align="R")
        pdf.cell(32, 7, f"${money(price)}", align="R")
        pdf.cell(36, 7, f"${money(qty * price)}", align="R")

    subtotal = line_total(items)
    tax = round(subtotal * tax_rate, 2)
    y = y + 16 + len(items) * 8
    pdf.line(110, y - 2, 196, y - 2)
    for i, (label, value, bold) in enumerate([
        ("Subtotal", subtotal, False),
        (f"Sales Tax ({tax_rate * 100:g}%)", tax, False),
        ("Total Due", subtotal + tax, True),
    ]):
        pdf.set_font("Helvetica", "B" if bold else "", 11 if bold else 10)
        pdf.set_xy(110, y + i * 7)
        pdf.cell(50, 7, f"{label}:")
        pdf.cell(36, 7, f"${money(value)}", align="R")

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(14, 270)
    pdf.multi_cell(182, 4, footer or "Please quote the invoice number with your payment. Thank you for your business.")
    pdf.output(str(path))


# ======================================================================= Layout B
# IT-services style: "TAX INVOICE", "Bill #", "Your Ref" for the PO, "Amount Due (USD)".
def totals(items, tax_rate):
    """(subtotal, tax, total) for invoices that show tax separately."""
    subtotal = line_total(items)
    tax = round(subtotal * tax_rate, 2)
    return subtotal, tax, subtotal + tax


def layout_b(path, vendor, meta, items, tax_rate):
    subtotal, tax, total = totals(items, tax_rate)
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(False)
    pdf.set_text_color(124, 58, 237)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_xy(14, 14)
    pdf.cell(0, 6, "TAX INVOICE")
    pdf.set_text_color(20, 20, 20)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(14, 22)
    pdf.cell(0, 10, vendor)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.set_xy(14, 33)
    pdf.cell(0, 5, "880 Tech Park Avenue, Austin, TX 78701  |  accounts@brightpath-it.example")

    pdf.set_draw_color(124, 58, 237)
    pdf.set_line_width(0.6)
    pdf.rect(14, 44, 182, 26)
    pdf.set_text_color(20, 20, 20)
    for i, (label, value) in enumerate(meta):
        col, row = i % 2, i // 2
        pdf.set_xy(18 + col * 92, 47 + row * 10)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(30, 6, f"{label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(50, 6, value)

    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(14, 76)
    pdf.cell(0, 6, "Billed to: Orbit Retail Pvt Ltd, 42 Lake View Road, Pune 411001")

    y = 88
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(14, y)
    for text, w, align in [("Item", 100, "L"), ("Hours/Qty", 22, "R"), ("Rate", 28, "R"), ("Line Total", 32, "R")]:
        pdf.cell(w, 8, text, border="B", align=align)
    pdf.set_font("Helvetica", "", 10)
    for i, (desc, qty, price) in enumerate(items):
        pdf.set_xy(14, y + 10 + i * 8)
        pdf.cell(100, 7, desc)
        pdf.cell(22, 7, str(qty), align="R")
        pdf.cell(28, 7, money(price), align="R")
        pdf.cell(32, 7, money(qty * price), align="R")

    y = y + 18 + len(items) * 8
    for i, (label, value, bold) in enumerate([
        ("Sub-total", subtotal, False), (f"Tax @ {tax_rate * 100:g}%", tax, False), ("Amount Due (USD)", total, True),
    ]):
        pdf.set_font("Helvetica", "B" if bold else "", 11 if bold else 10)
        pdf.set_xy(112, y + i * 7)
        pdf.cell(52, 7, f"{label}:")
        pdf.cell(32, 7, money(value), align="R")

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(14, 272)
    pdf.cell(0, 4, "Payment by ACH within 15 days. Late payments attract 1.5% monthly interest.")
    pdf.output(str(path))


# ======================================================================= Layout C
# Facility-services style: "Invoice Number", "Date of Issue", "Purchase Order", tax INCLUDED, bundled lines.
def layout_c(path, vendor, meta, items):
    total = line_total(items)
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_auto_page_break(False)
    pdf.set_fill_color(22, 128, 74)
    pdf.rect(0, 0, 8, 297, style="F")
    pdf.set_text_color(22, 128, 74)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(16, 14)
    pdf.cell(0, 10, vendor)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(100, 100, 100)
    pdf.set_xy(16, 25)
    pdf.cell(0, 5, "Unit 7, Riverside Business Park, Pune 411014  |  GST Reg: 27AAGCG4521K1ZQ")

    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_xy(16, 38)
    pdf.cell(0, 8, "Invoice")
    for i, (label, value) in enumerate(meta):
        pdf.set_xy(16, 48 + i * 6)
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(40, 6, f"{label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(60, 6, value)
    pdf.set_xy(120, 48)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Customer")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_xy(120, 54)
    pdf.cell(0, 6, "Orbit Retail Pvt Ltd")

    y = 80
    pdf.set_fill_color(232, 245, 238)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_xy(16, y)
    for text, w, align in [("Description", 104, "L"), ("Qty", 16, "R"), ("Price", 30, "R"), ("Amount", 30, "R")]:
        pdf.cell(w, 8, text, fill=True, align=align)
    pdf.set_font("Helvetica", "", 10)
    for i, (desc, qty, price) in enumerate(items):
        pdf.set_xy(16, y + 10 + i * 8)
        pdf.cell(104, 7, desc)
        pdf.cell(16, 7, str(qty), align="R")
        pdf.cell(30, 7, money(price), align="R")
        pdf.cell(30, 7, money(qty * price), align="R")

    y = y + 18 + len(items) * 8
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_xy(110, y)
    pdf.cell(50, 7, "Total (incl. tax):")
    pdf.cell(36, 7, f"USD {money(total)}", align="R")
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(120, 120, 120)
    pdf.set_xy(16, 272)
    pdf.cell(0, 4, "All prices include applicable taxes. Thank you for choosing GreenLeaf.")
    pdf.output(str(path))


# ======================================================================= Scanned (image only)
def scanned(path, vendor, meta, items, tax_rate):
    """Draw the invoice onto an image (like a phone scan) and put ONLY the image in the PDF."""
    subtotal, tax, total = totals(items, tax_rate)
    img = Image.new("L", (1240, 1754), 246)
    draw = ImageDraw.Draw(img)
    big, mid, small = (ImageFont.load_default(size=s) for s in (54, 30, 26))
    draw.text((90, 90), "TAX INVOICE", font=mid, fill=60)
    draw.text((90, 140), vendor, font=big, fill=20)
    draw.text((90, 215), "880 Tech Park Avenue, Austin, TX 78701", font=small, fill=90)
    y = 300
    for label, value in meta:
        draw.text((90, y), f"{label}: {value}", font=mid, fill=25)
        y += 48
    draw.text((90, y + 20), "Billed to: Orbit Retail Pvt Ltd, Pune", font=mid, fill=25)
    y += 110
    draw.line((90, y, 1150, y), fill=40, width=3)
    draw.text((90, y + 15), "Item", font=mid, fill=25)
    draw.text((760, y + 15), "Qty", font=mid, fill=25)
    draw.text((960, y + 15), "Line Total", font=mid, fill=25)
    y += 70
    for desc, qty, price in items:
        draw.text((90, y), desc, font=mid, fill=25)
        draw.text((770, y), str(qty), font=mid, fill=25)
        draw.text((960, y), money(qty * price), font=mid, fill=25)
        y += 50
    y += 40
    for label, value in [("Sub-total", subtotal), (f"Tax @ {tax_rate * 100:g}%", tax), ("Amount Due (USD)", total)]:
        draw.text((620, y), f"{label}: {money(value)}", font=mid, fill=20)
        y += 50

    # make it look scanned: slight rotation, speckles, blur
    rng = random.Random(7)
    img = img.rotate(0.8, fillcolor=235, resample=Image.BICUBIC)
    px = img.load()
    for _ in range(9000):
        x, yy = rng.randrange(img.width), rng.randrange(img.height)
        px[x, yy] = rng.randrange(150, 230)
    img = img.filter(ImageFilter.GaussianBlur(0.7))

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=70)
    buf.seek(0)
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.image(buf, x=0, y=0, w=210, h=297)
    pdf.output(str(path))


# ======================================================================= the sample set
def main():
    OUT.mkdir(exist_ok=True)
    acme = ("Acme Office Supplies Inc.", ["12 Market Street, Springfield, IL 62704", "billing@acme-office.example  |  +1 217 555 0142"], (47, 91, 234))

    # 01 Happy path: explicit PO, amounts match exactly
    layout_a(OUT / "01_happy_path_acme.pdf", *acme,
             meta=[("Invoice No", "INV-2026-0107"), ("Invoice Date", "Nov 02, 2026"),
                   ("PO Number", "PO-1001"), ("Payment Terms", "Net 30")],
             items=[("A4 copy paper (box of 10 reams)", 20, 45.00), ("Ballpoint pens (pack of 50)", 10, 18.50),
                    ("Ergonomic office chair", 4, 289.00)],
             tax_rate=0.08)

    # 02 Close but not exact: different layout, total 1.5% above PO (inside 2% tolerance)
    layout_b(OUT / "02_close_match_brightpath.pdf", "BrightPath IT Services LLC",
             meta=[("Bill #", "BP-7815"), ("Invoice Date", "2026-11-03"), ("Your Ref", "PO-1002"), ("Due Date", "2026-11-18")],
             items=[("Laptop imaging and setup", 20, 120.00), ("On-site IT support (hours)", 20, 95.00),
                    ("Network switch configuration", 1, 400.00)],
             tax_rate=0.08)

    # 03 Edge case: no PO reference on the invoice -> must be inferred
    layout_a(OUT / "03_no_po_reference_nimbus.pdf", "Nimbus Cloud Hosting Ltd",
             ["Suite 400, 1 Cloud Way, Seattle, WA 98101", "invoices@nimbus-cloud.example"], (14, 116, 144),
             meta=[("Invoice No", "NCH-55120"), ("Invoice Date", "November 4, 2026"),
                   ("Account", "ORB-2231"), ("Payment Terms", "Net 30")],
             items=[("Annual hosting plan - Business tier", 1, 3000.00), ("Managed backups (monthly)", 12, 25.00)],
             tax_rate=0.09,
             footer="Service period: Nov 2026 to Oct 2027. Thank you for hosting with Nimbus.")

    # 04 Edge case: second invoice on a PO that is already 60% billed -> over-billing
    layout_c(OUT / "04_split_po_overbilled_greenleaf.pdf", "GreenLeaf Facility Management",
             meta=[("Invoice Number", "GL-2026-1102"), ("Date of Issue", "02-Nov-2026"), ("Purchase Order", "PO-1003")],
             items=[("Monthly facility services - November 2026 (bundled)", 1, 5000.00)])

    # 05 Edge case: same invoice re-sent with different formatting of the invoice number
    layout_a(OUT / "05_duplicate_resubmitted_acme.pdf", *acme,
             meta=[("Invoice No", "INV 2026/0042"), ("Invoice Date", "Oct 14, 2026"),
                   ("PO Number", "PO-1005"), ("Payment Terms", "Net 30")],
             items=[("Toner cartridge - black (HP 58A)", 8, 95.00), ("A4 copy paper (box of 10 reams)", 7, 45.00)],
             tax_rate=0.08, banner="PAYMENT REMINDER - copy of invoice re-sent")

    # 06 Edge case: scanned invoice, no text layer
    scanned(OUT / "06_scanned_brightpath.pdf", "BrightPath IT Services LLC",
            meta=[("Bill #", "BP-7840"), ("Invoice Date", "2026-11-05"), ("Your Ref", "PO-1006")],
            items=[("Help-desk support retainer - November", 1, 1150.00)], tax_rate=0.08)

    # 07 Unknown vendor quoting another vendor's PO
    layout_a(OUT / "07_unknown_vendor.pdf", "Quantum Print Solutions",
             ["77 Industrial Estate, Mumbai 400072", "sales@quantumprint.example"], (190, 70, 30),
             meta=[("Invoice No", "QPS-3391"), ("Invoice Date", "Nov 06, 2026"),
                   ("PO Number", "PO-1001"), ("Payment Terms", "Immediate")],
             items=[("Product brochures (A4, 500 pcs)", 2, 340.00), ("Roll-up banners", 3, 120.00)],
             tax_rate=0.08)

    # 08 Missing critical information: no invoice number, no date
    layout_c(OUT / "08_missing_fields_greenleaf.pdf", "GreenLeaf Facility Management",
             meta=[("Purchase Order", "PO-1008")],
             items=[("Deep cleaning - warehouse (one-off)", 1, 850.00)])

    for p in sorted(OUT.glob("*.pdf")):
        print("created", p.relative_to(OUT.parent))


if __name__ == "__main__":
    sys.exit(main())
