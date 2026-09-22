"""Reading an identifier out of a sentence, without turning every word into one.

The hard part is not finding `JAZ01865` — a regex does that. The hard part is
*not* finding one in "get me the parts", "what about the engine", or "PRH04588"
when that is an engine serial the user is quoting back at us.

So this does not run one pattern. It proposes candidates from the whole
utterance, then scores each on evidence a person would use: a label beside it
("serial", "s/n", "سيريال"), the shape Caterpillar actually uses, whether it is
an ordinary word, and where it sits in the sentence. A low score is reported as
low, not silently promoted.
"""
from __future__ import annotations

import re
import unicodedata

from app.agent.state import ExtractedSerial

# ── normalization ───────────────────────────────────────────────────────────
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_SEPARATORS = re.compile(r"[\s\-_/.،,]+")


def normalize_serial(raw: str) -> str:
    """Upper-case, strip separators, resolve Arabic-Indic digits.

    `JAZ 01865`, `jaz-01865` and `JAZ٠١٨٦٥` are the same machine, and a user
    typing any of them means the same thing.
    """
    text = unicodedata.normalize("NFKC", str(raw or "")).translate(_ARABIC_INDIC)
    return _SEPARATORS.sub("", text).upper()


# ── what a serial looks like ────────────────────────────────────────────────
#: Caterpillar's Product Identification Number is 3 letters + 5 digits
#: (JAZ01865, PRH04588, G1B00974 — the last with a digit inside the prefix).
#: This is the shape we are most confident about; it is not the only legal one.
_CAT_PIN = re.compile(r"^[A-Z][A-Z0-9]{2}\d{5}$")
#: Older and internal forms: a letter-led alphanumeric run.
_PLAUSIBLE = re.compile(r"^[A-Z0-9]{3,17}$")
#: Must contain a digit. "ENGINE", "PARTS" and "SERIAL" are words, not serials.
_HAS_DIGIT = re.compile(r"\d")
_HAS_LETTER = re.compile(r"[A-Z]")

#: Words that survive normalization and would otherwise look like identifiers.
_STOPWORDS = frozenset({
    "SERIAL", "SERIALNUMBER", "NUMBER", "MACHINE", "EQUIPMENT", "ENGINE", "PARTS",
    "PART", "DATA", "INFO", "DETAILS", "DETAIL", "BUILD", "DATE", "MODEL", "CAT",
    "CATERPILLAR", "SIS", "PIN", "SN", "THE", "AND", "FOR", "GET", "FIND", "CHECK",
    "SHOW", "WHAT", "WHICH", "THIS", "THAT", "PLEASE", "YES", "NO", "OK", "HELP",
    "ALL", "ANY", "NOW", "MAIA", "MAYA", "TYPE", "STATUS", "HISTORY", "GROUP",
})

#: A label immediately before a token is the strongest signal there is.
_LABEL = re.compile(
    r"(serial\s*(?:number|no\.?|num|#)?|s\s*/\s*n|sn|pin|asset|unit|machine|equipment"
    r"|سيريال|السيريال|رقم\s*(?:المعدة|السيريال)?|المعدة)\s*(?:is|=|:|رقم)?\s*$",
    re.I)

#: One alphanumeric run. Words are found first and joined second, so a label
#: never gets glued onto the identifier beside it ("serial JAZ01865" is two
#: tokens, not one 14-character mystery).
_WORD = re.compile(r"[A-Za-z0-9\u0660-\u0669\u06F0-\u06F9]+")
#: What may sit between the halves of one split identifier: "JAZ 01865".
_JOINABLE = re.compile(r"^[\s\-_/]{1,2}$")


def _looks_like_prose(normalized: str) -> bool:
    return normalized in _STOPWORDS or not _HAS_DIGIT.search(normalized)


def score_serial(normalized: str, *, labelled: bool, alone: bool) -> tuple[float, str]:
    """How sure are we that this token is an equipment identifier?

    Returns (confidence, why). The `why` is kept because a rejected candidate
    is worth explaining — "that looks like a word, not a serial" is a better
    answer than silence.
    """
    if not _PLAUSIBLE.match(normalized):
        return 0.0, "not alphanumeric, or the wrong length for an identifier"
    if normalized in _STOPWORDS:
        return 0.0, "an ordinary word, not an identifier"
    if not _HAS_DIGIT.search(normalized):
        return 0.0, "no digits — serials always carry them"

    score, why = 0.35, "alphanumeric of a plausible length"
    if _CAT_PIN.match(normalized):
        score, why = 0.80, "matches the Caterpillar PIN shape (3 characters + 5 digits)"
    elif _HAS_LETTER.search(normalized) and len(normalized) >= 6:
        score, why = 0.55, "letters and digits, serial-like length"

    if labelled:
        score, why = min(1.0, score + 0.18), f"{why}; labelled as a serial"
    if alone:
        score = min(1.0, score + 0.10)
        why = f"{why}; sent on its own"
    return round(score, 3), why


def extract_serials(utterance: str) -> list[ExtractedSerial]:
    """Every plausible identifier in the sentence, best first.

    Returning several is deliberate: "is JAZ01865 the same as JAZ01868?" has two,
    and a caller that silently took the first would answer the wrong question.
    """
    text = unicodedata.normalize("NFKC", str(utterance or ""))
    bare_normalized = normalize_serial(text.strip())
    words = list(_WORD.finditer(text))
    found: dict[str, ExtractedSerial] = {}

    def offer(raw: str, start_at: int) -> None:
        normalized = normalize_serial(raw)
        if not normalized or _looks_like_prose(normalized):
            return
        labelled = bool(_LABEL.search(text[:start_at]))
        alone = bare_normalized == normalized
        confidence, why = score_serial(normalized, labelled=labelled, alone=alone)
        if confidence <= 0.0:
            return
        existing = found.get(normalized)
        if existing is None or confidence > existing.confidence:
            found[normalized] = ExtractedSerial(
                raw=raw.strip(), normalized=normalized, confidence=confidence,
                reason=why, format_valid=format_valid(normalized))

    for index, word in enumerate(words):
        offer(word.group(0), word.start())
        # "JAZ 01865" — a letter run and a digit run split by a space or dash is
        # one identifier typed with a thumb in the way. Offered as an ADDITIONAL
        # candidate only when joining produces the documented PIN shape, so two
        # unrelated words are never fused into an imaginary machine.
        if index + 1 < len(words):
            nxt = words[index + 1]
            if _JOINABLE.match(text[word.end():nxt.start()] or ""):
                joined = word.group(0) + nxt.group(0)
                if is_cat_pin(normalize_serial(joined)):
                    offer(joined, word.start())

    ordered = sorted(found.values(), key=lambda e: (-e.confidence, -len(e.normalized)))
    kept: list[ExtractedSerial] = []
    for entry in ordered:
        # A half of an identifier we already accepted whole is the same mention.
        if any(entry.normalized in k.normalized and entry.normalized != k.normalized
               for k in kept):
            continue
        kept.append(entry)
    return kept


def is_cat_pin(normalized: str) -> bool:
    """The documented Caterpillar PIN shape. True says 'well-formed', never 'exists'."""
    return bool(_CAT_PIN.match(normalized or ""))


def format_valid(normalized: str) -> bool:
    """Could this be an identifier at all? Deliberately wider than `is_cat_pin`:
    refusing an unusual-but-real serial is worse than looking it up and missing."""
    value = normalized or ""
    return bool(_PLAUSIBLE.match(value)
                and _HAS_DIGIT.search(value)
                and value not in _STOPWORDS)
