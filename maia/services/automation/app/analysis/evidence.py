"""The evidence model. Every value Maia reports carries where it came from.

    DIRECT   read exactly as Caterpillar SIS published it (via the verified store)
    DERIVED  calculated here from DIRECT values; `calculation` names how

A calculated value is never labelled DIRECT.
"""
from __future__ import annotations

from typing import Any


def snapshot_ref(snap: Any) -> dict[str, Any]:
    return {"source": "SIS", "source_label": snap.source_label, "run_id": snap.run_id,
            "retrieved_at": snap.retrieved_at_iso, "snapshot": snap.file}


def direct(value: Any, *, field: str, snap: Any) -> dict[str, Any]:
    return {"value": value, "classification": "DIRECT", "field": field, **snapshot_ref(snap)}


def derived(value: Any, *, calculation: str, snap: Any | None = None,
            snaps: list[Any] | None = None, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"value": value, "classification": "DERIVED",
                           "calculation": calculation}
    if snap is not None:
        out.update(snapshot_ref(snap))
    if snaps:
        out["source"] = "SIS"
        out["snapshots"] = [snapshot_ref(s) for s in snaps]
    out.update(extra)
    return out
