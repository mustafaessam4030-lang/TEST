"""Recommendation detectors. Each candidate is built ONLY from registry metrics (via Analytics), so its
headline number is exactly what the Analysis page and the assistant show for the same segment.

Ranking is transparent: score = 50 * impact + 30 * urgency + 20 * confidence, each in [0, 1].
- impact     = affected assets / the largest candidate's affected assets
- urgency    = share of the affected assets with the fault in the last 7 days
- confidence = 1.0 for >= 30 affected assets, 0.7 for >= 10 (smaller findings are not raised)
Projections are labelled as hypotheses; nothing here is executed operationally.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from .analysis import Analytics
from .metrics import Period

DETECTOR_VERSION = "repeat_high_faults/1"
MIN_ASSETS = 10
WINDOW_DAYS = 30
RECENT_DAYS = 7
WEIGHTS = {"impact": 50, "urgency": 30, "confidence": 20}


def _dt(v):
    if v is None:
        return None
    return datetime.fromisoformat(str(v).replace("T", " ")[:19])


def priority_of(score: float) -> str:
    return "High" if score >= 70 else "Medium" if score >= 45 else "Low"


def repeat_high_faults(an: Analytics, top: int = 8) -> list[dict]:
    """Assets where the same HIGH-severity fault recurred on >= 3 days in the last 30 days, per fault."""
    metric_id = "repeat_high_fault_assets"
    as_of = an.as_of("fault_events")
    period = Period(as_of.date() - timedelta(days=WINDOW_DAYS - 1), as_of.date() + timedelta(days=1))
    br = an.breakdown(metric_id, "fault_description", period, {}, limit=top)
    found = [r for r in br["rows"] if r["value"] is not None and float(r["value"]) >= MIN_ASSETS]
    if not found:
        return []
    biggest = max(float(r["value"]) for r in found)
    out = []
    for r in found:
        fault = r["segment"]
        segment = {"fault_description": [fault]}
        headline = an.metric(metric_id, period, segment, compare=False)          # same SQL as the dashboard
        drill = an.drilldown(metric_id, period, segment, limit=500)
        assets = int(headline["value"] or 0)
        recent_cut = as_of - timedelta(days=RECENT_DAYS)
        recent = sum(1 for d in drill["rows"] if _dt(d.get("last_seen")) and _dt(d["last_seen"]) >= recent_cut)
        events = sum(int(d.get("events") or 0) for d in drill["rows"])
        families = Counter(d.get("product_family") for d in drill["rows"] if d.get("product_family"))
        family, fam_n = families.most_common(1)[0] if families else ("(not set)", 0)
        impact = assets / biggest
        urgency = recent / len(drill["rows"]) if drill["rows"] else 0.0
        confidence = 1.0 if assets >= 30 else 0.7
        score = round(WEIGHTS["impact"] * impact + WEIGHTS["urgency"] * urgency + WEIGHTS["confidence"] * confidence, 1)
        out.append({
            "candidate_key": f"{DETECTOR_VERSION}|{fault}",
            "detector": DETECTOR_VERSION,
            "title": f"Investigate repeat “{fault}” on {assets} assets",
            "explanation": (f"{assets} assets reported the HIGH-severity fault “{fault}” on 3 or more different days "
                            f"in the last {WINDOW_DAYS} days ({period.label}); {recent} of them in the last {RECENT_DAYS} days. "
                            f"Most are {family} ({fam_n} of {len(drill['rows'])})."),
            "problem": "A HIGH-severity fault that keeps returning on the same machines points to an unresolved cause "
                       "(component, configuration, operation or sensor).",
            "metric_id": metric_id,
            "segment": {"filters": segment, "period_start": period.start.isoformat(), "period_end": period.end.isoformat()},
            "measured": {"assets": assets, "high_events_on_these_assets": events, "assets_seen_last_7_days": recent,
                         "main_product_family": family, "drill_rows": len(drill["rows"]),
                         "drill_truncated": drill["truncated"]},
            "evidence": headline["evidence"] + drill["evidence"] + br["evidence"],
            "score": score,
            "score_breakdown": {"impact": round(impact, 3), "urgency": round(urgency, 3), "confidence": confidence,
                                "weights": WEIGHTS},
            "priority": priority_of(score),
            "confidence": "High" if confidence == 1.0 else "Medium",
            "expected_impact": (f"Hypothesis, not measured: if the cause is common to these assets, fixing it would remove "
                                f"up to {events} HIGH-severity fault events per {WINDOW_DAYS} days on them. "
                                "Assumes the repeats share a cause; validate on a sample of assets first."),
            "actions": ["Pull the asset list (drill-down) and group it by customer site and model.",
                        f"Have product support review fault “{fault}” history on 5–10 of the assets.",
                        "Decide whether the cause is equipment, configuration or alarm set-up; open service jobs where needed.",
                        f"Re-check this metric after {WINDOW_DAYS} days."],
            "risks": "Repeats can come from alarm configuration or a faulty sensor rather than a failing component; "
                     "telemetry covers only connected assets that report fault history.",
            "owner": None,   # no owner/team data in the source tables: not assigned automatically
            "data_as_of": as_of.isoformat(),
        })
    return sorted(out, key=lambda c: (-c["score"], c["title"]))


DETECTORS = {"repeat_high_faults": repeat_high_faults}


def all_candidates(an: Analytics) -> list[dict]:
    cands = []
    for fn in DETECTORS.values():
        cands.extend(fn(an))
    return sorted(cands, key=lambda c: (-c["score"], c["title"]))


TOP_OPP_VERSION = "top_machine/1"
SIGNAL_CLASSES = ("OLGA + telemetry", "OLGA only", "Telemetry only")


def top_opportunity_candidate(row: dict, list_evidence: list[dict], top_value: float | None = None) -> dict:
    """A plan item for one machine from the daily ranking. Values are OLGA's; timing and condition come from telemetry.
    Nothing is projected beyond OLGA's own value."""
    rank = row.get("overall_rank") or row.get("class_rank")
    score = float(row["score"])
    priority = "High" if score >= 80 else "Medium" if score >= 50 else "Low"
    due_value = float(row.get("due_value") or 0)
    what = row.get("top_job") or row.get("repeat_fault") or "inspection"
    signals = row.get("signal_class")
    return {
        "candidate_key": f"{TOP_OPP_VERSION}|{row['asset_serial']}",
        "detector": TOP_OPP_VERSION,
        "title": f"#{rank} {row.get('model')} {row['asset_serial']} ({row.get('branch')}): {what}",
        "explanation": f"{row.get('reason')}. Signal: {signals}; score {score:.1f} = value {row.get('value_points')} + "
                       f"urgency {row.get('urgency_points')} + condition {row.get('condition_points')} "
                       f"(ranking of {row.get('run_date')}).",
        "problem": ("Work is due on this machine and/or it keeps reporting a HIGH-severity fault; acting first on the "
                    "highest-ranked machines protects parts and service revenue and machine availability."),
        "metric_id": "top_opportunities_value",
        "segment": {"filters": {}, "asset_serial": row["asset_serial"], "run_date": str(row.get("run_date")),
                    "signal_class": signals},
        "measured": {k: row.get(k) for k in ("overall_rank", "class_rank", "signal_class", "score", "value_points",
                                               "urgency_points", "condition_points", "due_value", "due_opportunities",
                                               "top_job", "earliest_due", "days_to_due", "due_basis", "smu_now",
                                               "hours_per_day", "max_fault_days", "repeat_fault", "high_fault_events",
                                               "customer_number", "branch")},
        "evidence": list_evidence,
        "score": round(score, 1),
        "score_breakdown": {"value_points": row.get("value_points"), "urgency_points": row.get("urgency_points"),
                            "condition_points": row.get("condition_points"),
                            "rule": "35 x value percentile + 30 x urgency + 35 x fault condition"},
        "priority": priority,
        "confidence": "Telemetry only (no OLGA value)" if signals == "Telemetry only" else
                      ("OLGA value, telemetry-timed" if row.get("due_basis") == "telemetry hours" else "OLGA estimate"),
        "expected_impact": (f"Up to {due_value:,.0f} of parts and labour if the due work is won (OLGA estimate, not a win "
                            "probability)." if due_value else
                            "No OLGA value exists for this machine; impact is avoided downtime and repair work, not quantified "
                            "by the data."),
        "actions": ["Call the customer to confirm machine hours, condition and site access.",
                    f"Quote the due work ({row.get('top_job') or 'inspection and repair'}) and check parts or reman availability.",
                    ("Investigate the repeating fault “" + str(row.get("repeat_fault")) + "” with product support.")
                    if row.get("repeat_fault") else "Offer a condition-monitoring connection if the machine is not connected.",
                    "Record the outcome (won / lost / deferred) in the plan."],
        "risks": "OLGA intervals are model-based and telemetry hours can lag; the work may already be done elsewhere.",
        "owner": row.get("branch"),
        "data_as_of": f"{row.get('run_date')}T00:00:00",
    }
