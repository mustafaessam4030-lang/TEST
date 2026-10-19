"""Surgical cell patching for .xlsx files.

WHY NOT openpyxl: openpyxl rebuilds the package on save and drops embedded
images, drawings and printer settings. This template carries the Mantrac
letterhead and the BOE screenshot, and a cheque request that prints without
its letterhead is not usable. So we patch the sheet XML in place and
repackage the zip, leaving every part we did not touch byte-for-byte
identical.

Only three things are ever rewritten:
  1. the <c> elements we are told to set,
  2. xl/calcChain.xml is removed (Excel rebuilds it; a stale chain that
     references a cell whose formula changed makes Excel complain), and
  3. calcPr gains fullCalcOnLoad="1" so formulas recalculate on open.
"""
from __future__ import annotations
import re, shutil, zipfile, datetime as _dt
from pathlib import Path

_ESC = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}


def xesc(s: str) -> str:
    return "".join(_ESC.get(ch, ch) for ch in str(s))


def col_index(ref: str) -> int:
    """'AB12' -> 28. Used to keep inserted cells in column order, which
    Excel requires within a row."""
    n = 0
    for ch in re.match(r"([A-Z]+)", ref).group(1):
        n = n * 26 + (ord(ch) - 64)
    return n


def excel_serial(d: _dt.date | _dt.datetime) -> float:
    """Excel's 1900 date system, including its deliberate 1900 leap-year bug
    (day 60 = 29-Feb-1900, which never existed) — hence the +1 offset that
    every implementation carries."""
    if isinstance(d, _dt.datetime):
        base = _dt.datetime(1899, 12, 30)
        return (d - base).total_seconds() / 86400.0
    return (d - _dt.date(1899, 12, 30)).days


class Cell:
    """A value to write. kind is one of: num, str, formula, date."""

    __slots__ = ("kind", "value")

    def __init__(self, kind: str, value):
        self.kind, self.value = kind, value

    def inner(self) -> tuple[str, str]:
        """Returns (extra attributes, inner XML)."""
        if self.kind == "num":
            return "", f"<v>{self.value}</v>"
        if self.kind == "date":
            return "", f"<v>{excel_serial(self.value)}</v>"
        if self.kind == "formula":
            f = self.value.lstrip("=")
            # no cached <v>: fullCalcOnLoad makes Excel compute it on open
            return "", f"<f>{xesc(f)}</f>"
        if self.kind == "str":
            return ' t="inlineStr"', f"<is><t xml:space=\"preserve\">{xesc(self.value)}</t></is>"
        raise ValueError(f"unknown cell kind {self.kind!r}")


def num(v):      return Cell("num", v)
def text(v):     return Cell("str", v)
def formula(v):  return Cell("formula", v)
def date(v):     return Cell("date", v)


def _insert_row(xml: str, r: int) -> str:
    """Insert <row r="r"></row> into sheetData, keeping rows in ascending
    order. Excel omits entirely empty rows from the XML, so a cell in a
    never-used row has no row element to go into."""
    rows = [(m.start(), int(m.group(1))) for m in
            re.finditer(r'<row[^>]*\br="(\d+)"', xml)]
    new = f'<row r="{r}">'
    body = f'{new}</row>'
    for start, n in rows:
        if n > r:
            return xml[:start] + body + xml[start:]
    # no later row: append just before </sheetData>
    m = re.search(r'</sheetData>', xml)
    if not m:
        return xml
    return xml[:m.start()] + body + xml[m.start():]


