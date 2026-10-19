"""
A replica of a real ICUMS Bill of Entry (the "Sheet4" example of Mantrac
Ghana's Duty Payment cheque-request workbook: BOE 40726534505 / 00, BL/AWB
J552493, total 653,670.54) as a PDF with a text layer.

The real form is boxed: a box's label sits at its top-left and its value
below it or to its right, and the text layer is NOT in reading order (the
labels come first, the values after). So a label is not followed by its
value in the extracted text — which is exactly why the line-by-line rules
failed on the real document (only "user_reference" and a wrong "bl_awb"
were found). The positions below are the example's, scaled to A4.

    icums_boe_pdf()                         the example as printed
    icums_boe_pdf(bl="X1", total=None, ...) a variant (see the arguments)
"""

import fitz

SCALE = 595.0 / 647.0          # the example image is 647 px wide; A4 is 595 pt

# The example's B ACCOUNTING DETAILS: (tax, code, exempted, payable).
TAX_LINES = [
    ("Import Duty", "01", "0.00", "163,253.97"),
    ("Import VAT", "02", "0.00", "304,446.44"),
    ("Processing Fee", "05", "0.00", "8,315.97"),
    ("ECOWAS Levy", "06", "0.00", "9,996.63"),
    ("Network Charge", "32", "0.00", "7,106.49"),
    ("Network Charge VAT", "33", "0.00", "1,066.03"),
    ("Ghana Shippers Authority SNF Fee", "25", "0.00", "12.00"),
    ("Import NHIL", "47", "0.00", "50,741.08"),
    ("Network Charge NHIL", "48", "0.00", "177.67"),
    ("1% Withholding Tax on Import", "56", "0.00", "0.00"),
    ("GHS Disinfection Fee", "63", "0.00", "4,712.12"),
    ("MoTI e-IDF Fee", "72", "0.00", "5.00"),
    ("Special Import Levy (2%)", "78", "0.00", "13,931.55"),
    ("Ghana Export-Import Bank (EXIM) Levy", "87", "0.00", "14,994.97"),
    ("Ghana Education Trust (GET) Fund Import", "88", "0.00", "50,741.08"),
    ("Network Charge GET Fund Levy", "89", "0.00", "177.67"),
    ("Inspection Fee", "93", "0.00", "19,993.24"),
    ("African Union Import Levy", "98", "0.00", "3,998.63"),
]
TOTAL = "653,670.54"

# Box borders (image px): x0, y0, x1, y1.
BOXES = [
    (12, 60, 328, 104), (328, 60, 442, 88), (442, 60, 640, 128), (328, 88, 442, 116),
    (12, 104, 76, 128), (76, 104, 190, 128), (190, 104, 328, 128),
    (12, 128, 328, 174), (328, 128, 640, 174), (12, 174, 328, 230), (328, 174, 472, 204),
    (472, 174, 640, 204), (328, 204, 472, 230), (472, 204, 640, 230),
    (12, 230, 328, 258), (328, 230, 472, 258), (472, 230, 640, 258),
    (12, 258, 168, 286), (168, 258, 328, 286), (328, 258, 472, 286), (472, 258, 566, 286),
    (566, 258, 640, 286), (12, 286, 168, 306), (168, 286, 328, 306), (328, 286, 640, 336),
    (12, 306, 328, 336), (12, 336, 328, 510), (328, 336, 640, 510),
    (12, 510, 328, 820), (328, 510, 640, 840),
]


