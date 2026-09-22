"""Snowflake as the record, the local folder as evidence.

    MirroredRepository(primary=SnowflakeEquipmentRepository,
                       mirror=LocalJsonRepository)

The rule is one line: **a lookup succeeds only if the primary saved it.**

  * writes go to the primary first; if that fails the lookup fails
    (PERSISTENCE_FAILED) exactly as it would without a mirror
  * the mirror then writes JSON + TXT + screenshots — things a warehouse is the
    wrong place for and a person wants to open. A mirror failure is logged and
    never fails a lookup the primary already holds
  * every read comes from the primary, so there is one answer to "what do we
    know about this serial", never two that can drift
  * run progress is the one exception: the chat polls it every second while a
    lookup runs, so each step lands in the mirror and only the start and the
    outcome go to the primary. The warehouse keeps the audit, not the heartbeat.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.logging import log
from app.models.schemas import EquipmentRecord, RunRecord, RunStatus
from app.repositories.base import ExtractionArtifact

logger = logging.getLogger(__name__)


class MirroredRepository:
    def __init__(self, primary: Any, mirror: Any) -> None:
        self.primary = primary
        self.mirror = mirror

    # The store labels records with the registry's source names; the mirror is
    # the one that writes them into files.
    @property
    def source_labels(self) -> dict[str, str]:
        return getattr(self.mirror, "source_labels", {})

    @property
    def results(self) -> Any:
        return getattr(self.mirror, "results", None)

    async def _mirror(self, op: str, *args: Any, **kwargs: Any) -> None:
        try:
            await getattr(self.mirror, op)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - evidence, never the record
            log(logger, logging.WARNING, "store.mirror_failed", op=op,
                error=str(exc)[:200])

    # ── reads: the primary only ─────────────────────────────────────────────
    async def get_current(self, serial_number: str, source: str) -> dict[str, Any] | None:
        return await self.primary.get_current(serial_number, source)

    async def get_any_source(self, serial_number: str) -> list[dict[str, Any]]:
        return await self.primary.get_any_source(serial_number)

    async def known_serials(self, limit: int = 500) -> list[str]:
        return await self.primary.known_serials(limit)

    async def history(self, serial_number: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]:
        return await self.primary.history(serial_number, source, limit)

    # ── writes: primary decides, mirror follows ─────────────────────────────
    async def upsert(self, record: EquipmentRecord, *,
                     extraction: ExtractionArtifact | None = None) -> bool:
        changed = await self.primary.upsert(record, extraction=extraction)
        await self._mirror("upsert", record, extraction=extraction)
        return changed

    async def mark_not_found(self, serial_number: str, source: str, run_id: str) -> None:
        await self.primary.mark_not_found(serial_number, source, run_id)
        await self._mirror("mark_not_found", serial_number, source, run_id)

    async def save_run(self, run: RunRecord) -> None:
        await self._mirror("save_run", run)
        if not run.steps_executed or run.status != RunStatus.RUNNING:
            await self.primary.save_run(run)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        # Live progress is in the mirror; a run from another process or an
        # older session is only in the warehouse.
        try:
            run = await self.mirror.get_run(run_id)
        except Exception:  # noqa: BLE001
            run = None
        return run or await self.primary.get_run(run_id)

    # ── coordination: the primary, so two workers share one claim ───────────
    async def find_active_run(self, idempotency_key: str) -> str | None:
        return await self.primary.find_active_run(idempotency_key)

    async def claim_idempotency(self, idempotency_key: str, run_id: str, **kw: Any) -> bool:
        return await self.primary.claim_idempotency(idempotency_key, run_id, **kw)

    async def release_idempotency(self, idempotency_key: str) -> None:
        await self.primary.release_idempotency(idempotency_key)

    async def health(self) -> bool:
        return await self.primary.health()

    def close(self) -> None:
        if hasattr(self.primary, "close"):
            self.primary.close()
