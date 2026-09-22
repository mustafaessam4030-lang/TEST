"""Checking Cortex's answer against the facts the tools produced.

The model may phrase, compare, summarise and reason. It may not introduce a
value. Every part number, serial and date in its answer must appear in this
turn's facts, and every fact it cites must exist. An answer that fails is not
shown: a deterministic answer built from the facts replaces it, and the
response says so.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.domain.parts import PART_NUMBER_RE

#: what Cortex must return (AI_COMPLETE response_format / agent final message)
CORTEX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "cited_fact_ids": {"type": "array", "items": {"type": "string"}},
        "derived_findings": {"type": "array", "items": {
            "type": "object",
            "properties": {"statement": {"type": "string"},
                           "fact_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["statement", "fact_ids"], "additionalProperties": False}},
        "inferred": {"type": "array", "items": {
            "type": "object",
            "properties": {"statement": {"type": "string"},
                           "basis_fact_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["statement", "basis_fact_ids"], "additionalProperties": False}},
        "missing_fields": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["answer", "cited_fact_ids", "derived_findings", "inferred",
                 "missing_fields", "confidence"],
    "additionalProperties": False,
}

_SERIAL_TOKEN = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{7,17}\b")
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_US_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")


def claimed_tokens(text: str) -> set[str]:
    """Values an answer asserts: part numbers, serial-shaped identifiers, dates."""
    text = text or ""
    out = {m.group(0).upper() for m in PART_NUMBER_RE.finditer(text)}
    out |= {m.group(0) for m in _SERIAL_TOKEN.finditer(text.upper())}
    out |= set(_ISO_DATE.findall(text))
    for m, d, y in _US_DATE.findall(text):
        try:
            out.add(datetime(int(y), int(m), int(d)).date().isoformat())
        except ValueError:
            out.add(f"{m}/{d}/{y}")
    return out


def _known(facts_known: set[str]) -> set[str]:
    known = set(facts_known)
    for token in list(known):
        for m, d, y in _US_DATE.findall(token):
            try:
                known.add(datetime(int(y), int(m), int(d)).date().isoformat())
            except ValueError:
                pass
    return known


def verify(output: dict[str, Any], factbook: Any) -> dict[str, Any]:
    """Return {passed, blocked_tokens, dropped_fact_ids, output(cleaned)}."""
    by_id = factbook.by_id()
    known = _known(factbook.known_values())
    cleaned = {
        "answer": str(output.get("answer") or "").strip(),
        "cited_fact_ids": [i for i in output.get("cited_fact_ids") or [] if i in by_id],
        "derived_findings": [], "inferred": [],
        "missing_fields": [str(m) for m in output.get("missing_fields") or []][:30],
        "confidence": output.get("confidence") if output.get("confidence") in
        ("high", "medium", "low") else "medium",
    }
    dropped = [i for i in output.get("cited_fact_ids") or [] if i not in by_id]
    blocked: set[str] = set()

    def check(text: str) -> bool:
        bad = {t for t in claimed_tokens(text) if t.upper() not in known and t not in known}
        blocked.update(bad)
        return not bad

    answer_ok = bool(cleaned["answer"]) and check(cleaned["answer"])
    for item in output.get("derived_findings") or []:
        if not isinstance(item, dict):
            continue
        ids = [i for i in item.get("fact_ids") or [] if i in by_id]
        statement = str(item.get("statement") or "").strip()
        if statement and ids and check(statement):
            cleaned["derived_findings"].append({"statement": statement, "fact_ids": ids})
    for item in output.get("inferred") or []:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement") or "").strip()
        if statement and check(statement):
            cleaned["inferred"].append({
                "statement": statement,
                "basis_fact_ids": [i for i in item.get("basis_fact_ids") or [] if i in by_id]})
    return {"passed": answer_ok and not blocked, "blocked_tokens": sorted(blocked),
            "dropped_fact_ids": dropped, "output": cleaned}
