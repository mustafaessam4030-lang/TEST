"""The Data Quality score — transparent, configurable, and refused when unfounded.

    score = Σ(weight_i × component_i) / Σ(weight_i over computable components)

Components (0..1), each with its inputs shown:
    completeness        expected identity fields populated
    parts_completeness  part rows carrying a part number and a name/description
    validity            serial / date / part-number formats
    consistency         machine serial = searched, groups belong to it, no duplicate rows
    freshness           1 up to fresh_days, falling linearly to 0 at expired_days

If fewer than `min_components` can be computed — an empty result, say — there
is no score: "Insufficient data to calculate quality score."
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.analysis.equipment_analyzer import SERIAL_RE, as_date


def _round(x: float) -> float:
    return round(x, 4)


def analyze_quality(snap: Any, eq: dict[str, Any], parts: dict[str, Any], as_of: datetime,
                    cfg: dict[str, Any]) -> dict[str, Any]:
    qcfg = cfg.get("quality") or {}
    weights = qcfg.get("weights") or {}
    components: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []
    rec = snap.record

    expected = eq["fields"]["expected"]["value"]
    populated = eq["fields"]["populated"]["value"]
    if expected and (populated or parts["total_records"]["value"]):
        components["completeness"] = {"value": _round(populated / expected),
                                      "inputs": {"populated": populated, "expected": expected}}
        reasons.append(f"{round(100 * populated / expected)}% of expected fields populated "
                       f"({populated} of {expected})")

    rows = parts["rows"]
    if rows:
        cols = parts["columns_published"]
        text_col = "part_name" if "part_name" in cols else (
            "description" if "description" in cols else None)
        complete = [r for r in rows if r["part_number"] and (text_col is None or r[text_col])]
        components["parts_completeness"] = {
            "value": _round(len(complete) / len(rows)),
            "inputs": {"complete_rows": len(complete), "rows": len(rows),
                       "requires": ["part_number"] + ([text_col] if text_col else [])}}
        gaps = len(rows) - len(complete)
        reasons.append(f"{gaps} of {len(rows)} part rows lack a part number"
                       + (f" or {text_col.replace('_', ' ')}" if text_col else "")
                       if gaps else f"all {len(rows)} part rows carry a part number"
                       + (f" and {text_col.replace('_', ' ')}" if text_col else ""))
        missing_qty = parts["missing_quantities"]["value"]
        if missing_qty:
            reasons.append(f"{missing_qty} part rows have no quantity recorded")
        missing_desc = parts["missing_descriptions"]["value"]
        if missing_desc:
            reasons.append(f"{missing_desc} part rows have an empty description")

    # validity: each applicable check passes or fails
    checks: list[tuple[str, bool]] = []
    checks.append(("searched serial has a valid format", bool(SERIAL_RE.match(snap.serial))))
    for f in ("machine_serial_number", "engine_serial_number"):
        if rec.get(f):
            checks.append((f"{f.replace('_', ' ')} has a valid format",
                           bool(SERIAL_RE.match(str(rec[f])))))
    for f in ("machine_build_date", "engine_build_date", "build_date"):
        if rec.get(f):
            d = as_date(rec[f])
            checks.append((f"{f.replace('_', ' ')} is a real past date",
                           bool(d and 1950 <= d.year and d <= as_of.date())))
    numbers = [r["part_number"] for r in rows if r["part_number"]]
    if numbers:
        bad = len(parts["malformed_part_numbers"]["value"])
        checks.append((f"part numbers match the expected pattern ({bad} do not)", bad == 0))
    if checks:
        passed = sum(1 for _, ok in checks if ok)
        components["validity"] = {"value": _round(passed / len(checks)),
                                  "inputs": {"passed": passed, "checks": len(checks),
                                             "detail": [{"check": c, "pass": ok}
                                                        for c, ok in checks]}}
        for c, ok in checks:
            if not ok:
                reasons.append(f"validity: {c} — failed")
        if rec.get("machine_serial_number") and SERIAL_RE.match(str(rec["machine_serial_number"])):
            reasons.append("machine serial is valid")
        if rec.get("engine_serial_number"):
            reasons.append("engine serial is present")

    # consistency
    ccheck = [c for c in eq["consistency"] if c["result"] in ("PASS", "FAIL")]
    dupe_rows = len(parts["exact_duplicate_rows"]["value"])
    ctotal = len(ccheck) + (1 if rows else 0)
    if ctotal:
        cpass = sum(1 for c in ccheck if c["result"] == "PASS") + (1 if rows and not dupe_rows
                                                                   else 0)
        components["consistency"] = {"value": _round(cpass / ctotal),
                                     "inputs": {"passed": cpass, "checks": ctotal,
                                                "duplicate_rows": dupe_rows}}
        for c in ccheck:
            if c["result"] == "FAIL":
                reasons.append(f"consistency: {c['check']} — failed")
        if dupe_rows:
            reasons.append(f"{dupe_rows} identical part rows repeated within a group")

    fr = eq["freshness"]
    if fr["value"] is not None:
        fcfg = cfg.get("freshness") or {}
        fresh, expired = fcfg.get("fresh_days", 30), fcfg.get("expired_days", 365)
        age = fr["value"]
        val = 1.0 if age <= fresh else max(0.0, 1 - (age - fresh) / max(1, expired - fresh))
        components["freshness"] = {"value": _round(val),
                                   "inputs": {"age_days": age, "fresh_days": fresh,
                                              "expired_days": expired}}
        reasons.append(f"retrieval is {fr['status'].lower()} ({age:g} days old)")

    applicable = {k: v for k, v in components.items() if weights.get(k)}
    if len(applicable) < int(qcfg.get("min_components", 3)):
        return {"score_pct": None, "status": "INSUFFICIENT_DATA",
                "message": "Insufficient data to calculate quality score.",
                "components": components, "reasons": reasons,
                "classification": "DERIVED",
                "calculation": "requires at least "
                               f"{qcfg.get('min_components', 3)} computable components"}
    total_w = sum(weights[k] for k in applicable)
    score = sum(weights[k] * v["value"] for k, v in applicable.items()) / total_w
    for k, v in components.items():
        v["weight"] = weights.get(k)
    return {"score_pct": round(100 * score, 1), "status": "OK",
            "components": components, "reasons": reasons, "classification": "DERIVED",
            "calculation": "Σ(weight × component) / Σ(weight) over "
                           + ", ".join(sorted(applicable)),
            "weights": {k: weights[k] for k in sorted(applicable)}}