def _labels(o):
    """(x, y, text) of the printed labels (image px, baseline)."""
    return [
        (150, 22, "GHANA REVENUE AUTHORITY (GRA) CUSTOMS DIVISION"),
        (215, 38, "DECLARATION(BILL OF ENTRY) FOR CUSTOMS USE ONLY"),
        (258, 52, "(LOCAL CURRENCY : GHANA CEDI)"),
        (16, 70, "2   Exporter & Address"), (232, 70, "No :"),
        (333, 70, "1   Regime"), (385, 70, "45   CL Plan"),
        (447, 70, "A Office Code :"), (447, 82, "Manifest No :"),
        (447, 96, "BL/AWB :"),
        (447, 108, "Bill of Entry(BOE) No :"),
        (447, 120, "Date :"),
        (333, 96, "3   Gross Mass Kg"),
        (16, 110, "4   Items"), (80, 110, "5   Total Package"), (195, 110, "6   Reference number"),
        (16, 136, "7   Importer & Address"), (232, 136, "No :"),
        (333, 136, "8   Consignee / Actual Exporter"), (505, 136, "No :"),
        (16, 182, "9   Declarant/Representative"), (230, 182, "No :"),
        (333, 182, "10   Country of Consig/Destinat"), (478, 182, "11   Type of Licence"),
        (333, 212, "12   Delivery Terms & Place"), (478, 212, "13   Total Invoice Fcy"),
        (612, 212, "CC"),
        (16, 240, "14   M Trans, Ship/Aircaft ID, Nationality"),
        (333, 240, "15   Total FOB Fcy-Imp/Ncy-Exp"),
        (478, 240, "16   Curr Code"), (572, 240, "Rate of Xchange"),
        (16, 266, "17   Port of Loading/Unloading"), (172, 266, "18   Place Ship/Land & Ct Ind."),
        (333, 266, "19   FOB Ncy(Import/Export)"), (478, 266, "20   Valuation Method"),
        (572, 266, "44   CRMS Level"),
        (16, 294, "21   Entry/Exit Office"), (172, 294, "22   Identification Warehouse"),
        (333, 294, "23   Financial and banking data Bank Code :"),
        (340, 306, "Term of Payment :"), (340, 318, "Bank name :"),
        (340, 330, "Branch name"), (560, 330, "A/C No :"),
        (16, 316, "24   Attached Documents"),
        (16, 346, "25   Marks & Numbers"),
        (333, 346, "26   Item No"), (420, 346, "27   Commodity Code"),
        (560, 346, "DGD Ref. No."),
        (333, 370, "28   Cty.Org/Dest"), (420, 370, "29   Zone"), (490, 370, "30   CPC"),
        (570, 370, "Concess"),
        (333, 400, "31   Gross Wt kg"), (400, 400, "32   Net Wt kg"),
        (490, 400, "33   FOB Fcy"), (612, 400, "CC"),
        (333, 426, "34   FOB Ncy"), (420, 426, "35   Freight Ncy"),
        (490, 426, "36   Insurance Ncy"),
        (333, 454, "37   Other Costs"), (420, 454, "38   Supp U1"), (490, 454, "39   Supp U2"),
        (333, 480, "41   Licence No :"), (490, 480, "42   Customs Value"),
        (333, 492, "DV :"), (490, 492, "DQty :"), (333, 506, "Annexed Docs :"),
        (16, 520, "40 Tax"), (50, 530, "Tax base Amt"), (110, 530, "TBC"),
        (150, 530, "Rate"), (190, 520, "Amount"), (175, 530, "Exempted/Suspended"),
        (260, 530, "Amount Payable"),
        (333, 520, "43 Previous Declaration :"), (333, 534, "B ACCOUNTING DETAILS"),
        (490, 534, "Mode of Payment :"),
        (333, 548, "Receipt Number :"), (490, 548, "Date :"),
        (333, 562, "Bank Guarantee :"), (490, 562, "Date :"),
        (333, 576, "BG Amount :"),
        (530, 590, "Amount"), (588, 590, "Amount Payable"),
        (333, 600, "Taxes"), (470, 600, "Code"), (520, 600, "Exempted/Suspended"),
        (16, 700, "I......................................................do hereby declare that :"),
        (16, 712, "The information and particulars herein entered electronically are true"),
        (16, 760, "Date This.............................Day of.........................."),
        (16, 772, "Signature"), (16, 784, "Capacity in which acting...................."),
        (16, 880, "Doc Status : Assessed"),
        (190, 880, "Bill No. : KIA1-G-{0}-01".format(o["number"].split("/")[0].strip())),
        (530, 880, "No. of print : 1"), (590, 880, "Page No : A-1"),
    ]


