"""OCR for scanned pages through the tesseract command-line tool.

Tesseract is optional. When it is missing, scanned pages are reported with
status ``ocr_unavailable`` rather than silently skipped.
"""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class OcrError(RuntimeError):
    pass


@dataclass
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]  # PDF points
    block: int
    paragraph: int


@dataclass
class OcrResult:
    lines: list[OcrLine]
    confidence: float  # mean word confidence, 0-100


def tesseract_path() -> str | None:
    return shutil.which("tesseract")


def available() -> bool:
    return tesseract_path() is not None


def ocr_png(png: bytes, scale: float, lang: str = "eng", timeout_s: int = 120) -> OcrResult:
    """Run tesseract on a rendered page. ``scale`` converts pixels back to PDF points."""
    exe = tesseract_path()
    if not exe:
        raise OcrError("tesseract is not installed")
    with tempfile.TemporaryDirectory(prefix="eka-ocr-") as tmp:
        image = Path(tmp) / "page.png"
        image.write_bytes(png)
        try:
            proc = subprocess.run([exe, str(image), "stdout", "-l", lang, "--psm", "3", "tsv"],
                                  capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise OcrError("tesseract timed out after {0}s".format(timeout_s)) from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        raise OcrError("tesseract failed: " + (detail[-1] if detail else "exit {0}".format(proc.returncode)))
    return _parse_tsv(proc.stdout.decode("utf-8", "replace"), scale)


def _parse_tsv(tsv: str, scale: float) -> OcrResult:
    lines: dict[tuple[int, int, int], list[dict]] = {}
    confidences: list[float] = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        conf = float(row.get("conf") or -1)
        if conf >= 0:
            confidences.append(conf)
        key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
        lines.setdefault(key, []).append(row)
    out = []
    for (block, par, _), words in sorted(lines.items()):
        x0 = min(int(w["left"]) for w in words)
        y0 = min(int(w["top"]) for w in words)
        x1 = max(int(w["left"]) + int(w["width"]) for w in words)
        y1 = max(int(w["top"]) + int(w["height"]) for w in words)
        out.append(OcrLine(" ".join(w["text"].strip() for w in words),
                           (x0 / scale, y0 / scale, x1 / scale, y1 / scale), block, par))
    return OcrResult(out, sum(confidences) / len(confidences) if confidences else 0.0)
