"""The decision flow. This is the deterministic brain the LLM orchestrates.

validate → cache → freshness → (source) → normalize → validate → save → respond
Every branch ends with attribution attached or with a data-free error.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.adapters.browser import BrowserPool, RunContext
from app.adapters.registry import SourceRegistry
from app.core.errors import AutomationError, ErrorCode
from app.core.hashing import data_hash, idempotency_key
from app.core.logging import log
from app.core.retry import with_retry
from app.domain.freshness import FreshnessPolicy
from app.domain.normalize import normalize_equipment_data
from app.domain.validate import validate_equipment_data, validate_serial
from app.models.schemas import (
    Attribution, CacheInfo, EquipmentRecord, EquipmentSearchRequest, EquipmentSearchResponse,
    FallbackData, Freshness, InProgressResponse, RecordStatus, RunStatus, SearchMode,
)
from app.services.run_recorder import RunRecorder

logger = logging.getLogger(__name__)

STEPS = ["LEARN_LAYOUT", "ACQUIRE_CONTEXT", "ENSURE_SESSION", "HEALTH_CHECK",
         "SEARCH_SERIAL", "EXTRACT_RAW", "NORMALIZE", "VALIDATE", "PERSIST"]


class EquipmentService:
    def __init__(self, *, repo: Any, registry: SourceRegistry, pool: BrowserPool,
                 freshness: FreshnessPolicy, settings: Any, capture: Any = None) -> None:
        self.repo = repo
        self.registry = registry
        self.pool = pool
        self.freshness = freshness
        self.settings = settings
        self.capture = capture
        # Background runs (wait=false) and the runs paused for a human at MFA.
        self._tasks: set[asyncio.Task] = set()
        self._paused: dict[str, asyncio.Event] = {}

    # ── public API ──────────────────────────────────────────────────────────
    async def lookup(self, req: EquipmentSearchRequest) -> Any:
        serial = validate_serial(req.serial_number)
        source = self.registry.resolve(req.source)
        breaker = self.registry.breaker(source)

        cached = await self._safe_get_current(serial, source)
        decision = self.freshness.evaluate(
            cached, source=source, mode=req.mode.value,
            circuit_state=breaker.state,
            actor_role="engineer" if req.reason == "backfill" else "user")

        log(logger, logging.INFO, "equipment.decision", serial_number=serial, source=source,
            freshness=decision.freshness.value, action=decision.action, reason=decision.reason)

        # 1. Negative cache: an authoritative "not there", cheaply.
        if decision.freshness == Freshness.NEGATIVE_CACHED:
            raise AutomationError(
                ErrorCode.SERIAL_NOT_FOUND,
                "The source has no record for this serial number (cached result).",
                details={"age_days": decision.age_days, "cached": True,
                         "automation_run_id": (cached or {}).get("automation_run_id")})

        # 2. Cache is good enough — no browser is launched.
        if decision.action == "use_cache" and cached:
            return self._from_cache(cached, source, decision)

        if req.mode == SearchMode.CACHE_ONLY:
            raise AutomationError(ErrorCode.SERIAL_NOT_FOUND,
                                  "Not present in the internal data store.",
                                  details={"mode": "cache_only"})

        # 3. Source path, guarded by the breaker.
        if not breaker.allow():
            if cached and decision.action == "refresh_or_fallback":
                return self._from_cache(cached, source, decision, stale_notice=True)
            raise AutomationError(ErrorCode.CIRCUIT_OPEN,
                                  f"Lookups for '{source}' are paused after repeated failures.",
                                  details=breaker.snapshot())

        if not self.settings.allow_live_automation:
            raise AutomationError(
                ErrorCode.CIRCUIT_OPEN,
                "Live automation is disabled in this environment (MAYA_ALLOW_LIVE_AUTOMATION).",
                details={"source": source})

        key = req.idempotency_key or idempotency_key(source, serial, req.mode.value)
        recorder = RunRecorder(
            self.repo, serial_number=serial, source=source, trigger=req.reason,
            requested_by=req.requested_by,
            selector_version=self.registry.entry(source).config.get("selector_version"))

        if not await self.repo.claim_idempotency(key, recorder.run_id):
            existing = await self.repo.find_active_run(key)
            return InProgressResponse(serial_number=serial,
                                      automation_run_id=existing or recorder.run_id)

        await recorder.start()

        if not req.wait:
            # Fire and forget: the caller polls GET /v1/runs/{id} and watches each
            # step land. This is what gives the chat UI live progress.
            task = asyncio.create_task(
                self._run_and_record(recorder, source, serial, req, cached, decision, breaker, key))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            return InProgressResponse(serial_number=serial, automation_run_id=recorder.run_id)

        return await self._run_and_record(recorder, source, serial, req, cached, decision,
                                          breaker, key)

    async def _run_and_record(self, recorder: RunRecorder, source: str, serial: str,
                              req: EquipmentSearchRequest, cached: dict[str, Any] | None,
                              decision: Any, breaker: Any, key: str) -> Any:
        started = time.monotonic()
        try:
            record = await self._run_automation(recorder, source, serial, req)
        except AutomationError as err:
            breaker.record_failure(err.code)
            await recorder.fail(err)
            if err.code == ErrorCode.SERIAL_NOT_FOUND:
                await self._safe_mark_not_found(serial, source, recorder.run_id)
            err.details.setdefault("automation_run_id", recorder.run_id)
            err.details["fallback"] = self._fallback(cached, source, decision)
            if not req.wait:
                log(logger, logging.WARNING, "equipment.background_failed",
                    run_id=recorder.run_id, error_code=err.code.value)
                return None          # the failure is on the run record; nobody is waiting
            raise
        else:
            breaker.record_success()
            await recorder.succeed(
                field_count=sum(1 for v in record.model_dump().values() if v not in (None, [], {})),
                quality_score=record.quality.score)
            persisted = True
            try:
                await self.repo.upsert(record)
            except AutomationError as db_err:
                persisted = False  # the answer survives a warehouse outage; it is flagged
                log(logger, logging.ERROR, "equipment.persist_failed",
                    serial_number=serial, error=db_err.message)
            return EquipmentSearchResponse(
                serial_number=serial, source=source,
                attribution=self._attribution(source, record, Freshness.FRESH, 0.0),
                cache=CacheInfo(hit=False, freshness=Freshness.FRESH, age_days=0.0,
                                policy_action=decision.action),
                data=record, persisted=persisted,
                execution_time_ms=int((time.monotonic() - started) * 1000),
                automation_run_id=recorder.run_id, retrieved_at=record.retrieved_at)
        finally:
            await self.repo.release_idempotency(key)

    async def get_from_store(self, serial_raw: str, source: str | None = None) -> Any:
        serial = validate_serial(serial_raw)
        resolved = self.registry.resolve(source)
        cached = await self._safe_get_current(serial, resolved)
        decision = self.freshness.evaluate(cached, source=resolved, mode="cache_only")
        if not cached or cached.get("status") == RecordStatus.NOT_FOUND.value:
            raise AutomationError(ErrorCode.SERIAL_NOT_FOUND,
                                  "Not present in the internal data store.",
                                  details={"freshness": decision.freshness.value})
        return self._from_cache(cached, resolved, decision)

    async def history(self, serial_raw: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]:
        serial = validate_serial(serial_raw)
        rows = await self.repo.history(serial, source, limit)
        return [{"version_at": r.get("version_at"), "retrieved_at": r.get("retrieved_at"),
                 "source_system": r.get("source_system"), "data_hash": r.get("data_hash"),
                 "quality_score": r.get("quality_score"),
                 "automation_run_id": r.get("automation_run_id"),
                 "changed_fields": self._diff_keys(rows, r)} for r in rows]

    # ── automation ──────────────────────────────────────────────────────────
    async def _run_automation(self, recorder: RunRecorder, source: str, serial: str,
                              req: EquipmentSearchRequest) -> EquipmentRecord:
        # Learn the page contract on first use, so nobody has to run a capture
        # script by hand. It proves every selector by using it; if it cannot, the
        # run fails with WEBSITE_CHANGED rather than proceeding on guesses.
        if (self.capture and self.settings.auto_capture
                and not self.capture.contract_is_usable(source)
                and not self.capture.already_attempted(source)):
            async with recorder.step("LEARN_LAYOUT"):
                outcome = await self.capture.learn(
                    source, good_serial=serial,
                    timeout_s=self.settings.auto_capture_timeout_s)
                log(logger, logging.INFO, "equipment.layout_learned",
                    run_id=recorder.run_id, **outcome)

        adapter = self.registry.adapter(source)
        caps = adapter.capabilities()
        deadline_s = min(req.timeout_ms, self.settings.run_deadline_ms) / 1000.0

        async def attempt(n: int) -> EquipmentRecord:
            if n > 1:
                recorder.note_retry()
            ctx: RunContext | None = None
            async with recorder.step("ACQUIRE_CONTEXT"):
                ctx = await self.pool.acquire(
                    run_id=recorder.run_id, source_id=source, slot="default",
                    step_timeout_ms=self.settings.step_timeout_ms,
                    use_saved_session=(n == 1))  # attempt 2+ escalates isolation
            assert ctx is not None
            try:
                async with recorder.step("ENSURE_SESSION", url_provider=lambda: ctx.page.url):
                    try:
                        await adapter.ensure_session(ctx, force_relogin=(n > 1))
                    except AutomationError as err:
                        if err.code == ErrorCode.SESSION_EXPIRED:
                            self.pool.vault.invalidate(source, "default")
                            await adapter.ensure_session(ctx, force_relogin=True)
                        elif err.code in (ErrorCode.MFA_REQUIRED, ErrorCode.CAPTCHA_DETECTED):
                            # Never bypassed. Hold this exact session open for a person,
                            # then carry on where we left off.
                            await self._await_human(recorder, err)
                            await adapter.ensure_session(ctx)
                        else:
                            raise

                # Selector contract gate: a changed page fails here, cleanly, before
                # any interaction can misread it as "serial not found".
                if hasattr(adapter, "preflight"):
                    async with recorder.step("HEALTH_CHECK",
                                             url_provider=lambda: ctx.page.url) as box:
                        report = await adapter.preflight(ctx)
                        if not report.ok:
                            box["artifact_uri"] = (
                                await self.pool.capture_artifacts(ctx, "HEALTH_CHECK")
                            ).get("screenshot")
                            report.raise_if_failed(source=source)

                async with recorder.step("SEARCH_SERIAL", url_provider=lambda: ctx.page.url) as box:
                    try:
                        outcome = await adapter.search(ctx, serial)
                    except AutomationError as err:
                        if err.code == ErrorCode.SESSION_EXPIRED:
                            # Self-heal: one relogin + one replay, not counted as a retry.
                            self.pool.vault.invalidate(source, "default")
                            await adapter.ensure_session(ctx, force_relogin=True)
                            outcome = await adapter.search(ctx, serial)
                        else:
                            box["artifact_uri"] = (
                                await self.pool.capture_artifacts(ctx, "SEARCH_SERIAL")
                            ).get("screenshot")
                            raise
                    if not outcome.found:
                        raise AutomationError(
                            ErrorCode.SERIAL_NOT_FOUND,
                            "The source has no record for this serial number.",
                            details={"evidence": outcome.evidence}, step="SEARCH_SERIAL")

                async with recorder.step("EXTRACT_RAW", url_provider=lambda: ctx.page.url) as box:
                    try:
                        raw = await adapter.extract(ctx)
                    except AutomationError:
                        box["artifact_uri"] = (
                            await self.pool.capture_artifacts(ctx, "EXTRACT_RAW")
                        ).get("screenshot")
                        raise

                async with recorder.step("NORMALIZE"):
                    record = normalize_equipment_data(
                        {"fields": raw.fields, "specifications": raw.specifications,
                         "parts_data": raw.parts_data, "payload_kind": raw.payload_kind,
                         "serial_number_raw": req.serial_number},
                        serial_number=serial, source_system=source,
                        source_url=raw.source_url,
                        retrieved_at=raw.retrieved_at or datetime.now(timezone.utc),
                        run_id=recorder.run_id,
                        field_map=self.registry.entry(source).config.get("field_map"),
                        selector_version=caps.selector_version)

                async with recorder.step("VALIDATE"):
                    record = validate_equipment_data(record)
                    record.data_hash = data_hash(record.model_dump(mode="json"))
                return record
            finally:
                if ctx is not None:
                    await self.pool.release(ctx, persist_session_slot="default")

        return await with_retry(attempt, deadline_s=deadline_s,
                                on_attempt_failed=lambda n, e: log(
                                    logger, logging.WARNING, "automation.attempt_failed",
                                    attempt=n, error_code=e.code.value,
                                    run_id=recorder.run_id))

    # ── human-in-the-loop ───────────────────────────────────────────────────
    async def _await_human(self, recorder: RunRecorder, err: AutomationError) -> None:
        """Pause the run — keeping the browser session open — until a person is done.

        Used when the source demands MFA or another human verification. We never
        attempt to satisfy it ourselves; we hold the session and wait.
        """
        if self.settings.headless or not self.settings.allow_human_resume:
            raise err            # nobody can act on a headless run: fail honestly
        event = asyncio.Event()
        self._paused[recorder.run_id] = event
        await recorder.set_status(
            RunStatus.AWAITING_HUMAN,
            note="Waiting for a human to complete sign-in verification at the source.")
        log(logger, logging.WARNING, "automation.awaiting_human", run_id=recorder.run_id,
            error_code=err.code.value, timeout_s=self.settings.human_wait_timeout_s)
        try:
            await asyncio.wait_for(event.wait(), timeout=self.settings.human_wait_timeout_s)
        except asyncio.TimeoutError:
            raise AutomationError(
                err.code, "Nobody completed the verification in time.",
                details={"waited_s": self.settings.human_wait_timeout_s},
                step="ENSURE_SESSION") from err
        finally:
            self._paused.pop(recorder.run_id, None)
        await recorder.set_status(RunStatus.RUNNING)
        log(logger, logging.INFO, "automation.resumed_by_human", run_id=recorder.run_id)

    def resume_run(self, run_id: str) -> bool:
        """Called by the operator once they have finished the verification."""
        event = self._paused.get(run_id)
        if event is None:
            return False
        event.set()
        return True

    def paused_runs(self) -> list[str]:
        return sorted(self._paused)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _from_cache(self, cached: dict[str, Any], source: str, decision: Any,
                    stale_notice: bool = False) -> EquipmentSearchResponse:
        record = EquipmentRecord.model_validate(self._clean_row(cached))
        return EquipmentSearchResponse(
            serial_number=record.serial_number,
            source="internal_store",
            attribution=self._attribution(source, record, decision.freshness, decision.age_days),
            cache=CacheInfo(hit=True, freshness=decision.freshness, age_days=decision.age_days,
                            policy_action="use_cache_stale" if stale_notice else decision.action),
            data=record, persisted=True,
            automation_run_id=record.automation_run_id,
            retrieved_at=record.retrieved_at)

    def _attribution(self, source: str, record: EquipmentRecord, freshness: Freshness,
                     age_days: float | None) -> Attribution:
        return Attribution(
            source=source, source_label=self.registry.label(source),
            source_url=record.source_url, retrieved_at=record.retrieved_at,
            automation_run_id=record.automation_run_id, freshness=freshness,
            age_days=age_days)

    def _fallback(self, cached: dict[str, Any] | None, source: str,
                  decision: Any) -> dict[str, Any]:
        if not cached or cached.get("status") == RecordStatus.NOT_FOUND.value:
            return FallbackData(available=False).model_dump(mode="json")
        try:
            record = EquipmentRecord.model_validate(self._clean_row(cached))
        except Exception:
            return FallbackData(available=False).model_dump(mode="json")
        return FallbackData(
            available=True, freshness=decision.freshness, age_days=decision.age_days,
            attribution=self._attribution(source, record, decision.freshness, decision.age_days),
            data=record).model_dump(mode="json")

    @staticmethod
    def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
        allowed = set(EquipmentRecord.model_fields)
        return {k: v for k, v in row.items() if k in allowed}

    @staticmethod
    def _diff_keys(rows: list[dict[str, Any]], row: dict[str, Any]) -> list[str]:
        idx = rows.index(row)
        if idx + 1 >= len(rows):
            return []
        prev, cur = rows[idx + 1].get("snapshot") or {}, row.get("snapshot") or {}
        return sorted(k for k in set(prev) | set(cur) if prev.get(k) != cur.get(k))

    async def _safe_get_current(self, serial: str, source: str) -> dict[str, Any] | None:
        try:
            return await self.repo.get_current(serial, source)
        except AutomationError as err:
            log(logger, logging.ERROR, "repo.read_failed", error=err.message)
            return None  # a warehouse outage must not block a live lookup

    async def _safe_mark_not_found(self, serial: str, source: str, run_id: str) -> None:
        try:
            await self.repo.mark_not_found(serial, source, run_id)
        except AutomationError:
            pass
