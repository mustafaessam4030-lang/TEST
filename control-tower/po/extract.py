"""
PDF → text → typed fields. Reading only; nothing here writes a template.

Text: the PDF's own text layer, page by page. A page with almost no text is a
scan: it is rendered at 300 dpi and read with Tesseract when Tesseract is
installed (TESSERACT_CMD or on PATH). With neither, that page is reported
unreadable — never guessed.

Fields: the field rules of the earlier BOE automation (boe_to_duty_request.py,
written against standard ICUMS wording), with three changes that matter here:

  * every match is collected, not only the first. Two different values for
    one field is AMBIGUOUS, and an ambiguous field never fills a template;
  * a field that is not found is MISSING with no value — the old script put
    today's date in for an unreadable declaration date; this never does;
  * each value keeps the line it was read from, as evidence.
"""

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

MIN_NATIVE_CHARS = 120
OCR_DPI = 300

FOUND, MISSING, AMBIGUOUS = "FOUND", "MISSING", "AMBIGUOUS"


class Unreadable(Exception):
    """The PDF could not be opened, or holds no readable text."""


# ── TEXT ─────────────────────────────────────────────────────────────────

def _tesseract():
    exe = os.environ.get("TESSERACT_CMD") or "tesseract"
    return exe if Path(exe).is_file() else shutil.which(exe)


def _ocr_page(page):
    exe = _tesseract()
    if not exe:
        return None
    pix = page.get_pixmap(dpi=OCR_DPI)
    with tempfile.TemporaryDirectory() as tmp:
        image = Path(tmp) / "page.png"
        pix.save(str(image))
        try:
            out = subprocess.run([exe, str(image), "stdout", "--psm", "6"],
                                 capture_output=True, text=True, timeout=120)
        except Exception:
            return None
    return out.stdout if out.returncode == 0 else None


def read_pdf(data):
    """
    {"text", "pages", "methods": ["text"|"ocr"|"unreadable", ...], "chars"}.
    Raises Unreadable when the file is not a PDF or no page yields text.
    """
    try:
        import fitz                                   # PyMuPDF
    except Exception as error:                        # pragma: no cover
        raise Unreadable("PyMuPDF is not installed: {0}".format(error))
    if not data or not bytes(data[:5]).startswith(b"%PDF"):
        raise Unreadable("the file is not a PDF")
    try:
        doc = fitz.open(stream=bytes(data), filetype="pdf")
    except Exception as error:
        raise Unreadable("the PDF could not be opened: {0}".format(str(error)[:120]))
    texts, methods = [], []
    with doc:
        if doc.needs_pass:
            raise Unreadable("the PDF is password-protected")
        for page in doc:
            text = page.get_text("text") or ""
            if len(text.strip()) >= MIN_NATIVE_CHARS:
                texts.append(text)
                methods.append("text")
                continue
            ocr = _ocr_page(page)
            if ocr and ocr.strip():
                texts.append(ocr)
                methods.append("ocr")
            elif text.strip():
                texts.append(text)
                methods.append("text")
            else:
                methods.append("unreadable")
        pages = len(methods)
    joined = "\n".join(texts)
    if not joined.strip():
        raise Unreadable("no page of the PDF holds readable text ({0} page(s){1})".format(
            pages, "" if _tesseract() else "; OCR is not installed"))
    return {"text": joined, "pages": pages, "methods": methods, "chars": len(joined)}


# ── FIELD RULES (DUTY_REQUEST_V1, from boe_to_duty_request.py) ───────────

NUM = r"([\d][\d,\. ]{0,20}\d|\d)"
GAP = r"[^\d\n]{0,80}?"

