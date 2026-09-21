"""Validation gate. Nothing reaches Maia or the warehouse without passing here."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.errors import AutomationError, ErrorCode
from app.models.schemas import EquipmentRecord, Quality, RecordStatus, SERIAL_RE

REQUIRED_FIELDS = ("serial_number", "machine_serial_number", "source_system", "retrieved_at")
# Violations that mean "this value is corrupt", not "this value is thin". These
# quarantine the record whatever the score is: a build date in 2099 is not a
# low-quality answer, it is a wrong one.
FATAL_VIOLATIONS = ("missing_required", "build_date_out_of_range", "build_date_not_iso",
                    "build_date_unparseable", "invalid_url",
                    "machine_build_date_out_of_range", "machine_build_date_not_iso",
                    "machine_build_date_unparseable",
                    "engine_build_date_out_of_range", "engine_build_date_not_iso",
                    "engine_build_date_unparseable",
                    # The record on screen was a different machine. Nothing about
                    # it can be served: it answers a question nobody asked.
                    "serial_mismatch")
SCORED_FIELDS = ("equipment_model", "equipment_type", "build_date", "engine_family",
                 "specifications", "parts_manual_url",
                 "machine_serial_number", "machine_build_date",
                 "engine_serial_number", "engine_build_date", "parts_data")
#: Every ISO date on the record is range-checked the same way.
DATE_FIELDS = ("build_date", "machine_build_date", "engine_build_date")
_ISO_PARTIAL = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
# A serial is one unspaced token. Anything with words in it is a label that
# was read instead of its value.
_SERIAL_VALUE = re.compile(r"^[A-Z0-9][A-Z0-9\-/]{1,24}$", re.I)


def validate_serial(raw: str) -> str:
    from app.models.schemas import normalize_serial

    serial = normalize_serial(raw)
    if not SERIAL_RE.match(serial):
        raise AutomationError(
            ErrorCode.INVALID_SERIAL,
            "Serial must be 3-17 alphanumeric characters.",
            details={"received": raw[:40]},
        )
    return serial


def validate_equipment_data(record: EquipmentRecord, *, min_score: float = 0.5) -> EquipmentRecord:
    """Return the record with a computed quality block, or raise INVALID_DATA."""
    violations = list(record.quality.violations)

    missing_required = [f for f in REQUIRED_FIELDS if not getattr(record, f, None)]
    violations.extend(f"missing_required:{f}" for f in missing_required)

    current_year = datetime.now(timezone.utc).year
    for date_field in DATE_FIELDS:
        value = getattr(record, date_field, None)
        if not value:
            continue
        if not _ISO_PARTIAL.match(value):
            violations.append(f"{date_field}_not_iso")
            continue
        year = int(value[:4])
        if year < 1925 or year > current_year + 1:
            violations.append(f"{date_field}_out_of_range:{year}")

    # A serial that is not serial-shaped means the label was read, not the value.
    for serial_field in ("machine_serial_number", "engine_serial_number"):
        value = getattr(record, serial_field, None)
        if value and not _SERIAL_VALUE.match(str(value)):
            violations.append(f"unreadable_value:{serial_field}")

    if record.equipment_type == "UNKNOWN":
        violations.append("equipment_type_unmapped")

    for url_field in ("parts_manual_url", "operation_manual_url", "source_url"):
        url = getattr(record, url_field)
        if url and not str(url).startswith(("http://", "https://")):
            violations.append(f"invalid_url:{url_field}")

    for spec in record.specifications:
        if spec.value is None and not spec.value_raw:
            violations.append(f"empty_spec:{spec.name[:24]}")

    present = sum(1 for f in SCORED_FIELDS if getattr(record, f, None))
    score = round(present / len(SCORED_FIELDS), 3)
    # Hard violations (as opposed to "source didn't publish this") cost real score.
    hard = [v for v in violations if not v.startswith(("empty_spec", "equipment_type_unmapped"))]
    score = round(max(0.0, score - 0.1 * len(hard)), 3)

    record.quality = Quality(
        score=score,
        required_present=len(REQUIRED_FIELDS) - len(missing_required),
        required_total=len(REQUIRED_FIELDS),
        violations=violations,
    )

    fatal = [v for v in violations if v.startswith(FATAL_VIOLATIONS)]
    if fatal or score < min_score:
        record.status = RecordStatus.QUARANTINED
        raise AutomationError(
            ErrorCode.INVALID_DATA,
            "Retrieved data failed validation; it was quarantined, not served.",
            details={"violations": violations, "score": score, "fatal": fatal},
        )
    return record
