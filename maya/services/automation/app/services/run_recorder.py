"""Run recorder: every automation attempt is an auditable object."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from app.core.errors import AutomationError, ErrorCode
from app.core.ids import run_id as new_run_id
from app.core.logging import log
from app.models.schemas import RunRecord, RunStatus, StepRecord

logger = logging.getLogger(__name__)


class RunRecorder:
    def __init__(self, repo: Any, *, serial_number: str, source: str,
                 trigger: str = "user_request", requested_by: str = "unknown",
                 trace_id: str | None = None, selector_version: str | None = None) -> None:
        self.repo = repo
        self.record = RunRecord(
            automation_run_id=new_run_id(),
            serial_number=serial_number,
            source=source,
            trigger=trigger,
            requested_by=requested_by,
            started_at=datetime.now(timezone.utc),
            trace_id=trace_id,
            selector_version=selector_version,
        )
        self._seq = 0
        self._t0 = time.monotonic()

    @property
    def run_id(self) -> str:
        return self.record.automation_run_id

    async def start(self) -> None:
        await self._persist()

    @asynccontextmanager
    async def step(self, name: str, *, url_provider: Any = None) -> AsyncIterator[dict[str, Any]]:
        """Wrap one deterministic step: timing, outcome, url, artifacts, error code."""
        self._seq += 1
        seq = self._seq
        started = time.monotonic()
        box: dict[str, Any] = {"artifact_uri": None, "url": None}
        try:
            yield box
        except AutomationError as err:
            err.step = err.step or name
            self._append(seq, name, "FAILED", started, box,
                         error_code=err.code.value, url_provider=url_provider)
            log(logger, logging.WARNING, "run.step.failed", step=name,
                error_code=err.code.value, run_id=self.run_id)
            raise
        except Exception as exc:
            self._append(seq, name, "FAILED", started, box,
                         error_code=ErrorCode.INTERNAL_ERROR.value, url_provider=url_provider)
            raise AutomationError(ErrorCode.INTERNAL_ERROR, str(exc)[:200], step=name) from exc
        else:
            self._append(seq, name, "OK", started, box, url_provider=url_provider)
            log(logger, logging.INFO, "run.step.ok", step=name, run_id=self.run_id,
                duration_ms=int((time.monotonic() - started) * 1000))

    def _append(self, seq: int, name: str, status: str, started: float,
                box: dict[str, Any], *, error_code: str | None = None,
                url_provider: Any = None) -> None:
        url = box.get("url")
        if url is None and callable(url_provider):
            try:
                url = url_provider()
            except Exception:
                url = None
        self.record.steps_executed.append(StepRecord(
            seq=seq, step=name, status=status,
            duration_ms=int((time.monotonic() - started) * 1000),
            url=url, error_code=error_code, artifact_uri=box.get("artifact_uri"),
        ))

    def note_retry(self) -> None:
        self.record.retry_count += 1

    async def succeed(self, *, field_count: int, quality_score: float | None,
                      artifact_uri: str | None = None) -> RunRecord:
        self.record.status = RunStatus.SUCCESS
        self.record.extracted_field_count = field_count
        self.record.quality_score = quality_score
        self.record.artifact_uri = artifact_uri
        return await self._finish()

    async def fail(self, err: AutomationError) -> RunRecord:
        self.record.status = RunStatus.FAILED
        self.record.error_code = err.code.value
        self.record.error_message = err.message
        return await self._finish()

    async def _finish(self) -> RunRecord:
        self.record.completed_at = datetime.now(timezone.utc)
        self.record.execution_time_ms = int((time.monotonic() - self._t0) * 1000)
        await self._persist()
        log(logger, logging.INFO, "run.completed", run_id=self.run_id,
            status=self.record.status.value, ms=self.record.execution_time_ms,
            error_code=self.record.error_code, steps=len(self.record.steps_executed))
        return self.record

    async def _persist(self) -> None:
        try:
            await self.repo.save_run(self.record)
        except AutomationError as exc:
            # Losing the audit row must never lose the answer; it is alerted instead.
            log(logger, logging.ERROR, "run.persist_failed", run_id=self.run_id,
                error=exc.message)
