"""Figures in PDFs: find them at ingest, search them, render them from the original page.

Two kinds are found:

* ``image``   embedded raster images (photographs, scanned drawings, screenshots), excluding
              full-page scans, which are pages rather than figures;
* ``drawing`` clusters of vector paths (schematics, diagrams, charts), excluding table grids
              and simple boxes such as framed warnings.

Each figure keeps its document, page, bounding box, caption ("Figure 1 ..." near the
figure, when there is one) and the text labels drawn inside it, so it can be searched
and shown with a citation to its page. Rendering always crops the original page, so
what the user sees is exactly what the document contains.
"""
from __future__ import annotations

import logging
import re

import pymupdf

from .textutil import content_terms, normalize

log = logging.getLogger(__name__)

CAPTION = re.compile(r"^\s*(figure|fig\.|diagram|schematic|photo|illustration|drawing)\s*\d*", re.I)
MIN_PATHS = 8              # a vector cluster needs this many paths to count as a drawing
MIN_SIDE = 60              # points
MAX_PAGE_SHARE = 0.6       # larger images are page scans, not figures


def find_figures(data: bytes) -> list[dict]:
    """All figures in a PDF: [{page_no, seq, kind, bbox, caption, labels}]."""
    out = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # the extractor reports unreadable files; figures are optional
        log.info("figure scan skipped: %s", exc)
        return out
    for page in doc:
        try:
            out.extend(_page_figures(page))
        except Exception as exc:  # one odd page never stops the document
            log.info("figure scan failed on page %d: %s", page.number + 1, exc)
    return out


def _page_figures(page: pymupdf.Page) -> list[dict]:
    page_area = abs(page.rect)
    candidates: list[tuple[str, pymupdf.Rect]] = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"]) & page.rect
        if r.is_empty or r.width < MIN_SIDE or r.height < MIN_SIDE or abs(r) > MAX_PAGE_SHARE * page_area:
            continue
        candidates.append(("image", r))
    paths = page.get_drawings()
    if len(paths) >= MIN_PATHS:
        try:
            tables = [pymupdf.Rect(t.bbox) for t in page.find_tables().tables]
        except Exception:
            tables = []
        for r in page.cluster_drawings(drawings=paths, x_tolerance=6, y_tolerance=6):
            if r.width < MIN_SIDE * 1.5 or r.height < MIN_SIDE:
                continue
            grown = r + (-1, -1, 1, 1)
            inside = sum(1 for p in paths if pymupdf.Rect(p["rect"]) in grown)
            if inside < MIN_PATHS:
                continue
            if any(abs(r & t) > 0.4 * abs(r) for t in tables):
                continue                      # a table grid; the extractor reads tables as rows
            candidates.append(("drawing", r))
    candidates = _dedupe(candidates)
    if not candidates:
        return []
    blocks = [(pymupdf.Rect(b[:4]), normalize(b[4])) for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    figures = []
    for seq, (kind, r) in enumerate(sorted(candidates, key=lambda c: (c[1].y0, c[1].x0))):
        labels = " ".join(t for b, t in blocks if b.intersects(r) and not CAPTION.match(t))
        figures.append({"page_no": page.number + 1, "seq": seq, "kind": kind,
                        "bbox": [round(v, 1) for v in r], "caption": _caption(r, blocks), "labels": labels[:1000]})
    return figures


def _dedupe(candidates):
    """Drop a candidate mostly inside a bigger one (an image inside a framed drawing)."""
    keep = []
    for kind, r in sorted(candidates, key=lambda c: -abs(c[1])):
        if any(abs(r & k) > 0.8 * abs(r) for _, k in keep):
            continue
        keep.append((kind, r))
    return keep


def _caption(r: pymupdf.Rect, blocks) -> str:
    """'Figure 1 ...' just below (or above) the figure; else the nearest line below it."""
    below = sorted(((b.y0 - r.y1, t) for b, t in blocks if 0 <= b.y0 - r.y1 <= 60 and b.x0 < r.x1 and b.x1 > r.x0))
    above = sorted(((r.y0 - b.y1, t) for b, t in blocks if 0 <= r.y0 - b.y1 <= 40 and b.x0 < r.x1 and b.x1 > r.x0))
    for _, text in below + above:
        if CAPTION.match(text):
            return text[:300]
    if below and below[0][0] <= 24 and len(below[0][1]) <= 160:
        return below[0][1]
    return ""


def render(pdf_path, page_no: int, bbox: list[float], dpi: int = 160) -> bytes:
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_no - 1]
        clip = (pymupdf.Rect(bbox) + (-6, -6, 6, 6)) & page.rect
        return page.get_pixmap(clip=clip, dpi=dpi).tobytes("png")


def search_text(figure: dict, doc: dict) -> str:
    return " ".join((figure.get("caption") or "", figure.get("labels") or "", figure.get("section") or "",
                     doc.get("title") or "", doc.get("equipment") or ""))


def figure_terms(figure: dict, doc: dict) -> set[str]:
    return set(content_terms(search_text(figure, doc)))
