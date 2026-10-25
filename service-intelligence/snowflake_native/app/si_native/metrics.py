"""Metric registry and the ONLY code that turns a metric definition into SQL.

The Analysis page, the recommendation detectors and the assistant all call these builders,
so the same question always produces the same SQL and the same number.
Every user-supplied value (dates, filter values) is a bind parameter (`?`); identifiers come
only from the registry, never from user input.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path


KINDS = ("period", "snapshot")
GRAINS = ("day", "week", "month")
NOT_SET = "(not set)"
MAX_FILTER_VALUES = 50
MAX_FILTER_LEN = 200
PLACEHOLDER = re.compile(r":(start|end|as_of_date|as_of)\b")
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")
QUALIFIED = re.compile(r"^[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*$")
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|create|alter|drop|truncate|grant|revoke|call|execute|copy|put|get|use|"
    r"undrop|show|describe|system\$\w*|information_schema|account_usage)\b", re.I)


class RegistryError(ValueError):
    pass


class QueryError(ValueError):
    """A request the registry cannot answer (unknown metric, bad filter, missing period)."""


@dataclass(frozen=True)
class Source:
    id: str
    table: str
    label: str
    time_column: str
    loaded_column: str | None
    entity: str | None
    dimensions: tuple[str, ...]
    drill_columns: tuple[str, ...]
    history_from: date | None
    as_of_from: str = "loaded"          # loaded | latest_data
    list_columns: tuple[str, ...] = ()
    list_order: str | None = None
    list_latest: str | None = None
    list_class_column: str | None = None
    list_class_order: str | None = None


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    description: str
    source: str
    kind: str
    expression: str
    unit: str
    direction: str
    filter: str | None = None
    grouped_by: tuple[str, ...] = ()
    grouped_having: str | None = None
    grain: str | None = None
    synonyms: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    drill_measures: dict = field(default_factory=dict)
    drill_order: str | None = None


@dataclass(frozen=True)
class Period:
    start: date
    end: date            # exclusive

    def __post_init__(self):
        if self.end <= self.start:
            raise QueryError("The period end must be after its start")
        if (self.end - self.start).days > 3700:
            raise QueryError("Periods are limited to about 10 years")

    def previous(self) -> "Period":
        span = self.end - self.start
        return Period(self.start - span, self.start)

    @property
    def label(self) -> str:
        return f"{self.start:%d %b %Y} – {(self.end - timedelta(days=1)):%d %b %Y} (UTC)"


@dataclass(frozen=True)
class Spec:
    """One executable query: SQL text with `?` binds, the binds, and why it runs."""
    sql: str
    params: tuple
    purpose: str
    metric_id: str | None = None


def _check_sql_fragment(text: str, where: str) -> None:
    if ";" in text or "--" in text or "/*" in text:
        raise RegistryError(f"{where}: comments and statement separators are not allowed")
    if FORBIDDEN.search(re.sub(r"'[^']*'", "''", text)):
        raise RegistryError(f"{where}: only read-only expressions are allowed")


class Registry:
    def __init__(self, raw: dict):
        self.version = str(raw.get("version", ""))
        self.dimension_labels: dict[str, str] = dict(raw.get("dimension_labels") or {})
        self.sources: dict[str, Source] = {}
        for sid, s in (raw.get("sources") or {}).items():
            table = str(s.get("table", "")).upper()
            if not QUALIFIED.match(table):
                raise RegistryError(f"source {sid}: table must be DATABASE.SCHEMA.TABLE")
            lst = s.get("list") or {}
            cols = [s.get("time_column"), s.get("loaded_column"), s.get("entity"), *(s.get("dimensions") or []),
                    *(s.get("drill_columns") or []), *(lst.get("columns") or []), lst.get("order_by"), lst.get("latest_column"),
                    lst.get("class_column"), lst.get("class_order_by")]
            for c in cols:
                if c is not None and not IDENT.match(str(c)):
                    raise RegistryError(f"source {sid}: {c!r} is not a plain column name")
            hf = s.get("history_from")
            self.sources[sid] = Source(
                id=sid, table=table, label=str(s.get("label", sid)), time_column=s["time_column"],
                loaded_column=s.get("loaded_column"), entity=s.get("entity"),
                dimensions=tuple(s.get("dimensions") or ()), drill_columns=tuple(s.get("drill_columns") or ()),
                history_from=date.fromisoformat(str(hf)) if hf else None,
                as_of_from=str(s.get("as_of_from", "loaded")),
                list_columns=tuple(lst.get("columns") or ()), list_order=lst.get("order_by"),
                list_latest=lst.get("latest_column"), list_class_column=lst.get("class_column"),
                list_class_order=lst.get("class_order_by"))
            if self.sources[sid].as_of_from not in ("loaded", "latest_data"):
                raise RegistryError(f"source {sid}: as_of_from must be loaded or latest_data")
        self.reference_tables: tuple[str, ...] = tuple(str(t).upper() for t in raw.get("reference_tables") or ())
        for t in self.reference_tables:
            if not QUALIFIED.match(t):
                raise RegistryError(f"reference table {t!r} must be DATABASE.SCHEMA.TABLE")
        self.metrics: dict[str, Metric] = {}
        for m in raw.get("metrics") or []:
            mid = m.get("id", "")
            if not IDENT.match(mid) or mid in self.metrics:
                raise RegistryError(f"metric id {mid!r} is invalid or duplicated")
            if m.get("source") not in self.sources:
                raise RegistryError(f"metric {mid}: unknown source {m.get('source')!r}")
            if m.get("kind") not in KINDS:
                raise RegistryError(f"metric {mid}: kind must be one of {KINDS}")
            for key in ("expression", "filter"):
                if m.get(key):
                    _check_sql_fragment(str(m[key]), f"metric {mid} {key}")
            grouped = m.get("grouped") or {}
            if grouped:
                _check_sql_fragment(str(grouped.get("having", "")), f"metric {mid} grouped.having")
                for c in grouped.get("by") or []:
                    if not IDENT.match(c):
                        raise RegistryError(f"metric {mid}: grouped.by {c!r} is not a column name")
            drill = m.get("drill") or {}
            for k, expr in (drill.get("measures") or {}).items():
                if not IDENT.match(k):
                    raise RegistryError(f"metric {mid}: drill measure name {k!r} invalid")
                _check_sql_fragment(str(expr), f"metric {mid} drill measure {k}")
            if m.get("kind") == "period" and m.get("filter") and ":as_of" in m["filter"]:
                raise RegistryError(f"metric {mid}: period metrics are bounded by :start/:end, not :as_of")
            self.metrics[mid] = Metric(
                id=mid, name=m["name"], description=m["description"], source=m["source"], kind=m["kind"],
                expression=m["expression"], unit=m.get("unit", ""), direction=m.get("direction", "neutral"),
                filter=m.get("filter"), grouped_by=tuple(grouped.get("by") or ()), grouped_having=grouped.get("having"),
                grain=m.get("grain"), synonyms=tuple(m.get("synonyms") or ()), limitations=tuple(m.get("limitations") or ()),
                drill_measures=dict(drill.get("measures") or {}), drill_order=drill.get("order_by"))

    @classmethod
    def load(cls, path: Path) -> "Registry":
        import yaml   # only needed when the registry is read from metrics.yaml (multi-file deployment)
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def metric(self, metric_id: str) -> Metric:
        if metric_id not in self.metrics:
            raise QueryError(f"Unknown metric {metric_id!r}")
        return self.metrics[metric_id]

    def source_of(self, metric: Metric) -> Source:
        return self.sources[metric.source]

    def label(self, dim: str) -> str:
        return self.dimension_labels.get(dim, dim.replace("_", " ").title())


def normalise_filters(filters: dict | None, source: Source) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for dim, values in (filters or {}).items():
        if dim not in source.dimensions:
            raise QueryError(f"{dim!r} is not a filter for {source.label}")
        if isinstance(values, str):
            values = [values]
        vals = tuple(dict.fromkeys(str(v) for v in values if v is not None and str(v) != ""))
        if not vals:
            continue
        if len(vals) > MAX_FILTER_VALUES or any(len(v) > MAX_FILTER_LEN for v in vals):
            raise QueryError("Too many or too long filter values")
        out[dim] = vals
    return out


class _Sql:
    def __init__(self):
        self.parts: list[str] = []
        self.params: list = []

    def add(self, text: str, *params) -> "_Sql":
        self.parts.append(text)
        self.params.extend(params)
        return self

    def template(self, text: str, values: dict, table: str) -> "_Sql":
        text = text.replace("{table}", table)
        pos = 0
        for m in PLACEHOLDER.finditer(text):
            self.parts.append(text[pos:m.start()] + "?")
            self.params.append(values[m.group(1)])
            pos = m.end()
        self.parts.append(text[pos:])
        return self

    def text(self) -> str:
        return "".join(self.parts)


def _ts(d: date | datetime) -> str:
    """Bind format for TIMESTAMP_NTZ / DATE comparisons (UTC, no zone)."""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d %H:%M:%S")
    return d.strftime("%Y-%m-%d 00:00:00")


class Builder:
    def __init__(self, registry: Registry, max_rows: int = 500):
        self.r = registry
        self.max_rows = max_rows

    # -- shared WHERE ------------------------------------------------------------------------------
    def _where(self, q: _Sql, m: Metric, src: Source, period: Period | None, filters: dict, as_of: datetime) -> None:
        values = {"start": _ts(period.start) if period else None, "end": _ts(period.end) if period else None,
                  "as_of": _ts(as_of), "as_of_date": as_of.strftime("%Y-%m-%d")}
        q.add(" WHERE 1 = 1")
        if m.kind == "period":
            if period is None:
                raise QueryError(f"{m.name} needs a reporting period")
            q.add(f" AND {src.time_column} >= ? AND {src.time_column} < ?", values["start"], values["end"])
        if m.filter:
            q.add(" AND (").template(m.filter, values, src.table).add(")")
        for dim, vals in sorted(normalise_filters(filters, src).items()):
            named = [v for v in vals if v != NOT_SET]
            conds = []
            if named:
                conds.append(f"{dim} IN ({', '.join('?' for _ in named)})")
            if NOT_SET in vals:
                conds.append(f"{dim} IS NULL")
            q.add(" AND (" + " OR ".join(conds) + ")", *named)

    def _base(self, m: Metric, src: Source, period, filters, as_of, extra_group: tuple[str, ...] = ()) -> _Sql:
        """FROM clause: the table, or for grouped metrics the qualifying groups."""
        q = _Sql()
        if m.grouped_by:
            cols = list(dict.fromkeys((*m.grouped_by, *extra_group)))
            q.add(f"(SELECT {', '.join(cols)} FROM {src.table}")
            self._where(q, m, src, period, filters, as_of)
            q.add(f" GROUP BY {', '.join(cols)} HAVING {m.grouped_having}) g")
        else:
            q.add(src.table)
        return q

    def _assemble(self, select: str, m: Metric, src: Source, period, filters, as_of, tail: str = "",
                  extra_group: tuple[str, ...] = (), purpose: str = "value") -> Spec:
        q = _Sql().add(select + " FROM ")
        base = self._base(m, src, period, filters, as_of, extra_group)
        q.add(base.text(), *base.params)
        if not m.grouped_by:
            self._where(q, m, src, period, filters, as_of)
        q.add(tail)
        spec = Spec(q.text(), tuple(q.params), purpose, m.id)
        check_spec(spec, self.r)
        return spec

    # -- public builders ---------------------------------------------------------------------------
    def value(self, metric_id: str, period: Period | None, filters: dict, as_of: datetime) -> Spec:
        m = self.r.metric(metric_id)
        src = self.r.source_of(m)
        return self._assemble(f"SELECT {m.expression} AS value, COUNT(*) AS n", m, src, period, filters, as_of)

    def breakdown(self, metric_id: str, dimension: str, period: Period | None, filters: dict, as_of: datetime,
                  limit: int = 25) -> Spec:
        m = self.r.metric(metric_id)
        src = self.r.source_of(m)
        if dimension not in src.dimensions:
            raise QueryError(f"{m.name} cannot be broken down by {dimension!r}")
        seg = f"COALESCE(TO_VARCHAR({dimension}), '{NOT_SET}')"
        return self._assemble(f"SELECT {seg} AS segment, {m.expression} AS value, COUNT(*) AS n", m, src, period,
                              filters, as_of, tail=f" GROUP BY 1 ORDER BY value DESC NULLS LAST, 1 LIMIT {int(limit)}",
                              extra_group=(dimension,), purpose="breakdown")

    def series(self, metric_id: str, period: Period, grain: str, filters: dict, as_of: datetime) -> Spec:
        m = self.r.metric(metric_id)
        src = self.r.source_of(m)
        if m.kind != "period":
            raise QueryError(f"{m.name} is a current-state metric; it has no history in the source")
        grain = m.grain or grain
        if grain not in GRAINS:
            raise QueryError(f"grain must be one of {GRAINS}")
        bucket = f"DATE_TRUNC('{grain}', {src.time_column})::DATE"
        if m.grouped_by:
            # repeat-type metrics are evaluated per bucket: a group qualifies within its own bucket
            q = _Sql().add(f"SELECT bucket, {m.expression} AS value, COUNT(*) AS n FROM (SELECT {bucket} AS bucket, "
                           f"{', '.join(m.grouped_by)} FROM {src.table}")
            self._where(q, m, src, period, filters, as_of)
            q.add(f" GROUP BY 1, {', '.join(m.grouped_by)} HAVING {m.grouped_having}) g GROUP BY 1 ORDER BY 1")
            spec = Spec(q.text(), tuple(q.params), "series", m.id)
            check_spec(spec, self.r)
            return spec
        return self._assemble(f"SELECT {bucket} AS bucket, {m.expression} AS value, COUNT(*) AS n", m, src, period,
                              filters, as_of, tail=" GROUP BY 1 ORDER BY 1", purpose="series")

    def drilldown(self, metric_id: str, period: Period | None, filters: dict, as_of: datetime,
                  limit: int = 200) -> Spec:
        """Records behind a metric, aggregated to the source's drill columns (never raw text columns)."""
        m = self.r.metric(metric_id)
        src = self.r.source_of(m)
        limit = max(1, min(int(limit), self.max_rows))
        if m.grouped_by:
            measures = {"fault_days": "COUNT(DISTINCT occurred_at::DATE)", "events": "COUNT(*)",
                        "last_seen": "MAX(occurred_at)"} if src.id == "fault_events" else {"rows": "COUNT(*)"}
            extra = [f"ANY_VALUE({c}) AS {c}" for c in src.drill_columns if c not in m.grouped_by]
            q = _Sql().add(f"SELECT {', '.join([*m.grouped_by, *extra])}, "
                           + ", ".join(f"{e} AS {k}" for k, e in measures.items()) + f" FROM {src.table}")
            self._where(q, m, src, period, filters, as_of)
            order = next(iter(measures))
            q.add(f" GROUP BY {', '.join(m.grouped_by)} HAVING {m.grouped_having} ORDER BY {order} DESC, 1 LIMIT {limit}")
            spec = Spec(q.text(), tuple(q.params), "drilldown", m.id)
            check_spec(spec, self.r)
            return spec
        measures = m.drill_measures or {"records": "COUNT(*)"}
        order = m.drill_order or next(iter(measures))
        if src.entity and src.entity in src.drill_columns:
            # one row per entity (asset / opportunity); its attributes are constant or taken as any one value
            cols = [src.entity] + [f"ANY_VALUE({c}) AS {c}" for c in src.drill_columns if c != src.entity]
            group = src.entity
        else:
            cols = list(src.drill_columns)
            group = ", ".join(src.drill_columns)
        sel = f"SELECT {', '.join(cols)}, " + ", ".join(f"{e} AS {k}" for k, e in measures.items())
        return self._assemble(sel, m, src, period, filters, as_of,
                              tail=f" GROUP BY {group} ORDER BY {order} DESC NULLS LAST, 1 LIMIT {limit}",
                              purpose="drilldown")

    def freshness(self, source_id: str) -> Spec:
        src = self.r.sources[source_id]
        loaded = f"MAX({src.loaded_column})" if src.loaded_column else "NULL"
        spec = Spec(f"SELECT {loaded} AS loaded_at, MAX({src.time_column}) AS latest_data, COUNT(*) AS n FROM {src.table}",
                    (), "freshness")
        check_spec(spec, self.r)
        return spec

    def top_list(self, source_id: str, filters: dict, limit: int = 100, list_class: str | None = None) -> Spec:
        """The latest ranked list of a list-type source: the overall ranking, or one class's ranking."""
        src = self.r.sources[source_id]
        if not src.list_columns:
            raise QueryError(f"{src.label} has no ranked list")
        q = _Sql().add(f"SELECT {', '.join(src.list_columns)} FROM {src.table} WHERE {src.list_latest} = "
                       f"(SELECT MAX({src.list_latest}) FROM {src.table})")
        if list_class is None:
            q.add(f" AND {src.list_order} IS NOT NULL")
            order = src.list_order
        else:
            if not src.list_class_column:
                raise QueryError(f"{src.label} has no classes")
            q.add(f" AND {src.list_class_column} = ?", str(list_class)[:100])
            order = src.list_class_order
        for dim, vals in sorted(normalise_filters(filters, src).items()):
            named = [v for v in vals if v != NOT_SET]
            conds = ([f"{dim} IN ({', '.join('?' for _ in named)})"] if named else []) + \
                    ([f"{dim} IS NULL"] if NOT_SET in vals else [])
            q.add(" AND (" + " OR ".join(conds) + ")", *named)
        q.add(f" ORDER BY {order} LIMIT {max(1, min(int(limit), self.max_rows))}")
        spec = Spec(q.text(), tuple(q.params), "list", None)
        check_spec(spec, self.r)
        return spec

    def dimension_values(self, source_id: str, dimension: str, limit: int = 300) -> Spec:
        src = self.r.sources[source_id]
        if dimension not in src.dimensions:
            raise QueryError(f"{dimension!r} is not a dimension of {src.label}")
        spec = Spec(f"SELECT {dimension} AS value, COUNT(*) AS n FROM {src.table} WHERE {dimension} IS NOT NULL "
                    f"GROUP BY 1 ORDER BY n DESC LIMIT {int(limit)}", (), "dimension_values")
        check_spec(spec, self.r)
        return spec


def check_spec(spec: Spec, registry: Registry) -> None:
    """Last line of defence before execution: one read-only SELECT over registry tables only."""
    text = re.sub(r"'[^']*'", "''", spec.sql)
    if not re.match(r"^\s*(select|with)\b", text, re.I):
        raise QueryError("Only SELECT statements are executed")
    if ";" in text or "--" in text or "/*" in text:
        raise QueryError("Multiple statements and comments are not allowed")
    if FORBIDDEN.search(text):
        raise QueryError("Statement contains a non-read-only keyword")
    allowed = {s.table for s in registry.sources.values()} | set(registry.reference_tables)
    for ref in re.findall(r"\b([A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*\.[A-Z_][A-Z0-9_]*)\b", text, re.I):
        if ref.upper() not in allowed:
            raise QueryError(f"Table {ref} is not an approved source")
    if text.count("?") != len(spec.params):
        raise QueryError("Bind parameters do not match the statement")
