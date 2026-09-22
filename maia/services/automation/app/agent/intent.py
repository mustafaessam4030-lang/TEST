"""What is the user trying to do, and which fields do they actually want?

Deliberately rule-based rather than model-based, for two reasons. It is the
layer an evaluation suite can measure exactly, and it is the layer that must
still work when no model is in the loop — the browser chat calls it directly.
A model sitting on top may override the intent, but only by proposing one of
these names, which is then re-checked here.

It generalizes by matching *families* of meaning, not the example sentences in
a specification: any phrasing that asks about an engine lands on ENGINE_DETAILS
whether or not anyone wrote that phrasing down.
"""
from __future__ import annotations

import re

from app.agent.state import Intent

#: Canonical record fields a user can ask for by name, and the words people use.
FIELD_WORDS: dict[str, re.Pattern[str]] = {
    "machine_serial_number": re.compile(
        r"\b(machine\s+serial|serial\s+of\s+the\s+machine|pin)\b|سيريال\s*المعدة", re.I),
    "machine_build_date": re.compile(
        r"\b(machine\s+build|build\s+date|manufactur\w*\s+date|date\s+built|year\s+built)\b"
        r"|تاريخ\s*(التصنيع|الصنع)", re.I),
    "engine_serial_number": re.compile(
        r"\b(engine\s+serial|serial\s+of\s+the\s+engine)\b|سيريال\s*(الموتور|المحرك)", re.I),
    "engine_build_date": re.compile(
        r"\b(engine\s+build|engine.{0,12}date)\b|تاريخ\s*(الموتور|المحرك)", re.I),
    "equipment_model": re.compile(r"\b(model|model\s+number)\b|الموديل", re.I),
    "equipment_type": re.compile(r"\b(type|family|category)\b|النوع", re.I),
    "engine_family": re.compile(r"\b(engine\s+(model|family|arrangement))\b", re.I),
    "parts_data": re.compile(
        r"\b(parts?|part\s+numbers?|part\s+names?|entire\s+group|product\s+group)\b"
        r"|قطع|القطع", re.I),
    "specifications": re.compile(r"\b(spec\w*|weight|power|capacity)\b|مواصفات", re.I),
    "parts_manual_url": re.compile(r"\b(parts?\s+(manual|catalog|book))\b|كتالوج", re.I),
}

#: Word families, checked in order. First match wins, so the most specific
#: families are listed first.
_INTENT_RULES: list[tuple[Intent, re.Pattern[str]]] = [
    (Intent.HELP, re.compile(
        r"^\s*(help|what can you do|how (do|does) (this|you)|من أنت|ساعدني)\b", re.I)),
    (Intent.EQUIPMENT_HISTORY, re.compile(
        r"\b(history|changed?|change log|previous|versions?|last updated)\b|تاريخ\s*التغيير", re.I)),
    (Intent.PARTS_LOOKUP, re.compile(
        r"\b(parts?|part\s+numbers?|entire\s+group|product\s+group|catalog)\b|قطع|القطع", re.I)),
    (Intent.ENGINE_DETAILS, re.compile(
        r"\bengine\b|الموتور|المحرك", re.I)),
    (Intent.BUILD_DATE, re.compile(
        r"\b(build\s*date|manufactur\w*|date\s+built|year\s+built|when\s+was\s+it\s+(made|built))\b"
        r"|تاريخ\s*(التصنيع|الصنع)", re.I)),
    (Intent.STATUS_CHECK, re.compile(
        r"\b(exists?|available|do (we|you) have|is there|valid|known)\b|موجود", re.I)),
    (Intent.EQUIPMENT_LOOKUP, re.compile(
        r"\b(everything|all (the )?(data|info\w*)|full|complete|lookup|look\s?up|find|get|fetch"
        r"|retrieve|search|show|pull|data|info\w*|details?)\b"
        r"|دوريلي|دور على|هات|بيانات|الداتا|معلومات", re.I)),
]


def classify(utterance: str, *, has_serial: bool, has_context: bool,
             awaiting: str | None = None) -> tuple[Intent, float]:
    """Return (intent, confidence).

    `has_serial` and `has_context` matter: a bare "JAZ01865" is a lookup, and a
    bare "engine?" is only an engine question if we already know which machine.
    """
    text = (utterance or "").strip()
    if not text:
        return Intent.INVALID_REQUEST, 1.0

    # A reply to a question we asked is a reply, whatever it looks like.
    if awaiting:
        return Intent.CLARIFICATION, 0.9

    for intent, pattern in _INTENT_RULES:
        if pattern.search(text):
            # Field-shaped questions about a known machine are details, not a
            # fresh full lookup: "and the build date?" should not re-drive SIS.
            confidence = 0.9 if (has_serial or has_context) else 0.6
            return intent, confidence

    # No verb, just an identifier: the request is obvious.
    if has_serial:
        return Intent.EQUIPMENT_LOOKUP, 0.85

    # Something about a machine we are already discussing, phrased loosely.
    if has_context and requested_fields(text):
        return Intent.EQUIPMENT_DETAILS, 0.7

    return Intent.UNKNOWN, 0.3


def requested_fields(utterance: str) -> list[str]:
    """The specific fields named in the sentence, in canonical form.

    Empty means "the record" — not "nothing". A targeted question is answered
    from a targeted read, which is how we avoid driving a browser to answer
    something the store already holds.
    """
    text = utterance or ""
    return [field for field, pattern in FIELD_WORDS.items() if pattern.search(text)]


#: Fields implied by an intent when the user named none explicitly.
INTENT_FIELDS: dict[Intent, list[str]] = {
    Intent.ENGINE_DETAILS: ["engine_serial_number", "engine_build_date", "engine_family"],
    Intent.BUILD_DATE: ["machine_build_date", "build_date"],
    Intent.PARTS_LOOKUP: ["parts_data"],
    Intent.STATUS_CHECK: ["serial_number"],
}


def fields_for(intent: Intent, explicit: list[str]) -> list[str]:
    if explicit:
        return explicit
    return list(INTENT_FIELDS.get(intent, []))


def detect_language(utterance: str) -> str:
    """Arabic if the sentence contains Arabic letters. Mixed text reads as Arabic,
    which is how people here actually write."""
    return "ar" if re.search(r"[؀-ۿ]", utterance or "") else "en"
