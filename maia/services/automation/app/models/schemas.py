"""Wire and domain models. `data` never exists without `attribution`."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "1.2.0"
SERIAL_RE = re.compile(r"^[A-Z0-9]{3,17}$")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_serial(raw: str) -> str:
    """Upper-case, strip separators and Arabic-Indic digits. Pure, testable, server-side."""
    arabic_digits = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    cleaned = (raw or "").translate(arabic_digits)
    cleaned = re.sub(r"[\s\-_/.]", "", cleaned).upper()
    return cleaned


class SearchMode(str, Enum):
    AUTO = "auto"
    CACHE_ONLY = "cache_only"
    FORCE_REFRESH = "force_refresh"


class Freshness(str, Enum):
    FRESH = "FRESH"
    STALE = "STALE"
    HARD_STALE = "HARD_STALE"
    MISSING = "MISSING"
    NEGATIVE_CACHED = "NEGATIVE_CACHED"


class RecordStatus(str, Enum):
    ACTIVE = "ACTIVE"
    NOT_FOUND = "NOT_FOUND"
    STALE = "STALE"
    QUARANTINED = "QUARANTINED"
    SUPERSEDED = "SUPERSEDED"


class RunStatus(str, Enum):
    RUNNING = "RUNNING"
    AWAITING_HUMAN = "AWAITING_HUMAN"   # paused at MFA / verification, on purpose
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# ── Requests ────────────────────────────────────────────────────────────────
class EquipmentSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    serial_number: str = Field(min_length=1, max_length=40)
    source: str = "cat_sis"
    mode: SearchMode = SearchMode.AUTO
    wait: bool = True
    timeout_ms: int = Field(default=30_000, ge=1_000, le=120_000)
    reason: Literal["user_request", "stale_refresh", "backfill"] = "user_request"
    requested_by: str = "unknown"
    idempotency_key: str | None = None

    @field_validator("serial_number")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_serial(v)


class SaveEquipmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data: dict[str, Any]
    source: str
    automation_run_id: str | None = None
    requested_by: str = "unknown"


# ── Normalized record ───────────────────────────────────────────────────────
class EngineFamily(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    arrangement: str | None = None
    emissions: str | None = None


class Specification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group: str | None = None
    name: str
    value: float | str | None = None
    unit: str | None = None
    value_raw: str | None = None
    provenance_id: str | None = None


class Quality(BaseModel):
    model_config = ConfigDict(extra="forbid")
    score: float = 0.0
    required_present: int = 0
    required_total: int = 0
    violations: list[str] = Field(default_factory=list)


class FieldProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selector_id: str | None = None
    value_source: Literal["xhr", "dom", "derived", "manual"] = "dom"
    confidence: float = 1.0
    reason: Literal["OK", "NOT_PUBLISHED", "UNREADABLE"] = "OK"


class EquipmentRecord(BaseModel):
    """The canonical contract. Extra keys are rejected: a changed page is an error."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    serial_number: str
    serial_number_raw: str | None = None
    equipment_model: str | None = None
    equipment_type: str | None = None
    manufacturer: str | None = None
    build_date: str | None = None          # ISO-8601, partial allowed: YYYY | YYYY-MM | YYYY-MM-DD
    # The equipment-details block, read first from the detail page. The machine
    # serial is also the cross-check: it must equal the serial that was searched.
    machine_serial_number: str | None = None
    machine_build_date: str | None = None  # ISO-8601, as published
    engine_serial_number: str | None = None
    engine_build_date: str | None = None   # ISO-8601, as published
    engine_family: EngineFamily | None = None
    specifications: list[Specification] = Field(default_factory=list)
    parts_data: dict[str, Any] | None = None
    parts_manual_url: str | None = None
    operation_manual_url: str | None = None
    source_system: str
    source_url: str | None = None
    retrieved_at: datetime
    automation_run_id: str | None = None
    data_hash: str | None = None
    status: RecordStatus = RecordStatus.ACTIVE
    quality: Quality = Field(default_factory=Quality)
    field_provenance: dict[str, FieldProvenance] = Field(default_factory=dict)


# ── Responses ───────────────────────────────────────────────────────────────
class Attribution(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    source_label: str
    source_url: str | None = None
    retrieved_at: datetime
    automation_run_id: str | None = None
    freshness: Freshness
    age_days: float | None = None


class CacheInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hit: bool
    freshness: Freshness | None = None
    age_days: float | None = None
    policy_action: str | None = None


class EquipmentSearchResponse(BaseModel):
    """Success shape. There is no variant of this carrying data without attribution."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["success"] = "success"
    serial_number: str
    source: str
    attribution: Attribution
    cache: CacheInfo
    data: EquipmentRecord
    persisted: bool = True
    execution_time_ms: int | None = None
    automation_run_id: str | None = None
    retrieved_at: datetime


class InProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["in_progress"] = "in_progress"
    serial_number: str
    automation_run_id: str
    poll_after_ms: int = 4000


class FallbackData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool
    freshness: Freshness | None = None
    age_days: float | None = None
    attribution: Attribution | None = None
    data: EquipmentRecord | None = None


class ErrorResponse(BaseModel):
    """Failure shape. Note: no `data` field exists. Nothing to paraphrase."""

    model_config = ConfigDict(extra="forbid")
    status: Literal["error"] = "error"
    serial_number: str | None = None
    error_code: str
    retryable: bool
    message: str
    user_message_hint: str
    failed_step: str | None = None
    automation_run_id: str | None = None
    fallback: FallbackData | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ── Runs ────────────────────────────────────────────────────────────────────
class StepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seq: int
    step: str
    status: Literal["OK", "FAILED", "SKIPPED"]
    duration_ms: int
    url: str | None = None
    error_code: str | None = None
    artifact_uri: str | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    automation_run_id: str
    serial_number: str
    source: str
    trigger: str = "user_request"
    requested_by: str = "unknown"
    started_at: datetime
    completed_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    error_code: str | None = None
    error_message: str | None = None
    retry_count: int = 0
    execution_time_ms: int | None = None
    steps_executed: list[StepRecord] = Field(default_factory=list)
    extracted_field_count: int = 0
    quality_score: float | None = None
    selector_version: str | None = None
    artifact_uri: str | None = None
    trace_id: str | None = None
