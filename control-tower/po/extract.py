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

FIELD-LEVEL EVIDENCE. Every field records its raw text, normalised value,
the page it was read on, how that page was read (its text layer or OCR), a
confidence (HIGH: the PDF's own text, one value; LOW: read by OCR) and its
status: FOUND, MISSING, AMBIGUOUS (several different values) or MALFORMED
(a value is printed but is not a well-formed number under NUMBER GRAMMAR —
it is never coerced into a plausible one).

NUMBER GRAMMAR (the document locale, ICUMS / en-GH — one locale, no
guessing between conventions):

    accepted   1234   1234.56   1,234   1,234,567.89   0.5   11.2045
               a sign: -12.50 or (12.50) is negative
    rejected   1.234,56  1234,56  12,50   (comma decimals: another locale)
               1 234.56  (space grouping)   1,2,3  12,34,567  1,23  (grouping
               not in threes)   1.2.3  12.34.56   .5   10O0  1O00  12a
               (a letter inside or against the number)   45%  (a rate)
               01/10/2026  12:30  (a date or a time, not an amount)
    a currency symbol (₵ $ € £) or a code separated by a space may precede
    the number; a letter run into it may not (GHS653 is rejected).
    Decimals are kept as printed (up to 6 places); nothing is rounded here.
"""

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

MIN_NATIVE_CHARS = 120
MAX_PDF_BYTES = int(os.environ.get("PO_MAX_PDF_BYTES") or 40 * 1024 * 1024)
OCR_DPI = 300

FOUND, MISSING, AMBIGUOUS, MALFORMED = "FOUND", "MISSING", "AMBIGUOUS", "MALFORMED"


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
    if not data:
        raise Unreadable("the file is empty (0 bytes)")
    head = bytes(data[:1024]).lstrip()
    if not head.startswith(b"%PDF"):
        kind = "an HTML page" if head[:200].lower().find(b"<html") >= 0 or \
            head[:15].lower().startswith(b"<!doctype") else "not a PDF"
        raise Unreadable("the file is {0}, not a PDF".format(kind))
    if len(data) > MAX_PDF_BYTES:
        raise Unreadable("the file is {0:,} bytes — over the {1:,}-byte limit for a Bill of "
                         "Entry".format(len(data), MAX_PDF_BYTES))
    # A PDF ends with %%EOF (possibly followed by a little whitespace or an
    # incremental-update tail). Without it the download was cut short.
    if b"%%EOF" not in bytes(data[-65536:]):
        raise Unreadable("the PDF is truncated: it has no end-of-file marker (the download "
                         "was cut short)")
    try:
        doc = fitz.open(stream=bytes(data), filetype="pdf")
    except Exception as error:
        raise Unreadable("the PDF could not be opened: {0}".format(str(error)[:120]))
    if getattr(doc, "is_repaired", False):
        doc.close()
        raise Unreadable("the PDF is damaged: it only opens after repair, so its content "
                         "cannot be trusted")
    texts, methods, spans, offset = [], [], [], 0
    with doc:
        if doc.needs_pass:
            raise Unreadable("the PDF is password-protected")
        for number, page in enumerate(doc, 1):
            text = page.get_text("text") or ""
            if len(text.strip()) >= MIN_NATIVE_CHARS:
                chosen, method = text, "text"
            else:
                ocr = _ocr_page(page)
                if ocr and ocr.strip():
                    chosen, method = ocr, "ocr"
                elif text.strip():
                    chosen, method = text, "text"
                else:
                    methods.append("unreadable")
                    continue
            texts.append(chosen)
            methods.append(method)
            # Where each page's text sits in the joined text: every value can
            # name the page it was read on, and how that page was read.
            spans.append({"start": offset, "end": offset + len(chosen), "page": number,
                          "method": method})
            offset += len(chosen) + 1
        pages = len(methods)
    joined = "\n".join(texts)
    if not joined.strip():
        raise Unreadable("no page of the PDF holds readable text ({0} page(s){1})".format(
            pages, "" if _tesseract() else "; OCR is not installed"))
    import hashlib
    return {"text": joined, "pages": pages, "methods": methods, "chars": len(joined),
            "spans": spans,
            "integrity": {"bytes": len(data), "header": "%PDF", "eof_marker": True,
                          "repaired": False, "encrypted": False, "within_size_limit": True,
                          "opened": True, "readable_pages": len(spans),
                          "sha256": hashlib.sha256(bytes(data)).hexdigest(), "verified": True}}


# ── FIELD RULES (DUTY_REQUEST_V1, from boe_to_duty_request.py) ───────────

# A number as it is captured: digits with commas and dots between them, no
# spaces (a space ends it). Whether it is WELL-FORMED is decided by
# to_float() and _token_ok(), never by the capture.
NUM = r"(\d[\d,.]{0,24}\d|\d)"
CURRENCY_BEFORE = set(" \t:(=-$\u20b5\u20ac\u00a3")
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
    """
    The value of a printed number under NUMBER GRAMMAR (see the module
    docstring) — or None when it is not a well-formed number. Never coerced:
    '1,2,3' or '1.234,56' is None, not 123 or 1234.56. '-12.50' and '(12.50)'
    are negative. NOT rounded: 11.2045 stays 11.2045 (float noise only is
    removed at 6 places).
    """
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip()
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.strip("()").lstrip("-").strip()
    if re.fullmatch(r"\d+(\.\d{1,6})?", s):
        pass
    elif re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d{1,6})?", s):
        s = s.replace(",", "")
    else:
        return None
    try:
        value = round(float(s), 6)
    except ValueError:
        return None
    return -value if negative else value


def _token_ok(text, start, end):
    """
    Is the number captured at text[start:end] a whole token? Not when a
    letter or digit is run into it ('1O00', 'GHS653', '12a'), when it is a
    rate ('45%'), or when it starts with a dot ('.5').
    -> (ok, the whole token as printed)
    """
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    token_start, token_end = start, end
    while token_start > 0 and not text[token_start - 1].isspace():
        token_start -= 1
    while token_end < len(text) and not text[token_end].isspace():
        token_end += 1
    token = text[token_start:token_end]
    if before not in CURRENCY_BEFORE and not before.isspace():
        return False, token
    if after.isalnum() or after in "%_/:":
        return False, token
    if after in ",." and end + 1 < len(text) and text[end + 1].isalnum():
        return False, token
    return True, token


def _signed(text, start, end, raw):
    """The raw number with its sign: a minus written against it, or brackets round it."""
    before = text[max(0, start - 1):start]
    after = text[end:end + 1]
    if before == "-":
        return "-" + raw
    if before == "(" and after == ")":
        return "(" + raw + ")"
    return raw


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


def _where(spans, position):
    """(page, method) of a position in the joined text."""
    for span in spans or []:
        if span["start"] <= position <= span["end"]:
            return span["page"], span["method"]
    return None, None


def _all_matches(text, patterns, signed=False, spans=None, number=False):
    """Every candidate: (raw, line, page, method, malformed)."""
    out = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I | re.M):
            raw = m.group(1).strip(" .:-") if not number else m.group(1)
            malformed = False
            if number:
                ok, token = _token_ok(text, m.start(1), m.end(1))
                if not ok:
                    raw, malformed = token, True
            if signed and not malformed:
                raw = _signed(text, m.start(1), m.end(1), raw)
            line = text[text.rfind("\n", 0, m.start()) + 1:text.find("\n", m.end())
                        if text.find("\n", m.end()) >= 0 else len(text)].strip()
            page, method = _where(spans, m.start(1))
            out.append((raw, line[:160], page, method, malformed))
    return out


def _field(name, label, values, normaliser, kind):
    """One typed field from every candidate the rules found."""
    seen, malformed = {}, []
    for raw, line, page, method, bad in values:
        value = None if bad else normaliser(raw)
        if value in (None, ""):
            if kind == "amount" or bad:
                # A value IS printed here, but it is not a well-formed
                # number: kept as evidence, never skipped, never coerced.
                malformed.append({"raw": raw, "line": line, "page": page, "method": method})
            continue
        seen.setdefault(value, (raw, line, page, method))
    base = {"name": name, "label": label, "kind": kind}
    if malformed:
        return dict(base, status=MALFORMED, value=None, evidence=malformed[0]["line"],
                    candidates=malformed + [{"value": v, "raw": r, "line": l, "page": p,
                                             "method": m} for v, (r, l, p, m) in seen.items()],
                    note="printed but not a well-formed number: " +
                         ", ".join("'{0}'".format(x["raw"]) for x in malformed[:4]),
                    page=malformed[0]["page"], method=malformed[0]["method"], confidence="NONE")
    if not seen:
        return dict(base, status=MISSING, value=None, evidence=None, candidates=[],
                    page=None, method=None, confidence="NONE")
    if len(seen) > 1:
        return dict(base, status=AMBIGUOUS, value=None, evidence=None,
                    candidates=[{"value": v, "raw": r, "line": l, "page": p, "method": m}
                                for v, (r, l, p, m) in seen.items()],
                    page=None, method=None, confidence="NONE")
    value, (raw, line, page, method) = next(iter(seen.items()))
    return dict(base, status=FOUND, value=value, raw=raw, evidence=line, candidates=[],
                page=page, method=method, confidence="LOW" if method == "ocr" else "HIGH")


def _vat_lines(text, spans=None):
    """
    The VAT / levy lines: for each labelled line, its RIGHTMOST figure (the
    amount column). That figure must be a well-formed number standing on its
    own; one that is not (3O4,446.44) makes the line MALFORMED — never the
    digits that happen to follow the bad character.
    """
    found, offset = [], 0
    for line in text.splitlines():
        for label, pattern in VAT_LABELS:
            if not re.search(pattern, line, re.I):
                continue
            tokens = [t for t in line.split() if re.search(r"\d", t)]
            page, method = _where(spans, offset)
            entry = {"label": label, "line": line.strip()[:160], "page": page, "method": method}
            if not tokens:
                break
            last = tokens[-1]
            core = last.strip("()").lstrip("-").lstrip("$\u20b5\u20ac\u00a3")
            value = to_float(("-" if last.startswith(("-", "(")) else "") + core)
            if value is None:
                found.append(dict(entry, amount=None, malformed=last))
            else:
                # A zero line is printed content too: kept (G20 shows +0.00),
                # never silently dropped.
                found.append(dict(entry, amount=value))
            break
        offset += len(line) + 1
    return found


def _vat_field(label, lines):
    """
    The VAT/levy block. One line per label; a label printed twice (the same
    amount or not) is AMBIGUOUS — never summed twice, never one silently dropped.
    """
    bad = [l for l in lines if l.get("malformed")]
    if bad:
        return {"name": "vat_lines", "label": label, "kind": "lines", "status": MALFORMED,
                "value": None, "evidence": bad[0]["line"],
                "note": "printed but not a well-formed number: " +
                        ", ".join("{0} '{1}'".format(l["label"], l["malformed"]) for l in bad),
                "candidates": [{"value": None, "raw": l["malformed"], "line": l["line"],
                                "page": l.get("page")} for l in bad],
                "confidence": "NONE"}
    seen = {}
    for l in lines:
        seen.setdefault(l["label"], []).append(l)
    repeated = {k: v for k, v in seen.items() if len(v) > 1}
    if repeated:
        return {"name": "vat_lines", "label": label, "kind": "lines", "status": AMBIGUOUS,
                "value": None, "evidence": None,
                "note": "printed more than once: " + ", ".join(sorted(repeated)),
                "candidates": [{"value": l["amount"], "raw": l["label"], "line": l["line"]}
                               for v in repeated.values() for l in v]}
    ocr = any(l.get("method") == "ocr" for l in lines)
    return {"name": "vat_lines", "label": label, "kind": "lines",
            "status": FOUND if lines else MISSING, "value": lines or None,
            "evidence": "; ".join(l["line"] for l in lines[:6]) or None, "candidates": [],
            "pages": sorted({l.get("page") for l in lines if l.get("page")}),
            "method": "ocr" if ocr else ("text" if lines else None),
            "confidence": ("LOW" if ocr else "HIGH") if lines else "NONE"}


INVOICE_NO = re.compile(
    r"(?<![a-z+])(?:commercial\s+|supplier(?:'s)?\s+)?invoice\s*(?:no\.?|number|nr\.?|#)\s*"
    r"[:.\-]?[ \t]*(\S*)", re.I)
INVOICE_VALUE = re.compile(r"[A-Z0-9][A-Z0-9\-/]{2,29}", re.I)
# What may follow the value on the same line: nothing, a gap of 2+ spaces,
# or another label ("Date: …").
INVOICE_AFTER_OK = re.compile(r"\s*$|\s{2,}|\s+[A-Za-z][A-Za-z .]{0,30}:")


def printed_invoice_no(text, spans=None):
    """
    The supplier invoice No. as the Bill of Entry prints it, under an
    explicit "Invoice No." / "Invoice Number" label — never "Invoice Value",
    never a line naming UNA+, never a guess.

        {"value", "evidence", "page"}   exactly one well-formed value
        {"candidates": [...]}           several different values (none chosen)
        {"malformed": [...]}            the label is printed but its value is
                                        not a clean token: '2600 005261',
                                        'INV#12', 'N/A', a value over 30
                                        characters — never truncated
        {}                              no explicit Invoice No. label at all
    """
    found, malformed, offset = [], [], 0
    for line in (text or "").splitlines():
        if re.search(r"una\+", line, re.I):
            offset += len(line) + 1
            continue
        for m in INVOICE_NO.finditer(line):
            token = m.group(1).strip().rstrip(".,;")
            page, _method = _where(spans, offset + m.start())
            shown = " ".join(line.split())[:120]
            whole = INVOICE_VALUE.fullmatch(token or "")
            after = line[m.end(1):]
            if not token or not whole or not re.search(r"\d", token) or \
                    not INVOICE_AFTER_OK.match(after):
                malformed.append({"raw": (token + after.split("  ")[0]).strip()[:60],
                                  "line": shown, "page": page})
                continue
            value = token.rstrip("-/").upper()
            if value not in [v for v, _e, _p in found]:
                found.append((value, shown, page))
        offset += len(line) + 1
    if malformed:
        return {"malformed": malformed[:5],
                "candidates": [v for v, _e, _p in found][:5]}
    if len(found) == 1:
        return {"value": found[0][0], "evidence": "'{0}'".format(found[0][1]),
                "page": found[0][2]}
    if found:
        return {"candidates": [v for v, _e, _p in found][:5]}
    return {}


def extract(text, doctype, spans=None):
    """Every PDF field of the document type, typed, with its evidence. {name: field}."""
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
        fields[name] = _field(name, labels[name], _all_matches(text, patterns, spans=spans),
                              norm, kind)
        if name == "document_date" and fields[name]["status"] == MISSING:
            unreadable = _all_matches(text, patterns, spans=spans)
            if unreadable:
                fields[name]["note"] = "a date was printed ({0}) but could not be read for " \
                                       "sure".format(unreadable[0][0])
    for name, patterns in AMOUNT_RULES.items():
        if name in labels:
            fields[name] = _field(name, labels[name],
                                  _all_matches(text, patterns, signed=True, spans=spans,
                                               number=True), to_float, "amount")
    if "vat_lines" in labels:
        fields["vat_lines"] = _vat_field(labels["vat_lines"], _vat_lines(text, spans))
    return fields


def looks_like(fields):
    """True when the text is plausibly this document type at all."""
    hits = sum(1 for n in ("document_number", "bl_awb", "duty_amount_ghs", "cif_usd")
               if fields.get(n, {}).get("status") != MISSING)
    return hits >= 2
