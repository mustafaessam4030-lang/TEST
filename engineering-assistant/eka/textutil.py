"""Text normalisation and tokenisation that leaves technical identifiers intact.

Engineering text is full of tokens a generic tokenizer destroys: part numbers
(``1R-0750``), thread sizes (``M12x1.5``), decimals (``3.5``), model names
(``HX-200``). The tokenizer keeps each of those as one token and also emits
its punctuation-free form (``1r0750``) so a user who types it without the dash
still matches. Nothing here rewrites a value or a unit.
"""
from __future__ import annotations

import re
import unicodedata

# Unicode dashes and spaces that PDFs emit in place of their ASCII forms.
_DASHES = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
_SPACES = dict.fromkeys(map(ord, "     "), " ")

TOKEN = re.compile(r"[a-z0-9]+(?:[-./x][a-z0-9]+)*", re.I)
NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])\d+(?:\.\d+)?(?![\w])")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
# An identifier mixes letters and digits, optionally joined by - . / (HX-200, 1R-0750, M12x1.5).
IDENTIFIER = re.compile(r"\b(?=[A-Za-z0-9./-]*\d)(?=[A-Za-z0-9./-]*[A-Za-z])[A-Za-z0-9]+(?:[-./][A-Za-z0-9]+)*\b")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")

STOPWORDS = frozenset("""
a about above after again all also am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has
have having he her here hers him his how i if in into is it its itself just me more most my no
nor not of off on once only or other our out over own please same she should so some such than
that the their theirs them then there these they this those through to too under until up very
was we were what when where which while who whom why will with would you your yours tell show
give find get need know document documents manual say says said according
compare comparison difference differences differ versus vs between both two each
""".split())


def normalize(text: str) -> str:
    """Canonical form shared by documents and queries: NFC, ASCII dashes, single spaces."""
    text = unicodedata.normalize("NFC", text or "").translate(_DASHES).translate(_SPACES)
    text = text.replace("­", "")  # soft hyphen
    return re.sub(r"[ \t]+", " ", text).strip()


def _stem(word: str) -> str:
    if word.isalpha() and len(word) > 4:
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
    return word


def tokens(text: str, keep_stopwords: bool = False) -> list[str]:
    """Search tokens. Compound identifiers also yield their joined form."""
    out: list[str] = []
    for match in TOKEN.finditer(THOUSANDS.sub("", normalize(text)).lower()):
        tok = match.group(0)
        if not keep_stopwords and tok in STOPWORDS:
            continue
        out.append(_stem(tok))
        if re.search(r"[-./]", tok) and re.search(r"[a-z]", tok):
            out.append(re.sub(r"[-./]", "", tok))
        if re.search(r"[-/]", tok):
            out.extend(_stem(part) for part in re.split(r"[-/]", tok)
                       if part and part not in STOPWORDS)
    return out


def content_terms(text: str) -> list[str]:
    """Distinct query terms that carry meaning (no stopwords, no 1-letter noise)."""
    seen: dict[str, None] = {}
    for match in TOKEN.finditer(THOUSANDS.sub("", normalize(text)).lower()):
        tok = match.group(0)
        if tok in STOPWORDS or (len(tok) < 2 and not tok.isdigit()):
            continue
        seen.setdefault(_stem(tok), None)
    return list(seen)


def numbers(text: str) -> set[str]:
    """Standalone numeric values, canonicalised so that 1,200 == 1200 and 3.50 == 3.5.

    Digits inside identifiers (the 200 in HX-200) are not values; identifiers()
    covers those.
    """
    found = set()
    for raw in NUMBER.findall(IDENTIFIER.sub(" ", normalize(text))):
        value = raw.replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        found.add(value.lstrip("0") or "0")
    return found


def identifiers(text: str) -> set[str]:
    """Alphanumeric identifiers (part numbers, model names), upper-cased."""
    return {m.group(0).upper() for m in IDENTIFIER.finditer(normalize(text))}


def compact(text: str) -> str:
    """Lower-case, punctuation-free form used for containment checks."""
    return re.sub(r"[^a-z0-9]+", "", normalize(text).lower())


def sentences(text: str) -> list[str]:
    """Split a passage into sentences/lines. Lines are kept: list items and table rows are lines."""
    out: list[str] = []
    for line in normalize_block(text).split("\n"):
        line = line.strip()
        if not line:
            continue
        out.extend(part.strip() for part in SENTENCE_END.split(line) if part.strip())
    return out


def normalize_block(text: str) -> str:
    """Normalise a multi-line block while keeping its line structure."""
    lines = [normalize(line) for line in (text or "").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def excerpt(text: str, limit: int = 320) -> str:
    text = normalize(text.replace("\n", " "))
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"
