"""Deterministic intent router for analysis requests — keywords, patterns, serials.

No model. English and Egyptian/Modern Arabic keywords. The first matching
pattern wins, in the order below (most specific first), so "compare … and
…" is never read as "analyze".

A sentence that is not an analysis request returns NONE, and the rest of
Maia handles it exactly as before (retrieval, small talk, catalog, …).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.agent.entities import extract_serials

INTENTS = ("COMPARE_EQUIPMENT", "WHAT_CHANGED", "DUPLICATE_PARTS", "MISSING_DATA",
           "DATA_QUALITY", "SHOW_ANOMALIES", "SHOW_PARTS", "SUMMARY", "RAW_DATA",
           "ANALYZE_EQUIPMENT")

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("COMPARE_EQUIPMENT", re.compile(
        r"\b(compare|comparison|versus|vs\.?|difference between|differences between)\b"
        r"|قارن|قارني|مقارنة|الفرق بين", re.I)),
    ("WHAT_CHANGED", re.compile(
        r"\bwhat(?:'s| has| have)?\s+changed\b|\bchanges?\b|\bchanged\b|\bsince (?:the )?last\b"
        r"|\bhistory\b|\bprevious (?:snapshot|retrieval)\b"
        r"|اتغير|اتغيّر|تغير|تغيّر|التغييرات|التغيرات|ايه الجديد|إيه الجديد", re.I)),
    ("DUPLICATE_PARTS", re.compile(
        r"\bduplicat\w*|\brepeated\b|\brepeats?\b|مكرر|المكررة|متكرر", re.I)),
    ("MISSING_DATA", re.compile(
        r"\bmissing\b|\bincomplete\b|\bgaps?\b|\bempty\b|\bnull\b|\bnot published\b"
        r"|ناقص|الناقصة|الناقصه|ناقصة|مفقود|فاضي|فاضية", re.I)),
    ("DATA_QUALITY", re.compile(
        r"\b(data )?quality\b|\bscore\b|\breliab\w*|\btrust\w*|جودة|جوده|الجودة", re.I)),
    ("SHOW_ANOMALIES", re.compile(
        r"\banomal\w*|\boutliers?\b|\bunusual\b|\babnormal\b|\bodd\b"
        r"|شاذ|شاذة|غير طبيعي|غريب|غريبة", re.I)),
    ("SHOW_PARTS", re.compile(
        r"\b(show|list|analy[sz]e|summari[sz]e|statistics|stats|breakdown|count)\b.*\bparts\b"
        r"|\bparts\s+(analysis|statistics|stats|breakdown|summary|by group|groups?)\b"
        r"|^\s*parts\s*\??\s*$|تحليل القطع|اعرض القطع|وريني القطع|القطع كلها", re.I)),
    ("SUMMARY", re.compile(
        r"\bsummary\b|\bsummari[sz]e\b|\boverview\b|\bbrief\b|ملخص|لخص|لخّص", re.I)),
    ("RAW_DATA", re.compile(r"\braw json\b|\bshow (?:the )?json\b|\braw data\b", re.I)),
    ("ANALYZE_EQUIPMENT", re.compile(
        r"\banaly[sz](?:e|is|ing|es)\b|\binsights?\b|\breport\b|\bdeep dive\b"
        r"|حلل|حلّل|تحليل|تقرير", re.I)),
]


@dataclass
class Routed:
    intent: str                       # one of INTENTS, or "NONE"
    serials: list[str] = field(default_factory=list)
    serial_source: str | None = None  # "utterance" | "context"
    matched: str | None = None        # the words that decided it, for the trace


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
        if m:
            source = "utterance" if serials else None
            if not serials and active_serial:
                serials, source = [active_serial], "context"
            if intent == "COMPARE_EQUIPMENT" and len(serials) == 1 and active_serial \
                    and active_serial != serials[0]:
                serials = [active_serial, serials[0]]
            return Routed(intent, serials, source, m.group(0))
    return Routed("NONE", serials)
