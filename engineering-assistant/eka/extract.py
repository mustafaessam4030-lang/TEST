"""PDF -> pages of structured elements, with page numbers kept from the source.

Per page the extractor produces an ordered list of elements (heading, text,
warning, table) with PDF-point bounding boxes, and a status. A page that cannot
be read is reported with the reason; it is never dropped silently:

    ok               native text extracted
    ocr              no usable text layer; text recovered by OCR
    ocr_low_conf     OCR succeeded but mean word confidence is low
    empty            no text and no images: a blank page
    ocr_unavailable  scanned page, but tesseract is not installed / OCR disabled
    ocr_failed       scanned page, OCR ran and failed or found no text
    error            the page raised an exception during extraction

Images on a page are counted but never interpreted; answers do not describe
diagrams.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

import pymupdf

from . import ocr as ocr_mod
from .config import Settings
from .textutil import normalize, normalize_block

log = logging.getLogger(__name__)
if hasattr(pymupdf, "no_recommend_layout"):
    pymupdf.no_recommend_layout()  # keep stdout clean; layout analysis is not used

WARNING_RE = re.compile(r"^[\s|\[\]*>-]*(WARNING|CAUTION|DANGER|NOTICE|IMPORTANT)\b\s*[:!\-]?", re.I)
NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+){0,3})\.?\s+[A-Z][^.]{2,80}$")
LOW_OCR_CONFIDENCE = 60.0

BBox = tuple[float, float, float, float]


class ExtractionError(RuntimeError):
    """The whole document could not be read (corrupt, encrypted, not a PDF)."""


@dataclass
class Element:
    kind: str                      # heading | text | warning | table
    text: str
    bbox: BBox
    level: int = 0                 # heading level, 1 = largest
    header: list[str] = field(default_factory=list)       # table column names
    rows: list[list[str]] = field(default_factory=list)   # table body rows
    row_bboxes: list[BBox] = field(default_factory=list)
    caption: str = ""


@dataclass
class PageResult:
    page_no: int                   # 1-based, as printed by a PDF viewer
    status: str
    method: str                    # text | ocr | none
    elements: list[Element] = field(default_factory=list)
    chars: int = 0
    images: int = 0
    reason: str = ""
    ocr_confidence: float | None = None


@dataclass
class Extraction:
    page_count: int
    pages: list[PageResult]
    metadata: dict


@dataclass
class _Line:
    text: str
    bbox: BBox
    size: float
    bold: bool


@dataclass
class _Item:
    """A block of lines (or a table) to be ordered on the page."""
    bbox: BBox
    lines: list[_Line] = field(default_factory=list)
    table: Element | None = None

    @property
    def chars(self) -> int:
        return sum(len(line.text) for line in self.lines) if self.table is None else 200


def open_pdf(data: bytes) -> pymupdf.Document:
    if not data.lstrip()[:5].startswith(b"%PDF"):
        raise ExtractionError("not a PDF file (missing %PDF header)")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # pymupdf raises its own FileDataError and others
        raise ExtractionError("the PDF is corrupted or unreadable: {0}".format(exc)) from exc
    if doc.needs_pass:
        doc.close()
        raise ExtractionError("the PDF is password protected")
    if doc.page_count == 0:
        doc.close()
        raise ExtractionError("the PDF has no pages")
    return doc


def extract(data: bytes, settings: Settings, progress=None) -> Extraction:
    """Extract every page. ``progress(done, total)`` is called after each page."""
    doc = open_pdf(data)
    try:
        repeated, body_size = _document_profile(doc)
        pages = []
        for index in range(doc.page_count):
            try:
                pages.append(_extract_page(doc[index], index + 1, settings, repeated, body_size))
            except Exception as exc:
                log.warning("page %d extraction failed: %s", index + 1, exc)
                pages.append(PageResult(index + 1, "error", "none", reason=str(exc)[:300]))
            if progress:
                progress(index + 1, doc.page_count)
        return Extraction(doc.page_count, pages, _metadata(doc, pages))
    finally:
        doc.close()


# ── native text ────────────────────────────────────────────────────────────

def _page_lines(page: pymupdf.Page) -> list[list[_Line]]:
    """Text blocks as lists of lines, each line with its dominant font size/weight."""
    flags = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP | pymupdf.TEXT_DEHYPHENATE
    blocks = []
    for block in page.get_text("dict", flags=flags)["blocks"]:
        if block.get("type") != 0:
            continue
        lines = []
        for line in block["lines"]:
            spans = [s for s in line["spans"] if s["text"].strip()]
            if not spans:
                continue
            text = normalize("".join(s["text"] for s in line["spans"]))
            weight = Counter()
            for s in spans:
                weight[(round(s["size"], 1), bool(s["flags"] & 16))] += len(s["text"].strip())
            (size, bold), _ = weight.most_common(1)[0]
            lines.append(_Line(text, tuple(line["bbox"]), size, bold))
        if lines:
            blocks.append(lines)
    return blocks


PAGE_FURNITURE = re.compile(r"\bpage\s*\d+(\s*(of|/)\s*\d+)?\s*$", re.I)


def _in_margin(bbox: BBox, height: float) -> bool:
    return bbox[3] < height * 0.08 or bbox[1] > height * 0.92


def _margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", text.strip().lower())


def _document_profile(doc: pymupdf.Document) -> tuple[set[str], float]:
    """Running headers/footers (lines in the top/bottom 8% repeated on most pages)
    and the body font size, measured over the whole document so heading levels
    are consistent from page to page."""
    seen: Counter = Counter()
    sizes: Counter = Counter()
    for page in doc:
        height = page.rect.height
        keys = set()
        for block in _page_lines(page):
            for line in block:
                sizes[line.size] += len(line.text)
                if line.bbox[3] < height * 0.08 or line.bbox[1] > height * 0.92:
                    keys.add(_margin_key(line.text))
        seen.update(keys)
    repeated = ({key for key, count in seen.items() if count >= max(3, doc.page_count // 2)}
                if doc.page_count >= 3 else set())
    return repeated, (sizes.most_common(1)[0][0] if sizes else 10.0)


def _inside(inner: BBox, outer: BBox, pad: float = 2.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - pad <= cx <= outer[2] + pad and outer[1] - pad <= cy <= outer[3] + pad


def _tables(page: pymupdf.Page) -> list[Element]:
    out = []
    try:
        found = page.find_tables()
    except Exception as exc:  # table detection is best effort
        log.info("table detection failed on page %d: %s", page.number + 1, exc)
        return out
    for table in found.tables:
        rows = [[normalize((cell or "").replace("\n", " ")) for cell in row] for row in table.extract()]
        header = [normalize((name or "").replace("\n", " ")) for name in table.header.names]
        row_boxes = [tuple(r.bbox) for r in table.rows]
        # A caption line just above the grid is sometimes taken as an external
        # header ("Table 2 ...", "", ""): the first grid row is the real header.
        if table.header.external and rows and sum(1 for h in header if h) <= len(header) // 2 \
                and all(rows[0]):
            header, rows, row_boxes = rows[0], rows[1:], row_boxes[1:]
        if rows and [c.lower() for c in rows[0]] == [h.lower() for h in header]:
            rows, row_boxes = rows[1:], row_boxes[1:]
        body = [(r, b) for r, b in zip(rows, row_boxes) if any(cell for cell in r)]
        if len(header) < 2 or not body:
            continue
        header = [h or "Column {0}".format(i + 1) for i, h in enumerate(header)]
        out.append(Element("table", "", tuple(table.bbox), header=header,
                           rows=[r for r, _ in body], row_bboxes=[b for _, b in body]))
    return out


def _order(items: list[_Item], width: float) -> list[_Item]:
    """Reading order. Two-column pages read left column, then right, between full-width bands."""
    mid = width / 2
    left = [i for i in items if i.bbox[2] <= mid + 8]
    right = [i for i in items if i.bbox[0] >= mid - 8]
    two_column = (len(left) >= 2 and len(right) >= 2
                  and sum(i.chars for i in left) >= 150 and sum(i.chars for i in right) >= 150)
    if not two_column:
        return sorted(items, key=lambda i: (round(i.bbox[1]), i.bbox[0]))
    full = sorted((i for i in items if i not in left and i not in right), key=lambda i: i.bbox[1])
    ordered, remaining = [], set(map(id, left + right))
    columns = left + right

    def flush(limit: float):
        band = [i for i in columns if id(i) in remaining and i.bbox[1] < limit]
        for column in (left, right):
            for item in sorted((i for i in band if i in column), key=lambda i: i.bbox[1]):
                ordered.append(item)
                remaining.discard(id(item))

    for item in full:
        flush(item.bbox[1])
        ordered.append(item)
    flush(float("inf"))
    return ordered


def _classify(items: list[_Item], body_size: float) -> list[Element]:
    elements: list[Element] = []
    pending_caption = ""
    for item in items:
        if item.table is not None:
            table = item.table
            table.caption = pending_caption
            pending_caption = ""
            elements.append(table)
            continue
        para: list[_Line] = []
        kind = "text"

        def flush_para():
            nonlocal para, kind
            if para:
                text = join_lines([l.text for l in para])
                elements.append(Element(kind, text, _union(l.bbox for l in para)))
            para, kind = [], "text"

        for line in item.lines:
            heading_level = _heading_level(line, body_size)
            if heading_level:
                flush_para()
                elements.append(Element("heading", line.text, line.bbox, level=heading_level))
                continue
            if WARNING_RE.match(line.text):
                flush_para()
                kind = "warning"
            para.append(line)
        flush_para()
        # A "Table N ..." line directly before a table becomes its caption.
        last = elements[-1] if elements else None
        if last is not None and last.kind in ("text", "heading"):
            last_line = last.text.split("\n")[-1]
            if re.match(r"^table\s+\d+", last_line, re.I):
                pending_caption = last_line
                # The caption now belongs to the table; do not keep it twice.
                rest = last.text[: -len(last_line)].strip()
                if rest:
                    last.text = rest
                else:
                    elements.pop()
    return elements


LIST_ITEM = re.compile(r"^\s*(?:\(?\d{1,2}[.)]|[a-z][.)]|[-\u2022*\u25aa\u25cf]|step\s+\d+)\s+", re.I)


def join_lines(lines: list[str]) -> str:
    """Re-flow wrapped lines into prose; list items and steps keep their own line."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if not out:
            out = line
        elif LIST_ITEM.match(line) or out.endswith(":"):
            out += "\n" + line
        else:
            out += " " + line
    return out


