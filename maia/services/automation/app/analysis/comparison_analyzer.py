"""Two machines side by side: IDENTICAL / DIFFERENT / MISSING per attribute."""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.analysis.evidence import snapshot_ref
from app.domain.parts import flatten_parts

ATTRIBUTES = ("equipment_model", "equipment_type", "manufacturer", "build_date",
              "machine_build_date", "engine_build_date", "machine_serial_number",
              "engine_serial_number")


def _status(a: Any, b: Any) -> str:
    if a in (None, "") or b in (None, ""):
        return "MISSING"
    return "IDENTICAL" if a == b else "DIFFERENT"


def compare_equipment(sa: Any, sb: Any, qa: dict[str, Any], qb: dict[str, Any],
                      fa: dict[str, Any], fb: dict[str, Any]) -> dict[str, Any]:
    ra, rb = sa.record, sb.record
    attrs = [{"attribute": k, "a": ra.get(k), "b": rb.get(k), "status": _status(ra.get(k),
                                                                              rb.get(k))}
             for k in ATTRIBUTES]
    rows_a, rows_b = flatten_parts(sa.parts_data), flatten_parts(sb.parts_data)
    pa = {r["part_number"] for r in rows_a if r["part_number"]}
    pb = {r["part_number"] for r in rows_b if r["part_number"]}
    ga = {r["group_name"] for r in rows_a if r["group_name"]}
    gb = {r["group_name"] for r in rows_b if r["group_name"]}

    def qty(rows: list[dict[str, Any]]) -> Counter:
        c: Counter = Counter()
        for r in rows:
            if r["part_number"] and r["quantity_required"] is not None:
                c[r["part_number"]] += r["quantity_required"]
        return c

    qta, qtb = qty(rows_a), qty(rows_b)
    qty_diff = [{"part_number": pn, "a": qta[pn], "b": qtb[pn]}
                for pn in sorted(set(qta) & set(qtb)) if qta[pn] != qtb[pn]]
    union = pa | pb
    return {
        "a": {"serial": sa.serial, **snapshot_ref(sa)},
        "b": {"serial": sb.serial, **snapshot_ref(sb)},
        "attributes": attrs,
        "parts": {
            "total_a": len(rows_a), "total_b": len(rows_b),
            "unique_a": len(pa), "unique_b": len(pb),
            "common": sorted(pa & pb), "only_a": sorted(pa - pb), "only_b": sorted(pb - pa),
            "similarity_pct": round(100 * len(pa & pb) / len(union), 1) if union else None,
            "quantity_differences": qty_diff,
        },
        "groups": {"common": sorted(ga & gb), "only_a": sorted(ga - gb),
                   "only_b": sorted(gb - ga)},
        "quality": {"a": qa.get("score_pct"), "b": qb.get("score_pct")},
        "freshness": {"a": {"days": fa.get("value"), "status": fa.get("status")},
                      "b": {"days": fb.get("value"), "status": fb.get("status")}},
        "classification": "DERIVED",
        "calculation": "attribute equality; set operations on part numbers and groups",
    }
