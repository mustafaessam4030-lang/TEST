"""Source registry: config + adapter + circuit breaker, one entry per source."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from app.core.errors import AutomationError, ErrorCode
from app.core.retry import CircuitBreaker
from app.adapters.base import SourceAdapter


@dataclass
class SourceEntry:
    source_id: str
    label: str
    config: dict[str, Any]
    factory: Callable[[dict[str, Any]], SourceAdapter]
    breaker: CircuitBreaker
    enabled: bool = True
    precedence: int = 100


class SourceRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, SourceEntry] = {}

    def register(self, source_id: str, label: str, config: dict[str, Any],
                 factory: Callable[[dict[str, Any]], SourceAdapter],
                 *, enabled: bool = True, precedence: int = 100) -> None:
        self._entries[source_id] = SourceEntry(
            source_id=source_id, label=label, config=config, factory=factory,
            breaker=CircuitBreaker(), enabled=enabled, precedence=precedence)

    def entry(self, source_id: str) -> SourceEntry:
        entry = self._entries.get(source_id)
        if entry is None:
            raise AutomationError(ErrorCode.INVALID_SERIAL,
                                  f"Unknown source '{source_id}'.",
                                  details={"known": sorted(self._entries)})
        return entry

    def adapter(self, source_id: str) -> SourceAdapter:
        entry = self.entry(source_id)
        if not entry.enabled:
            raise AutomationError(ErrorCode.CIRCUIT_OPEN, f"Source '{source_id}' is disabled.")
        return entry.factory(entry.config)

    def breaker(self, source_id: str) -> CircuitBreaker:
        return self.entry(source_id).breaker

    def label(self, source_id: str) -> str:
        return self.entry(source_id).label

    def resolve(self, requested: str | None) -> str:
        """`auto` walks the precedence order — this is the multi-source hook."""
        if requested and requested != "auto":
            return requested
        enabled = [e for e in self._entries.values() if e.enabled]
        if not enabled:
            raise AutomationError(ErrorCode.CIRCUIT_OPEN, "No source is enabled.")
        return sorted(enabled, key=lambda e: e.precedence)[0].source_id

    def snapshot(self) -> list[dict[str, Any]]:
        return [{"source_id": e.source_id, "label": e.label, "enabled": e.enabled,
                 "precedence": e.precedence, "circuit": e.breaker.snapshot(),
                 "selector_version": e.config.get("selector_version")}
                for e in sorted(self._entries.values(), key=lambda x: x.precedence)]
