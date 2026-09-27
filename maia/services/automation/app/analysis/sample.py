"""A SAMPLE store for demos and tests — written by the real LocalJsonRepository,
so its format is exactly what a live SIS run writes.

It is marked `"sample": true` in every file, and the analysis engine refuses to
treat it as a real retrieval unless explicitly asked (`include_samples=True`).

Content: the identity values of JAZ01865 as observed on SIS (machine/engine
serials and build dates) and the part numbers seen on its page, arranged in
the table layout SIS uses (Part Number, Serial Number, Part Name, Install
Ind., Install Date, Description). Group sizes, names and extra rows are
ILLUSTRATIVE — the real stored file on the user's PC is the source of truth.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

COLS = ["Part Number", "Serial Number", "Part Name", "Install Ind.", "Install Date",
        "Description"]


def _row(*cells: str) -> dict:
    return {"cells": list(cells), "values": dict(zip(COLS, cells))}


def _groups(serial: str, engine: str, extra: bool) -> list[dict]:
    product = [_row("444-5867", "", "General AR", "Factory", "", "GENERAL ARRANGEMENT"),
               _row("256-3170", "", "Generator Gp", "Factory", "", "GENERATOR GROUP"),
               _row("399-2338", "", "Control Panel Gp", "Factory", "", "")]
    eng = [_row("269-7022", engine, "Engine Ar", "Factory", "", "ENGINE ARRANGEMENT"),
           _row("421-8926", "", "Exhaust Gp", "Factory", "", "EXHAUST GROUP"),
           _row("252-7394", "", "Fuel Filter Gp", "Factory", "", "FUEL FILTER GROUP"),
           _row("355-9019", "", "Cooling Gp", "Factory", "", "COOLING GROUP"),
           _row("444-5867", "", "General AR", "Factory", "", "GENERAL ARRANGEMENT"),
           _row("", "", "Lines Gp", "Factory", "", "")]
    gen = [_row("256-3170", "", "Generator Gp", "Factory", "", "GENERATOR GROUP")]
    if extra:
        eng.append(_row("286-4915", "", "Clamp", "Factory", "", "CLAMP"))
    groups = [
        {"title": f"Product - Entire Group ({serial})", "group_serial": serial,
         "is_entire_group": True, "columns": COLS, "rows": product},
        {"title": f"Engine - Entire Group ({engine})", "group_serial": engine,
         "is_entire_group": True, "columns": COLS, "rows": eng},
        {"title": f"Generator - Entire Group ({serial})", "group_serial": serial,
         "is_entire_group": True, "columns": COLS, "rows": gen},
        {"title": f"Attachments - Entire Group ({serial})", "group_serial": serial,
         "is_entire_group": False, "columns": COLS, "rows": []},
    ]
    return groups


async def _write(root: Path, now: datetime) -> list[Path]:
    from app.models.schemas import EquipmentRecord
    from app.repositories.base import ExtractionArtifact
    from app.repositories.local_json_repo import LocalJsonRepository

    store = LocalJsonRepository(root)
    serial, engine = "JAZ01865", "PRH04588"
    for n, (when, extra) in enumerate(((now - timedelta(days=12), False),
                                       (now - timedelta(days=2), True)), 1):
        groups = _groups(serial, engine, extra)
        record = EquipmentRecord(
            serial_number=serial, source_system="cat_sis", retrieved_at=when,
            automation_run_id=f"run_SAMPLE{n:02d}", equipment_model="C32",
            equipment_type="Generator Set", machine_serial_number=serial,
            machine_build_date="2014-08-02", engine_serial_number=engine,
            engine_build_date="2014-06-30",
            parts_data={"group_titles": [g["title"] for g in groups],
                        "group_count": len(groups),
                        "entire_group_title": groups[0]["title"], "columns": COLS,
                        "total_rows": sum(len(g["rows"]) for g in groups),
                        "serial_mismatched_groups": [], "groups": groups})
        await store.upsert(record, extraction=ExtractionArtifact(
            fields={"machine_serial_number": serial}, parts_data=record.parts_data,
            selector_version="sample"))
    files = sorted(root.glob(f"{serial}_run_SAMPLE*.json"))
    for f in files:                       # mark every file as a sample
        doc = json.loads(f.read_text(encoding="utf-8"))
        doc["sample"] = True
        doc["note"] = "SAMPLE for demos/tests — NOT a real SIS retrieval."
        f.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    return files


def write_sample_store(root: str | Path, now: datetime | None = None) -> list[Path]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_write(root, now or datetime.now(timezone.utc)))
