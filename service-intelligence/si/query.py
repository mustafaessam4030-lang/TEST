"""Compile registry metrics into parameterised Snowflake SQL.

This is the only place KPI SQL is written. Values chosen by a user (filter
values, dates, thresholds) are always bind parameters (`?`); identifiers come
only from the contract and the registry, never from user text.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from . import contract
from .registry import TEMPLATE, Metric, Registry

GRAINS = ("day", "week", "month", "quarter")
MAX_FILTER_VALUES = 50
MAX_FILTER_LEN = 200


class QueryError(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    start: date          # inclusive, reporting time zone
    end: date            # exclusive, reporting time zone

    def __post_init__(self):
        if self.end <= self.start:
            raise QueryError("Period end must be after its start")
        if (self.end - self.start).days > 3660:
            raise QueryError("Period longer than ten years is not allowed")

    def previous(self) -> "Period":
        length = self.end - self.start
        return Period(self.start - length, self.start)

    def utc_bounds(self, tz: ZoneInfo) -> tuple[datetime, datetime]:
        def conv(d: date) -> datetime:
            return datetime.combine(d, time(0), tz).astimezone(timezone.utc).replace(tzinfo=None)
        return conv(self.start), conv(self.end)

    def label(self) -> str:
        last = date.fromordinal(self.end.toordinal() - 1)
        return f"{self.start.isoformat()} to {last.isoformat()}"


Filters = dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class QuerySpec:
    sql: str
    params: tuple
    purpose: str
    metric_id: str | None = None
    meta: dict = field(default_factory=dict, compare=False, hash=False)


class _Sql:
    """Accumulates SQL text and its bind parameters in textual order."""

    def __init__(self):
        self.parts: list[str] = []
        self.params: list = []

    def add(self, text: str, *params) -> "_Sql":
        self.parts.append(text)
        self.params.extend(params)
        return self

    def template(self, text: str, values: dict) -> "_Sql":
        pos = 0
        for m in TEMPLATE.finditer(text):
            self.parts.append(text[pos:m.start()] + "?")
            self.params.append(values[m.group(1)])
            pos = m.end()
        self.parts.append(text[pos:])
        return self

    def text(self) -> str:
        return "".join(self.parts)


def normalise_filters(filters: dict | None, available: frozenset[str]) -> Filters:
    out: Filters = {}
    for dim, values in (filters or {}).items():
        if dim not in contract.DIMENSIONS:
            raise QueryError(f"{dim!r} is not a filterable dimension")
        if dim not in available:
            raise QueryError(f"{dim!r} is not mapped to source data, so it cannot be filtered")
        if isinstance(values, str):
            values = [values]
        vals = tuple(dict.fromkeys(str(v) for v in values if v is not None))
        if not vals:
            continue
        if len(vals) > MAX_FILTER_VALUES or any(len(v) > MAX_FILTER_LEN for v in vals):
            raise QueryError("Too many or too long filter values")
        out[dim] = vals
    return out


class QueryBuilder:
    def __init__(self, registry: Registry, view: str, available: frozenset[str], tz: ZoneInfo, max_rows: int):
        self.registry = registry
        self.view = view
        self.available = available
        self.tz = tz
        self.max_rows = max_rows

    # -- helpers -----------------------------------------------------------------------------------
    def check_available(self, metric: Metric) -> None:
        missing = [c for c in metric.requires if c not in self.available]
        if missing:
            raise QueryError(f"{metric.name} is unavailable: source data has no {', '.join(missing)}")

    def _values(self, as_of: datetime, aged_days: int | None) -> dict:
        return {"as_of": as_of, "aged_days": int(aged_days if aged_days is not None else self.registry.param("aged_days"))}

    def _local(self, column: str) -> str:
        if self.tz.key == "UTC":
            return column
        return f"CONVERT_TIMEZONE('UTC', '{self.tz.key}', {column})"

    def _where(self, q: _Sql, metric: Metric | None, period: Period | None, filters: Filters, scope: Filters,
               values: dict, extra: str | None = None, extra_params: tuple = ()) -> None:
        q.add(" WHERE 1 = 1")
        if metric and metric.kind == "period":
            if period is None:
                raise QueryError(f"{metric.name} needs a reporting period")
            lo, hi = period.utc_bounds(self.tz)
            q.add(f" AND {metric.time_column} >= ? AND {metric.time_column} < ?", lo, hi)
        if metric and metric.filter:
            q.add(" AND (").template(metric.filter, values).add(")")
        for group in (filters, scope):
            for dim, vals in sorted(group.items()):
                named = [v for v in vals if v != contract.NOT_SET]
                conds = []
                if named:
                    conds.append(f"{dim} IN ({', '.join('?' for _ in named)})")
                if contract.NOT_SET in vals:
                    conds.append(f"{dim} IS NULL")
                q.add(" AND (" + " OR ".join(conds) + ")", *named)
        if extra:
            q.add(f" AND ({extra})", *extra_params)

    # -- metric queries ----------------------------------------------------------------------------
    def value(self, metric_id: str, period: Period | None, filters: Filters, scope: Filters, as_of: datetime,
              aged_days: int | None = None) -> QuerySpec:
        m = self.registry.metric(metric_id)
        self.check_available(m)
        values = self._values(as_of, aged_days)
        q = _Sql().add("SELECT ").template(m.expression, values).add(" AS metric_value, COUNT(DISTINCT request_id) AS n")
        q.add(f" FROM {self.view}")
        self._where(q, m, period, filters, scope, values)
        return QuerySpec(q.text(), tuple(q.params), "metric_value", m.id)

    def series(self, metric_id: str, period: Period, grain: str, filters: Filters, scope: Filters,
               as_of: datetime, aged_days: int | None = None) -> QuerySpec:
        m = self.registry.metric(metric_id)
        self.check_available(m)
        if m.kind != "period":
            raise QueryError(f"{m.name} is a current-state metric; it has no history to trend")
        if grain not in GRAINS:
            raise QueryError(f"grain must be one of {GRAINS}")
        values = self._values(as_of, aged_days)
        bucket = f"DATE_TRUNC('{grain}', {self._local(m.time_column)})"
        q = _Sql().add(f"SELECT {bucket} AS bucket, ").template(m.expression, values)
        q.add(f" AS metric_value, COUNT(DISTINCT request_id) AS n FROM {self.view}")
        self._where(q, m, period, filters, scope, values)
        q.add(f" GROUP BY 1 ORDER BY 1 LIMIT {min(self.max_rows, 5000)}")
        return QuerySpec(q.text(), tuple(q.params), "metric_series", m.id, {"grain": grain})

    def breakdown(self, metric_id: str, dimension: str, period: Period | None, filters: Filters, scope: Filters,
                  as_of: datetime, aged_days: int | None = None, limit: int = 25) -> QuerySpec:
        m = self.registry.metric(metric_id)
        self.check_available(m)
        if dimension not in contract.DIMENSIONS:
            raise QueryError(f"{dimension!r} is not a dimension")
        if dimension not in self.available:
            raise QueryError(f"{dimension!r} is not mapped to source data")
        values = self._values(as_of, aged_days)
        q = _Sql().add(f"SELECT COALESCE({dimension}, '{contract.NOT_SET}') AS segment, ").template(m.expression, values)
        q.add(f" AS metric_value, COUNT(DISTINCT request_id) AS n FROM {self.view}")
        self._where(q, m, period, filters, scope, values)
        q.add(f" GROUP BY 1 ORDER BY n DESC, 1 LIMIT {max(1, min(int(limit), 200))}")
        return QuerySpec(q.text(), tuple(q.params), "metric_breakdown", m.id, {"dimension": dimension})

    def drilldown(self, metric_id: str, period: Period | None, filters: Filters, scope: Filters, as_of: datetime,
                  aged_days: int | None = None, limit: int = 100, offset: int = 0) -> QuerySpec:
        m = self.registry.metric(metric_id)
        self.check_available(m)
        values = self._values(as_of, aged_days)
        cols = [c for c in contract.DRILLDOWN_COLUMNS if c in self.available or c in ("request_id", "status_class")]
        order = m.time_column or "created_at"
        q = _Sql().add(f"SELECT {', '.join(cols)} FROM {self.view}")
        self._where(q, m, period, filters, scope, values)
        limit = max(1, min(int(limit), self.max_rows))
        q.add(f" ORDER BY {order} DESC, request_id LIMIT {limit} OFFSET {max(0, int(offset))}")
        return QuerySpec(q.text(), tuple(q.params), "drilldown", m.id, {"columns": cols})

    # -- data quality ------------------------------------------------------------------------------
    def quality(self, scope: Filters) -> QuerySpec:
        """Completeness, duplicates, ordering errors and freshness over the whole contract view."""
        sel = ["COUNT(*) AS total_rows", "COUNT(DISTINCT request_id) AS distinct_ids"]
        for c in contract.NAMES:
            if c in self.available and c != "request_id":
                sel.append(f"COUNT({c}) AS nonnull_{c}")
        sel.append("SUM(IFF(status_class = 'UNMAPPED', 1, 0)) AS unmapped_status")
        if "closed_at" in self.available:
            sel.append("SUM(IFF(closed_at < created_at, 1, 0)) AS closed_before_created")
            sel.append("SUM(IFF(status_class = 'CLOSED' AND closed_at IS NULL, 1, 0)) AS closed_without_time")
            sel.append("SUM(IFF(status_class = 'OPEN' AND closed_at IS NOT NULL, 1, 0)) AS open_with_close_time")
        sel += ["MIN(created_at) AS first_created", "MAX(created_at) AS last_created"]
        for c in ("updated_at", "loaded_at"):
            if c in self.available:
                sel.append(f"MAX({c}) AS last_{c}")
        q = _Sql().add("SELECT " + ", ".join(sel) + f" FROM {self.view}")
        self._where(q, None, None, {}, scope, {})
        return QuerySpec(q.text(), tuple(q.params), "data_quality")

    def dimension_values(self, dimension: str, scope: Filters, limit: int = 200) -> QuerySpec:
        if dimension not in contract.DIMENSIONS or dimension not in self.available:
            raise QueryError(f"{dimension!r} is not an available dimension")
        q = _Sql().add(f"SELECT COALESCE({dimension}, '{contract.NOT_SET}') AS value, COUNT(*) AS n FROM {self.view}")
        self._where(q, None, None, {}, scope, {})
        q.add(f" GROUP BY 1 ORDER BY n DESC LIMIT {limit}")
        return QuerySpec(q.text(), tuple(q.params), "dimension_values", meta={"dimension": dimension})
