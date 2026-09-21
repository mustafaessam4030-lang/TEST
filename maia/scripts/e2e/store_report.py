"""Answer, from the files on disk, the questions a person actually asks.

Shared by `sis_lookup.py` (right after a run) and `show_sis_result.py` (any
time afterwards), so the two can never report a lookup differently.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def find(store: Path, serial: str, run_id: str | None = None) -> Path | None:
    """The result file for a serial — a named run, or the most recent one."""
    if run_id:
        candidate = store / f"{serial}_{run_id}.json"
        return candidate if candidate.exists() else None
    matches = sorted(store.glob(f"{serial}_run_*.json"),
                     key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def mark(value: Any) -> str:
    """A field is captured or it is not. `null` is an answer, not a maybe."""
    return f"YES  ({value})" if value not in (None, "", [], {}) else "NO   (null)"


def render(path: Path) -> str:
    doc = json.loads(path.read_text(encoding="utf-8"))
    parts = doc.get("parts_summary") or {}
    record = doc.get("record") or {}
    raw = (doc.get("raw_data") or {}).get("extracted_fields") or {}
    populated = {k: v for k, v in record.items() if v not in (None, "", [], {})}
    serial = doc.get("serial_number") or path.stem.split("_")[0]
    run_id = doc.get("run_id") or "unknown"
    shots = doc.get("screenshots") or {}
    groups = parts.get("group_titles") or []
    numbers = parts.get("part_numbers") or []
    txt = path.with_suffix(".txt")

    lines = [
        "=" * 72,
        f"  LOCAL STORE RESULT — {serial}",
        "=" * 72,
        f"  source                   : {doc.get('source')}",
        f"  run id                   : {run_id}",
        f"  retrieved at             : {doc.get('retrieved_at')}",
        f"  page title               : {doc.get('page_title')}",
        f"  final url                : {doc.get('final_url')}",
        "",
        f"  JSON                     : {path}",
        f"  TXT                      : {txt}{'' if txt.exists() else '   (MISSING)'}",
        f"  screenshots              : {path.parent / f'{serial}_{run_id}'}"
        f"  [{', '.join(sorted(shots)) or 'none'}]",
        "",
        f"  extracted field count    : {len(populated)} populated record fields, "
        f"{len(raw)} raw page fields, {len(doc.get('specifications') or [])} specifications",
        f"  Machine Serial Number    : {mark(doc.get('machine_serial_number'))}",
        f"  Machine Build Date       : {mark(doc.get('machine_build_date'))}",
        f"  Engine Serial Number     : {mark(doc.get('engine_serial_number'))}",
        f"  Engine Build Date        : {mark(doc.get('engine_build_date'))}",
        "  Parts group(s)           : "
        + mark(", ".join(groups)[:80] if groups else None),
        "  Part number(s)           : "
        + (f"YES  ({len(numbers)} parts, e.g. {', '.join(numbers[:4])})"
           if numbers else "NO   (null)"),
        f"  quality score            : {(doc.get('quality') or {}).get('score')}",
        f"  extraction status        : {doc.get('extraction_status')}",
    ]
    violations = ((doc.get("quality") or {}).get("violations")) or []
    if violations:
        lines.append(f"  violations               : {', '.join(violations)}")
    lines.append("=" * 72)
    return "\n".join(lines)
