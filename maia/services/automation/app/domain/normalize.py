"""raw source payload -> canonical EquipmentRecord.

Rules: nothing is invented. A field the source does not publish becomes `null`
with reason NOT_PUBLISHED; a field we failed to read is omitted and recorded as
a violation. Units are split, and the raw text is always retained.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.models.schemas import (
    EngineFamily, EquipmentRecord, FieldProvenance, Quality, Specification,
    normalize_serial,
)

# Controlled vocabulary. Anything unmapped stays UNKNOWN rather than being guessed.
TYPE_VOCAB: dict[str, str] = {
    "excavator": "HYDRAULIC_EXCAVATOR",
    "hydraulic excavator": "HYDRAULIC_EXCAVATOR",
    "track-type tractor": "TRACK_TYPE_TRACTOR",
    "dozer": "TRACK_TYPE_TRACTOR",
    "wheel loader": "WHEEL_LOADER",
    "loader": "WHEEL_LOADER",
    "motor grader": "MOTOR_GRADER",
    "grader": "MOTOR_GRADER",
    "backhoe loader": "BACKHOE_LOADER",
    "articulated truck": "ARTICULATED_TRUCK",
    "generator set": "GENERATOR_SET",
    "genset": "GENERATOR_SET",
    "engine": "ENGINE",
}

_NUM_UNIT = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([A-Za-z°%/]+(?:\s?[A-Za-z°%/]+)?)?\s*$")
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def split_value_unit(raw: str) -> tuple[float | str | None, str | None]:
    """'2600 kg' -> (2600.0, 'kg'). Non-numeric text is kept verbatim as the value."""
    if raw is None:
        return None, None
    text = str(raw).strip()
    if not text:
        return None, None
    m = _NUM_UNIT.match(text)
    if not m:
        return text, None
    number = float(m.group(1).replace(",", "."))
    return number, (m.group(2).strip() if m.group(2) else None)


def parse_build_date(raw: str | None, *, date_order: str = "day_first") -> str | None:
    """Return ISO-8601, keeping the precision the source actually gave.

    `date_order` settles only what the value itself cannot: a day past the 12th
    fixes the order on its own, whatever the setting says. It is the source
    contract that declares the order (SIS publishes MM/DD/YYYY), never a guess
    made here, and an unparseable value stays null with a violation.
    """
    if not raw:
        return None
    text = str(raw).strip()
    for pattern, fmt in ((r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d"),
                         (r"^\d{4}-\d{2}$", "%Y-%m"),
                         (r"^\d{4}$", "%Y")):
        if re.match(pattern, text):
            return text
    m = re.match(r"^(\d{2})/(\d{4})$", text)             # 07/2019
    if m:
        return f"{m.group(2)}-{m.group(1)}"
    m = re.match(r"^([A-Za-z]{3})[a-z]*\s+(\d{4})$", text)  # Jul 2019
    if m and m.group(1).lower() in _MONTHS:
        return f"{m.group(2)}-{_MONTHS[m.group(1).lower()]:02d}"
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", text)
    if m:
        first, second, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if first > 12 and second <= 12:
            day, month = first, second            # day-first, unambiguously
        elif second > 12 and first <= 12:
            month, day = first, second            # month-first, unambiguously
        elif date_order == "month_first":
            month, day = first, second
        else:
            day, month = first, second
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return None
    return None  # unparseable: better null + violation than a fabricated date


def map_equipment_type(raw: str | None) -> str | None:
    if not raw:
        return None
    key = str(raw).strip().lower()
    for token, mapped in TYPE_VOCAB.items():
        if token in key:
            return mapped
    return "UNKNOWN"


def normalize_equipment_data(
    raw: dict[str, Any],
    *,
    serial_number: str,
    source_system: str,
    source_url: str | None,
    retrieved_at: datetime,
    run_id: str | None,
    field_map: dict[str, str] | None = None,
    selector_version: str | None = None,
    date_order: str = "day_first",
) -> EquipmentRecord:
    """Apply the source's declarative field mapping and build the canonical record."""
    field_map = field_map or {}
    fields = raw.get("fields", raw) or {}

    def pick(canonical: str) -> tuple[Any, str | None]:
        """Return (value, selector_id) for a canonical field via the source mapping."""
        source_key = field_map.get(canonical, canonical)
        value = fields.get(source_key)
        return (value if value not in ("", []) else None), source_key

    provenance: dict[str, FieldProvenance] = {}
    violations: list[str] = []

    def record(canonical: str, value: Any, selector: str | None,
               value_source: str = "dom") -> Any:
        if value is None:
            provenance[canonical] = FieldProvenance(
                selector_id=selector, value_source=value_source, confidence=1.0,
                reason="NOT_PUBLISHED")
            return None
        provenance[canonical] = FieldProvenance(
            selector_id=selector, value_source=value_source, confidence=1.0, reason="OK")
        return value

    value_source = "xhr" if raw.get("payload_kind") == "xhr" else "dom"

    model_v, model_sel = pick("equipment_model")
    type_v, type_sel = pick("equipment_type")
    build_v, build_sel = pick("build_date")
    engine_v, engine_sel = pick("engine_family")
    parts_url_v, parts_url_sel = pick("parts_manual_url")
    op_url_v, op_url_sel = pick("operation_manual_url")

    build_iso = parse_build_date(build_v, date_order=date_order) if build_v else None
    if build_v and not build_iso:
        violations.append(f"build_date_unparseable:{str(build_v)[:32]}")

    # ── the equipment-details block ─────────────────────────────────────────
    machine_serial_v, machine_serial_sel = pick("machine_serial_number")
    machine_build_v, machine_build_sel = pick("machine_build_date")
    engine_serial_v, engine_serial_sel = pick("engine_serial_number")
    engine_build_v, engine_build_sel = pick("engine_build_date")

    def as_date(canonical: str, value: Any) -> str | None:
        if not value:
            return None
        iso = parse_build_date(value, date_order=date_order)
        if not iso:
            violations.append(f"{canonical}_unparseable:{str(value)[:32]}")
        return iso

    machine_build_iso = as_date("machine_build_date", machine_build_v)
    engine_build_iso = as_date("engine_build_date", engine_build_v)

    # The machine serial is the page's own answer to "which machine is this?".
    # Disagreeing with the serial that was searched means the wrong record was
    # read — recorded here, and refused by the validator.
    if machine_serial_v and normalize_serial(str(machine_serial_v)) != normalize_serial(serial_number):
        violations.append("serial_mismatch:machine_serial_number")

    specs: list[Specification] = []
    for idx, row in enumerate(raw.get("specifications") or []):
        name = (row.get("name") or "").strip()
        if not name:
            continue
        value, unit = split_value_unit(row.get("value"))
        spec_id = f"p_{idx:03d}"
        specs.append(Specification(
            group=row.get("group"), name=name, value=value,
            unit=unit or row.get("unit"), value_raw=str(row.get("value") or "") or None,
            provenance_id=spec_id))

    engine = None
    if isinstance(engine_v, dict):
        engine = EngineFamily(**{k: engine_v.get(k) for k in ("model", "arrangement", "emissions")})
    elif engine_v:
        engine = EngineFamily(model=str(engine_v).strip())

    record_obj = EquipmentRecord(
        serial_number=normalize_serial(serial_number),
        serial_number_raw=raw.get("serial_number_raw") or serial_number,
        equipment_model=record("equipment_model", (str(model_v).strip() if model_v else None), model_sel, value_source),
        equipment_type=record("equipment_type", map_equipment_type(type_v), type_sel, value_source),
        manufacturer=record("manufacturer", raw.get("manufacturer") or "Caterpillar", "constant", "derived"),
        build_date=record("build_date", build_iso, build_sel, value_source),
        machine_serial_number=record(
            "machine_serial_number",
            (str(machine_serial_v).strip() if machine_serial_v else None),
            machine_serial_sel, value_source),
        machine_build_date=record("machine_build_date", machine_build_iso,
                                  machine_build_sel, value_source),
        engine_serial_number=record(
            "engine_serial_number",
            (str(engine_serial_v).strip() if engine_serial_v else None),
            engine_serial_sel, value_source),
        engine_build_date=record("engine_build_date", engine_build_iso,
                                 engine_build_sel, value_source),
        engine_family=record("engine_family", engine, engine_sel, value_source),
        specifications=specs,
        parts_data=raw.get("parts_data"),
        parts_manual_url=record("parts_manual_url", parts_url_v, parts_url_sel, value_source),
        operation_manual_url=record("operation_manual_url", op_url_v, op_url_sel, value_source),
        source_system=source_system,
        source_url=source_url,
        retrieved_at=retrieved_at,
        automation_run_id=run_id,
        quality=Quality(violations=violations),
        field_provenance=provenance,
    )
    if selector_version:
        record_obj.field_provenance["_selector_version"] = FieldProvenance(
            selector_id=selector_version, value_source="derived")
    return record_obj
