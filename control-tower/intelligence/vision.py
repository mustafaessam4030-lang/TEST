"""
Reading an image — and saying only what can be seen.

    VISUAL FACT   text that is on the image: read from the page text captured
                  with a screenshot (exact), or by OCR (with a confidence)
    INFERENCE     a conclusion drawn from visual facts, ONLY by a rule whose
                  evidence is itself on the image; each says which fact it
                  rests on
    UNCLEAR       something that looks like a field but was read with low
                  confidence: "I can't reliably read that field"

OCR is the Tesseract command line, if it is installed (TESSERACT_CMD, PATH,
or the Windows default). No Python packages, no network, no model. Without
it ATLAS says it cannot read text from images on this machine — it never
guesses.

A verification screen (a security code, a CAPTCHA, "verify you are human")
is not read: no field is extracted from it, nothing on it is reported, and
an upload of one is refused.
"""

import os
import re
import shutil
import subprocess
import threading

# Phrases that mark a security verification. Matched case-insensitively on
# whatever text the image yields; any hit stops all extraction.
SECURITY = re.compile(
    r"security\s*code|captcha|verify\s+(?:that\s+)?you\s+are\s+(?:a\s+)?human|"
    r"i'?m\s+not\s+a\s+robot|enter\s+(?:the\s+)?code|verification\s+code|"
    r"slide\s+right\s+to|press\s*(?:&|and)\s*hold|are\s+you\s+a\s+robot|"
    r"one[-\s]?time\s+(?:pass)?code|\botp\b", re.I)

CARRIERS = [
    ("Air France KLM Cargo", r"air\s*france\s*klm|afkl"), ("Air France Cargo", r"air\s*france"),
    ("KLM Cargo", r"\bklm\b"), ("Qatar Airways Cargo", r"qatar"),
    ("DHL Express", r"\bdhl\b"), ("Grimaldi Lines", r"grimaldi"),
    ("MSC", r"\bmsc\b"), ("Maersk", r"maersk"), ("CMA CGM", r"cma\s*cgm"),
    ("COSCO", r"\bcosco\b"), ("ONE", r"\bocean\s+network\s+express\b"),
    ("Hapag-Lloyd", r"hapag"), ("Astral Aviation", r"astral"),
]
REFERENCES = [
    ("air waybill", re.compile(r"\b\d{3}[-\s]\d{8}\b")),
    ("bill of lading", re.compile(r"\b(?:S\d{9}|ANRB\d{5,}|MEDU[A-Z]{0,2}\d{6,})\b")),
    ("DHL reference", re.compile(r"\bK\d{6}\b")),
    ("container", re.compile(r"\b[A-Z]{4}\d{7}\b")),
    ("tracking number", re.compile(r"\b\d{9,12}\b")),
]
STATUSES = [
    ("Human timeout", r"human\s+timeout|timed\s+out\s+waiting\s+for\s+a\s+person"),
    ("Human verification required", r"human\s+verification\s+required|human\s+action\s+required"),
    ("Waiting for a person", r"waiting\s+for\s+(?:a\s+person|human|you)"),
    ("Session lost", r"session\s+lost"),
    ("Failed", r"\bfailed\b"), ("Skipped", r"\bskipped\b"),
    ("Success", r"\bsuccess\b|written\s+and\s+read\s+back"),
    ("Arrived", r"\barrived\b|actual\s+arrival"), ("Delivered", r"\bdelivered\b"),
    ("In transit", r"in\s+transit|on\s+the\s+way"),
    ("Estimated arrival", r"estimated\s+arrival|\beta\b"),
    ("Processing", r"\bprocessing\b|tracking\s+in\s+progress"),
    ("Navigation error", r"navigation\s+error|did\s+not\s+open|err_[a-z_]+"),
    ("Nothing written", r"nothing\s+(?:was\s+)?written"),
]
DATE = re.compile(r"\b(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})\b")
ERROR_LINE = re.compile(r"error|failed|timeout|timed out|err_[a-z_]+|could not|refused", re.I)

_cache = {}
_lock = threading.Lock()


