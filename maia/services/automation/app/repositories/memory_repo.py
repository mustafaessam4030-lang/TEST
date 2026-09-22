"""In-memory repository for dev and tests. Same semantics as the warehouse one."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.hashing import data_hash
from app.models.schemas import EquipmentRecord, RecordStatus, RunRecord
from app.repositories.base import ExtractionArtifact


class MemoryEquipmentRepository:
    def __init__(self) -> None:
        self._current: dict[tuple[str, str], dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._runs: dict[str, dict[str, Any]] = {}
        self._claims: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_current(self, serial_number: str, source: str) -> dict[str, Any] | None:
        return self._current.get((source, serial_number))

    async def get_any_source(self, serial_number: str) -> list[dict[str, Any]]:
        return [v for (_s, sn), v in self._current.items() if sn == serial_number]

    async def known_serials(self, limit: int = 500) -> list[str]:
        return sorted({sn for (_s, sn) in self._current})[:limit]

    async def upsert(self, record: EquipmentRecord, *,
                     extraction: ExtractionArtifact | None = None) -> bool:
        # `extraction` is the raw page read. This store keeps only the
        # canonical record; the local JSON store is the one that keeps both.
        payload = record.model_dump(mode="json")
        payload["data_hash"] = record.data_hash or data_hash(payload)
        key = (record.source_system, record.serial_number)
        async with self._lock:
            existing = self._current.get(key)
            changed = not existing or existing.get("data_hash") != payload["data_hash"]
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            payload["last_verified_at"] = payload["updated_at"]
            self._current[key] = payload
            if changed:
                self._history.append({**payload, "version_at": payload["updated_at"]})
        return changed

    async def mark_not_found(self, serial_number: str, source: str, run_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            self._current[(source, serial_number)] = {
                "serial_number": serial_number,
                "source_system": source,
                "status": RecordStatus.NOT_FOUND.value,
                "retrieved_at": now,
                "updated_at": now,
                "automation_run_id": run_id,
            }

    async def history(self, serial_number: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]:
        rows = [h for h in self._history
                if h["serial_number"] == serial_number
                and (source is None or h["source_system"] == source)]
        return sorted(rows, key=lambda r: r["version_at"], reverse=True)[:limit]

    async def save_run(self, run: RunRecord) -> None:
        self._runs[run.automation_run_id] = run.model_dump(mode="json")

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._runs.get(run_id)

    async def find_active_run(self, idempotency_key: str) -> str | None:
        return self._claims.get(idempotency_key)

    async def claim_idempotency(self, idempotency_key: str, run_id: str) -> bool:
        async with self._lock:
            if idempotency_key in self._claims:
                return False
            self._claims[idempotency_key] = run_id
            return True

    async def release_idempotency(self, idempotency_key: str) -> None:
        self._claims.pop(idempotency_key, None)

    async def health(self) -> bool:
        return True
