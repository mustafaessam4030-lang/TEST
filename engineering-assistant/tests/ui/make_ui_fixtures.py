"""Extra synthetic PDFs for UI tests: a very long title, a wide 25-row table, and a fresh upload.

    python tests/ui/make_ui_fixtures.py <out_dir>
"""
import sys
import time
from pathlib import Path

import pymupdf

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

doc = pymupdf.open()
title = ("ZX-7 Heavy Duty Hydraulic Excavator Swing Drive, Final Drive and Undercarriage Component "
         "Specifications, Inspection Limits and Maintenance Intervals Handbook")
doc.set_metadata({"title": title})
page = doc.new_page(width=842, height=595)              # landscape A4 for a wide table
page.insert_text((40, 40), "ZX-7 component specifications", fontsize=16, fontname="hebo")
page.insert_text((40, 60), "Document No: ZX-7-SPEC    Revision: 3    Model: ZX-7 excavator", fontsize=9)
page.insert_text((40, 84), "Table 1 ZX-7 component limits", fontsize=10, fontname="hebo")
cols = ["Component", "Part number", "Nominal", "Wear limit", "Torque (N.m)", "Interval (h)"]
widths = [190, 110, 110, 110, 110, 110]
rows = [["Swing bearing bolt {0}".format(i), "7Y-{0:04d}".format(1200 + i), "{0} mm".format(20 + i),
         "{0} mm".format(18 + i), str(300 + 10 * i), str(250 * (1 + i % 4))] for i in range(1, 26)]
top, rh = 92, 17
xs = [40]
for w in widths:
    xs.append(xs[-1] + w)
for r, row in enumerate([cols] + rows):
    for c, cell in enumerate(row):
        page.insert_text((xs[c] + 4, top + r * rh + 12), cell, fontsize=8.5, fontname="hebo" if r == 0 else "helv")
for r in range(len(rows) + 2):
    page.draw_line((xs[0], top + r * rh), (xs[-1], top + r * rh), width=0.6)
for x in xs:
    page.draw_line((x, top), (x, top + rh * (len(rows) + 1)), width=0.6)
page.insert_text((40, 575), "SYNTHETIC TEST DOCUMENT - NOT FOR ENGINEERING USE", fontsize=7)
doc.save(out / "ZX-7_long_title_wide_table.pdf", no_new_id=True)

fresh = pymupdf.open()
p = fresh.new_page()
p.insert_text((72, 72), "PX-3 Pump Datasheet", fontsize=18, fontname="hebo")
p.insert_text((72, 100), "Model: PX-3 pump. Maximum inlet pressure: 6 bar. Generated {0}.".format(time.time()), fontsize=10)
fresh.save(out / "PX-3_attachment.pdf")
(out / "not_a_pdf.txt").write_text("this is not a PDF")
print("\n".join(str(p) for p in sorted(out.iterdir())))
