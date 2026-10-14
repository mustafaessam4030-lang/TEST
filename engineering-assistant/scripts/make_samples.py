"""Generate the synthetic, non-confidential sample PDFs used by tests and evaluation.

Every product, part number and value in these documents is invented. Each page
carries a "SYNTHETIC TEST DOCUMENT" footer. Re-running the script produces the
same files.

    python scripts/make_samples.py [output_dir]
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import pymupdf

FOOTER = "SYNTHETIC TEST DOCUMENT - NOT FOR ENGINEERING USE"
W, H = 595, 842          # A4 in points
MARGIN = 56


class Writer:
    def __init__(self, title: str):
        self.doc = pymupdf.open()
        self.doc.set_metadata({"title": title, "creator": "eka sample generator"})
        self.page = None
        self.y = 0.0
        self.new_page()

    # ── layout primitives ──
    def new_page(self):
        self.page = self.doc.new_page(width=W, height=H)
        self.y = MARGIN
        self.page.insert_text((MARGIN, H - 30), "{0}  |  Page {1}".format(FOOTER, self.doc.page_count),
                              fontsize=7, fontname="helv", color=(0.4, 0.4, 0.4))

    def _ensure(self, height: float):
        if self.y + height > H - 70:
            self.new_page()

    @staticmethod
    def wrap(text: str, size: float, width: float, font: str = "helv") -> list[str]:
        lines, current = [], ""
        for word in text.split():
            trial = (current + " " + word).strip()
            if pymupdf.get_text_length(trial, fontname=font, fontsize=size) <= width:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def text(self, text: str, size: float = 10, font: str = "helv", x: float = MARGIN,
             width: float = W - 2 * MARGIN, gap: float = 6, color=(0, 0, 0)):
        lines = self.wrap(text, size, width, font)
        self._ensure(len(lines) * size * 1.35 + gap)
        for line in lines:
            self.y += size * 1.35
            self.page.insert_text((x, self.y), line, fontsize=size, fontname=font, color=color)
        self.y += gap

    def title(self, text: str):
        self.text(text, size=20, font="hebo", gap=10)

    def heading(self, text: str):
        self._ensure(60)
        self.y += 6
        self.text(text, size=14, font="hebo", gap=4)

    def warning(self, text: str):
        lines = self.wrap(text, 10, W - 2 * MARGIN - 20, "hebo")
        height = len(lines) * 13.5 + 12
        self._ensure(height + 8)
        rect = pymupdf.Rect(MARGIN, self.y + 2, W - MARGIN, self.y + 2 + height)
        self.page.draw_rect(rect, color=(0.8, 0.1, 0.1), fill=(1, 0.95, 0.9), width=1)
        self.y += 4
        for line in lines:
            self.y += 13.5
            self.page.insert_text((MARGIN + 10, self.y), line, fontsize=10, fontname="hebo", color=(0.6, 0, 0))
        self.y = rect.y1 + 8

    def table(self, caption: str, header: list[str], rows: list[list[str]], widths: list[float]):
        row_h = 18
        self._ensure(row_h * (len(rows) + 1) + 40)
        self.text(caption, size=10, font="hebo", gap=2)
        top = self.y + 4
        xs = [MARGIN]
        for w in widths:
            xs.append(xs[-1] + w)
        for r, row in enumerate([header] + rows):
            y0 = top + r * row_h
            for c, cell in enumerate(row):
                self.page.insert_text((xs[c] + 4, y0 + 12.5), cell, fontsize=9,
                                      fontname="hebo" if r == 0 else "helv")
        bottom = top + row_h * (len(rows) + 1)
        for r in range(len(rows) + 2):
            y = top + r * row_h
            self.page.draw_line((xs[0], y), (xs[-1], y), width=0.7)
        for x in xs:
            self.page.draw_line((x, top), (x, bottom), width=0.7)
        self.y = bottom + 10

    def two_columns(self, left: list[tuple[str, str]], right: list[tuple[str, str]]):
        col_w = (W - 2 * MARGIN - 24) / 2
        start = self.y
        for column, x in ((left, MARGIN), (right, MARGIN + col_w + 24)):
            self.y = start
            for kind, text in column:
                if kind == "h":
                    self.text(text, size=11, font="hebo", x=x, width=col_w, gap=2)
                else:
                    self.text(text, size=10, x=x, width=col_w)
        self.y = max(self.y, start)

    def save(self, path: Path):
        self.doc.save(path, garbage=4, deflate=True)


# ── documents ──────────────────────────────────────────────────────────────

def hx200(rev: str) -> Writer:
    new = rev == "B"
    w = Writer("HX-200 Hydraulic Pump Service Manual")
    w.title("HX-200 Hydraulic Pump Service Manual")
    w.text("Document No: HX-200-SM")
    w.text("Revision: {0}    Date: {1}".format(rev, "2025-03-10" if new else "2023-06-01"))
    w.text("Model: HX-200 axial piston pump")
    w.heading("1 Introduction")
    w.text("This manual covers inspection, adjustment and repair of the HX-200 axial piston pump "
           "used on mobile hydraulic equipment. Read the safety section before starting any work.")
    w.heading("1.1 Safety")
    w.warning("WARNING: Relieve all hydraulic pressure before disconnecting any line. Fluid escaping "
              "under pressure can penetrate the skin and cause serious injury.")
    w.warning("CAUTION: Pump surfaces can exceed 80 C during operation. Allow the pump to cool before service.")

    w.new_page()
    w.heading("2 Specifications")
    w.text("The values below apply to pumps with serial numbers from HX2A00001 onward.")
    spec = [
        ["Rated pressure", "350 bar (5076 psi)" if new else "320 bar (4641 psi)", "Continuous duty"],
        ["Peak pressure", "420 bar (6092 psi)" if new else "400 bar (5802 psi)", "Maximum 10 s"],
        ["Rated flow", "120 L/min", "At 1800 rpm"],
        ["Displacement", "67 cm3/rev", "Fixed"],
        ["Relief valve setting", "380 bar" if new else "350 bar", "Factory set"],
        ["Dry weight", "48 kg", "Without fluid"],
        ["Operating temperature", "-20 to 80 C", "Fluid temperature"],
        ["Shaft seal kit", "4J-5521", "Part number"],
    ]
    w.table("Table 1 HX-200 performance specifications", ["Parameter", "Value", "Notes"], spec,
            [170, 170, 143])

    if new:
        w.heading("3 Torque Specifications")
        w.table("Table 2 Fastener torque values",
                ["Fastener", "Thread", "Torque (N.m)", "Torque (lb.ft)"],
                [["Mounting flange bolt", "M16x2.0", "210", "155"],
                 ["Port cover bolt", "M10x1.5", "49", "36"],
                 ["Drain plug", "M18x1.5", "60", "44"]],
                [170, 100, 107, 106])
        w.text("Apply thread-locking compound 9S-3263 to the mounting flange bolts before installation. "
               "Tighten the bolts in a cross pattern in two stages.")

        w.new_page()
        w.heading("4 Shaft Seal Replacement")
        w.text("Special tools: seal driver 1U-7650 and seal puller 5P-3520.")
        w.warning("WARNING: Lower all attachments to the ground and relieve hydraulic pressure before removing the pump.")
        steps = [
            "1. Drain the pump case through the drain plug and discard the fluid.",
            "2. Remove the four port cover bolts and the port cover.",
            "3. Remove the retaining ring from the shaft bore.",
            "4. Use seal puller 5P-3520 to remove the old shaft seal. Do not scratch the shaft.",
            "5. Lubricate the new seal from kit 4J-5521 with clean hydraulic oil.",
            "6. Install the new seal with seal driver 1U-7650 until it is flush with the bore.",
            "7. Install the retaining ring and the port cover. Tighten the port cover bolts to 49 N.m.",
        ]
        for step in steps:
            w.text(step, gap=3)
        w.text("After assembly, fill the case with clean fluid and run the pump at low idle for 5 minutes. "
               "Check for leaks at the shaft seal.")

        w.new_page()
        w.heading("5 Troubleshooting")
        w.two_columns(
            [("h", "Pump noise"),
             ("p", "Symptom: the pump makes a loud whining noise under load."),
             ("p", "Cause: cavitation caused by a clogged suction strainer."),
             ("p", "Remedy: clean the suction strainer and check that inlet vacuum does not exceed 0.2 bar.")],
            [("h", "Low output pressure"),
             ("p", "Symptom: the system cannot reach rated pressure."),
             ("p", "Cause: a worn or contaminated relief valve cartridge."),
             ("p", "Remedy: replace relief valve cartridge 1R-0750 and verify the relief setting.")])

        w.new_page()          # page 6 is left blank on purpose (empty page handling)
        w.new_page()
    else:
        w.new_page()
    w.heading("{0} Fluid Recommendations".format("6" if new else "3"))
    w.text("Use ISO VG 46 anti-wear hydraulic oil. Change filter element 1R-0741 every {0} service hours. "
           "Change the hydraulic fluid every 2000 service hours or after any pump failure.".format(
               500 if new else 250))
    if not new:
        w.text("This revision is superseded when a later revision of HX-200-SM is issued.")
    return w


def cv90() -> Writer:
    w = Writer("CV-90 Conveyor Gearbox Datasheet")
    w.title("CV-90 Conveyor Gearbox Datasheet")
    w.text("Document No: CV-90-DS")
    w.text("Revision: 1    Date: 2024-11-02")
    w.text("Model: CV-90 helical conveyor gearbox")
    w.heading("1 Ratings")
    w.table("Table 1 CV-90 ratings", ["Parameter", "Value"],
            [["Input power", "3.5 kW (4.7 hp)"], ["Output speed", "45 rpm"], ["Gear ratio", "32:1"],
             ["Oil capacity", "2.4 L"], ["Ambient temperature", "-10 to 40 C"]], [240, 243])
    w.heading("2 Installation")
    w.table("Table 2 Mounting torque", ["Fastener", "Thread", "Torque (N.m)", "Torque (lb.ft)"],
            [["Mounting flange bolt", "M16x2.0", "180", "133"], ["Foot mounting bolt", "M20x2.5", "350", "258"]],
            [170, 100, 107, 106])
    w.text("Align the gearbox output shaft with the conveyor drive pulley to within 0.1 mm before tightening.")
    w.heading("3 Lubrication")
    w.text("Fill with ISO VG 220 gear oil. Change the gear oil every 2500 operating hours.")
    return w


def scanned_checklist(path: Path):
    """Image-only pages: rendered text rasterised with noise, as a scanner would produce."""
    src = Writer("TL-5 Daily Inspection Checklist")
    src.title("TL-5 DAILY INSPECTION CHECKLIST")
    src.text("Model: TL-5 tracked loader", size=12)
    src.heading("WALK-AROUND CHECKS")
    for line in [
        "1. Check track tension: track sag must be 40-50 mm measured at the midpoint.",
        "2. Check the engine oil level with the machine on level ground.",
        "3. Inspect the bucket teeth for wear and replace any tooth worn beyond 50 percent.",
        "4. Drain water from the fuel water separator.",
    ]:
        src.text(line, size=12, gap=8)
    src.warning("WARNING: Never work under a raised bucket unless the lift arm support is engaged.")
    src.new_page()
    src.heading("OPERATOR SIGN-OFF")
    src.text("Record the hour meter reading and any defects found before starting the shift. "
             "Report any hydraulic leak to the site supervisor immediately.", size=12)

    rng = random.Random(42)
    out = pymupdf.open()
    for page in src.doc:
        pix = page.get_pixmap(dpi=200, colorspace=pymupdf.csGRAY)
        data = bytearray(pix.samples)
        for _ in range(len(data) // 60):          # speckle noise
            data[rng.randrange(len(data))] = rng.randrange(120, 230)
        noisy = pymupdf.Pixmap(pymupdf.csGRAY, pix.width, pix.height, bytes(data), False)
        target = out.new_page(width=W, height=H)
        target.insert_image(target.rect, pixmap=noisy)
    out.set_metadata({"title": "TL-5 Daily Inspection Checklist (scan)"})
    out.save(path, garbage=4, deflate=True)


def main(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    hx200("B").save(out_dir / "HX-200_Service_Manual_RevB.pdf")
    hx200("A").save(out_dir / "HX-200_Service_Manual_RevA.pdf")
    cv90().save(out_dir / "CV-90_Gearbox_Datasheet.pdf")
    scanned_checklist(out_dir / "TL-5_Inspection_Checklist_scanned.pdf")
    (out_dir / "corrupted.pdf").write_bytes(b"%PDF-1.7\n" + bytes(random.Random(7).randrange(256) for _ in range(4000)))
    blank = pymupdf.open()
    blank.new_page(width=W, height=H)
    blank.save(out_dir / "blank.pdf")
    for path in sorted(out_dir.glob("*.pdf")):
        print("{0:45s} {1:>8,d} bytes".format(path.name, path.stat().st_size))


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "samples")
