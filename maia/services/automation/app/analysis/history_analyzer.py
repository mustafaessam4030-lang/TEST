"""What changed between two stored SIS retrievals of the same serial.

Always names the two snapshots compared (file, run id, retrieval time).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from app.analysis.evidence import snapshot_ref
from app.domain.parts import flatten_parts

VOLATILE = {"retrieved_at", "automation_run_id", "data_hash", "field_provenance", "quality",
            "parts_data"}
SERIAL_FIELDS = {"serial_number", "machine_serial_number", "engine_serial_number"}
DATE_FIELDS = {"machine_build_date", "engine_build_date", "build_date"}


def _flat(record: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in record.items():
        if not prefix and k in VOLATILE:
            continue
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flat(v, key + "."))
        else:
            out[key] = v
    return out


def _parts_index(snap: Any) -> Counter:
    return Counter((r["group_name"] or "(unnamed)", r["part_number"] or "(none)")
                   for r in flatten_parts(snap.parts_data))


def _qty(snap: Any) -> dict[tuple[str, str], Any]:
    out: dict[tuple[str, str], Any] = {}
    for r in flatten_parts(snap.parts_data):
        if r["part_number"]:
            out.setdefault((r["group_name"] or "(unnamed)", r["part_number"]),
                           r["quantity_text"])
    return out


def compare_snapshots(old: Any, new: Any) -> dict[str, Any]:
    a, b = _flat(old.record), _flat(new.record)
    added = sorted(k for k in b if k not in a and b[k] not in (None, "", []))
    removed = sorted(k for k in a if k not in b and a[k] not in (None, "", []))
    changed = sorted(k for k in a.keys() & b.keys() if a[k] != b[k])
    field_changes = [{"field": k, "previous": a[k], "latest": b[k],
                      "kind": "serial" if k in SERIAL_FIELDS else
                      "build_date" if k in DATE_FIELDS else "field"} for k in changed]

    pa, pb = _parts_index(old), _parts_index(new)
    added_parts = sorted((pb - pa).elements())
    removed_parts = sorted((pa - pb).elements())
    qa, qb = _qty(old), _qty(new)
    qty_changes = [{"group": g, "part_number": pn, "previous": qa[(g, pn)],
                    "latest": qb[(g, pn)]}
                   for (g, pn) in sorted(qa.keys() & qb.keys()) if qa[(g, pn)] != qb[(g, pn)]]
    ga = {g for g, _ in pa}
    gb = {g for g, _ in pb}
    meta = [{"field": k, "previous": old.metadata.get(k), "latest": new.metadata.get(k)}
            for k in sorted(new.metadata) if k != "data_hash"
            and old.metadata.get(k) != new.metadata.get(k)]
    same_hash = bool(old.metadata.get("data_hash")) and \
        old.metadata.get("data_hash") == new.metadata.get("data_hash")
    return {
        "previous": snapshot_ref(old), "latest": snapshot_ref(new),
        "identical_data": same_hash and not (added or removed or changed or added_parts
                                             or removed_parts),
        "fields_added": added, "fields_removed": removed, "fields_changed": field_changes,
        "parts_added": [{"group": g, "part_number": pn} for g, pn in added_parts],
        "parts_removed": [{"group": g, "part_number": pn} for g, pn in removed_parts],
        "quantity_changes": qty_changes,
        "groups_added": sorted(gb - ga), "groups_removed": sorted(ga - gb),
        "serial_changes": [c for c in field_changes if c["kind"] == "serial"],
        "build_date_changes": [c for c in field_changes if c["kind"] == "build_date"],
        "metadata_changes": meta,
        "classification": "DERIVED",
        "calculation": "set difference of fields and (group, part_number) rows between "
                       "the two snapshots",
    }


def analyze_history(snaps: list[Any]) -> dict[str, Any]:
    timeline = [{"run_id": s.run_id, "retrieved_at": s.retrieved_at_iso, "snapshot": s.file}
                for s in snaps]
    if len(snaps) < 2:
        return {"snapshots": len(snaps), "timeline": timeline, "comparison": None,
                "message": "Only one stored SIS retrieval — nothing to compare yet."}
    return {"snapshots": len(snaps), "timeline": timeline,
            "comparison": compare_snapshots(snaps[-2], snaps[-1])}