TEXT_RULES = {
    "user_reference": [
        r"user\s*ref(?:erence)?\s*[:.\-]?\s*([A-Z0-9][A-Z0-9\-/_]{3,30})",
    ],
    "document_number": [
        r"(?:bill\s+of\s+entry|b\.?o\.?e\.?)\s*(?:no|number|#)?\s*[:.\-]?\s*([0-9]{6,}\s*/?\s*[0-9]{0,4})",
        r"declaration\s*(?:no|number)?\s*[:.\-]?\s*([0-9]{6,}\s*/?\s*[0-9]{0,4})",
    ],
    "bl_awb": [
        r"(?:bl\s*/?\s*awb|b/l|awb|airway\s*bill|master\s*bill)\s*(?:no|number|#)?\s*[:.\-]?\s*"
        r"([A-Z0-9][A-Z0-9\-/]{4,25})",
    ],
    "document_date": [
        r"(?:date\s+of\s+(?:entry|declaration|assessment)|assessment\s+date)\s*[:.\-]?\s*"
        r"([0-9]{1,4}[\-/.\s][A-Za-z0-9]{1,9}[\-/.\s][0-9]{2,4})",
        r"^\s*date\s*[:.\-]?\s*([0-9]{1,4}[\-/.\s][A-Za-z0-9]{1,9}[\-/.\s][0-9]{2,4})",
    ],
}

AMOUNT_RULES = {
    "cif_usd": [
        rf"(?:total\s+invoice\s+value|invoice\s+value|c\.?i\.?f\.?|c\.?f\.?r\.?){GAP}{NUM}",
    ],
    "exchange_rate": [
        rf"(?:exchange\s+rate|rate\s+of\s+exchange|bog\s+rate|fx\s+rate){GAP}{NUM}",
    ],
    "duty_amount_ghs": [
        rf"(?:total\s+duty(?:\s+(?:and|&)\s+(?:levies|taxes))?|total\s+amount\s+payable|"
        rf"grand\s+total|total\s+payable|amount\s+payable){GAP}{NUM}",
    ],
    "stated_import_duty": [
        rf"^\s*import\s+duty(?!\s*(?:and|&)){GAP}{NUM}",
    ],
}

VAT_LABELS = [
    ("Import VAT", r"import\s+v\.?a\.?t"),
    ("Network Charge VAT", r"net\s*w?o?r?k\s+charge\s+v\.?a\.?t(?!\s+fund)"),
    ("Import NHIL", r"(?:import\s+)?n\.?h\.?i\.?l"),
    ("GETFund Import", r"(?:ghana\s+education(?:al)?\s+trust|get\s*fund|getfund)"),
    ("Network Charge VAT Levy", r"net\s*w?o?r?k\s+charge\s+v\.?a\.?t\s+fund\s+levy"),
]

DATE_FORMATS = ["%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d", "%d/%b/%Y", "%d/%B/%Y"]


