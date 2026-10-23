"""Evidence-backed recommendation candidates.

Each detector reads registry metrics through `Analytics` (the same calculations
the dashboard shows), compares a segment with the whole or a period with the
previous one, and emits a candidate only when the sample is large enough. Every
candidate carries the measured figures, the query ids and SQL behind them, a
drill-down to the affected records, a transparent priority score, and an
expected-impact statement that is explicitly labelled as a projection.

Detectors find where a metric differs. They do not establish why; every
candidate says so.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from .analysis import Analytics
from .backends.base import BackendError
from .query import Filters, Period, QueryError

WEIGHTS = {"impact": 0.45, "urgency": 0.30, "confidence": 0.25}
FORMULA = "score = 0.45 × impact + 0.30 × urgency + 0.25 × confidence (each 1–5)"
CAUSATION_NOTE = ("This shows where the metric differs; it does not establish the cause. "
                  "Confirm the cause with the team before acting.")
EFFORT_NOTE = "Not estimated: the source has no effort or cost data. Add an estimate during review."
DETECTOR_DIMENSIONS = ("team", "region", "category", "service_type", "priority")


@dataclass(frozen=True)
class Thresholds:
    min_segment_n: int = 20
    aged_share_gap: float = 0.15
    aged_share_floor: float = 0.30
    slow_ratio: float = 1.5
    regression_pct: float = 0.25
    sla_gap: float = 0.10
    surge_pct: float = 0.40
    completeness_floor: float = 0.95
    issue_share_floor: float = 0.01
    past_due_share: float = 0.20


def _band(value: float, cuts: tuple[float, ...]) -> int:
    """1 + number of cut points the value reaches (cuts ascending, up to 4)."""
    return 1 + sum(value >= c for c in cuts)


def sample_confidence(n: int, completeness: float | None, stale: bool) -> int:
    score = _band(n, (20, 50, 100, 200))
    if completeness is not None and completeness < 0.9:
        score -= 1
    if stale:
        score -= 1
    return max(1, min(5, score))


def priority(impact: int, urgency: int, confidence: int) -> dict:
    score = round(WEIGHTS["impact"] * impact + WEIGHTS["urgency"] * urgency + WEIGHTS["confidence"] * confidence, 2)
    label = "High" if score >= 3.8 else "Medium" if score >= 2.8 else "Low"
    return {"score": score, "label": label, "impact": impact, "urgency": urgency, "confidence": confidence,
            "formula": FORMULA}


def _fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "hours":
        return f"{value:.1f} h"
    if unit == "days":
        return f"{value:.1f} days"
    return f"{value:,.0f}"


def _ev(label: str, result: dict, value=None, n=None, kind: str = "measured") -> dict:
    unit = result["metric"]["unit"]
    v = result.get("value") if value is None else value
    return {"label": label, "metric_id": result["metric"]["id"], "value": v, "display": _fmt(v, unit), "unit": unit,
            "n": result.get("n") if n is None else n, "kind": kind,
            "query_id": result.get("evidence", {}).get("query_id"), "sql": result.get("evidence", {}).get("sql"),
            "params": result.get("evidence", {}).get("params")}


class Recommender:
    def __init__(self, analytics: Analytics, thresholds: Thresholds | None = None):
        self.a = analytics
        self.t = thresholds or Thresholds()

    def _dims(self) -> list[str]:
        return [d for d in DETECTOR_DIMENSIONS if d in self.a.available]

    def _usable(self, metric_id: str) -> bool:
        return all(c in self.a.available for c in self.a.registry.metric(metric_id).requires)

    def snapshot(self, period: Period, filters: Filters, scope: Filters, as_of: datetime) -> dict:
        basis = json.dumps({"as_of": as_of.isoformat(), "period": [period.start.isoformat(), period.end.isoformat()],
                            "filters": filters, "scope": scope, "registry": self.a.registry.version}, sort_keys=True)
        return {"snapshot_id": "S-" + hashlib.sha256(basis.encode()).hexdigest()[:12], "as_of": as_of.isoformat(),
                "period": period.label(), "period_start": period.start.isoformat(), "period_end": period.end.isoformat(),
                "filters": {k: list(v) for k, v in filters.items()}, "scope": {k: list(v) for k, v in scope.items()},
                "registry_version": self.a.registry.version}

    def generate(self, period: Period, filters: Filters, scope: Filters, fresh: dict, user: str = "system") -> dict:
        as_of = fresh["as_of"]
        snap = self.snapshot(period, filters, scope, as_of)
        completeness = {c["column"]: c["share"] for c in fresh["quality"]["completeness"] if c["mapped"]}
        ctx = {"period": period, "filters": filters, "scope": scope, "as_of": as_of, "stale": fresh["stale"],
               "completeness": completeness, "user": user, "snapshot": snap}
        out, skipped = [], []
        for detector in (self._backlog_aging, self._past_due, self._sla_hotspots, self._slow_segments,
                         self._regressions, self._volume_surges, self._data_quality):
            try:
                out.extend(detector(ctx))
            except (QueryError, BackendError) as exc:
                skipped.append({"detector": detector.__name__.strip("_"), "reason": str(exc)})
        created = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for c in out:
            c["snapshot"] = snap
            c["generated_at"] = created
            c.setdefault("causation_note", CAUSATION_NOTE)
        out.sort(key=lambda c: (-c["priority"]["score"], c["key"]))
        for i, c in enumerate(out, 1):
            c["rank"] = i
        return {"snapshot": snap, "candidates": out, "skipped": skipped, "thresholds": self.t.__dict__,
                "ranking": FORMULA}

    # -- helpers -----------------------------------------------------------------------------------
    def _candidate(self, ctx, *, key, detector, title, summary, problem, metric_id, dimension, segment, evidence,
                   impact, urgency, n, expected, assumptions, actions, risks, dependencies=(), period_scoped=True):
        conf_basis = ctx["completeness"].get(dimension) if dimension else None
        confidence = sample_confidence(n, conf_basis, ctx["stale"])
        drill_filters = dict(ctx["filters"])
        if dimension:
            drill_filters[dimension] = (segment,)
        owner = None
        if dimension == "team":
            owner = {"team": segment, "basis": "The affected records carry this value in the source team field."}
        return {
            "key": key, "detector": detector, "title": title, "summary": summary, "problem": problem,
            "affected": {"metric_id": metric_id, "dimension": dimension, "segment": segment},
            "evidence": evidence,
            "drilldown": {"metric_id": metric_id, "filters": {k: list(v) for k, v in drill_filters.items()},
                          "period_scoped": period_scoped},
            "priority": priority(impact, urgency, confidence),
            "expected_impact": {"statement": expected, "kind": "projection", "assumptions": list(assumptions)},
            "effort": {"estimate": None, "note": EFFORT_NOTE},
            "owner_suggestion": owner,
            "actions": list(actions), "risks": list(risks), "dependencies": list(dependencies),
            "confidence_note": (f"Confidence {confidence}/5 from {n} records"
                                + ("; data is stale" if ctx["stale"] else "")
                                + (f"; {dimension} is {conf_basis * 100:.0f}% complete" if conf_basis is not None else "")),
        }

    # -- detectors ---------------------------------------------------------------------------------
    def _backlog_aging(self, ctx) -> list[dict]:
        if not self._usable("aged_backlog_share"):
            return []
        a, t, out = self.a, self.t, []
        overall = a.metric("aged_backlog_share", None, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
        total_aged = a.metric("aged_backlog", None, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
        if not overall.get("sufficient") or overall["value"] is None:
            return []
        aged_days = a.registry.param("aged_days")
        for dim in self._dims():
            shares = a.breakdown("aged_backlog_share", dim, None, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
            for row in shares["rows"]:
                share, n = row["value"], row["n"]
                if n < t.min_segment_n or share is None:
                    continue
                if share < t.aged_share_floor or share < overall["value"] + t.aged_share_gap:
                    continue
                aged = round(share * n)
                excess = max(0, round(aged - overall["value"] * n))
                seg = row["segment"]
                impact = _band(excess / max(1, total_aged["value"] or 1), (0.03, 0.08, 0.15, 0.25))
                urgency = _band(share, (0.3, 0.45, 0.6, 0.75))
                ev = [_ev(f"Aged share of open requests — {dim} {seg}", shares, share, n),
                      _ev("Aged share of open requests — all in scope", overall),
                      _ev("Aged open requests — all in scope", total_aged)]
                ev[0]["query_id"] = shares["evidence"]["query_id"]
                ev[0]["sql"] = shares["evidence"]["sql"]
                ev[0]["params"] = shares["evidence"]["params"]
                out.append(self._candidate(
                    ctx, key=f"backlog_aging:{dim}={seg}", detector="backlog_aging",
                    title=f"Clear the aged backlog in {a.registry.dimensions[dim].label.lower()} {seg}",
                    summary=(f"{_fmt(share, 'ratio')} of {n} open requests in {seg} are older than {aged_days} days, "
                             f"against {_fmt(overall['value'], 'ratio')} overall."),
                    problem="Open work is ageing faster here than elsewhere.",
                    metric_id="aged_backlog", dimension=dim, segment=seg, evidence=ev, impact=impact,
                    urgency=urgency, n=n, period_scoped=False,
                    expected=(f"If {seg} matched the overall aged share, about {excess} fewer requests would be "
                              f"older than {aged_days} days."),
                    assumptions=["Inflow and staffing stay as they are.",
                                 f"The {aged_days}-day ageing threshold is a configurable assumption."],
                    actions=[f"Review the {aged} aged open requests in {seg} (drill-down) and close any that are finished or obsolete.",
                             "Identify blockers (parts, approvals, customer response) for the rest and assign a date to each.",
                             "Re-check the aged share after two weeks."],
                    risks=["Closing records administratively without fixing the work would hide the problem."]))
        return out

    def _past_due(self, ctx) -> list[dict]:
        if not self._usable("open_past_due"):
            return []
        a = self.a
        past = a.metric("open_past_due", None, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
        backlog = a.metric("open_backlog", None, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
        if not backlog.get("value") or past.get("value") is None:
            return []
        share = past["value"] / backlog["value"]
        if share < self.t.past_due_share or past["value"] < 5:
            return []
        n = int(backlog["value"])
        return [self._candidate(
            ctx, key="past_due:all", detector="past_due",
            title="Triage open requests that are already past their SLA target",
            summary=f"{past['value']:,.0f} of {n:,} open requests ({_fmt(share, 'ratio')}) are past their SLA target time.",
            problem="These requests will count as breaches when they close.",
            metric_id="open_past_due", dimension=None, segment=None,
            evidence=[_ev("Open requests past SLA target", past), _ev("Open backlog", backlog)],
            impact=_band(share, (0.1, 0.2, 0.35, 0.5)), urgency=5, n=n, period_scoped=False,
            expected="Each one closed now stops further ageing; it will still be recorded as a breach.",
            assumptions=["SLA targets come from the mapped due time or the approved SLA policy."],
            actions=["Sort the past-due list by priority and age (drill-down).",
                     "Agree a recovery date for each P1/P2 item with its owning team.",
                     "Report the count weekly until it is below the agreed level."],
            risks=["Re-prioritising past-due work can delay requests that are still within target."])]

    def _sla_hotspots(self, ctx) -> list[dict]:
        if not self._usable("sla_compliance_rate"):
            return []
        a, t, out = self.a, self.t, []
        overall = a.metric("sla_compliance_rate", ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"],
                           compare=False, user=ctx["user"])
        if not overall.get("sufficient") or overall["value"] is None:
            return []
        for dim in self._dims():
            if dim == "priority":
                continue          # targets differ by priority by design
            bd = a.breakdown("sla_compliance_rate", dim, ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
            for row in bd["rows"]:
                if row["n"] < t.min_segment_n or row["value"] is None:
                    continue
                gap = overall["value"] - row["value"]
                if gap < t.sla_gap:
                    continue
                seg, n = row["segment"], row["n"]
                missed_extra = round(gap * n)
                ev = [{**_ev(f"SLA compliance — {dim} {seg}", bd, row["value"], n), **{
                    "query_id": bd["evidence"]["query_id"], "sql": bd["evidence"]["sql"], "params": bd["evidence"]["params"]}},
                      _ev("SLA compliance — all in scope", overall)]
                out.append(self._candidate(
                    ctx, key=f"sla_hotspot:{dim}={seg}", detector="sla_hotspot",
                    title=f"Raise SLA compliance in {a.registry.dimensions[dim].label.lower()} {seg}",
                    summary=(f"{seg} met its SLA target on {_fmt(row['value'], 'ratio')} of {n} closed requests "
                             f"in {ctx['period'].label()}, against {_fmt(overall['value'], 'ratio')} overall."),
                    problem="Requests here miss their SLA target more often than elsewhere.",
                    metric_id="sla_compliance_rate", dimension=dim, segment=seg, evidence=ev,
                    impact=_band(missed_extra / max(1, overall["n"]), (0.01, 0.03, 0.06, 0.1)),
                    urgency=_band(gap, (0.1, 0.15, 0.25, 0.35)), n=n,
                    expected=(f"Matching the overall rate would have avoided about {missed_extra} breaches "
                              f"in {ctx['period'].label()}."),
                    assumptions=["Same request mix and volume as the analysed period."],
                    actions=[f"Review breached requests in {seg} (drill-down) and group them by cause.",
                             "Check whether targets, staffing or parts availability differ for this segment.",
                             "Agree one corrective action and track compliance monthly."],
                    risks=["A different priority mix can explain part of the gap; compare by priority before acting."]))
        return out

    def _slow_segments(self, ctx) -> list[dict]:
        if not self._usable("median_resolution_hours"):
            return []
        a, t, out = self.a, self.t, []
        overall = a.metric("median_resolution_hours", ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"],
                           compare=False, user=ctx["user"])
        if not overall.get("sufficient") or not overall["value"]:
            return []
        for dim in self._dims():
            if dim == "priority":
                continue
            bd = a.breakdown("median_resolution_hours", dim, ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
            for row in bd["rows"]:
                if row["n"] < t.min_segment_n or row["value"] is None:
                    continue
                ratio = row["value"] / overall["value"]
                if ratio < t.slow_ratio:
                    continue
                seg, n = row["segment"], row["n"]
                ev = [{**_ev(f"Median resolution — {dim} {seg}", bd, row["value"], n), **{
                    "query_id": bd["evidence"]["query_id"], "sql": bd["evidence"]["sql"], "params": bd["evidence"]["params"]}},
                      _ev("Median resolution — all in scope", overall)]
                out.append(self._candidate(
                    ctx, key=f"slow_segment:{dim}={seg}", detector="slow_segment",
                    title=f"Investigate slow resolution in {a.registry.dimensions[dim].label.lower()} {seg}",
                    summary=(f"Median resolution in {seg} is {_fmt(row['value'], 'hours')} "
                             f"({ratio:.1f}× the overall {_fmt(overall['value'], 'hours')}) over {n} closed requests."),
                    problem="Requests here take markedly longer to resolve.",
                    metric_id="median_resolution_hours", dimension=dim, segment=seg, evidence=ev,
                    impact=_band(n / max(1, overall["n"]), (0.03, 0.08, 0.15, 0.25)),
                    urgency=_band(ratio, (2.0, 2.5, 3.5, 5.0)), n=n,
                    expected=(f"Bringing {seg} to the overall median would save about "
                              f"{row['value'] - overall['value']:.0f} hours on a typical request."),
                    assumptions=["Calendar hours; no business-hours calendar is available.",
                                 "Request mix (priority, type) in this segment is comparable to the whole."],
                    actions=[f"Compare {seg} by priority and category to rule out a different mix.",
                             "Review the slowest closed requests (drill-down) for common delays.",
                             "Agree a target and monitor the median monthly."],
                    risks=["Segments with harder work will look slow without being inefficient."]))
        return out

    def _regressions(self, ctx) -> list[dict]:
        if not self._usable("median_resolution_hours"):
            return []
        a, t, out = self.a, self.t, []
        prev = ctx["period"].previous()
        for dim in [None] + [d for d in self._dims() if d in ("region", "team", "category")]:
            if dim is None:
                cur_rows = [{"segment": None, **a.metric("median_resolution_hours", ctx["period"], ctx["filters"],
                                                         ctx["scope"], ctx["as_of"], compare=False, user=ctx["user"])}]
                prev_rows = {None: a.metric("median_resolution_hours", prev, ctx["filters"], ctx["scope"], ctx["as_of"],
                                            compare=False, user=ctx["user"])}
                cur_ev, prev_ev = cur_rows[0].get("evidence", {}), prev_rows[None].get("evidence", {})
            else:
                cur = a.breakdown("median_resolution_hours", dim, ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
                old = a.breakdown("median_resolution_hours", dim, prev, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
                cur_rows = cur["rows"]
                prev_rows = {r["segment"]: r for r in old["rows"]}
                cur_ev, prev_ev = cur["evidence"], old["evidence"]
            for row in cur_rows:
                before = prev_rows.get(row["segment"])
                if not before or row.get("value") is None or before.get("value") in (None, 0):
                    continue
                if row["n"] < t.min_segment_n or before["n"] < t.min_segment_n:
                    continue
                pct = (row["value"] - before["value"]) / before["value"]
                if pct < t.regression_pct:
                    continue
                seg = row["segment"]
                where = f"{a.registry.dimensions[dim].label.lower()} {seg}" if dim else "all requests in scope"
                unit = "hours"
                ev = [{"label": f"Median resolution {ctx['period'].label()} — {where}", "metric_id": "median_resolution_hours",
                       "value": row["value"], "display": _fmt(row["value"], unit), "unit": unit, "n": row["n"], "kind": "measured",
                       "query_id": cur_ev.get("query_id"), "sql": cur_ev.get("sql"), "params": cur_ev.get("params")},
                      {"label": f"Median resolution {prev.label()} — {where}", "metric_id": "median_resolution_hours",
                       "value": before["value"], "display": _fmt(before["value"], unit), "unit": unit, "n": before["n"],
                       "kind": "measured", "query_id": prev_ev.get("query_id"), "sql": prev_ev.get("sql"), "params": prev_ev.get("params")}]
                out.append(self._candidate(
                    ctx, key=f"resolution_regression:{dim or 'all'}={seg or 'all'}", detector="resolution_regression",
                    title=f"Find out why resolution slowed in {where}",
                    summary=(f"Median resolution rose {pct * 100:.0f}% from {_fmt(before['value'], unit)} to "
                             f"{_fmt(row['value'], unit)} compared with the previous period of equal length."),
                    problem="Resolution time got worse compared with the previous period.",
                    metric_id="median_resolution_hours", dimension=dim, segment=seg, evidence=ev,
                    impact=_band(row["n"] / max(1, row["n"] if dim is None else sum(r["n"] for r in cur_rows)),
                                 (0.03, 0.08, 0.15, 0.25)),
                    urgency=_band(pct, (0.25, 0.4, 0.6, 1.0)), n=min(row["n"], before["n"]),
                    expected=f"Returning to the previous median would save about {row['value'] - before['value']:.0f} hours per typical request.",
                    assumptions=["The previous period is a fair baseline (no seasonal or mix change)."],
                    actions=["Compare the request mix between the two periods (priority, category).",
                             "Ask the team what changed (staffing, parts supply, process).",
                             "Track the weekly median until it returns to baseline."],
                    risks=["A temporary spike or seasonal pattern can look like a regression."]))
        return out

    def _volume_surges(self, ctx) -> list[dict]:
        if not self._usable("requests_created"):
            return []
        a, t, out = self.a, self.t, []
        prev = ctx["period"].previous()
        total = a.metric("requests_created", ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"], compare=False, user=ctx["user"])
        for dim in [d for d in self._dims() if d in ("category", "service_type")]:
            cur = a.breakdown("requests_created", dim, ctx["period"], ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
            old_bd = a.breakdown("requests_created", dim, prev, ctx["filters"], ctx["scope"], ctx["as_of"], user=ctx["user"])
            old, old_ev = {r["segment"]: r for r in old_bd["rows"]}, old_bd["evidence"]
            for row in cur["rows"]:
                before = old.get(row["segment"])
                if not before or not before["value"] or row["value"] < t.min_segment_n:
                    continue
                pct = (row["value"] - before["value"]) / before["value"]
                if pct < t.surge_pct:
                    continue
                seg = row["segment"]
                ev = [{"label": f"Requests created {ctx['period'].label()} — {seg}", "metric_id": "requests_created",
                       "value": row["value"], "display": _fmt(row["value"], "count"), "unit": "count", "n": row["n"],
                       "kind": "measured", **{k: cur["evidence"][k] for k in ("query_id", "sql", "params")}},
                      {"label": f"Requests created {prev.label()} — {seg}", "metric_id": "requests_created",
                       "value": before["value"], "display": _fmt(before["value"], "count"), "unit": "count", "n": before["n"],
                       "kind": "measured", **{k: old_ev[k] for k in ("query_id", "sql", "params")}}]
                out.append(self._candidate(
                    ctx, key=f"volume_surge:{dim}={seg}", detector="volume_surge",
                    title=f"Investigate rising demand in {a.registry.dimensions[dim].label.lower()} {seg}",
                    summary=(f"{seg} requests rose {pct * 100:.0f}% ({before['value']:,.0f} → {row['value']:,.0f}) "
                             "compared with the previous period of equal length."),
                    problem="Demand in this segment is growing quickly; recurring failures are one possible reason.",
                    metric_id="requests_created", dimension=dim, segment=seg, evidence=ev,
                    impact=_band(row["value"] / max(1, total.get("value") or 1), (0.05, 0.1, 0.2, 0.3)),
                    urgency=_band(pct, (0.4, 0.7, 1.0, 2.0)), n=int(row["value"]),
                    expected="Not quantified: the effect depends on the cause, which the data does not show.",
                    assumptions=["The rise is not explained by a change in how requests are categorised."],
                    actions=[f"Review recent {seg} requests (drill-down) for repeated equipment, customers or faults.",
                             "Check capacity for this category over the next month.",
                             "If a recurring failure is confirmed, raise a preventive action with engineering."],
                    risks=["Re-categorisation or a one-off campaign can produce the same pattern."]))
        return out

    def _data_quality(self, ctx) -> list[dict]:
        t = self.t
        q = self.a.quality(ctx["scope"], ctx["user"])
        total = q["total_rows"]
        if not total:
            return []
        weak = [c for c in q["completeness"] if c["mapped"] and c["share"] is not None and c["share"] < t.completeness_floor]
        bad = [i for i in q["issues"] if i["share"] is not None and i["share"] >= t.issue_share_floor]
        if not weak and not bad:
            return []
        lines = [f"{c['column']} is {c['share'] * 100:.1f}% complete ({c['missing']:,} missing)" for c in weak]
        lines += [f"{i['description']}: {i['count']:,} rows ({i['share'] * 100:.1f}%)" for i in bad]
        ev = [{"label": l, "metric_id": None, "value": None, "display": "", "unit": None, "n": total, "kind": "measured",
               "query_id": q["evidence"]["query_id"], "sql": q["evidence"]["sql"], "params": q["evidence"]["params"]}
              for l in lines]
        worst = max([1 - c["share"] for c in weak] + [i["share"] for i in bad])
        return [{**self._candidate(
            ctx, key="data_quality:all", detector="data_quality",
            title="Close data-capture gaps that limit the analysis",
            summary="; ".join(lines[:3]) + ("…" if len(lines) > 3 else ""),
            problem="Missing or inconsistent fields reduce the reliability of every metric that uses them.",
            metric_id=None, dimension=None, segment=None, evidence=ev,
            impact=_band(worst, (0.02, 0.05, 0.1, 0.2)), urgency=2, n=total, period_scoped=False,
            expected="Not quantified: better capture raises confidence rather than changing a service outcome.",
            assumptions=[],
            actions=["Make the incomplete fields mandatory or defaulted in the source system where appropriate.",
                     "Fix or document the records listed under data quality on the Analysis page.",
                     "Extend the status mapping to cover unmapped status values."],
            risks=["Mandatory fields filled with placeholder values can be worse than blanks."]), "drilldown": None}]
