"""The extensibility seam. A source is Playwright, REST or JDBC behind this."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class SourceCapabilities:
    source_id: str
    label: str
    supports_serial_search: bool = True
    needs_login: bool = False
    kind: str = "browser"          # browser | api | jdbc | stream
    selector_version: str | None = None
    politeness_delay_ms: int = 0


@dataclass
class RawPayload:
    """Whatever the source gave us, untouched. Normalization happens elsewhere."""

    fields: dict[str, Any] = field(default_factory=dict)
    specifications: list[dict[str, Any]] = field(default_factory=list)
    parts_data: dict[str, Any] | None = None
    payload_kind: str = "dom"      # dom | xhr | api
    source_url: str | None = None
    retrieved_at: datetime | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass
class SearchOutcome:
    found: bool
    detail_url: str | None = None
    evidence: str | None = None    # why we believe this outcome (marker that matched)


@runtime_checkable
class SourceAdapter(Protocol):
    """Four methods. Nothing above this layer knows how the source works."""

    def capabilities(self) -> SourceCapabilities: ...

    async def ensure_session(self, ctx: Any, *, force_relogin: bool = False) -> None: ...

    async def search(self, ctx: Any, serial_number: str) -> SearchOutcome: ...

    async def extract(self, ctx: Any) -> RawPayload: ...
