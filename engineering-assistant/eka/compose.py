"""Luma's voice for extractive answers: natural sentences built only from source values.

A specification row such as ``Parameter: Rated pressure | Value: 350 bar (5076 psi) |
Notes: Continuous duty`` becomes "The HX-200's rated pressure is 350 bar (5076 psi),
continuous duty." Every value in the sentence is copied from a cell of the row, and the
caller re-checks the sentence with ``citations.unsupported_values`` before using it, so
composition can change the wording but never a number, unit or part number.
"""
from __future__ import annotations

import re

from .textutil import compact, content_terms, identifiers, normalize, tokens

VALUE_HEADERS = re.compile(r"^(value|values|rating|setting|specification|spec|data)$", re.I)
CONDITION_HEADERS = re.compile(r"^(notes?|conditions?|remarks?|comments?)$", re.I)
UNIT_IN_HEADER = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")
PART_NUMBER_NOTE = re.compile(r"^part (number|no\.?)$", re.I)
CONDITION_PREPOSITION = re.compile(r"^(at|under|for|with|without|in|during|per|when|above|below|up to)\b", re.I)


def short_equipment(doc: dict) -> str:
    """The model designation people say ("HX-200"), else the equipment or title."""
    text = " ".join((doc.get("equipment") or "", doc.get("title") or ""))
    ids = sorted((i for i in identifiers(text) if "-" in i or any(c.isalpha() for c in i)), key=len)
    ids = [i for i in ids if any(c.isdigit() for c in i) and any(c.isalpha() for c in i)]
    if ids:
        first = ids[0]
        # keep the spelling the document uses
        match = re.search(re.escape(first), text, re.I)
        return match.group(0) if match else first
    return (doc.get("equipment") or doc.get("title") or doc.get("filename") or "").strip()


def possessive(name: str) -> str:
    return name + ("'" if name.endswith("s") else "'s")


def parse_row(text: str) -> tuple[str, list[tuple[str, str]]]:
    """A table-row chunk -> (caption, [(header, value), ...])."""
    lines = text.split("\n")
    caption = lines[0] if len(lines) > 1 else ""
    cells = []
    for cell in lines[-1].split(" | "):
        header, _, value = cell.partition(": ") if ": " in cell else ("", "", cell)
        cells.append((header.strip(), value.strip()))
    return caption, cells


def _lower_first(text: str) -> str:
    if not text:
        return text
    if len(text) > 1 and text[1].isupper():   # an acronym ("PTO speed") keeps its case
        return text
    return text[0].lower() + text[1:]


def _with_unit(value: str, header: str) -> tuple[str, str]:
    """('210', 'Torque (N.m)') -> ('210 N.m', 'Torque')."""
    m = UNIT_IN_HEADER.match(header)
    if m and value and not re.search(r"[A-Za-z]", value):
        return "{0} {1}".format(value, m.group(2)), m.group(1)
    return value, (m.group(1) if m else header)


