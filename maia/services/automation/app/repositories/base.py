"""Repository seam: business logic never sees SQL — or a file path.

Two implementations live behind this today:

    EquipmentRepository
      ├── LocalJsonRepository    ← active while the warehouse is being built
      ├── MemoryEquipmentRepository (dev + tests)
      └── SnowflakeEquipmentRepository (later)

Swapping one for another is a configuration change (`MAIA_REPOSITORY`). Neither
Maia nor the SIS automation knows which one is in place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models.schemas import EquipmentRecord, RunRecord


@dataclass
class ExtractionArtifact:
    """Everything the page gave us, beyond the fields the schema names.

    The canonical record is deliberately narrow: `additionalProperties: false`
    is what stops a changed page from smuggling data in. But a field that has
    no column yet is still evidence, and throwing it away means re-driving a
    browser to get it back. So the raw extraction travels alongside the record
    and a store may keep as much of it as it can.

    A store that cannot use this ignores it. It is never a source of fact for
    Maia: only the validated record is.
    """

    fields: dict[str, Any] = field(default_factory=dict)
    specifications: list[dict[str, Any]] = field(default_factory=list)
    parts_data: dict[str, Any] | None = None
    payload_kind: str = "dom"
    final_url: str | None = None
    page_title: str | None = None
    selector_version: str | None = None
    extraction_status: str = "SUCCESS"
    #: name -> absolute path of a screenshot taken during this run
    screenshots: dict[str, str] = field(default_factory=dict)
    #: free-form evidence: what was scrolled, what settled, what was searched
    evidence: dict[str, Any] = field(default_factory=dict)


class EquipmentRepository(Protocol):
    async def get_current(self, serial_number: str, source: str) -> dict[str, Any] | None: ...

    async def get_any_source(self, serial_number: str) -> list[dict[str, Any]]: ...

    async def known_serials(self, limit: int = 500) -> list[str]:
        """Serials this store can name, for near-match suggestions.

        A store that cannot list is allowed to return nothing: then a mistyped
        serial simply gets "I couldn't find it" instead of "did you mean…",
        which is a worse answer but never a wrong one.
        """
        ...

    async def upsert(self, record: EquipmentRecord, *,
                     extraction: ExtractionArtifact | None = None) -> bool:
        """Return True when a new history version was written (data actually changed).

        Raising is meaningful: the caller turns a failure here into
        PERSISTENCE_FAILED and does NOT report the lookup as successful.
        """
        ...

    async def mark_not_found(self, serial_number: str, source: str, run_id: str) -> None: ...

    async def history(self, serial_number: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]: ...

    async def save_run(self, run: RunRecord) -> None: ...

    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    async def find_active_run(self, idempotency_key: str) -> str | None: ...

    async def claim_idempotency(self, idempotency_key: str, run_id: str) -> bool:
        """Atomically claim the key. False means another run already owns it."""
        ...

    async def release_idempotency(self, idempotency_key: str) -> None: ...

    async def health(self) -> bool: ...
