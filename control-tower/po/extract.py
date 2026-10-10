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


def _page_words(page):
    """The page's words with their boxes: (x0, y0, x1, y1, text). A word never
    spans two text runs (spans), so a value printed right against a label
    ("Import" | "88") stays two words."""
    out = []
    try:
        blocks = page.get_text("rawdict")["blocks"]
    except Exception:                                 # pragma: no cover
        return [tuple(w[:5]) for w in page.get_text("words") if str(w[4]).strip()]
    for block in blocks:
        for line in block.get("lines") or []:
            for span in line.get("spans") or []:
                token, box = "", None
                for ch in span.get("chars") or []:
                    c = ch.get("c") or ""
                    if c.isspace():
                        if token:
                            out.append(tuple(box) + (token,))
                        token, box = "", None
                        continue
                    b = ch["bbox"]
                    box = list(b) if box is None else [min(box[0], b[0]), min(box[1], b[1]),
                                                       max(box[2], b[2]), max(box[3], b[3])]
                    token += c
                if token:
                    out.append(tuple(box) + (token,))
    return out


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
    texts, methods, spans, offset, words = [], [], [], 0, []
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
            if method == "text":
                # Every word with its position: the ICUMS form is boxed, and
                # its text layer is not in reading order (see read_layout).
                words += [(number,) + w for w in _page_words(page)]
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
            "spans": spans, "words": words,
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

# The VAT / levy block (G20): the six lines the Duty Template adds up —
# =Import VAT + Network Charge VAT + Import NHIL + GETFund Import + Network
# Charge NHIL + Network Charge GET Fund Levy. Most specific first: a line is
# given the first label it matches.
VAT_LABELS = [
    ("Network Charge GET Fund Levy",
     r"net\s*w?o?r?k\s+charge\s+(?:get\s*fund|v\.?a\.?t\s+fund)\s+levy"),
    ("Network Charge NHIL", r"net\s*w?o?r?k\s+charge\s+n\.?h\.?i\.?l"),
    ("Network Charge VAT", r"net\s*w?o?r?k\s+charge\s+v\.?a\.?t(?!\s+fund)"),
    ("Import VAT", r"import\s+v\.?a\.?t"),
    ("Import NHIL", r"(?:import\s+)?n\.?h\.?i\.?l"),
    ("GETFund Import", r"(?:ghana\s+education(?:al)?\s+trust|get\s*fund|getfund)"),
]
VAT_ORDER = ["Import VAT", "Network Charge VAT", "Import NHIL", "GETFund Import",
             "Network Charge NHIL", "Network Charge GET Fund Levy"]

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


def resolve_by_identifier(field, identifier):
    """
    An AMBIGUOUS Declaration (BOE) No. settled by evidence from a second
    source: eHub's own number for this Bill of Entry (the identifier read from
    its document name). Settled only when EXACTLY ONE printed candidate has
    that number as its declaration number (the part before any "/ NN"
    suffix) — then that printed value is used, and every other candidate is
    kept as evidence. Two candidates with the same number but different
    suffixes, or none matching, stay AMBIGUOUS: a person decides at review.
    Nothing is typed in, and a value not printed in the document is never used.
    """
    if not isinstance(field, dict) or field.get("status") != AMBIGUOUS:
        return field
    wanted = re.sub(r"\D", "", str(identifier or ""))
    candidates = field.get("candidates") or []

    def number(c):
        m = DECLARATION_FORM.fullmatch(str(c.get("value") or "").strip())
        return m.group(1) if m else re.sub(r"\D", "", str(c.get("value") or ""))

    if len(wanted) < 6:
        return dict(field, note="ambiguous, and eHub gave no document number to check against")
    matching = [c for c in candidates if number(c) == wanted]
    if len(matching) != 1:
        return dict(field, note=(
            "ambiguous: {0} printed values carry eHub's number {1} with different "
            "suffixes".format(len(matching), identifier) if matching else
            "ambiguous: none of the printed values is eHub's number {0}".format(identifier)))
    chosen = matching[0]
    method = chosen.get("method")
    return dict(field, status=FOUND, value=chosen["value"], raw=chosen.get("raw"),
                evidence=chosen.get("line"), page=chosen.get("page"), method=method,
                confidence="LOW" if method == "ocr" else "HIGH", candidates=[],
                resolution={"rule": "the only printed value matching eHub's document number",
                            "identifier": identifier,
                            "chosen": {k: chosen.get(k) for k in ("value", "page", "line")},
                            "rejected": [{k: c.get(k) for k in ("value", "page", "line")}
                                         for c in candidates if c is not chosen]})


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