def _patch_sheet(xml: str, cells: dict[str, Cell]) -> tuple[str, list[str]]:
    """Replace or insert each cell. Returns (xml, notes)."""
    notes = []
    for ref, cell in cells.items():
        extra, inner = cell.inner()
        # keep the existing style index so the template's formatting survives
        m = re.search(rf'<c r="{ref}"((?:\s+[a-zA-Z:]+="[^"]*")*)\s*(/>|>.*?</c>)', xml, re.S)
        if m:
            attrs = m.group(1)
            s = re.search(r'\s+s="(\d+)"', attrs)
            style = f' s="{s.group(1)}"' if s else ""
            xml = xml[:m.start()] + f'<c r="{ref}"{style}{extra}>{inner}</c>' + xml[m.end():]
        else:
            # cell absent: insert into its row, in column order
            row_no = re.match(r"[A-Z]+(\d+)", ref).group(1)
            rm = re.search(rf'<row[^>]*\br="{row_no}"[^>]*>(.*?)</row>', xml, re.S)
            if not rm:
                # the row itself is absent (Excel omits entirely empty rows).
                # Insert an empty one in row order, then fall through to the
                # normal cell-insert path below.
                xml = _insert_row(xml, int(row_no))
                notes.append(f"row {row_no} created")
                rm = re.search(rf'<row[^>]*\br="{row_no}"[^>]*>(.*?)</row>', xml, re.S)
                if not rm:
                    notes.append(f"row {row_no} could not be created — {ref} skipped")
                    continue
            body, new = rm.group(1), f'<c r="{ref}"{extra}>{inner}</c>'
            existing = [(mm.start(), mm.group(1)) for mm in
                        re.finditer(r'<c r="([A-Z]+\d+)"', body)]
            pos = len(body)
            for start, r in existing:
                if col_index(r) > col_index(ref):
                    pos = start
                    break
            body = body[:pos] + new + body[pos:]
            xml = xml[:rm.start(1)] + body + xml[rm.end(1):]
            notes.append(f"{ref} inserted (was empty)")
    return xml, notes


def patch(src: Path, dst: Path, edits: dict[str, dict[str, Cell]],
          sheet_xml_for: dict[str, str]) -> list[str]:
    """edits: {sheet name: {cell ref: Cell}}.
    sheet_xml_for: {sheet name: 'xl/worksheets/sheetN.xml'}."""
    notes = []
    zin = zipfile.ZipFile(src)
    names = zin.namelist()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            n = item.filename
            if n == "xl/calcChain.xml":
                notes.append("calcChain.xml dropped (Excel rebuilds it)")
                continue
            data = zin.read(n)
            if n == "[Content_Types].xml":
                data = re.sub(rb'<Override PartName="/xl/calcChain\.xml"[^>]*/>', b"", data)
            elif n == "xl/_rels/workbook.xml.rels":
                data = re.sub(rb'<Relationship[^>]*calcChain\.xml"[^>]*/>', b"", data)
            elif n == "xl/workbook.xml":
                s = data.decode("utf-8")
                if "fullCalcOnLoad" not in s:
                    s = s.replace("<calcPr ", '<calcPr fullCalcOnLoad="1" ', 1)
                    notes.append("fullCalcOnLoad=1 set")
                data = s.encode("utf-8")
            else:
                for sheet, path in sheet_xml_for.items():
                    if n == path and sheet in edits:
                        s = data.decode("utf-8")
                        s, nn = _patch_sheet(s, edits[sheet])
                        notes += [f"[{sheet}] {x}" for x in nn]
                        data = s.encode("utf-8")
            zout.writestr(item, data)
    zin.close()
    return notes


def sheet_paths(src: Path) -> dict[str, str]:
    """Map sheet display name -> its worksheet XML path, via workbook rels."""
    z = zipfile.ZipFile(src)
    wb = z.read("xl/workbook.xml").decode("utf-8")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rel = {m.group(1): m.group(2) for m in
           re.finditer(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels)}
    out = {}
    for m in re.finditer(r'<sheet name="([^"]+)"[^>]*r:id="([^"]+)"', wb):
        t = rel.get(m.group(2), "")
        out[m.group(1)] = "xl/" + t.lstrip("/").replace("../", "")
    z.close()
    return out