def _heading_level(line: _Line, body_size: float) -> int:
    text = line.text.strip()
    if len(text) > 90 or len(text) < 2 or text.endswith((".", ",", ";", ":")) or WARNING_RE.match(text):
        return 0
    if re.match(r"^(table|figure|fig\.)\s+\d+", text, re.I):
        return 0
    if line.size >= body_size * 1.5:
        return 1
    if line.size >= body_size * 1.15:
        return 2
    if line.bold and (NUMBERED_HEADING.match(text) or len(text.split()) <= 8):
        return 3
    return 0


def _union(boxes) -> BBox:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _extract_page(page: pymupdf.Page, page_no: int, settings: Settings, repeated: set[str],
                  body_size: float) -> PageResult:
    images = len(page.get_images(full=False))
    blocks = _page_lines(page)
    height = page.rect.height
    blocks = [[l for l in block
               if not (_in_margin(l.bbox, height)
                       and (_margin_key(l.text) in repeated or PAGE_FURNITURE.search(l.text)))]
              for block in blocks]
    blocks = [b for b in blocks if b]
    chars = sum(len(l.text) for block in blocks for l in block)

    if chars < settings.ocr_min_chars:
        if images == 0 and chars == 0:
            return PageResult(page_no, "empty", "none", images=0, reason="blank page: no text and no images")
        if images:
            return _ocr_page(page, page_no, settings, images, blocks)

    tables = _tables(page) if settings.tables_enabled else []
    items = []
    for block in blocks:
        kept = [l for l in block if not any(_inside(l.bbox, t.bbox) for t in tables)]
        if kept:
            items.append(_Item(_union(l.bbox for l in kept), kept))
    items.extend(_Item(t.bbox, table=t) for t in tables)
    elements = _classify(_order(items, page.rect.width), body_size)
    return PageResult(page_no, "ok", "text", elements, chars=chars, images=images)


