"""Turn extracted pages into retrievable chunks without losing engineering context.

Rules:
* A chunk never spans pages, so every chunk has exactly one source page.
* Every chunk carries its section path ("4 Shaft Seal Replacement"), carried
  across page breaks, and the search text is prefixed with document title and
  section so a passage like "Tighten to 49 N.m" is findable by its context.
* Each table row is its own chunk and is rendered with its column names and the
  table caption: "Fastener: Port cover bolt | Thread: M10x1.5 | Torque (N.m): 49".
* Warnings are their own chunks (kind="warning") so they can be attached to
  answers about the procedure they belong to.
* Numbered procedure steps stay together up to the size limit; a split
  procedure is marked "(continued)".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .extract import BBox, Element, PageResult
from .textutil import normalize

STEP_RE = re.compile(r"^\s*(?:step\s*)?\d{1,2}[.)]\s+", re.I)


@dataclass
class Chunk:
    page_no: int
    seq: int
    kind: str              # text | table_row | warning | heading
    section: str
    text: str              # the source passage, shown and quoted in citations
    bboxes: list[BBox] = field(default_factory=list)
    ocr: bool = False

    def search_text(self, title: str) -> str:
        return "\n".join(part for part in (title, self.section, self.text) if part)


def table_row_text(header: list[str], row: list[str]) -> str:
    cells = []
    for name, value in zip(header, row):
        value = normalize(value)
        if value:
            cells.append("{0}: {1}".format(name, value) if name else value)
    return " | ".join(cells)


def chunk_pages(pages: list[PageResult], max_chars: int = 1000) -> list[Chunk]:
    chunks: list[Chunk] = []
    headings: list[tuple[int, str]] = []   # (level, text) stack, carried across pages

    def section() -> str:
        return " > ".join(text for _, text in headings[-2:])

    for page in pages:
        buffer: list[Element] = []
        ocr = page.method == "ocr"
        size = 0

        def emit(kind: str, text: str, boxes: list[BBox]):
            text = text.strip()
            if text:
                chunks.append(Chunk(page.page_no, len(chunks), kind, section(), text, boxes, ocr))

        def flush(continued: bool = False):
            nonlocal buffer, size
            if buffer:
                text = "\n".join(e.text for e in buffer)
                emit("text", text, [e.bbox for e in buffer])
            buffer, size = [], 0

        for element in page.elements:
            if element.kind == "heading":
                flush()
                level = element.level or 3
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, element.text))
                continue
            if element.kind == "warning":
                flush()
                emit("warning", element.text, [element.bbox])
                continue
            if element.kind == "table":
                flush()
                caption = element.caption or (headings[-1][1] if headings else "")
                for row, box in zip(element.rows, element.row_bboxes or [element.bbox] * len(element.rows)):
                    line = table_row_text(element.header, row)
                    emit("table_row", "{0}\n{1}".format(caption, line) if caption else line, [box])
                continue
            for piece in _split(element.text, max_chars):
                # Keep a numbered step with the steps before it; start a new chunk
                # only when the size limit is reached.
                if size and size + len(piece) > max_chars:
                    flush()
                    if STEP_RE.match(piece):
                        buffer.append(Element("text", "(continued)", element.bbox))
                buffer.append(Element("text", piece, element.bbox))
                size += len(piece)
        flush()
    # A page whose only content is headings still deserves a chunk (title pages).
    pages_with_chunks = {c.page_no for c in chunks}
    for page in pages:
        if page.page_no not in pages_with_chunks:
            titles = [e for e in page.elements if e.kind == "heading"]
            if titles:
                chunks.append(Chunk(page.page_no, len(chunks), "heading", titles[0].text,
                                    "\n".join(e.text for e in titles), [e.bbox for e in titles],
                                    page.method == "ocr"))
    return chunks


def _split(text: str, max_chars: int) -> list[str]:
    """Split oversize paragraphs at sentence boundaries; short ones pass through."""
    if len(text) <= max_chars:
        return [text]
    parts, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if current and len(current) + len(sentence) + 1 > max_chars:
            parts.append(current)
            current = sentence
        else:
            current = (current + " " + sentence).strip()
    if current:
        parts.append(current)
    return parts