def tesseract():
    """The Tesseract binary, or None."""
    for candidate in (os.environ.get("TESSERACT_CMD"), shutil.which("tesseract"),
                      r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if candidate and os.path.isfile(candidate):
            return candidate
    return None


def ocr(path, timeout=40):
    """
    Lines of text with a mean confidence each: [(text, conf 0-100)].
    Returns (lines, engine, error).
    """
    cmd = tesseract()
    if cmd is None:
        return None, None, "no OCR engine is installed on this machine (Tesseract)"
    key = (str(path), os.path.getmtime(path) if os.path.exists(path) else 0)
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        done = subprocess.run([cmd, str(path), "stdout", "--psm", "3", "tsv"],
                              capture_output=True, timeout=timeout)
        if done.returncode != 0:
            result = (None, "tesseract", (done.stderr or b"").decode("utf-8", "replace")[:200]
                      or "OCR failed")
        else:
            lines = {}
            for row in done.stdout.decode("utf-8", "replace").splitlines()[1:]:
                cols = row.split("\t")
                if len(cols) < 12 or not cols[11].strip():
                    continue
                try:
                    conf = float(cols[10])
                except ValueError:
                    continue
                if conf < 0:
                    continue
                key2 = (cols[2], cols[3], cols[4])
                lines.setdefault(key2, []).append((cols[11], conf))
            out = []
            for words in lines.values():
                text = " ".join(w for w, _ in words).strip()
                if text:
                    out.append((text, sum(c for _, c in words) / len(words)))
            result = (out, "tesseract", None)
    except subprocess.TimeoutExpired:
        result = (None, "tesseract", "OCR took too long on this image")
    except Exception as error:
        result = (None, "tesseract", str(error)[:200])
    with _lock:
        _cache[key] = result
    return result


def _level(conf):
    if conf is None:
        return "High"
    return "High" if conf >= 85 else "Medium" if conf >= 65 else "Low"


def parse(lines, source):
    """
    Visual facts from lines of text. `lines` is [(text, conf|None)]; conf
    None means exact text (page text captured with a screenshot).
    """
    facts, unclear, seen = [], [], set()

    def add(field, value, conf, line, kind=None):
        key = (field, value)
        if key in seen:
            return
        seen.add(key)
        item = {"field": field, "value": value, "confidence": _level(conf),
                "evidence": line.strip()[:160], "source": source}
        if kind:
            item["kind"] = kind
        (unclear if item["confidence"] == "Low" else facts).append(item)

    for text, conf in lines:
        for name, pattern in CARRIERS:
            if re.search(pattern, text, re.I):
                add("carrier", name, conf, text)
                break
        for kind, pattern in REFERENCES:
            for m in pattern.finditer(text):
                before = text[m.start() - 1] if m.start() > 0 else " "
                if kind == "tracking number" and (before.isalnum() or before in "$§&#@%"):
                    # Glued to a character OCR often confuses with a letter
                    # ("$330348776" for "S330348776"): ambiguous, never repaired.
                    unclear.append({"field": "reference", "value": before + m.group(0),
                                    "confidence": "Low", "evidence": text.strip()[:160],
                                    "source": source, "kind": "ambiguous",
                                    "digits": m.group(0)})
                    continue
                add("reference", m.group(0), conf, text, kind)
        for name, pattern in STATUSES:
            if re.search(pattern, text, re.I):
                add("status", name, conf, text)
        for match in DATE.findall(text):
            add("date", match, conf, text)
        if ERROR_LINE.search(text) and len(text) < 220 and not any(
                re.fullmatch(r"\W*(?:status:?\s*)?" + p + r"\W*", text.strip(), re.I)
                for _, p in STATUSES):
            add("message", text.strip(), conf, text)
    # A tracking-number match that is also part of a longer reference is noise.
    refs = [f["value"] for f in facts if f["field"] == "reference"]
    facts = [f for f in facts if not (f["field"] == "reference" and f.get("kind") == "tracking number"
                                       and any(f["value"] in r and f["value"] != r for r in refs))]
    return facts, unclear


def infer(facts):
    """Conclusions with their evidence. A rule fires only on what is visible."""
    statuses = {f["value"] for f in facts if f["field"] == "status"}
    messages = " ".join(f["value"] for f in facts if f["field"] == "message").casefold()
    out = []

    def add(text, because):
        out.append({"text": text, "because": because})

    if "Human timeout" in statuses:
        add("The automation stopped before a Hub write for that shipment — in ATA a human "
            "timeout means nothing was written.", "status shows Human timeout")
    if "Session lost" in statuses:
        add("The browser session that was waiting is gone; nothing was written.",
            "status shows Session lost")
    if statuses & {"Human verification required", "Waiting for a person"} and \
            "Human timeout" not in statuses:
        add("A person is needed on the carrier page; that shipment is paused until then.",
            "status shows a human verification or a wait for a person")
    if "Nothing written" in statuses and "Human timeout" not in statuses:
        add("No Hub write happened for that shipment.", "the text says nothing was written")
    if "Navigation error" in statuses or "did not open" in messages:
        add("The carrier page was not reached, so no shipment data could be read from it.",
            "a navigation error is shown")
    if "read back and matched" in messages or "Success" in statuses and \
            "written and read back" in messages:
        add("The Hub write was confirmed by read-back.", "the text says read back and matched")
    return out


def read_image(path, page_text=None):
    """
    Everything ATLAS may say about one image:
    {verification_screen, engine, facts, unclear, inferences, message}.
    """
    if page_text:
        lines = [(line, None) for line in page_text.splitlines() if line.strip()]
        engine, source, error = "page text captured with the screenshot", "page text", None
    else:
        lines, engine, error = ocr(path)
        source = "OCR"
    if lines is None:
        return {"verification_screen": False, "engine": engine, "facts": [], "unclear": [],
                "inferences": [], "message": "I can't read text from this image: {0}. "
                "It is stored as evidence, but I won't guess what it says.".format(error)}
    blob = " ".join(t for t, _ in lines)
    if SECURITY.search(blob):
        return {"verification_screen": True, "engine": engine, "facts": [], "unclear": [],
                "inferences": [],
                "message": "This image shows a security verification. I don't read, extract "
                           "or keep anything from verification screens — a person completes "
                           "those in the browser."}
    facts, unclear = parse(lines, source)
    message = None
    if not facts and not unclear:
        message = "I couldn't find any operational detail I can read reliably in this image."
    return {"verification_screen": False, "engine": engine, "facts": facts,
            "unclear": unclear, "inferences": infer(facts), "message": message}


def compare(a, b):
    """Field by field: what two readings share and where they differ."""
    def by_field(result):
        out = {}
        for f in result.get("facts") or []:
            out.setdefault(f["field"], set()).add(f["value"])
        return out
    fa, fb = by_field(a), by_field(b)
    same, differ = [], []
    for field in sorted(set(fa) | set(fb)):
        va, vb = fa.get(field, set()), fb.get(field, set())
        if va and va == vb:
            same.append((field, sorted(va)))
        else:
            differ.append((field, sorted(va), sorted(vb)))
    return {"same": same, "different": differ}
