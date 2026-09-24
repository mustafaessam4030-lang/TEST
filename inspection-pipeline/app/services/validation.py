"""Validation and in-run de-duplication.

Every input record ends up in exactly one bucket: valid, failed (with reason)
or identical duplicate. Nothing is dropped silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models import Inspection, SourceRecord, ValidationFailure
from app.services.normalization import MISSING, LookupMode, MappingError, lookup, map_record
from app.source_config import FieldMapping

logger = logging.getLogger(__name__)


@dataclass
class ValidationOutcome:
    valid: list[Inspection] = field(default_factory=list)
    failures: list[ValidationFailure] = field(default_factory=list)
    duplicates: int = 0


def _format_pydantic(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "record"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)


def _identifier(record: SourceRecord, mapping: FieldMapping, mode: LookupMode) -> str:
    value = lookup(record.raw, mapping.inspection_number, mode)
    if value is MISSING or value is None or not str(value).strip():
        return f"<unknown:{record.locator}>"
    return str(value).strip()


def validate_records(
    records: list[SourceRecord],
    *,
    mapping: FieldMapping,
    mode: LookupMode,
    run_id: UUID,
    source: str,
    collector_type: str,
    collected_at: datetime,
    date_formats: list[str] | None = None,
    tz: ZoneInfo | timezone = timezone.utc,
    allowed_statuses: list[str] | None = None,
) -> ValidationOutcome:
    outcome = ValidationOutcome()
    seen: dict[tuple[str, str], Inspection] = {}

    def fail(record: SourceRecord, error_type: str, message: str) -> None:
        outcome.failures.append(
            ValidationFailure(
                run_id=run_id,
                source=source,
                record_identifier=_identifier(record, mapping, mode),
                error_type=error_type,
                error_message=message,
                raw_data=record.raw,
            )
        )

    for record in records:
        try:
            fields: dict[str, Any] = map_record(
                record.raw, mapping, mode=mode, date_formats=date_formats or [], tz=tz
            )
        except (MappingError, ValueError) as exc:
            fail(record, "MAPPING", str(exc))
            continue

        try:
            inspection = Inspection(
                run_id=run_id,
                source=source,
                collector_type=collector_type,
                collected_at=collected_at,
                raw_data=record.raw,
                **fields,
            )
        except ValidationError as exc:
            fail(record, "VALIDATION", _format_pydantic(exc))
            continue

        if allowed_statuses and inspection.status not in allowed_statuses:
            fail(record, "VALIDATION", f"status: {inspection.status!r} is not an allowed status")
            continue

        existing = seen.get(inspection.business_key)
        if existing is None:
            seen[inspection.business_key] = inspection
            outcome.valid.append(inspection)
        elif existing.record_hash == inspection.record_hash:
            outcome.duplicates += 1
            logger.info("Identical duplicate of inspection %s ignored (%s)",
                        inspection.inspection_number, record.locator)
        else:
            fail(record, "DUPLICATE_CONFLICT",
                 f"inspection_number {inspection.inspection_number!r} appeared more than once "
                 "in this run with different content; first occurrence kept")

    logger.info(
        "Validation completed: %d valid, %d failed, %d identical duplicates",
        len(outcome.valid), len(outcome.failures), outcome.duplicates,
    )
    return outcome
