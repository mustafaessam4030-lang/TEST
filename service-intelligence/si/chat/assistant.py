"""The AI Service Assistant: question -> approved plan -> registry query -> explained answer.

Numbers in an answer come only from query results computed through the same
`Analytics` functions as the dashboard. Text is composed from templates around
those numbers, so nothing is invented; when a query fails or returns nothing,
the answer says so.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from ..analysis import Analytics
from ..backends.base import BackendError
from ..plans import PlanError, PlanStore, visible
from ..query import Filters, Period, QueryError
from ..recommend import Recommender, _fmt as fmt
from ..registry import Registry
from . import intent as intent_mod
from .intent import Plan

ERROR_TEXT = {
    "timeout": "The query took longer than the allowed time and was cancelled. Try a shorter period or a narrower filter.",
    "warehouse_unavailable": "The Snowflake warehouse is unavailable or suspended, so no figures could be retrieved.",
    "permission_denied": "The application's Snowflake role is not authorised for the data this needs.",
    "connection": "The application could not reach Snowflake.",
    "refused": "The query was refused by the safety check.",
    "not_configured": "Snowflake is not configured for this deployment.",
    "query_failed": "The query failed.",
}
EXAMPLES = [
    "What changed in service performance this month?",
    "Which categories contribute most to the current backlog?",
    "Compare SLA performance with the previous period.",
    "What are the strongest data-supported opportunities for improvement?",
    "Which regions have the longest resolution times?",
    "What data is missing before we can answer questions reliably?",
]
CONVERSATION_TTL_S = 2 * 3600
MAX_CONVERSATIONS = 2000


@dataclass
class AskContext:
    user: str
    scope: Filters
    filters: Filters
    ui_period: Period
    fresh: dict
    synthetic: bool


class Assistant:
    def __init__(self, registry: Registry, analytics: Analytics, recommender: Recommender, plans: PlanStore | None,
                 cortex=None):
        self.registry = registry
        self.a = analytics
        self.rec = recommender
        self.plans = plans
        self.cortex = cortex
        self._conv: dict[tuple[str, str], tuple[float, Plan]] = {}
        self._lock = threading.Lock()

    # -- conversation context (plan only; no data is kept) --------------------------------------------
    def _previous(self, user: str, conv: str) -> Plan | None:
        with self._lock:
            item = self._conv.get((user, conv))
            if item and time.time() - item[0] < CONVERSATION_TTL_S:
                return item[1]
        return None

    def _remember(self, user: str, conv: str, plan: Plan) -> None:
        with self._lock:
            if len(self._conv) >= MAX_CONVERSATIONS:
                oldest = min(self._conv, key=lambda k: self._conv[k][0])
                self._conv.pop(oldest, None)
            self._conv[(user, conv)] = (time.time(), plan)

    # -- entry point -------------------------------------------------------------------------------
    def ask(self, question: str, ctx: AskContext, conversation_id: str | None = None) -> dict:
        conv = conversation_id or uuid.uuid4().hex
        as_of: datetime = ctx.fresh["as_of"]
        today = as_of.date() if as_of else date.today()
        plan = intent_mod.parse(question, self.registry, today, self._previous(ctx.user, conv))
        if plan.intent == "unsupported" and self.cortex is not None:
            plan = self.cortex.plan(question, today, ctx.user) or plan
        base = {"conversation_id": conv, "question": question, "intent": plan.intent,
                "plan": {"metric_id": plan.metric_id, "dimension": plan.dimension, "compare": plan.compare,
                         "trend": plan.trend, "source": plan.source, "period_text": plan.period_text},
                "as_of": ctx.fresh.get("as_of_iso"), "filters": {k: list(v) for k, v in ctx.filters.items()},
                "scope": {k: list(v) for k, v in ctx.scope.items()},
                "text": [], "table": None, "chart": None, "definitions": [], "sources": [], "limitations": [],
                "followups": [], "notes": list(plan.notes), "status": "answered"}
        if ctx.synthetic:
            base["notes"].insert(0, "Answer computed from SYNTHETIC development data, not company data.")
        if ctx.fresh.get("stale"):
            base["notes"].append(f"Data was last refreshed {ctx.fresh.get('as_of_iso')} UTC, which is older than "
                                 f"{ctx.fresh.get('max_age_hours')} hours.")
        handler = getattr(self, f"_{plan.intent}")
        try:
            handler(plan, ctx, base)
        except BackendError as exc:
            base.update(status="error", text=[ERROR_TEXT.get(exc.kind, "The query failed."),
                                              "No figures are shown because none could be verified."])
            if exc.query_id:
                base["sources"].append({"label": "Failed query", "query_id": exc.query_id})
        except QueryError as exc:
            base.update(status="unavailable", text=[str(exc)])
        if plan.intent == "metric" and base["status"] == "answered":
            self._remember(ctx.user, conv, plan)
        return base

    # -- helpers -------------------------------------------------------------------------------------
    def _period(self, plan: Plan, ctx: AskContext) -> Period:
        return plan.period or ctx.ui_period

    def _source(self, out: dict, label: str, ev: dict | None) -> None:
        if ev:
            out["sources"].append({"label": label, "query_id": ev.get("query_id"), "sql": ev.get("sql"),
                                   "params": ev.get("params"), "cached": ev.get("cached")})

    def _define(self, out: dict, metric_id: str) -> None:
        m = self.registry.metric(metric_id)
        d = m.public(self.a.available)
        if d not in out["definitions"]:
            out["definitions"].append(d)
            out["limitations"].extend(x for x in m.limitations if x not in out["limitations"])

    def _scope_text(self, ctx: AskContext) -> str:
        parts = [f"{self.registry.dimensions[k].label if k in self.registry.dimensions else k}: {', '.join(v)}"
                 for k, v in {**ctx.filters}.items()]
        return f" (filters — {'; '.join(parts)})" if parts else ""

    # -- intents -------------------------------------------------------------------------------------
    def _metric(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        m = self.registry.metric(plan.metric_id)
        self._define(out, m.id)
        missing = [c for c in m.requires if c not in self.a.available]
        if missing:
            out.update(status="unavailable", text=[
                f"{m.name} cannot be calculated: the mapped source data has no {', '.join(missing)}.",
                "Map these fields in the source mapping (see SERVICE_DATA_CATALOG.md) to enable it."])
            return
        period = self._period(plan, ctx) if m.kind == "period" else None
        when = f"in {period.label()}" if period else f"as of {ctx.fresh.get('as_of_iso')} UTC"
        out["period"] = period.label() if period else None
        scope_txt = self._scope_text(ctx)

        if plan.trend and m.kind == "period":
            days = (period.end - period.start).days
            grain = "day" if days <= 45 else "week" if days <= 180 else "month"
            s = self.a.series(m.id, period, grain, ctx.filters, ctx.scope, ctx.fresh["as_of"], user=ctx.user)
            self._source(out, f"{m.name} by {grain}", s["evidence"])
            if not s["points"]:
                out.update(status="no_data", text=[f"No records match {m.name.lower()} {when}{scope_txt}."])
                return
            out["chart"] = {"type": "line", "unit": m.unit, "label": m.name,
                            "points": [{"x": p["bucket"][:10], "y": p["value"], "n": p["n"], "sufficient": p["sufficient"],
                                        "partial": p["partial"]} for p in s["points"]]}
            pts = [p for p in s["points"] if p["sufficient"] and not p["partial"] and p["value"] is not None]
            if pts:
                hi = max(pts, key=lambda p: p["value"])
                lo = min(pts, key=lambda p: p["value"])
                out["text"].append(f"{m.name} by {grain} {when}{scope_txt}: highest {fmt(hi['value'], m.unit)} "
                                   f"({hi['bucket'][:10]}), lowest {fmt(lo['value'], m.unit)} ({lo['bucket'][:10]}).")
            out["text"].append(f"{len(s['points'])} {grain}s are plotted. Partial {grain}s and points with too few "
                               "records are hollow and excluded from the highest/lowest comparison.")
            out["followups"] = [f"Break {m.name.lower()} down by {d}" for d in list(self.registry.dimensions)[:2]]
            return

        if plan.dimension:
            if plan.dimension not in self.a.available:
                out.update(status="unavailable", text=[f"{self.registry.dimensions[plan.dimension].label} is not mapped "
                                                       "to source data, so this breakdown is not possible."])
                return
            b = self.a.breakdown(m.id, plan.dimension, period, ctx.filters, ctx.scope, ctx.fresh["as_of"], user=ctx.user)
            self._source(out, f"{m.name} by {b['dimension_label'].lower()}", b["evidence"])
            rows = [r for r in b["rows"] if r["value"] is not None]
            if not rows:
                out.update(status="no_data", text=[f"No records match {m.name.lower()} {when}{scope_txt}."])
                return
            ranked = sorted([r for r in rows if r["sufficient"]], key=lambda r: r["value"], reverse=plan.order == "desc")
            small = [r for r in rows if not r["sufficient"]]
            total = sum(r["value"] for r in rows) if m.unit == "count" else None
            label = b["dimension_label"]
            out["table"] = {"columns": [{"key": "segment", "label": label}, {"key": "value", "label": m.name, "unit": m.unit}]
                            + ([{"key": "share", "label": "Share of total", "unit": "ratio"}] if total else [])
                            + [{"key": "n", "label": "Records", "unit": "count"}],
                            "rows": [{"segment": r["segment"], "value": r["value"], "n": r["n"],
                                      **({"share": r["value"] / total} if total else {}),
                                      "sufficient": r["sufficient"]} for r in ranked + small]}
            out["chart"] = {"type": "bar", "unit": m.unit, "label": m.name,
                            "points": [{"x": r["segment"], "y": r["value"], "n": r["n"]} for r in ranked[:12]]}
            if ranked:
                top = ranked[0]
                word = "Highest" if plan.order == "desc" else "Lowest"
                share = f", {fmt(top['value'] / total, 'ratio')} of the total" if total else ""
                out["text"].append(f"{m.name} by {label.lower()} {when}{scope_txt}. {word}: {top['segment']} at "
                                   f"{fmt(top['value'], m.unit)}{share} ({top['n']:,} records).")
                if len(ranked) > 1:
                    nxt = ", ".join(f"{r['segment']} {fmt(r['value'], m.unit)}" for r in ranked[1:4])
                    out["text"].append(f"Next: {nxt}.")
            if small:
                out["text"].append(f"{len(small)} {label.lower()} value(s) have fewer than {m.min_sample} records and "
                                   "are listed but not ranked.")
            if any(r["segment"] == "(not set)" for r in rows):
                out["limitations"].append(f"'(not set)' groups records with no {label.lower()} recorded in the source.")
            out["text"].append("These figures show where the metric differs; they do not show why.")
            out["followups"] = [f"Show the records behind {m.name.lower()} for {ranked[0]['segment']}" if ranked else None,
                                f"Compare {m.name.lower()} with the previous period" if m.kind == "period" else None,
                                "What are the strongest data-supported opportunities for improvement?"]
            out["followups"] = [f for f in out["followups"] if f]
            out["drilldown"] = {"metric_id": m.id, "dimension": plan.dimension,
                                "segment": ranked[0]["segment"] if ranked else None}
            return

        r = self.a.metric(m.id, period, ctx.filters, ctx.scope, ctx.fresh["as_of"], compare=m.kind == "period",
                          user=ctx.user)
        self._source(out, m.name + (f" ({period.label()})" if period else ""), r.get("evidence"))
        if r.get("n", 0) == 0 or r.get("value") is None:
            out.update(status="no_data", text=[f"No records match {m.name.lower()} {when}{scope_txt}, so there is no "
                                               "figure to report."])
            return
        out["text"].append(f"{m.name} {when}{scope_txt}: {fmt(r['value'], m.unit)} (based on {r['n']:,} records).")
        if not r["sufficient"]:
            out["text"].append(r["reason"])
        prev = r.get("previous")
        if prev:
            self._source(out, f"{m.name} ({prev['label']})", prev.get("evidence"))
            if r.get("change"):
                ch = r["change"]
                better = (ch["abs"] > 0) == (m.direction == "higher_is_better") if m.direction != "neutral" and ch["abs"] else None
                verdict = "" if better is None else (" — an improvement" if better else " — a deterioration")
                pct = f" ({ch['pct'] * 100:+.1f}%)" if ch.get("pct") is not None else ""
                delta = (f"{ch['abs'] * 100:+.1f} percentage points" if m.unit == "ratio" else f"{ch['abs']:+,.1f}"
                         + ("" if m.unit == "count" else f" {m.unit}"))
                out["text"].append(f"Previous period of equal length ({prev['label']}): {fmt(prev['value'], m.unit)}; "
                                   f"change {delta}{pct}{verdict}.")
                out["table"] = {"columns": [{"key": "period", "label": "Period"},
                                            {"key": "value", "label": m.name, "unit": m.unit},
                                            {"key": "n", "label": "Records", "unit": "count"}],
                                "rows": [{"period": period.label(), "value": r["value"], "n": r["n"]},
                                         {"period": prev["label"], "value": prev["value"], "n": prev["n"]}]}
            elif plan.compare:
                out["text"].append(f"No reliable comparison: the previous period ({prev['label']}) has "
                                   f"{prev['n']} records.")
            out["notes"].append("A change between periods is a measurement, not an explanation of its cause.")
        out["followups"] = [f"Break {m.name.lower()} down by {d}" for d in self.registry.dimensions
                            if d in self.a.available][:3]
        if m.kind == "period":
            out["followups"].append(f"Show the {m.name.lower()} trend over time")
        out["drilldown"] = {"metric_id": m.id}

    def _what_changed(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        period = self._period(plan, ctx)
        out["period"] = period.label()
        rows, changes = [], []
        for m in self.registry.metrics.values():
            if any(c not in self.a.available for c in m.requires):
                continue
            r = self.a.metric(m.id, period if m.kind == "period" else None, ctx.filters, ctx.scope, ctx.fresh["as_of"],
                              compare=m.kind == "period", user=ctx.user)
            self._define(out, m.id)
            self._source(out, m.name, r.get("evidence"))
            prev = r.get("previous") or {}
            if prev:
                self._source(out, f"{m.name} (previous)", prev.get("evidence"))
            row = {"metric": m.name, "value": r.get("value"), "unit": m.unit, "n": r.get("n"),
                   "previous": prev.get("value"), "change_pct": (r.get("change") or {}).get("pct"),
                   "kind": "current state" if m.kind == "snapshot" else "period"}
            rows.append(row)
            if r.get("change") and r["change"].get("pct") is not None and m.direction != "neutral":
                better = (r["change"]["abs"] > 0) == (m.direction == "higher_is_better")
                changes.append((abs(r["change"]["pct"]), m, r, better))
            elif r.get("change") and r["change"].get("pct") is not None:
                changes.append((abs(r["change"]["pct"]), m, r, None))
        if not rows:
            out.update(status="unavailable", text=["No approved metric can be calculated from the mapped data."])
            return
        prev = period.previous()
        out["text"].append(f"Compared {period.label()} with the previous period of equal length ({prev.label()}){self._scope_text(ctx)}.")
        if period.end > (ctx.fresh["as_of"].date() if ctx.fresh.get("as_of") else period.end):
            out["text"].append("The current period is not complete, so it is compared with a previous period of the same length.")
        changes.sort(key=lambda c: -c[0])
        for _, m, r, better in changes[:4]:
            word = "" if better is None else (" (better)" if better else " (worse)")
            out["text"].append(f"• {m.name}: {fmt(r['previous']['value'], m.unit)} → {fmt(r['value'], m.unit)} "
                               f"({r['change']['pct'] * 100:+.1f}%){word}.")
        if not changes:
            out["text"].append("No metric has enough records in both periods for a reliable comparison.")
        snaps = [x for x in rows if x["kind"] == "current state" and x["value"] is not None]
        if snaps:
            out["text"].append("Current state: " + "; ".join(f"{x['metric']} {fmt(x['value'], x['unit'])}" for x in snaps[:3]) + ".")
        out["table"] = {"columns": [{"key": "metric", "label": "Metric"}, {"key": "value", "label": "Current"},
                                    {"key": "previous", "label": "Previous"}, {"key": "change_pct", "label": "Change", "unit": "ratio"},
                                    {"key": "n", "label": "Records", "unit": "count"}],
                        "rows": rows, "mixed_units": True}
        out["notes"].append("Changes are measured differences; they do not establish causes.")
        out["followups"] = ["What are the strongest data-supported opportunities for improvement?",
                            "Which regions have the longest resolution times?"]

    def _recommendations(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        period = self._period(plan, ctx)
        out["period"] = period.label()
        res = self.rec.generate(period, ctx.filters, ctx.scope, ctx.fresh, user=ctx.user)
        cands = res["candidates"]
        if not cands:
            out.update(status="no_data", text=[f"No detector found a segment that meets the evidence thresholds in "
                                               f"{period.label()}{self._scope_text(ctx)}."])
            return
        out["text"].append(f"{len(cands)} evidence-backed opportunities for {period.label()}{self._scope_text(ctx)}, "
                           f"ranked by {res['ranking']}.")
        for c in cands[:5]:
            out["text"].append(f"{c['rank']}. {c['title']} — {c['priority']['label']} priority "
                               f"(score {c['priority']['score']}). {c['summary']}")
            for e in c["evidence"][:1]:
                self._source(out, f"Evidence: {c['title']}", e)
        out["table"] = {"columns": [{"key": "rank", "label": "#"}, {"key": "title", "label": "Opportunity"},
                                    {"key": "priority", "label": "Priority"}, {"key": "score", "label": "Score"}],
                        "rows": [{"rank": c["rank"], "title": c["title"], "priority": c["priority"]["label"],
                                  "score": c["priority"]["score"]} for c in cands[:10]]}
        out["text"].append("These are proposals generated from the data; none is approved. Review and save them "
                           "on the Recommendations page. Expected impacts there are projections with stated assumptions.")
        out["notes"].append(cands[0]["causation_note"])
        out["followups"] = [f"Show the records behind: {cands[0]['title']}", "What data is missing before we can answer reliably?"]

    def _evidence(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        if self.plans is None:
            out.update(status="unavailable", text=["Plan storage is not configured, so saved recommendations cannot be read."])
            return
        try:
            rec = self.plans.get(plan.rec_id)
        except PlanError as exc:
            out.update(status="no_data", text=[str(exc) + ". Generated candidates receive an R- id only when "
                                               "they are saved to the plan."])
            return
        p = rec["payload"]
        if not visible(p, ctx.scope):
            out.update(status="restricted", text=[f"{plan.rec_id} is outside your data scope."])
            return
        out["text"].append(f"{rec['rec_id']}: {rec['title']} — status {rec['status']}"
                           + (f", owner {rec['owner']}" if rec.get("owner") else "") + ".")
        out["text"].append(p.get("summary", ""))
        snap = p.get("snapshot", {})
        out["text"].append(f"Generated from snapshot {snap.get('snapshot_id')} (data as of {snap.get('as_of')} UTC, "
                           f"period {snap.get('period')}, registry {snap.get('registry_version')}).")
        out["table"] = {"columns": [{"key": "label", "label": "Evidence"}, {"key": "display", "label": "Value"},
                                    {"key": "n", "label": "Records", "unit": "count"}, {"key": "query_id", "label": "Query id"}],
                        "rows": [{k: e.get(k) for k in ("label", "display", "n", "query_id")} for e in p.get("evidence", [])]}
        for e in p.get("evidence", []):
            self._source(out, e.get("label"), e)
        exp = p.get("expected_impact") or {}
        if exp:
            out["text"].append(f"Expected impact (projection): {exp.get('statement')} Assumptions: "
                               + ("; ".join(exp.get("assumptions") or ["none stated"])) + ".")
        out["notes"].append("Evidence is the snapshot saved with the recommendation; current figures may differ.")
        out["followups"] = ["What are the strongest data-supported opportunities for improvement?"]

    def _missing_data(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        q = ctx.fresh["quality"]
        self._source(out, "Data quality profile", q["evidence"])
        unavailable = [m for m in self.registry.metrics.values() if any(c not in self.a.available for c in m.requires)]
        unmapped = [c["column"] for c in q["completeness"] if not c["mapped"]]
        if unmapped:
            out["text"].append("Not mapped to any source field: " + ", ".join(unmapped) + ".")
        if unavailable:
            out["text"].append("So these metrics cannot be answered: " + ", ".join(m.name for m in unavailable) + ".")
        else:
            out["text"].append("Every approved metric has the source fields it needs.")
        weak = [c for c in q["completeness"] if c["mapped"] and c["share"] is not None and c["share"] < 0.99]
        if weak:
            out["text"].append("Incomplete fields: " + "; ".join(f"{c['column']} {c['share'] * 100:.1f}% filled"
                                                                for c in weak) + ".")
        issues = [i for i in q["issues"] if i["count"]]
        for i in issues:
            out["text"].append(f"{i['description']}: {i['count']:,} rows.")
        out["text"].append(f"Data covers {q.get('first_created', '?')[:10] if q.get('first_created') else '?'} to "
                           f"{ctx.fresh.get('as_of_iso', '?')} UTC ({q['total_rows']:,} rows); "
                           + ("it is stale." if ctx.fresh.get("stale") else "it is within the freshness limit."))
        out["text"].append("Not available in any mapped source: business-hours calendars, status history (needed for "
                           "a backlog trend), effort or cost data, and root-cause codes.")
        out["table"] = {"columns": [{"key": "column", "label": "Contract field"}, {"key": "mapped", "label": "Mapped"},
                                    {"key": "share", "label": "Filled", "unit": "ratio"}],
                        "rows": q["completeness"]}
        out["followups"] = ["How is SLA compliance defined?", "What changed in service performance this month?"]

    def _definition(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        m = self.registry.metric(plan.metric_id)
        self._define(out, m.id)
        d = m.public(self.a.available)
        out["text"].append(f"{m.name}: {m.description}")
        out["text"].append(f"Calculation: {m.expression}" + (f" over records where {m.filter}" if m.filter else "")
                           + (f", counted by {m.time_column} in the reporting period." if m.kind == "period"
                              else ", as of the last data refresh."))
        if m.min_sample:
            out["text"].append(f"Figures based on fewer than {m.min_sample} records are flagged as unreliable.")
        if not d["available"]:
            out["text"].append("Currently unavailable: the mapped source has no " + ", ".join(d["missing_columns"]) + ".")
        out["text"].append(f"Registry version {self.registry.version}. The dashboard and recommendations use this same definition.")
        out["followups"] = [f"What is the {m.name.lower()} this month?"]

    def _help(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        out["text"] = ["I answer questions about service operations using the approved metric definitions and the "
                       "data your role can see. I show the period, the definition and the query behind every number.",
                       "Try one of these:"] + [f"• {e}" for e in EXAMPLES]
        out["followups"] = EXAMPLES[:3]

    def _unsupported(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        names = ", ".join(m.name.lower() for m in self.registry.metrics.values())
        out.update(status="unsupported", text=[
            "I can't answer that reliably from the approved service metrics, so I won't guess.",
            f"I can report on: {names} — by {', '.join(d.label.lower() for d in self.registry.dimensions.values())}, "
            "for a period you name."])
        out["followups"] = EXAMPLES[:3]

    def _restricted(self, plan: Plan, ctx: AskContext, out: dict) -> None:
        out.update(status="restricted", text=[plan.reason or "That request is not allowed."])
        out["followups"] = EXAMPLES[:2]
