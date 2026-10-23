"""Analytical operations shared by the dashboard, the recommendation engine and the assistant.

Each function returns plain dicts carrying the value, its sample size, the
reporting period, the metric definition and the evidence (query id, SQL and
bind values) so any number on any page can be traced to the query behind it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from . import contract
from .executor import Executor
from .query import Filters, Period, QueryBuilder, QuerySpec
from .registry import Registry


def _num(v):
    if isinstance(v, Decimal):
        return float(v)
    return v


def _jsonable(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return _num(v)


def evidence(spec: QuerySpec, result) -> dict:
    return {"query_id": result.query_id, "purpose": spec.purpose, "sql": spec.sql,
            "params": [_jsonable(p) for p in spec.params], "cached": result.cached,
            "elapsed_ms": result.elapsed_ms}


def bucket_end(start: datetime, grain: str) -> datetime:
    if grain == "day":
        return start + timedelta(days=1)
    if grain == "week":
        return start + timedelta(days=7)
    months = 1 if grain == "month" else 3
    month = start.month - 1 + months
    return start.replace(year=start.year + month // 12, month=month % 12 + 1, day=1)


class Analytics:
    def __init__(self, registry: Registry, builder: QueryBuilder, executor: Executor, available: frozenset[str],
                 freshness_max_age_h: int = 36):
        self.registry = registry
        self.builder = builder
        self.executor = executor
        self.available = available
        self.freshness_max_age_h = freshness_max_age_h

    # -- freshness & quality -------------------------------------------------------------------------
    def quality(self, scope: Filters, user: str = "system") -> dict:
        spec = self.builder.quality(scope)
        res = self.executor.run(spec, user=user)
        r = {k: _num(v) for k, v in res.first().items()}
        total = int(r.get("total_rows") or 0)
        completeness = []
        for c in contract.COLUMNS:
            if c.name == "request_id":
                continue
            if c.name not in self.available:
                completeness.append({"column": c.name, "mapped": False, "share": None, "required": c.required})
            else:
                nn = int(r.get(f"nonnull_{c.name}") or 0)
                completeness.append({"column": c.name, "mapped": True, "required": c.required,
                                     "share": (nn / total) if total else None, "missing": total - nn})
        issues = []
        dup = total - int(r.get("distinct_ids") or 0)
        checks = [
            ("duplicate_ids", dup, "Rows sharing a request id in the contract view"),
            ("unmapped_status", r.get("unmapped_status"), "Rows whose status is not in the reviewed status mapping"),
            ("closed_before_created", r.get("closed_before_created"), "Close time earlier than creation time (excluded from resolution metrics)"),
            ("closed_without_time", r.get("closed_without_time"), "Closed status but no close time (excluded from close-time metrics)"),
            ("open_with_close_time", r.get("open_with_close_time"), "Open status but a close time is recorded"),
        ]
        for key, count, desc in checks:
            if count is not None:
                issues.append({"check": key, "count": int(count), "share": (int(count) / total) if total else None,
                               "description": desc})
        return {"total_rows": total, "completeness": completeness, "issues": issues,
                "first_created": _jsonable(r.get("first_created")), "last_created": _jsonable(r.get("last_created")),
                "last_updated_at": _jsonable(r.get("last_updated_at")), "last_loaded_at": _jsonable(r.get("last_loaded_at")),
                "evidence": evidence(spec, res), "_raw": r}

    def freshness(self, scope: Filters, user: str = "system", now: datetime | None = None) -> dict:
        q = self.quality(scope, user)
        r = q["_raw"]
        basis, as_of = None, None
        for col, label in (("last_loaded_at", "load time"), ("last_updated_at", "last source update"),
                           ("last_created", "latest creation time")):
            if r.get(col) is not None:
                basis, as_of = label, r[col]
                break
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        age_h = (now - as_of).total_seconds() / 3600 if as_of else None
        return {"as_of": as_of, "as_of_iso": _jsonable(as_of), "basis": basis, "age_hours": age_h,
                "stale": age_h is None or age_h > self.freshness_max_age_h, "max_age_hours": self.freshness_max_age_h,
                "first_created": r.get("first_created"), "total_rows": q["total_rows"], "quality": q}

    def period_notes(self, period: Period, fresh: dict) -> list[str]:
        notes = []
        tz = self.builder.tz
        lo, hi = period.utc_bounds(tz)
        first, as_of = fresh.get("first_created"), fresh.get("as_of")
        if first and lo < first:
            notes.append(f"The period starts before the earliest record ({first.date().isoformat()}); "
                         "earlier dates have no data, which is not the same as zero activity.")
        if as_of and hi > as_of:
            notes.append(f"The period runs past the last data refresh ({as_of.isoformat(timespec='minutes')} UTC); "
                         "the final days are incomplete.")
        return notes

    # -- metrics -----------------------------------------------------------------------------------
    def metric(self, metric_id: str, period: Period | None, filters: Filters, scope: Filters, as_of: datetime,
               compare: bool = True, user: str = "system") -> dict:
        m = self.registry.metric(metric_id)
        out = {"metric": m.public(self.available), "period": None, "as_of": _jsonable(as_of)}
        missing = [c for c in m.requires if c not in self.available]
        if missing:
            out.update(value=None, n=0, available=False,
                       reason=f"Not available: the mapped source has no {', '.join(missing)}.")
            return out
        spec = self.builder.value(m.id, period, filters, scope, as_of)
        res = self.executor.run(spec, user=user)
        row = res.first()
        value, n = _num(row.get("metric_value")), int(row.get("n") or 0)
        sufficient = n >= (m.min_sample or 1)
        out.update(available=True, value=value, n=n, sufficient=sufficient, evidence=evidence(spec, res))
        if m.kind == "period":
            out["period"] = {"start": period.start.isoformat(), "end": period.end.isoformat(), "label": period.label()}
            if compare:
                prev = period.previous()
                pspec = self.builder.value(m.id, prev, filters, scope, as_of)
                pres = self.executor.run(pspec, user=user)
                prow = pres.first()
                pval, pn = _num(prow.get("metric_value")), int(prow.get("n") or 0)
                psuff = pn >= (m.min_sample or 1)
                out["previous"] = {"value": pval, "n": pn, "sufficient": psuff, "label": prev.label(),
                                   "start": prev.start.isoformat(), "end": prev.end.isoformat(),
                                   "evidence": evidence(pspec, pres)}
                if value is not None and pval is not None and sufficient and psuff:
                    out["change"] = {"abs": value - pval, "pct": ((value - pval) / pval) if pval else None}
        if not sufficient:
            out["reason"] = f"Only {n} records; at least {m.min_sample} are needed for a reliable figure."
        return out

    def series(self, metric_id: str, period: Period, grain: str, filters: Filters, scope: Filters, as_of: datetime,
               user: str = "system") -> dict:
        spec = self.builder.series(metric_id, period, grain, filters, scope, as_of)
        res = self.executor.run(spec, user=user)
        m = self.registry.metric(metric_id)
        tz = self.builder.tz
        as_of_local = as_of.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None) if as_of else None
        points = []
        for r in res.records():
            start = r["bucket"] if isinstance(r["bucket"], datetime) else datetime.combine(r["bucket"], datetime.min.time())
            end = bucket_end(start, grain)
            partial = (start.date() < period.start or end.date() > period.end
                       or (as_of_local is not None and end > as_of_local))
            points.append({"bucket": _jsonable(r["bucket"]), "value": _num(r["metric_value"]), "n": int(r["n"]),
                           "sufficient": int(r["n"]) >= (m.min_sample or 1), "partial": partial})
        return {"metric": m.public(self.available), "grain": grain, "points": points,
                "period": {"start": period.start.isoformat(), "end": period.end.isoformat(), "label": period.label()},
                "evidence": evidence(spec, res)}

    def breakdown(self, metric_id: str, dimension: str, period: Period | None, filters: Filters, scope: Filters,
                  as_of: datetime, limit: int = 25, user: str = "system") -> dict:
        m = self.registry.metric(metric_id)
        spec = self.builder.breakdown(metric_id, dimension, period if m.kind == "period" else None, filters, scope,
                                      as_of, limit=limit)
        res = self.executor.run(spec, user=user)
        rows = [{"segment": r["segment"], "value": _num(r["metric_value"]), "n": int(r["n"]),
                 "sufficient": int(r["n"]) >= (m.min_sample or 1)} for r in res.records()]
        return {"metric": m.public(self.available), "dimension": dimension,
                "dimension_label": self.registry.dimensions[dimension].label if dimension in self.registry.dimensions else dimension,
                "rows": rows, "period": ({"label": period.label(), "start": period.start.isoformat(),
                                          "end": period.end.isoformat()} if m.kind == "period" and period else None),
                "as_of": _jsonable(as_of), "evidence": evidence(spec, res), "truncated": res.truncated}

    def drilldown(self, metric_id: str, period: Period | None, filters: Filters, scope: Filters, as_of: datetime,
                  limit: int, offset: int, user: str = "system") -> dict:
        m = self.registry.metric(metric_id)
        spec = self.builder.drilldown(metric_id, period if m.kind == "period" else None, filters, scope, as_of,
                                      limit=limit + 1, offset=offset)
        res = self.executor.run(spec, user=user)
        recs = res.records()
        has_more = len(recs) > limit
        rows = [{k: _jsonable(v) for k, v in r.items()} for r in recs[:limit]]
        return {"metric": m.public(self.available), "columns": spec.meta["columns"], "rows": rows,
                "offset": offset, "limit": limit, "has_more": has_more, "evidence": evidence(spec, res)}

    def default_period(self, as_of: datetime | None, days: int = 90) -> Period:
        end_local = (as_of.replace(tzinfo=timezone.utc).astimezone(self.builder.tz).date() if as_of
                     else datetime.now(self.builder.tz).date()) + timedelta(days=1)
        return Period(end_local - timedelta(days=days), end_local)