# ── THE ICUMS FORM, READ BY POSITION ─────────────────────────────────────
#
# A real ICUMS Bill of Entry is a boxed form. A box's label sits at its
# top-left; its value is beside the label (the header block: "BL/AWB :
# J552493") or on the row below it, inside the box ("13 Total Invoice Fcy" /
# "174,071.09"). The PDF's text layer is NOT in reading order — labels come
# first, values after — so the line rules above, which take what follows a
# label in the text, read the wrong value or none (the real failure: only the
# user reference was found, and "BL/AWB" took box 6's reference number).
#
# Here every word keeps its position, and each value is read where the form
# prints it:
#
#   beside  the first group of words to the right of the label on its row
#   below   the next row down, within the label's column — from the label's
#           box number to the next label on its row
#   table   B ACCOUNTING DETAILS: one row per tax — name, code, exempted,
#           amount payable — down to its "Total" row. The per-item table
#           (box 40) on the left is never read.
#
# A value is accepted only when it has the field's form (a date, a number
# under NUMBER GRAMMAR, a reference); a number that is printed but not
# well-formed is MALFORMED, never coerced. Nothing is read from the text
# order, and nothing from the line rules is mixed in.

ICUMS_LABELS = {
    "document_number": r"bill\s+of\s+entry\s*\(\s*boe\s*\)\s*no\b\.?\s*:?",
    "bl_awb": r"\bbl\s*/\s*awb\b\s*(?:no\b\.?)?\s*:?",
    "date": r"\bdate\b\s*:?",
    "user_reference": r"\buser\s+ref(?:erence)?\b\.?\s*:?",
    "doc_status": r"\bdoc(?:ument)?\s+status\s*:?",
    "delivery_terms": r"\bdelivery\s+terms\s*(?:&|and)\s*place\b",
    "invoice_fcy": r"\btotal\s+invoice\s+fcy\b",
    "curr_code": r"\bcurr(?:ency)?\s+code\b",
    "rate": r"\brate\s+of\s+x?\s*change\b",
    "fob_ncy": r"\bfob\s+ncy\s*\(\s*import\s*/\s*export\s*\)",
}
# The six VAT / levy lines of the Duty Template's G20, as ICUMS names them in
# B ACCOUNTING DETAILS (the whole name, nothing more), and the ICUMS tax code
# each is printed with: a name and its code must agree, or the block stops.
ICUMS_VAT_CODES = {"Import VAT": "02", "Network Charge VAT": "33", "Import NHIL": "47",
                   "GETFund Import": "88", "Network Charge NHIL": "48",
                   "Network Charge GET Fund Levy": "89"}
ICUMS_VAT = [
    ("Import VAT", r"import\s+vat"),
    ("Network Charge VAT", r"network\s+charge\s+vat"),
    ("Import NHIL", r"import\s+nhil"),
    ("GETFund Import", r"ghana\s+education\s+trust\s*\(\s*get\s*\)\s*fund\s+import|"
                       r"get\s*fund\s+import"),
    ("Network Charge NHIL", r"network\s+charge\s+nhil"),
    ("Network Charge GET Fund Levy", r"network\s+charge\s+get\s*fund\s+levy"),
]
REFERENCE_FORM = re.compile(r"[A-Z0-9][A-Z0-9\-/_]{3,30}", re.I)
DECLARATION_FORM = re.compile(r"(\d{6,})(?:\s*/\s*(\d{1,4}))?")