def to_float(raw):
    """'1,234.56' / '1.234,56' / '1 234.56' -> 1234.56; None when not a number."""
    if not raw:
        return None
    s = str(raw).replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if re.fullmatch(r"\d+,\d{2}", s) else s.replace(",", "")
    s = re.sub(r"[^\d.]", "", s)
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def parse_date(raw):
    """A date as written, to ISO (YYYY-MM-DD); None when it cannot be read for sure."""
    s = re.sub(r"[\s.\-]+", "/", str(raw or "").strip())
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normal_reference(value):
    """BL/AWB as compared: letters and digits only, upper case."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def _all_matches(text, patterns):
    out = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.M):
            raw = m.group(1).strip(" .:-")
            line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.end())
                        if text.find("\n", m.end()) >= 0 else len(text)].strip()
            out.append((raw, line[:160]))
    return out


def _field(name, label, values, normaliser, kind):
    """One typed field from every candidate the rules found."""
    seen = {}
    for raw, line in values:
        value = normaliser(raw)
        if value in (None, ""):
            continue
        seen.setdefault(value if not isinstance(value, str) else value, (raw, line))
    if not seen:
        return {"name": name, "label": label, "kind": kind, "status": MISSING, "value": None,
                "evidence": None, "candidates": []}
    if len(seen) > 1:
        return {"name": name, "label": label, "kind": kind, "status": AMBIGUOUS, "value": None,
                "evidence": None,
                "candidates": [{"value": v, "raw": r, "line": l} for v, (r, l) in seen.items()]}
    value, (raw, line) = next(iter(seen.items()))
    return {"name": name, "label": label, "kind": kind, "status": FOUND, "value": value,
            "raw": raw, "evidence": line, "candidates": []}


def _vat_lines(text):
    found = []
    for line in text.splitlines():
        for label, pattern in VAT_LABELS:
            if not re.search(pattern, line, re.I):
                continue
            amounts = re.findall(NUM, line)
            value = to_float(amounts[-1]) if amounts else None
            if value:
                found.append({"label": label, "amount": value, "line": line.strip()[:160]})
            break
    return found


INVOICE_NO = re.compile(
    r"(?<![a-z+])(?:commercial\s+|supplier(?:'s)?\s+)?invoice\s*(?:no\.?|number|nr\.?|#)\s*[:.\-]?\s*"
    r"([A-Z0-9][A-Z0-9\-/]{2,29})", re.I)


def printed_invoice_no(text):
    """
    The supplier invoice No. as the document prints it, under an explicit
    "Invoice No." / "Invoice Number" label (never "invoice value", never
    "UNA+ Invoice Number"). {"value", "evidence"} for one distinct value,
    {"candidates": [...]} for several, {} for none.
    """
    found = []
    for line in (text or "").splitlines():
        if re.search(r"una\+", line, re.I):
            continue
        for m in INVOICE_NO.finditer(line):
            value = m.group(1).strip().rstrip(".-/").upper()
            if re.search(r"\d", value) and value not in [v for v, _e in found]:
                found.append((value, " ".join(line.split())[:120]))
    if len(found) == 1:
        return {"value": found[0][0], "evidence": "'{0}'".format(found[0][1])}
    if found:
        return {"candidates": [v for v, _e in found][:5]}
    return {}


def extract(text, doctype):
    """Every PDF field of the document type, typed. {name: field}."""
    labels = {f["name"]: f["label"] for f in doctype["fields"]}
    fields = {}
    for name, patterns in TEXT_RULES.items():
        if name not in labels:
            continue
        if name == "document_number":
            norm = lambda raw: re.sub(r"\s*/\s*", " / ", raw.strip())      # noqa: E731
        elif name == "document_date":
            norm = parse_date
        elif name == "bl_awb":
            norm = lambda raw: raw.strip().upper()                          # noqa: E731
        else:
            norm = lambda raw: raw.strip()                                  # noqa: E731
        kind = "date" if name == "document_date" else "text"
        fields[name] = _field(name, labels[name], _all_matches(text, patterns), norm, kind)
        if name == "document_date" and fields[name]["status"] == MISSING:
            unreadable = _all_matches(text, patterns)
            if unreadable:
                fields[name]["note"] = "a date was printed ({0}) but could not be read for " \
                                       "sure".format(unreadable[0][0])
    for name, patterns in AMOUNT_RULES.items():
        if name in labels:
            fields[name] = _field(name, labels[name], _all_matches(text, patterns), to_float,
                                  "amount")
    if "vat_lines" in labels:
        lines = _vat_lines(text)
        fields["vat_lines"] = {
            "name": "vat_lines", "label": labels["vat_lines"], "kind": "lines",
            "status": FOUND if lines else MISSING, "value": lines or None,
            "evidence": "; ".join(l["line"] for l in lines[:6]) or None, "candidates": []}
    return fields


def looks_like(fields):
    """True when the text is plausibly this document type at all."""
    hits = sum(1 for n in ("document_number", "bl_awb", "duty_amount_ghs", "cif_usd")
               if fields.get(n, {}).get("status") != MISSING)
    return hits >= 2
