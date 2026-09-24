"""Pipeline run (audit) and validation failure models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    STARTED = "STARTED"
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PipelineRun(BaseModel):
    run_id: UUID = Field(default_factory=uuid4)
    source: str
    collector_type: str
    filters: dict[str, Any] = Field(default_factory=dict)
    start_time: datetime = Field(default_factory=utcnow)
    end_time: datetime | None = None
    status: RunStatus = RunStatus.STARTED
    records_found: int = 0
    records_valid: int = 0
    records_loaded: int = 0
    records_failed: int = 0
    records_duplicate: int = 0
    records_inserted: int = 0
    records_updated: int = 0
    records_unchanged: int = 0
    expected_total: int | None = None
    error_message: str | None = None

    def summary(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class ValidationFailure(BaseModel):
    run_id: UUID
    source: str
    record_identifier: str
    error_type: str  # MAPPING | VALIDATION | DUPLICATE_CONFLICT
    error_message: str
    raw_data: dict[str, Any]
    failed_at: datetime = Field(default_factory=utcnow)
