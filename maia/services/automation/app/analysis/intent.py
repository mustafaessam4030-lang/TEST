"""Deterministic intent router — investigation modes and analysis focuses.

No model: keywords, patterns and serial extraction, English and Arabic.

Investigation modes:
    PARTS            parts, part numbers, spares, components  (قطع, القطعة)
    TROUBLESHOOTING  troubleshooting, codes, faults, events, symptoms, problems (مشاكل, كود)
    FULL_ANALYSIS    "full analysis", both                                  (تحليل كامل)
    MODEL_3D         "show it in 3D", "locate in the 3D model" — a follow-up step
    ASK_MODE         "Analyze JAZ01865" with no mode → Maia asks which one

Analysis focuses on stored data (unchanged): COMPARE_EQUIPMENT, WHAT_CHANGED,
DUPLICATE_PARTS, MISSING_DATA, DATA_QUALITY, SHOW_ANOMALIES, SUMMARY, RAW_DATA.

A mode keyword with no serial in the sentence AND no machine in the
conversation returns NONE, so "Find a part" still opens the parts catalog and
the rest of Maia behaves exactly as before.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.agent.entities import extract_serials
from app.domain.parts import PART_NUMBER_RE

MODES = ("PARTS", "TROUBLESHOOTING", "FULL_ANALYSIS", "MODEL_3D", "ASK_MODE")
FOCUSES = ("COMPARE_EQUIPMENT", "WHAT_CHANGED", "DUPLICATE_PARTS", "MISSING_DATA",
           "DATA_QUALITY", "SHOW_ANOMALIES", "SUMMARY", "RAW_DATA")
INTENTS = MODES + FOCUSES

TR_CODE_RE = re.compile(r"\b((?:\d{1,4}-){1,2}\d{1,4}|E\d{3,5}(?:\(\d\))?)\b", re.I)

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("COMPARE_EQUIPMENT", re.compile(
        r"\b(compare|comparison|versus|vs\.?|difference between|differences between)\b"
        r"|قارن|قارني|مقارنة|الفرق بين", re.I)),
    ("WHAT_CHANGED", re.compile(
        r"\bwhat(?:'s| has| have)?\s+changed\b|\bchanges?\b|\bchanged\b|\bsince (?:the )?last\b"
        r"|\bhistory\b|\bprevious (?:snapshot|retrieval)\b"
        r"|اتغير|اتغيّر|تغير|تغيّر|التغييرات|التغيرات|ايه الجديد|إيه الجديد", re.I)),
    ("DUPLICATE_PARTS", re.compile(r"\bduplicat\w*|\brepeated\b|مكرر|المكررة|متكرر", re.I)),
    ("MISSING_DATA", re.compile(
        r"\bmissing\b|\bincomplete\b|\bgaps?\b|\bnot published\b"
        r"|ناقص|الناقصة|الناقصه|ناقصة|مفقود", re.I)),
    # Arabic words need letter boundaries: "جودة" sits inside "الموجودة" (existing).
    ("DATA_QUALITY", re.compile(r"\bdata quality\b|\bquality\b|\bscore\b"
                                r"|(?<![\u0621-\u064A])(?:ال)?جود[ةه](?![\u0621-\u064A])", re.I)),
    ("SHOW_ANOMALIES", re.compile(
        r"\banomal\w*|\boutliers?\b|\bunusual\b|\babnormal\b|شاذ|شاذة|غير طبيعي|غريب", re.I)),
    ("FULL_ANALYSIS", re.compile(
        r"\bfull\s+(analysis|investigation|report|check)\b|\beverything\b|\bboth\b"
        r"|تحليل كامل|تحليل شامل|الاتنين|الإثنين|كله", re.I)),
    ("MODEL_3D", re.compile(
        r"\b3\s?d\b|\bthree[- ]d\b|\blocate\b.*\b(model|viewer)\b|\bshow (it|me)? ?in (the )?model\b"
        r"|ثلاثي|الموديل ثلاثي|3d", re.I)),
    ("TROUBLESHOOTING", re.compile(
        r"troubleshoot\w*|\btrouble\b|\berrors?\b|\bfaults?\b|\bevents?\b|\bcodes?\b"
        r"|\bsymptoms?\b|\bproblems?\b|\bissues?\b|\bdiagnos\w*|what'?s wrong|\bwhat is wrong"
        r"|مشاكل|مشكلة|المشاكل|عطل|أعطال|اعطال|الاعطال|كود|أكواد|اكواد|أعراض|اعراض|الأعراض"
        r"|تشخيص", re.I)),
    ("PARTS", re.compile(
        r"\bparts?\b|\bspares?\b|\bpart numbers?\b|\bcomponents?\b"
        r"|قطع|القطع|القطعة|قطعة|قطع الغيار|قطعه", re.I)),
    ("SUMMARY", re.compile(r"\bsummary\b|\bsummari[sz]e\b|\boverview\b|ملخص|لخص|لخّص", re.I)),
    ("RAW_DATA", re.compile(r"\braw json\b|\bshow (?:the )?json\b|\braw data\b", re.I)),
    ("ASK_MODE", re.compile(
        r"\banaly[sz](?:e|is|ing|es)\b|\binvestigat\w*|\binsights?\b|\breport\b|\bdeep dive\b"
        r"|\bcheck\b.*\b[A-Z]{3}\d{5}\b|حلل|حلّل|تحليل|تقرير|افحص", re.I)),
]
_GROUP = re.compile(r"\b(?:in|from|of)\s+the\s+([a-z][a-z /&-]{1,30}?)\s+group\b"
                    r"|\b([a-z]{3,20})\s+group\b", re.I)
_QTY = re.compile(r"\b(?:quantity|qty)\s*(?:of|=|:|is)?\s*(\d+(?:\.\d+)?)\b", re.I)
_TEXT = re.compile(r"\b(?:search|find|look for)\s+(?:parts?\s+)?(?:for\s+)?['\"]?"
                   r"([a-z][a-z0-9 /-]{2,40}?)['\"]?(?:\s+(?:on|in|for)\s+[A-Z]{3}\d{5}|\s*$)",
                   re.I)


@dataclass
class Routed:
    intent: str                       # one of INTENTS, or "NONE"
    serials: list[str] = field(default_factory=list)
    serial_source: str | None = None  # "utterance" | "context"
    matched: str | None = None
    filters: dict[str, Any] = field(default_factory=dict)

    @property
    def mode(self) -> str | None:
        return self.intent if self.intent in MODES else (
            "PARTS" if self.intent in FOCUSES else None)


def _filters(intent: str, text: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if intent in ("PARTS", "MODEL_3D", "FULL_ANALYSIS"):
        pn = PART_NUMBER_RE.search(text)
        if pn:
            out["part_number"] = pn.group(0).upper()
        g = _GROUP.search(text)
        if g:
            name = (g.group(1) or g.group(2) or "").strip()
            if name.lower() not in ("entire", "the", "a", "this", "that", "part", "parts"):
                out["group"] = name
        q = _QTY.search(text)
        if q:
            out["quantity"] = float(q.group(1))
        t = _TEXT.search(text)
        if t and not pn:
            words = t.group(1).strip()
            if words.lower() not in ("part", "parts", "a part", "the part") \
                    and not re.search(r"\b[A-Z]{2,4}\s?\d{4,10}\b", words, re.I):
                out["text"] = words
    if intent in ("TROUBLESHOOTING", "MODEL_3D", "FULL_ANALYSIS"):
        c = TR_CODE_RE.search(text)
        if c and not PART_NUMBER_RE.fullmatch(c.group(1)):
            out["code"] = c.group(1).upper()
    return out


def route(utterance: str, active_serial: str | None = None) -> Routed:
    text = (utterance or "").strip()
    if not text:
        return Routed("NONE")
    serials: list[str] = []
    for e in extract_serials(text):
        if e.confidence >= 0.5 and e.normalized not in serials:
            serials.append(e.normalized)
    for intent, pattern in _PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        source = "utterance" if serials else None
        if not serials and active_serial:
            serials, source = [active_serial], "context"
        if intent in MODES and intent != "ASK_MODE" and not serials:
            # No machine at all: not an investigation — leave it to the rest of Maia.
            return Routed("NONE")
        if intent == "COMPARE_EQUIPMENT" and len(serials) == 1 and active_serial \
                and active_serial != serials[0]:
            serials = [active_serial, serials[0]]
        return Routed(intent, serials, source, m.group(0), _filters(intent, text))
    return Routed("NONE", serials)