def _ocr_page(page, page_no, settings, images, blocks) -> PageResult:
    base = PageResult(page_no, "ocr_unavailable", "none", images=images,
                      chars=sum(len(l.text) for b in blocks for l in b))
    if not settings.ocr_enabled:
        base.reason = "scanned page (no text layer); OCR is disabled"
        return base
    if not ocr_mod.available():
        base.reason = "scanned page (no text layer); tesseract is not installed"
        return base
    scale = settings.ocr_dpi / 72.0
    png = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csGRAY).tobytes("png")
    try:
        result = ocr_mod.ocr_png(png, scale, settings.ocr_lang, settings.ocr_timeout_s)
    except ocr_mod.OcrError as exc:
        base.status, base.reason = "ocr_failed", str(exc)[:300]
        return base
    if not result.lines:
        base.status, base.reason = "ocr_failed", "OCR found no text on the scanned page"
        return base
    elements: list[Element] = []
    groups: dict[tuple[int, int], list] = {}
    height = page.rect.height
    for line in result.lines:
        if _in_margin(line.bbox, height) and PAGE_FURNITURE.search(line.text):
            continue
        groups.setdefault((line.block, line.paragraph), []).append(line)
    for lines in groups.values():
        para: list = []
        kind = "text"
        for line in lines:
            line.text = text = normalize(line.text).lstrip("|[]*> ")
            if not text:
                continue
            if _ocr_heading(text):
                if para:
                    elements.append(Element(kind, join_lines([l.text for l in para]), _union(l.bbox for l in para)))
                    para, kind = [], "text"
                elements.append(Element("heading", text, line.bbox, level=2))
                continue
            if WARNING_RE.match(text):
                if para:
                    elements.append(Element(kind, join_lines([l.text for l in para]), _union(l.bbox for l in para)))
                para, kind = [], "warning"
            para.append(line)
        if para:
            elements.append(Element(kind, join_lines([l.text for l in para]), _union(l.bbox for l in para)))
    low = result.confidence < LOW_OCR_CONFIDENCE
    return PageResult(page_no, "ocr_low_conf" if low else "ocr", "ocr", elements,
                      chars=sum(len(l.text) for l in result.lines), images=images,
                      ocr_confidence=round(result.confidence, 1),
                      reason="OCR mean word confidence {0:.0f}/100".format(result.confidence) if low else "")