def row_sentence(row_text: str, subject: str, question: str = "") -> str | None:
    """A natural sentence for one specification row, or None when the row has no clear shape."""
    _, cells = parse_row(row_text)
    if len(cells) < 2 or not cells[0][1]:
        return None
    label = cells[0][1]
    rest = [(h, v) for h, v in cells[1:] if v]
    if not rest:
        return None
    asked = set(content_terms(question))
    owner = "The " + possessive(subject) if subject else "The"

    value_cells = [(h, v) for h, v in rest if VALUE_HEADERS.match(h)]
    if value_cells:  # Parameter | Value | Notes
        value = value_cells[0][1]
        notes = [v for h, v in rest if CONDITION_HEADERS.match(h)]
        if notes and PART_NUMBER_NOTE.match(notes[0]):
            return "{0} {1} part number is {2}.".format(owner, _lower_first(label), value)
        sentence = "{0} {1} is {2}".format(owner, _lower_first(label), value)
        if notes:
            note = _lower_first(notes[0])
            sentence += (" " if CONDITION_PREPOSITION.match(note) else ", ") + note
        return _capital(sentence) + "."

    # Fastener | Thread | Torque (N.m) | Torque (lb.ft): pick the columns the question asks about
    with_units = [(_with_unit(v, h), h) for h, v in rest]
    matched = [(vu, h) for vu, h in with_units if asked & set(content_terms(vu[1] or h))]
    if matched:
        base = matched[0][0][1] or matched[0][1]
        values = [vu[0] for vu, _ in matched]
        others = [vu for vu, h in with_units if all(vu[0] != m[0][0] for m in matched)]
        sentence = "{0} {1} {2} is {3}".format(owner, _lower_first(label), _lower_first(base), values[0])
        if len(values) > 1:
            sentence += " ({0})".format(", ".join(values[1:]))
        extra = [("{0} {1}".format(_lower_first(name), value)) for value, name in others if name]
        if extra:
            sentence += "; {0}".format(", ".join(extra))
        return _capital(sentence) + "."
    parts = ["{0} {1}".format(_lower_first(name), value) if name else value for (value, name), _ in with_units]
    return _capital("{0} {1}: {2}".format(owner, _lower_first(label), ", ".join(parts))) + "."


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def row_cells(row_text: str, question: str = "") -> dict:
    """Structured cells for comparison tables: label, value (with unit), condition."""
    caption, cells = parse_row(row_text)
    label = cells[0][1] if cells else ""
    rest = [(h, v) for h, v in cells[1:] if v]
    value_cells = [(h, v) for h, v in rest if VALUE_HEADERS.match(h)]
    asked = set(content_terms(question))
    matched = [_with_unit(v, h) for h, v in rest if asked & set(content_terms(_with_unit(v, h)[1]))]
    if value_cells:
        value = value_cells[0][1]
        condition = next((v for h, v in rest if CONDITION_HEADERS.match(h)), "")
    elif matched:
        value = matched[0][0] + (" ({0})".format(", ".join(m[0] for m in matched[1:])) if len(matched) > 1 else "")
        condition = ""
        measure = matched[0][1]
    else:
        value = ", ".join(_with_unit(v, h)[0] if UNIT_IN_HEADER.match(h) else
                          ("{0} {1}".format(h, v) if h else v) for h, v in rest)
        condition = ""
    return {"caption": caption, "label": label, "value": value, "condition": condition,
            "measure": locals().get("measure", "")}


def question_topic(question: str) -> str:
    """'What is the maximum lifting capacity of the TL-5?' -> 'maximum lifting capacity of the TL-5'."""
    text = normalize(question).strip().rstrip("?.! ")
    text = re.sub(r"^(please\s+)?(can you\s+|could you\s+)?(tell me\s+|show me\s+|give me\s+)?", "", text, flags=re.I)
    stripped = re.sub(r"^(what|which)\s+(is|are|was|were)\s+(the\s+)?", "", text, flags=re.I)
    if stripped == text or len(stripped) > 90 or len(stripped.split()) < 2:
        return ""        # only "what is the X of the Y" questions name a topic cleanly
    text = stripped
    return "the " + text if not re.match(r"^(the|a|an)\b", text, re.I) else text


def same_condition(a: str, b: str) -> bool:
    return compact(a) == compact(b)


def numeric_value(value: str) -> tuple[float, str] | None:
    """'350 bar (5076 psi)' -> (350.0, 'bar'); ranges and part numbers -> None."""
    parts = value.split()
    if not parts or not re.fullmatch(r"-?\d+(?:\.\d+)?", parts[0]):
        return None     # part numbers, ratios ("32:1") and words are not quantities
    if re.match(r"^\s*-?\d+(?:\.\d+)?\s*(to|-)\s*\d", value):
        return None     # ranges
    unit = parts[1] if len(parts) > 1 and re.fullmatch(r"[A-Za-z][A-Za-z0-9./%]*", parts[1]) else ""
    return float(parts[0]), unit


def lower_heading(text: str) -> str:
    """'Shaft Seal Replacement' -> 'shaft seal replacement' (acronyms and model names keep their case)."""
    return " ".join(w if (w.isupper() or any(c.isdigit() for c in w)) and len(w) > 1 else w.lower()
                    for w in text.split())


def plain_heading(section: str) -> str:
    """'HX-200 Hydraulic Pump Service Manual > 2 Specifications' -> 'Specifications'."""
    last = section.split(" > ")[-1]
    return re.sub(r"^[\d.]+\s+", "", last).strip()


def words(text: str) -> set[str]:
    return set(tokens(text))
