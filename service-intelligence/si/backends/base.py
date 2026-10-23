from __future__ import annotations

from dataclasses import dataclass, field


class BackendError(RuntimeError):
    """A warehouse failure, classified so the UI can explain it.

    kind: not_configured | connection | warehouse_unavailable | permission_denied |
          timeout | query_failed | refused
    """

    def __init__(self, kind: str, message: str, query_id: str | None = None):
        super().__init__(message)
        self.kind = kind
        self.query_id = query_id


@dataclass
class Result:
    columns: list[str]
    rows: list[tuple]
    query_id: str
    elapsed_ms: int
    truncated: bool = False
    cached: bool = False
    meta: dict = field(default_factory=dict)

    def records(self) -> list[dict]:
        return [dict(zip(self.columns, r)) for r in self.rows]

    def first(self) -> dict:
        return self.records()[0] if self.rows else {}


class Backend:
    kind = "base"
    synthetic = False
    label = ""

    def query(self, sql: str, params: tuple, *, timeout_s: int, max_rows: int, tag: str) -> Result:
        raise NotImplementedError

    def write(self, sql: str, params: tuple, *, tag: str, native: bool = False) -> Result:
        """Plan-store statements only (SI_APP schema), on the writer connection.

        native=True marks backend-specific SQL that must not be translated."""
        raise NotImplementedError

    def close(self) -> None:
        pass
