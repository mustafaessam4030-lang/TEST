"""SIS Troubleshooting: rows read verbatim → structured entries → relationships.

    CODE → SYSTEM → SUBSYSTEM → COMPONENT → CONDITION / SYMPTOM

What is DIRECT: the row text, the section it was listed under, the count SIS
displayed, the code at the start of the row, and the description after it.
What is DERIVED: splitting the description into component and condition (the
method is named on every entry), grouping, counting, and the FMI consistency
check against the standard failure-mode table.

System and subsystem are filled only when SIS shows them; otherwise they are
reported as not published — never inferred from the code number.

Wording describes what SIS lists: "Troubleshooting code 36-1-5 identifies
Cylinder #1 Injector with Current Below Normal." — never "has failed".
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

CODE_RE = re.compile(
    r"^\s*(?P<code>(?:\d{1,4}-){1,2}\d{1,4}|E\d{2,5}(?:\s*\(\d\))?|[A-Z]{1,3}\d{2,6}(?:-\d{1,4}){0,2})"
    r"(?=[\s:–—-]|$)[\s:–—-]*(?P<rest>.*)$")

# Standard failure-mode identifiers (FMI), wording as Cat service literature uses it.
FMI_TEXT = {
    0: "high", 1: "low", 2: "erratic, intermittent, or incorrect", 3: "voltage above normal",
    4: "voltage below normal", 5: "current below normal", 6: "current above normal",
    7: "not responding properly", 8: "abnormal frequency, pulse width, or period",
    9: "abnormal update rate", 10: "abnormal rate of change", 11: "other failure mode",
    12: "failure", 13: "out of calibration", 14: "special instruction",
    15: "high - least severe", 16: "high - moderate severity", 17: "low - least severe",
    18: "low - moderate severity", 19: "received network data in error",
    31: "condition exists",
}
CONDITIONS = sorted({*FMI_TEXT.values(), "data erratic, intermittent, or incorrect",
                     "open circuit", "short to ground", "short to battery", "signal not present",
                     "above normal", "below normal", "not responding", "intermittent"},
                    key=len, reverse=True)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def parse_row(text: str, section: str) -> dict[str, Any]:
    text = _norm(text)
    entry: dict[str, Any] = {"raw": text, "section": section, "code": None, "description": text,
                             "system": None, "subsystem": None, "component": None,
                             "condition": None, "fmi": None, "split_method": None,
                             "kind": "symptom" if section == "Symptoms" else "code"}
    m = CODE_RE.match(text)
    if m and m.group("rest"):
        entry["code"] = m.group("code").replace(" ", "")
        entry["description"] = _norm(m.group("rest"))
        parts = entry["code"].split("-")
        if len(parts) >= 2 and parts[-1].isdigit():
            entry["fmi"] = int(parts[-1])
    desc = entry["description"]
    if entry["kind"] == "symptom" and not entry["code"]:
        entry["condition"] = desc
        entry["split_method"] = "symptom row: the whole text is the symptom"
        return entry
    if " : " in desc or ": " in desc:
        comp, cond = [x.strip() for x in re.split(r"\s*:\s*", desc, maxsplit=1)]
        entry.update(component=comp or None, condition=cond or None,
                     split_method="split at ':'")
        return entry
    low = desc.lower()
    for phrase in CONDITIONS:
        if low.endswith(phrase) and len(low) > len(phrase) + 2:
            cut = len(desc) - len(phrase)
            entry.update(component=desc[:cut].strip(" -–—,") or None,
                         condition=desc[cut:].strip(),
                         split_method=f"trailing condition phrase '{phrase}'")
            return entry
    if entry["code"]:
        entry["component"] = desc
        entry["split_method"] = "no condition phrase found: whole description kept as component"
    return entry


def parse_troubleshooting(tr: dict[str, Any] | None) -> dict[str, Any]:
    tr = tr or {}
    entries = []
    for sec in tr.get("sections") or []:
        for row in sec.get("rows") or []:
            entries.append(parse_row(row, sec.get("section") or ""))
    return {"status": tr.get("status") or "NOT_CAPTURED", "entries": entries,
            "sections": [{k: s.get(k) for k in ("section", "count_displayed", "items_read")}
                         for s in tr.get("sections") or []],
            "reason": tr.get("reason")}


def fmi_check(entry: dict[str, Any]) -> str | None:
    if entry.get("fmi") is None or not entry.get("condition"):
        return None
    std = FMI_TEXT.get(entry["fmi"])
    if std is None:
        return "UNKNOWN_FMI"
    return "CONSISTENT" if std in entry["condition"].lower() or entry["condition"].lower() in std \
        else "DIFFERS"


def relationship_text(e: dict[str, Any]) -> str:
    if e.get("code") and e.get("component") and e.get("condition"):
        return (f"Troubleshooting code {e['code']} identifies {e['component']} "
                f"with {e['condition']}.")
    if e.get("code"):
        return f"Troubleshooting code {e['code']}: {e['description']}."
    return f"SIS lists the symptom: {e['description']}."


def analyze_troubleshooting(tr: dict[str, Any] | None, *, code: str | None = None,
                            ref: dict[str, Any] | None = None) -> dict[str, Any]:
    parsed = parse_troubleshooting(tr)
    entries = parsed["entries"]
    for e in entries:
        e["fmi_check"] = fmi_check(e)
        e["relationship"] = relationship_text(e)
        e["evidence"] = {"row": {"classification": "DIRECT", "source": "SIS",
                                 "section": e["section"], **(ref or {})},
                         "split": {"classification": "DERIVED", "method": e["split_method"]}}
    selected = [e for e in entries if not code or (e["code"] or "").upper() == code.upper()]
    tree: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for e in selected:
        if e["kind"] != "code":
            continue
        tree[e["system"] or "(system not published by SIS)"][e["component"] or "(component not split)"
                                                            ].append(
            {"code": e["code"], "condition": e["condition"], "section": e["section"]})
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in entries:
        if e["code"]:
            by_code[e["code"]].append(e)
    duplicates = sorted(c for c, es in by_code.items() if len(es) > 1)
    conflicts = sorted(c for c, es in by_code.items() if len({x["description"] for x in es}) > 1)
    comp_codes: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        if e["component"] and e["code"]:
            comp_codes[e["component"]].add(e["code"])
    sys_comps: dict[str, set[str]] = defaultdict(set)
    for e in entries:
        if e["system"] and e["component"]:
            sys_comps[e["system"]].add(e["component"])
    return {
        "status": parsed["status"], "reason": parsed["reason"],
        "sections": parsed["sections"],
        "entries": selected, "entry_count": len(selected), "all_entries": len(entries),
        "codes": sorted({e["code"] for e in selected if e["code"]}),
        "systems": sorted({e["system"] for e in selected if e["system"]}),
        "components": sorted({e["component"] for e in selected if e["component"]
                              and e["kind"] == "code"}),
        "conditions": sorted({e["condition"] for e in selected if e["condition"]
                              and e["kind"] == "code"}),
        "symptoms": sorted({e["description"] for e in selected if e["kind"] == "symptom"}),
        "tree": {s: dict(c) for s, c in sorted(tree.items())},
        "components_with_multiple_codes": {c: sorted(v) for c, v in sorted(comp_codes.items())
                                           if len(v) > 1},
        "systems_with_multiple_components": {s: sorted(v) for s, v in sorted(sys_comps.items())
                                             if len(v) > 1},
        "duplicate_codes": duplicates, "conflicting_codes": conflicts,
        "fmi_differs": sorted({e["code"] for e in entries if e.get("fmi_check") == "DIFFERS"}),
        "filter": {"code": code} if code else None,
        "classification": {"rows": "DIRECT", "component_condition_split": "DERIVED",
                           "grouping_and_counts": "DERIVED"},
    }