def _values(o):
    """(x, y, text, right_aligned) of the printed values (image px, baseline)."""
    out = [
        (22, 82, "UNA TRADING FZE", False),
        (22, 96, "PLOT S60525 JEBEL ALY FREEZONE P.O. BOX 18747 DUBAI", False),
        (343, 81, "40", False), (398, 81, "PMD", False), (557, 70, "KIA1", False),
        (430, 108, "1,327.6000", True),
        (28, 122, "119", False), (180, 122, "48", True),
        (205, 122, o["reference"], False),
        (255, 136, "C0002864576", False), (22, 152, "MANTRAC GHANA LIMITED", False),
        (22, 165, "RING ROAD WEST, NORTH INDUSTRIAL AREA, ACCRA ACCRA", False),
        (525, 136, "C0002864576", False), (340, 152, "MANTRAC GHANA LIMITED", False),
        (340, 165, "RING ROAD WEST, NORTH INDUSTRIAL AREA, ACCRA ACCRA", False),
        (255, 182, "CH000154", False), (22, 198, "DHL LOGISTICS GHANA LIMITED", False),
        (22, 210, "TEMA MAIN HARBOUR AREA, TEMA, TEMA", False),
        (345, 197, "BE", False), (395, 197, "Belgium", False),
        (357, 224, o["terms"][0], False), (393, 224, o["terms"][1], False),
        (22, 252, "40", False),
        (466, 250, "154,681.17", True), (497, 252, o["rate_currency"], False),
        (33, 280, "BRU", False), (75, 280, "Brussels, Belgium -", False),
        (195, 280, "WAKIA1SWGL", False), (300, 280, "G", False),
        (466, 280, o["fob_ncy"], True),
        (500, 280, "Transaction Value", False), (590, 280, "Blue", False),
        (40, 306, "KIA1", False), (80, 306, "CEPS KIA", False),
        (440, 306, "20261023", False),
        (22, 328, "BLA, FPC, GSA, IDF, ITC, INV, PKL", False),
        (22, 358, "AS ADDRS", False), (345, 358, "0001", False),
        (440, 358, "7307990000", False), (345, 384, "AE", False), (430, 384, "GEN", False),
        (500, 384, "40D01", False), (370, 412, "11.1563", True), (440, 412, "11.1563", True),
        (560, 412, "393.46", True), (612, 412, "USD", False),
        (370, 438, "4,519.16", True), (440, 438, "443.69", True), (560, 438, "49.62", True),
        (370, 466, "385.69", True), (440, 466, "1.0000", True),
        (560, 492, "5,398.16", True), (560, 504, "1.0000", True), (420, 506, "FPC, GSA", False),
        (22, 440, "Quantity & unit :  48  PK", False), (22, 460, "Description of goods", False),
        (22, 472, "TUBE AS BOOM        5682319", False),
    ]
    # Box 40, the per-item taxes (left): figures on the same rows as the
    # B ACCOUNTING lines — they must never be read as the accounting amounts.
    left = [("01", "5,398.16", "24", "20.00", "0.00", "1,079.63"),
            ("02", "6,477.79", "31", "15.00", "0.00", "971.67"),
            ("05", "5,398.16", "24", "0.00", "0.00", "0.00"),
            ("06", "5,398.16", "24", "0.50", "0.00", "26.99")]
    for i, (code, base, tbc, rate, exm, pay) in enumerate(left):
        y = 612 + 11 * i
        out += [(22, y, code, False), (100, y, base, True), (115, y, tbc, False),
                (170, y, rate, True), (230, y, exm, True), (318, y, pay, True)]
    out.append((318, 612 + 11 * len(left) + 4, "2,637.09", True))
    out += [
        (557, 96, o["bl"], False),
        (557, 108, o["number"], False),
        (557, 124, o["date"], False),
        (590, 224, o["invoice_fcy"], True), (610, 224, o["invoice_currency"], False),
        (630, 252, o["rate"], True),
    ]
    y = 612
    for label, code, exempted, payable in o["tax_lines"]:
        out += [(333, y, label, False), (480, y, code, False), (560, y, exempted, True),
                (630, y, payable, True)]
        y += 11
    if o["total"] is not None:
        out += [(480, y + 2, "Total", False), (560, y + 2, "0.00", True),
                (630, y + 2, o["total"], True)]
    out.append((345, y + 16, "Tax Code 90,91,92 payable by Agent", False))
    return out


def icums_boe_pdf(bl="J552493", number="40726534505 / 00", date="15/07/2026",
                  user_reference="DDAO9116093", invoice_fcy="174,071.09",
                  invoice_currency="USD", rate="11.4857", rate_currency="USD",
                  fob_ncy="1,776,621.53", terms=("CPT", "ACCRA"), tax_lines=None,
                  total=TOTAL, reference="2607150575GCH000154", extra_footer=None,
                  font_scale=1.0, reading_order=False):
    """reading_order: write the text top to bottom instead (labels and values
    interleaved); font_scale: print every word larger or smaller."""
    o = dict(bl=bl, number=number, date=date, invoice_fcy=invoice_fcy,
             invoice_currency=invoice_currency, rate=rate, rate_currency=rate_currency,
             fob_ncy=fob_ncy, terms=terms, tax_lines=TAX_LINES if tax_lines is None else tax_lines,
             total=total, reference=reference)
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for x0, y0, x1, y1 in BOXES:
        page.draw_rect(fitz.Rect(x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE),
                       color=(0, 0, 0), width=0.5)

    queue = []

    def put(x, y, text, size=6.0, bold=False, right=False):
        if reading_order and not getattr(put, "flushing", False):
            queue.append((y, x, text, size, bold, right))
            return
        size = size * font_scale
        font = "hebo" if bold else "helv"
        if right:
            x = x - fitz.get_text_length(text, fontname=font, fontsize=size) / SCALE
        page.insert_text((x * SCALE, y * SCALE), text, fontsize=size, fontname=font)

    # The text layer in the real form's order: the labels first, then the
    # values — and the header's "BL/AWB :" label is followed by box 6's
    # reference number, so a label-then-value reading takes the wrong value.
    labels = _labels(o)
    for x, y, text in labels:
        if text == "BL/AWB :":
            put(x, y, text)
            put(205, 122, o["reference"], size=6.5, bold=True)
            continue
        put(x, y, text, size=8.5 if y < 60 else 6.0, bold=y < 60)
    for x, y, text, right in _values(o):
        if (x, y) == (205, 122):
            continue
        put(x, y, text, size=6.5, bold=True, right=right)
    footer = "User Reference : {0}".format(user_reference) if user_reference else None
    if footer:
        put(380, 880, footer)
    if extra_footer:
        put(16, 892, extra_footer)
    put.flushing = True
    for y, x, text, size, bold, right in sorted(queue):
        put(x, y, text, size=size, bold=bold, right=right)
    return doc.tobytes()