def _rows(words):
    """The words of each page as printed rows, top to bottom, each left to right.
    -> [{"page", "words": [(x0, y0, x1, y1, text)], "y", "h"}]"""
    rows = []
    for page in sorted({w[0] for w in words}):
        on_page = sorted((w[1:] for w in words if w[0] == page),
                         key=lambda w: ((w[1] + w[3]) / 2, w[0]))
        current, centre = [], None
        for w in on_page:
            mid, h = (w[1] + w[3]) / 2, max(w[3] - w[1], 1.0)
            if current and abs(mid - centre) > 0.4 * h:
                rows.append(current)
                current = []
            if not current:
                centre = mid
            current.append(w)
        if current:
            rows.append(current)
        for i in range(len(rows)):
            if isinstance(rows[i], list):
                ws = sorted(rows[i], key=lambda w: w[0])
                heights = sorted(w[3] - w[1] for w in ws)
                rows[i] = {"page": page, "words": ws,
                           "y": sum((w[1] + w[3]) / 2 for w in ws) / len(ws),
                           "top": min(w[1] for w in ws), "bottom": max(w[3] for w in ws),
                           "h": max(heights[len(heights) // 2], 1.0)}
    return rows


def _row_text(row, start=0, end=None):
    return " ".join(w[4] for w in row["words"][start:end])


def _clusters(row, start=0):
    """Groups of words on a row, split where the gap is wider than a word space."""
    out, current = [], []
    for i in range(start, len(row["words"])):
        w = row["words"][i]
        if current and w[0] - row["words"][i - 1][2] > 1.2 * row["h"]:
            out.append(current)
            current = []
        current.append(i)
    if current:
        out.append(current)
    return out


def _find(rows, pattern):
    """Every place a label is printed: {"row", "first", "last", "x0", "x1", "box_x0"}."""
    hits = []
    for r, row in enumerate(rows):
        text, bounds, at = "", [], 0
        for w in row["words"]:
            bounds.append((at, at + len(w[4])))
            text += w[4] + " "
            at += len(w[4]) + 1
        for m in re.finditer(pattern, text, re.I):
            covered = [i for i, (a, b) in enumerate(bounds) if a < m.end() and b > m.start()]
            if not covered:
                continue
            first, last = covered[0], covered[-1]
            words = row["words"]
            box_x0 = words[first][0]
            # The box number printed before the label ("13  Total Invoice Fcy").
            if first > 0 and re.fullmatch(r"\d{1,2}", words[first - 1][4]) and \
                    words[first][0] - words[first - 1][2] <= 2.5 * row["h"]:
                box_x0 = words[first - 1][0]
            hits.append({"row": r, "first": first, "last": last, "x0": words[first][0],
                         "x1": words[last][2], "box_x0": box_x0, "page": row["page"],
                         "label": _row_text(row, first, last + 1)})
    return hits


def _beside(rows, hit):
    """The first group of words right of the label on its row (':' skipped)."""
    row = rows[hit["row"]]
    start = hit["last"] + 1
    while start < len(row["words"]) and re.fullmatch(r"[:.\-]+", row["words"][start][4]):
        start += 1
    if start >= len(row["words"]):
        return None
    group = _clusters(row, start)[0]
    return _row_text(row, group[0], group[-1] + 1)


def _below(rows, hit):
    """The value printed under the label, inside its column: from the label's box
    number to the next label on the label's row."""
    row = rows[hit["row"]]
    tol = 0.5 * row["h"]
    left = hit["box_x0"] - tol
    right = float("inf")
    after = [i for i in range(hit["last"] + 1, len(row["words"]))
             if row["words"][i][0] - row["words"][i - 1][2] > 1.2 * row["h"]]
    if after:
        right = row["words"][after[0]][0] - tol
    for nxt in rows[hit["row"] + 1:]:
        if nxt["page"] != row["page"] or nxt["top"] > row["bottom"] + 2.5 * row["h"]:
            break
        inside = [w for w in nxt["words"] if left <= (w[0] + w[2]) / 2 < right]
        if inside:
            return " ".join(w[4] for w in inside)
    return None


def _candidate(raw, hit, how, rows):
    row = rows[hit["row"]]
    line = "{0} → {1}  ({2} the label)".format(hit["label"], raw, how)
    return raw, line[:160], row["page"], "text"


def _layout_value(rows, hits, form, ways=("beside", "below")):
    """Candidates [(raw, line, page, method, malformed)] for one field: the first
    way that yields a value of the field's form, per printed label."""
    out = []
    for hit in hits:
        for how in ways:
            raw = _beside(rows, hit) if how == "beside" else _below(rows, hit)
            if not raw:
                continue
            verdict = form(raw)
            if verdict == "skip":
                continue
            out.append(_candidate(raw, hit, how, rows) + (verdict == "malformed",))
            break
    return out


def _amount_form(raw):
    """'ok' for a well-formed number, 'malformed' for digits that are not one,
    'skip' for no digits at all (the box is empty: a label was read)."""
    token = raw.strip()
    if not re.search(r"\d", token):
        return "skip"
    if " " in token or to_float(token) is None:
        return "malformed"
    return "ok"


def _date_form(raw):
    return "ok" if parse_date(raw) else "skip"


def _declaration_form(raw):
    return "ok" if DECLARATION_FORM.fullmatch(raw.strip()) else "skip"


def _reference_form(raw):
    token = raw.strip()
    return "ok" if REFERENCE_FORM.fullmatch(token) and re.search(r"\d", token) else "skip"


def _text_form(raw):
    return "ok" if re.search(r"[A-Za-z]", raw) else "skip"


def _currency_form(raw):
    return "ok" if re.fullmatch(r"[A-Z]{3}", raw.strip()) else "skip"


def _tax_tables(rows):
    """B ACCOUNTING DETAILS, per page: {"lines": [...], "total": {...} | None,
    "malformed": [...]} — read from its "Taxes  Code  Exempted/Suspended"
    heading to its Total row, column by column: the name left of the Code
    column, the tax code in it, the amounts right of it (the last is Amount
    Payable). A row that does not fit the columns is MALFORMED — the table is
    never cut short or read around it."""
    tables = []
    for hit in _find(rows, r"\btaxes\b"):
        head = rows[hit["row"]]
        code_word = next((w for w in head["words"] if w[4].lower() == "code" and
                          w[0] > hit["x1"]), None)
        if code_word is None:
            continue
        tol = 0.5 * head["h"]
        left = hit["x0"] - head["h"]
        code_x = []
        amounts_left = next((w[0] - tol for w in head["words"] if w[0] > code_word[2] and
                             re.search(r"exempt|amount", w[4], re.I)), code_word[2] + tol)
        lines, total, malformed = [], None, []
        for row in rows[hit["row"] + 1:]:
            if row["page"] != head["page"]:
                break
            words = [w for w in row["words"] if w[0] >= left]
            if not words:
                continue
            mid = lambda w: (w[0] + w[2]) / 2                       # noqa: E731
            amounts = [w[4] for w in words if mid(w) >= amounts_left]
            rest = [w for w in words if mid(w) < amounts_left]
            line = " ".join(w[4] for w in words)[:160]
            if [w[4].lower() for w in rest] == ["total"]:
                value = to_float(amounts[-1]) if amounts else None
                total = {"raw": amounts[-1] if amounts else None, "amount": value,
                         "line": line, "page": row["page"]}
                if amounts and value is None:
                    malformed.append({"raw": amounts[-1], "line": line, "page": row["page"]})
                break
            if rest and not amounts and lines and not re.search(r"\d", line):
                lines[-1]["label"] += " " + line                  # a name printed on two rows
                continue
            if not amounts and not any(re.fullmatch(r"\d{2,3}", w[4]) for w in rest):
                if lines:
                    break                                         # past the table
                continue
            # The code: the last word before the amounts, 2–3 digits, clear of
            # the name (not printed over it).
            fits = len(rest) >= 2 and re.fullmatch(r"\d{2,3}", rest[-1][4]) and \
                rest[-2][2] <= rest[-1][0] + 0.5 and 1 <= len(amounts) <= 2
            if not fits:
                malformed.append({"raw": line, "line": line, "page": row["page"],
                                  "why": "the row does not fit the columns name / code / "
                                         "amounts"})
                lines.append({"label": " ".join(w[4] for w in rest), "code": None,
                              "amount": None, "malformed": line, "line": line,
                              "page": row["page"], "method": "text"})
                continue
            name, code = [w[4] for w in rest[:-1]], [rest[-1][4]]
            code_x.append(rest[-1][0])
            value = to_float(amounts[-1])
            entry = {"label": " ".join(name), "code": code[0],
                     "exempted": to_float(amounts[0]) if len(amounts) > 1 else None,
                     "amount": value, "line": line, "page": row["page"], "method": "text"}
            if value is None:
                entry["malformed"] = amounts[-1]
                malformed.append({"raw": amounts[-1], "line": line, "page": row["page"]})
            lines.append(entry)
        # Every tax code stands in one column.
        if code_x:
            centre = sorted(code_x)[len(code_x) // 2]
            for l in lines:
                if l.get("code") and not l.get("malformed"):
                    x = code_x.pop(0)
                    if abs(x - centre) > 1.5 * head["h"]:
                        l["malformed"] = l["line"]
                        malformed.append({"raw": l["line"], "line": l["line"],
                                          "page": l["page"], "why": "the code is out of "
                                                                    "the Code column"})
        if lines or total:
            tables.append({"lines": lines, "total": total, "malformed": malformed,
                           "page": head["page"]})
    return tables


def is_icums_form(words):
    """True when the positioned words are an ICUMS Bill of Entry (the boxed form)."""
    if not words:
        return False
    rows = _rows(words)
    return bool(_find(rows, ICUMS_LABELS["document_number"]) or
                any(re.search(r"\bcode\b", _row_text(rows[h["row"]]), re.I)
                    for h in _find(rows, r"\btaxes\b")))


def read_layout(words, doctype):
    """The ICUMS form's fields, read by position. {name: field} like extract()."""
    labels = {f["name"]: f["label"] for f in doctype["fields"]}
    label = lambda name, default: labels.get(name, default)          # noqa: E731
    rows = _rows(words)
    hits = {k: _find(rows, p) for k, p in ICUMS_LABELS.items()}
    fields = {}

    def put(name, default, candidates, normaliser, kind, note=None):
        fields[name] = _field(name, label(name, default), candidates, normaliser, kind)
        if note and fields[name]["status"] == MISSING:
            fields[name]["note"] = note

    put("document_number", "Declaration (BOE) No.",
        _layout_value(rows, hits["document_number"], _declaration_form),
        lambda raw: re.sub(r"\s*/\s*", " / ", raw.strip()), "text")
    put("bl_awb", "BL / AWB No.", _layout_value(rows, hits["bl_awb"], _reference_form),
        lambda raw: raw.strip().upper(), "text")
    # The declaration date: the "Date :" of the header block — stacked under
    # "Bill of Entry(BOE) No", not the receipt or bank-guarantee dates.
    header = []
    for boe in hits["document_number"]:
        top = rows[boe["row"]]
        for d in hits["date"]:
            row = rows[d["row"]]
            if row["page"] == top["page"] and abs(d["x0"] - boe["x0"]) <= 2 * top["h"] and \
                    0 < row["y"] - top["y"] <= 4 * top["h"] and d not in header:
                header.append(d)
    put("document_date", "Declaration date", _layout_value(rows, header, _date_form),
        parse_date, "date")
    put("user_reference", "User reference",
        _layout_value(rows, hits["user_reference"], _reference_form, ways=("beside",)),
        lambda raw: raw.strip(), "text")
    put("doc_status", "Document status",
        _layout_value(rows, hits["doc_status"], _text_form, ways=("beside",)),
        lambda raw: raw.strip(), "text")
    put("delivery_terms", "Delivery terms & place",
        _layout_value(rows, hits["delivery_terms"], _text_form, ways=("below",)),
        lambda raw: " ".join(raw.split()), "text")
    put("cif_usd", "Total invoice value CFR/CIF (USD)",
        _layout_value(rows, hits["invoice_fcy"], _amount_form, ways=("below",)),
        to_float, "amount")
    # The invoice currency: box 13's "CC", the label after Total Invoice Fcy.
    cc = []
    for hit in hits["invoice_fcy"]:
        row = rows[hit["row"]]
        groups = _clusters(row, hit["last"] + 1)
        if groups and _row_text(row, groups[0][0], groups[0][-1] + 1).upper() == "CC":
            cc.append(dict(hit, first=groups[0][0], last=groups[0][-1],
                           x0=row["words"][groups[0][0]][0], box_x0=row["words"][groups[0][0]][0],
                           label="Total Invoice Fcy CC"))
    put("invoice_currency", "Invoice currency",
        _layout_value(rows, cc, _currency_form, ways=("below",)), lambda raw: raw.strip(),
        "text")
    put("exchange_rate", "Exchange rate",
        _layout_value(rows, hits["rate"], _amount_form, ways=("below", "beside")),
        to_float, "amount")
    put("rate_currency", "Exchange-rate currency",
        _layout_value(rows, hits["curr_code"], _currency_form, ways=("below",)),
        lambda raw: raw.strip(), "text")
    put("fob_ncy", "FOB Ncy (import/export)",
        _layout_value(rows, hits["fob_ncy"], _amount_form, ways=("below", "beside")),
        to_float, "amount")

    # B ACCOUNTING DETAILS: the tax lines, the Total, the import duty line, the
    # six VAT / levy lines.
    tables = _tax_tables(rows)
    distinct = []
    for t in tables:
        key = ([(l["code"], l["amount"]) for l in t["lines"]],
               (t["total"] or {}).get("amount"))
        if key not in [k for k, _t in distinct]:
            distinct.append((key, t))
    total_name = "duty_amount_ghs"
    if len(distinct) > 1:
        for name in (total_name, "stated_import_duty", "vat_lines", "tax_lines"):
            fields[name] = {"name": name, "label": label(name, name), "kind": "lines"
                            if name.endswith("lines") else "amount", "status": AMBIGUOUS,
                            "value": None, "evidence": None, "confidence": "NONE",
                            "note": "the document prints {0} different B ACCOUNTING DETAILS "
                                    "tables".format(len(distinct)),
                            "candidates": [{"value": (t["total"] or {}).get("amount"),
                                            "page": t["page"]} for _k, t in distinct]}
        return fields
    table = distinct[0][1] if distinct else {"lines": [], "total": None, "malformed": []}
    total = table["total"]
    put(total_name, "Total duty (GHS)",
        [(total["raw"], total["line"], total["page"], "text", total["amount"] is None)]
        if total and total["raw"] else [], to_float, "amount",
        note="B ACCOUNTING DETAILS prints no Total" if table["lines"] else None)
    duty_lines = [l for l in table["lines"] if re.fullmatch(r"import\s+duty", l["label"], re.I)]
    put("stated_import_duty", "Import duty line (GHS)",
        [(l.get("malformed") or str(l["amount"]), l["line"], l["page"], "text",
          bool(l.get("malformed"))) for l in duty_lines], to_float, "amount")
    vat = []
    for l in table["lines"]:
        for name, pattern in ICUMS_VAT:
            if re.fullmatch(pattern, " ".join(l["label"].split()), re.I):
                vat.append(dict(l, label=name, printed=l["label"]))
                break
    vat.sort(key=lambda l: VAT_ORDER.index(l["label"]))
    fields["vat_lines"] = _vat_field(label("vat_lines", "VAT / levy lines"), vat)
    # A VAT line's name and its tax code must agree, both ways: a code of the
    # block printed under another name (or a name under another code) means the
    # row was not read as printed — the block is not trusted.
    by_code = {c: n for n, c in ICUMS_VAT_CODES.items()}
    disagree = ["{0} printed with code {1}".format(l["printed"], l["code"]) for l in vat
                if ICUMS_VAT_CODES[l["label"]] != l["code"]]
    named = {l["code"] for l in vat}
    disagree += ["code {0} ({1}) printed as '{2}'".format(l["code"], by_code[l["code"]],
                                                         l["label"])
                 for l in table["lines"] if l.get("code") in by_code and l["code"] not in named]
    if disagree and fields["vat_lines"]["status"] != MALFORMED:
        fields["vat_lines"] = dict(fields["vat_lines"], status=AMBIGUOUS, value=None,
                                   note="the VAT / levy names and their tax codes disagree: " +
                                        "; ".join(disagree), confidence="NONE")
    tax = {"name": "tax_lines", "label": label("tax_lines", "B accounting details (tax lines)"),
           "kind": "lines", "candidates": [], "pages": [table.get("page")] if
           table.get("page") else []}
    if table["malformed"]:
        fields["tax_lines"] = dict(tax, status=MALFORMED, value=None,
                                   evidence=table["malformed"][0]["line"], confidence="NONE",
                                   note="; ".join(
                                       "a row that does not fit the columns: '{0}'".format(
                                           m["raw"]) if m.get("why") else
                                       "printed but not a well-formed number: '{0}'".format(
                                           m["raw"]) for m in table["malformed"][:4]))
    else:
        fields["tax_lines"] = dict(tax, status=FOUND if table["lines"] else MISSING,
                                   value=table["lines"] or None, method="text" if
                                   table["lines"] else None,
                                   evidence="{0} lines, Total {1}".format(
                                       len(table["lines"]), (total or {}).get("raw"))
                                   if table["lines"] else None,
                                   confidence="HIGH" if table["lines"] else "NONE")
    return fields


def extract(text, doctype, spans=None, words=None):
    """
    Every PDF field of the document type, typed, with its evidence. {name: field}.

    An ICUMS Bill of Entry (the boxed form, recognised from its positioned
    words) is read by position only (read_layout); any other text by the
    line rules.
    """
    if words and is_icums_form(words):
        return read_layout(words, doctype)
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
