"""Inspection data model.

``SourceRecord`` is what a collector returns: the untouched source payload.
``Inspection`` is the validated, typed record that is loaded into Snowflake.
Keeping the two apart means a record that fails validation is still available
(with its raw payload) to be written to VALIDATION_ERRORS instead of vanishing.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator


@dataclass(frozen=True)
class SourceRecord:
    raw: dict[str, Any]
    locator: str  # where it came from, e.g. "page=3,index=12"


class Attachment(BaseModel):
    """Attachment metadata. Extra attributes present in the source are preserved."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    url: str | None = None

    @model_validator(mode="after")
    def _has_identity(self) -> Attachment:
        if not (self.name or self.url):
            raise ValueError("attachment must have a name or a url")
        return self


def _require_text(value: Any) -> str:
    if value is None:
        raise ValueError("is required")
    text = str(value).strip()
    if not text:
        raise ValueError("must not be empty")
    return text


class Inspection(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: UUID
    source: str
    collector_type: str
    inspection_number: str
    serial_number: str
    status: str
    summary: dict[str, NonNegativeInt] | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    inspection_date: datetime | None = None
    collected_at: datetime
    raw_data: dict[str, Any]

    @field_validator("inspection_number", "serial_number", "status", mode="before")
    @classmethod
    def _required_text(cls, value: Any) -> str:
        return _require_text(value)

    @field_validator("collected_at")
    @classmethod
    def _collected_at_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("collected_at must be timezone-aware")
        return value

    @field_validator("inspection_date")
    @classmethod
    def _plausible_date(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("inspection_date must be timezone-aware after normalization")
        if value.year < 1990:
            raise ValueError(f"inspection_date {value.isoformat()} is implausibly old")
        if value > datetime.now(timezone.utc) + timedelta(days=1):
            raise ValueError(f"inspection_date {value.isoformat()} is in the future")
        return value

    @property
    def business_key(self) -> tuple[str, str]:
        return (self.source, self.inspection_number)

    @property
    def record_hash(self) -> str:
        """Content fingerprint. Identical content in a later run -> identical hash -> no update."""
        content = {
            "inspection_number": self.inspection_number,
            "serial_number": self.serial_number,
            "status": self.status,
            "summary": self.summary,
            "attachments": [a.model_dump(mode="json") for a in self.attachments],
            "inspection_date": self.inspection_date.isoformat() if self.inspection_date else None,
            "raw_data": self.raw_data,
        }
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
