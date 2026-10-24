"""Analysis service used by all three pages: metric values, comparisons, breakdowns, trends, drill-down,
freshness. Pure Python over a runner, so it is testable without Snowflake."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .metrics import Builder, Period, QueryError, Registry

MIN_SAMPLE = 10


def _as_datetime(v) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).replace("T", " ")[:19]
    try:
        return datetime.fromisoformat(s) if len(s) > 10 else datetime.combine(date.fromisoformat(s[:10]), datetime.min.time())
    except ValueError:
        return None


def evidence(result) -> dict:
    return {"query_id": result.query_id, "purpose": result.spec.purpose, "metric_id": result.spec.metric_id,
            "sql": result.spec.sql, "params": list(result.spec.params)}


class Analytics:
    def __init__(self, registry: Registry, runner, user: str = "unknown"):
        self.r = registry
        self.b = Builder(registry)
        self.run = lambda spec: runner.run(spec, user)
        self._fresh: dict[str, dict] = {}

    # -- freshness ---------------------------------------------------------------------------------
    def freshness(self, source_id: str) -> dict:
        if source_id not in self._fresh:
            res = self.run(self.b.freshness(source_id))
            row = res.rows[0] if res.rows else {}
            loaded = _as_datetime(row.get("loaded_at"))
            latest = _as_datetime(row.get("latest_data"))
            self._fresh[source_id] = {"source": source_id, "label": self.r.sources[source_id].label,
                                      "loaded_at": loaded, "latest_data": latest, "rows": row.get("n"),
                                      "evidence": evidence(res)}
        return self._fresh[source_id]

    def as_of(self, source_id: str) -> datetime:
        """The data's own 'now': latest load time if recorded, else the latest data point."""
        f = self.freshness(source_id)
        if self.r.sources[source_id].as_of_from == "latest_data":
            ts = f["latest_data"] or f["loaded_at"]
        else:
            ts = f["loaded_at"] or f["latest_data"]
        if ts is None:
            raise QueryError(f"{f['label']} has no data")
        return ts

    def default_period(self, source_id: str, days: int = 30) -> Period:
        end = self.as_of(source_id).date() + timedelta(days=1)
        return Period(end - timedelta(days=days), end)

    # -- metric ------------------------------------------------------------------------------------
    def metric(self, metric_id: str, period: Period | None, filters: dict | None = None, compare: bool = True) -> dict:
        m = self.r.metric(metric_id)
        src = self.r.source_of(m)
        as_of = self.as_of(src.id)
        filters = filters or {}
        res = self.run(self.b.value(metric_id, period if m.kind == "period" else None, filters, as_of))
        value, n = res.scalar("value"), res.scalar("n") or 0
        out = {"metric": m, "value": value, "n": n, "period": period if m.kind == "period" else None,
               "as_of": as_of, "filters": filters, "evidence": [evidence(res)], "notes": [], "previous": None,
               "change": None}
        if m.kind == "period" and period is not None:
            if src.history_from and period.start < src.history_from:
                out["notes"].append(f"The source has usable history only from {src.history_from:%b %Y}; "
                                    "earlier parts of the period are incomplete.")
            if period.end > as_of.date() + timedelta(days=1):
                out["notes"].append(f"The period runs past the latest data ({as_of:%d %b %Y %H:%M} UTC); it is incomplete.")
            if compare:
                prev = period.previous()
                pres = self.run(self.b.value(metric_id, prev, filters, as_of))
                pv = pres.scalar("value")
                out["previous"] = {"period": prev, "value": pv, "n": pres.scalar("n") or 0}
                out["evidence"].append(evidence(pres))
                if value is not None and pv not in (None, 0):
                    out["change"] = (float(value) - float(pv)) / abs(float(pv))
                if src.history_from and prev.start < src.history_from:
                    out["notes"].append("The comparison period starts before the source's usable history.")
        if n < MIN_SAMPLE and value is not None:
            out["notes"].append(f"Small sample ({n} records): treat the figure as indicative.")
        if value is None:
            out["notes"].append("No data matched this period and filters.")
        return out

    def breakdown(self, metric_id: str, dimension: str, period: Period | None, filters: dict | None = None,
                  limit: int = 25) -> dict:
        m = self.r.metric(metric_id)
        as_of = self.as_of(m.source)
        res = self.run(self.b.breakdown(metric_id, dimension, period if m.kind == "period" else None, filters or {},
                                        as_of, limit))
        return {"metric": m, "dimension": dimension, "rows": res.rows, "evidence": [evidence(res)], "as_of": as_of}

    def series(self, metric_id: str, period: Period, grain: str = "week", filters: dict | None = None) -> dict:
        m = self.r.metric(metric_id)
        as_of = self.as_of(m.source)
        res = self.run(self.b.series(metric_id, period, grain, filters or {}, as_of))
        return {"metric": m, "rows": res.rows, "grain": m.grain or grain, "evidence": [evidence(res)]}

    def drilldown(self, metric_id: str, period: Period | None, filters: dict | None = None, limit: int = 200) -> dict:
        m = self.r.metric(metric_id)
        as_of = self.as_of(m.source)
        res = self.run(self.b.drilldown(metric_id, period if m.kind == "period" else None, filters or {}, as_of, limit))
        return {"metric": m, "rows": res.rows, "truncated": len(res.rows) >= limit, "evidence": [evidence(res)]}

    def top_list(self, source_id: str, filters: dict | None = None, limit: int = 100) -> dict:
        res = self.run(self.b.top_list(source_id, filters or {}, limit))
        return {"rows": res.rows, "evidence": [evidence(res)], "freshness": self.freshness(source_id)}

    def dimension_values(self, source_id: str, dimension: str) -> list[str]:
        return [r["value"] for r in self.run(self.b.dimension_values(source_id, dimension)).rows if r["value"] is not None]


def fmt_value(v, unit: str) -> str:
    if v is None:
        return "—"
    v = float(v)
    if unit == "ratio":
        return f"{v:.1%}"
    if unit == "value":
        for div, suf in ((1e9, "bn"), (1e6, "M"), (1e3, "k")):
            if abs(v) >= div:
                return f"{v / div:,.1f}{suf}"
        return f"{v:,.0f}"
    return f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"
