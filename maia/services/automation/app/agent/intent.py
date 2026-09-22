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
        r"|retrieve|search|show|pull|data|info\w*|details?|check|machine|equipment|serial)\b"
        r"|دوريلي|دور على|هات|بيانات|الداتا|معلومات", re.I)),
]


# ── turns that are not equipment requests ───────────────────────────────────
# Asking for a secret is refused before anything else looks at the sentence.
_CREDENTIALS = re.compile(
    r"\b(password|passcode|credentials?|api[\s_-]*key|secret|cookies?|session\s+token"
    r"|access\s+token|auth\w*\s+header|mfa\s+code|login\s+details|username)\b"
    r"|كلمة\s*(السر|المرور)|الباسورد|باسورد", re.I)

# Whole-message small talk: praise, thanks, greetings, acknowledgements, goodbyes.
# Anchored to the full message so "thanks, now get JAZ01865" is still a lookup.
_SMALL_TALK = re.compile(
    r"^\s*(?:(?:very\s+|really\s+|so\s+)?(?:good|great|nice|perfect|excellent|awesome|amazing"
    r"|brilliant|cool|super|wonderful|impressive|fantastic|well\s+done|good\s+job|nice\s+work"
    r"|great\s+work|love\s+it|that'?s?\s+(?:great|good|perfect|it)|exactly|correct|right)"
    r"(?:\s+(?:analy\w*|answer|work|job|result|one|stuff|maia|bro|man))?(?:\s+(?:this|that|there))?"
    r"|thanks?(?:\s+(?:you|a\s+lot|so\s+much|maia|bro))*|thank\s+you(?:\s+(?:so\s+much|very\s+much|maia))?"
    r"|thx|ty|cheers|appreciated?"
    r"|hi|hello|hey|hiya|yo|good\s+(?:morning|afternoon|evening)|salam|hi\s+maia|hello\s+maia|hey\s+maia"
    r"|ok(?:ay)?|k|got\s+it|sure|alright|fine|understood|noted|cool\s+thanks"
    r"|bye|goodbye|see\s+you|later|good\s+night"
    r"|شكرا|شكراً|متشكر|تسلم|تمام|ممتاز|حلو|جميل|برافو|عاش|الله\s+ينور|تحفة"
    r"|السلام\s+عليكم|اهلا|أهلا|مرحبا|صباح\s+الخير|مساء\s+الخير|مع\s+السلامة|باي)"
    r"(?:\s+(?:يا\s+)?(?:maia|مايا))?\s*[.!?؟😀-🙏👍❤️]*\s*$", re.I)

# Requests the general assistant owns: the parts catalog, service bookings,
# branches, warranty, generators, prices, people. With no serial in the
# sentence these are not a machine-record lookup, whatever verb they use.
_GENERAL = re.compile(
    r"\b(book|booking|appointment|service\s+visit|branch(es)?|nearest|location|address|hours"
    r"|warranty|generators?|gensets?|kva|quote|price|prices|cost|stock|in\s+stock|order"
    r"|human|agent|technician|call\s+me|contact|complaint|ticket|fault\s+code|error\s+code"
    r"|find\s+a\s+part|part\s+number\s+\d|filter|oil|weather|news|who\s+are\s+you)\b"
    r"|صيانة|فرع|أقرب|اقرب|ضمان|مولد|سعر|أسعار|عرض\s+سعر|مخزون|موظف|مهندس|شكوى", re.I)


def is_small_talk(utterance: str) -> bool:
    return bool(_SMALL_TALK.match(utterance or ""))


def small_talk_kind(utterance: str) -> str:
    text = (utterance or "").strip().lower()
    if re.match(r"^(hi|hello|hey|hiya|yo|good\s+(morning|afternoon|evening)|salam|السلام|اهلا|أهلا|مرحبا|صباح|مساء)", text):
        return "greeting"
    if re.match(r"^(bye|goodbye|see\s+you|later|good\s+night|مع\s+السلامة|باي)", text):
        return "goodbye"
    if re.match(r"^(ok(ay)?|k|got\s+it|sure|alright|fine|understood|noted)\b", text):
        return "ack"
    return "thanks"


def classify(utterance: str, *, has_serial: bool, has_context: bool,
             awaiting: str | None = None) -> tuple[Intent, float]:
    """Return (intent, confidence).

    `has_serial` and `has_context` matter: a bare "JAZ01865" is a lookup, and a
    bare "engine?" is only an engine question if we already know which machine.
    """
    text = (utterance or "").strip()
    if not text:
        return Intent.INVALID_REQUEST, 1.0

    if _CREDENTIALS.search(text):
        return Intent.CREDENTIALS, 0.95

    # A reply to a question we asked is a reply, whatever it looks like.
    if awaiting:
        return Intent.CLARIFICATION, 0.9

    if not has_serial and is_small_talk(text):
        return Intent.SMALL_TALK, 0.95

    if not has_serial and _GENERAL.search(text):
        return Intent.GENERAL, 0.8

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

    # Anything else is still a question — for the general assistant, not a
    # reason to demand a serial number.
    return Intent.GENERAL if len(text.split()) > 1 else Intent.UNKNOWN, 0.4


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