def _ocr_heading(text: str) -> bool:
    if len(text) > 70 or text.endswith((".", ",", ":")) or WARNING_RE.match(text):
        return False
    letters = [c for c in text if c.isalpha()]
    return bool(NUMBERED_HEADING.match(text)) or (len(letters) >= 4 and all(c.isupper() for c in letters))


# ── document metadata ─────────────────────────────────────────────────────

DOC_NUMBER_RE = re.compile(r"\b(?:document|doc\.?)\s*(?:no\.?|number|#|id)\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-_./]{2,40})", re.I)
REVISION_RE = re.compile(r"\brev(?:ision)?\.?\s*[:\-]?\s*([A-Z0-9]{1,4}(?:\.\d+)?)\b", re.I)
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
MODEL_RE = re.compile(r"\b(?:equipment|model|models|applies to)\s*[:\-]\s*([^\n]{2,80})", re.I)


def _metadata(doc: pymupdf.Document, pages: list[PageResult]) -> dict:
    first_text, title = "", ""
    for page in pages[:2]:
        for element in page.elements:
            if element.kind == "table":
                first_text += "\n" + "\n".join(" ".join(r) for r in element.rows)
                continue
            first_text += "\n" + element.text
            if not title and element.kind == "heading" and element.level <= 2:
                title = element.text
    meta = doc.metadata or {}
    title = normalize(meta.get("title") or "") or title
    found = {
        "title": title[:200],
        "doc_number": _first(DOC_NUMBER_RE, first_text),
        "revision": _first(REVISION_RE, first_text),
        "revision_date": _first(DATE_RE, first_text),
        "equipment": _first(MODEL_RE, first_text),
    }
    return {k: (normalize_block(v) if v else "") for k, v in found.items()}


def _first(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip().rstrip(".,;") if match else ""
