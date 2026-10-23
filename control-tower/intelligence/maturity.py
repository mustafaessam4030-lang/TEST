"""
ATLAS maturity — EVALUATE. The stars are earned, not counted.

A month passing unlocks an evaluation; it does not grant a star. The
evaluation computes measured metrics from the event store and learning, and
ATLAS rises ONE level only if every criterion of the next level is met. The
result — metrics, each criterion with its value and threshold, the level
before and after — is recorded in maturity.json and never rewritten.

    ★      Observer     it watches real runs
    ★★     Analyst      it recognises recurring issues, with verified signals
    ★★★    Operator     its recovery knowledge is verified and its answers hold
    ★★★★   Strategist   high-confidence strategies, confirmed classification,
                        and a proposal a person approved
    ★★★★★  Expert       verified recovery at scale, human actions completed,
                        no regression

A rate needs a minimum sample before it can meet anything: "not enough data"
is a failed criterion, never a pass.
"""

import time

from . import events, learning, store

FILE = "maturity.json"
TITLES = {0: "Not yet rated", 1: "Observer", 2: "Analyst", 3: "Operator",
          4: "Strategist", 5: "Expert"}

# (metric, minimum, label, sample metric, minimum sample)
CRITERIA = {
    1: [("runs_observed", 1, "real runs observed", None, 0),
        ("shipments_observed", 20, "shipment outcomes observed", None, 0)],
    2: [("issues_learned", 3, "recurring issues learned (3+ times, 2+ runs)", None, 0),
        ("verified_signals", 10, "verified learning signals", None, 0),
        ("answered_rate", 0.80, "questions answered from run data",
         "questions", 20)],
    3: [("verified_signals", 20, "verified learning signals", None, 0),
        ("verified_successes", 10, "verified recovery successes", None, 0),
        ("verified_recovery_rate", 0.60, "verified recovery success rate",
         "verified_signals", 20),
        ("strategies_medium_plus", 1, "issues with a medium/high-confidence best strategy",
         None, 0),
        ("answered_rate", 0.90, "questions answered from run data", "questions", 20),
        ("helpful_rate", 0.70, "answers rated helpful", "feedback_n", 10)],
    4: [("verified_signals", 50, "verified learning signals", None, 0),
        ("verified_recovery_rate", 0.70, "verified recovery success rate",
         "verified_signals", 50),
        ("strategies_high", 3, "issues with a high-confidence best strategy", None, 0),
        ("classification_accuracy", 0.85, "operator-confirmed explanation accuracy",
         "classification_n", 20),
        ("approved_proposals", 1, "improvement proposals a person approved", None, 0)],
    5: [("verified_signals", 100, "verified learning signals", None, 0),
        ("verified_recovery_rate", 0.80, "verified recovery success rate",
         "verified_signals", 100),
        ("human_completion_rate", 0.90, "human actions completed and verified",
         "human_tasks", 10),
        ("classification_accuracy", 0.90, "operator-confirmed explanation accuracy",
         "classification_n", 30),
        ("helpful_rate", 0.85, "answers rated helpful", "feedback_n", 30),
        ("recovery_rate_change", 0.0, "no regression in recovery rate month on month",
         "previous_signals", 10)],
}


def stars(level):
    return "★" * level + "☆" * (5 - level)


def _month_bounds(month):
    year, mon = int(month[:4]), int(month[5:7])
    start = time.mktime((year, mon, 1, 0, 0, 0, 0, 0, -1))
    nxt = (year + (mon == 12), 1 if mon == 12 else mon + 1)
    end = time.mktime((nxt[0], nxt[1], 1, 0, 0, 0, 0, 0, -1))
    return start, end


def _prev(month):
    year, mon = int(month[:4]), int(month[5:7])
    return "{0:04d}-{1:02d}".format(year - (mon == 1), 12 if mon == 1 else mon - 1)


def current_month():
    return time.strftime("%Y-%m")


def metrics(month, rows=None):
    """Measured metrics, cumulative to the end of `month`, from the events."""
    rows = events.all_events() if rows is None else rows
    start, end = _month_bounds(month)
    upto = [r for r in rows if float(r.get("epoch") or 0) < end]
    snap = learning.build(upto)
    summary = snap["summary"]
    fb = snap["feedback"]
    q_total = sum(q["count"] for q in snap["questions"])
    q_answered = sum(q["answered"] for q in snap["questions"])
    helpful_n = fb.get("helpful", 0) + fb.get("not_helpful", 0)
    cls_n = fb.get("correct", 0) + fb.get("incorrect", 0)
    tasks = sum(h["tasks"] for h in snap["human"])
    completed = sum(h["completed_verified"] for h in snap["human"])
    this = learning.month_stats(snap, month) or {}
    prev = learning.month_stats(snap, _prev(month)) or {}

    def rate(cell):
        n = cell.get("recovery_successes", 0) + cell.get("recovery_failures", 0)
        return (cell.get("recovery_successes", 0) / n, n) if n else (None, 0)
    this_rate, _n = rate(this)
    prev_rate, prev_n = rate(prev)
    best = [i["strategies"][i["best"]] for i in snap["issues"] if i.get("best")]
    approvals = store.load_json(learning.PROPOSALS_FILE, {}).get("decisions", {})
    return {
        "month": month,
        "runs_observed": summary["runs"],
        "shipments_observed": sum(1 for r in upto if r.get("kind") == "shipment"),
        "issues_learned": summary["issues_learned"],
        "verified_signals": summary["verified_signals"],
        "verified_successes": summary["verified_successes"],
        "verified_recovery_rate": (summary["verified_successes"] / summary["verified_signals"]
                                   if summary["verified_signals"] else None),
        "unverified": summary["unverified"],
        "strategies_medium_plus": sum(1 for s in best if s["confidence"] in ("Medium", "High")),
        "strategies_high": sum(1 for s in best if s["confidence"] == "High"),
        "questions": q_total,
        "answered_rate": (q_answered / q_total) if q_total else None,
        "feedback_n": helpful_n,
        "helpful_rate": (fb.get("helpful", 0) / helpful_n) if helpful_n else None,
        "classification_n": cls_n,
        "classification_accuracy": (fb.get("correct", 0) / cls_n) if cls_n else None,
        "human_tasks": tasks,
        "human_completion_rate": (completed / tasks) if tasks else None,
        "approved_proposals": sum(1 for d in approvals.values()
                                  if d.get("status") == "APPROVED"
                                  and str(d.get("at") or "") < store.stamp(end)),
        "month_recovery_rate": this_rate,
        "previous_signals": prev_n,
        "recovery_rate_change": (this_rate - prev_rate) if this_rate is not None
        and prev_rate is not None else None,
    }


def check(level, m):
    """Each criterion of `level` with its value, threshold and verdict."""
    out = []
    for metric, minimum, label, sample, min_sample in CRITERIA.get(level, []):
        value = m.get(metric)
        n = m.get(sample) if sample else None
        if value is None or (sample and (n or 0) < min_sample):
            met, why = False, ("not enough data ({0} of {1} needed)".format(n or 0, min_sample)
                               if sample else "no data")
        else:
            met, why = value >= minimum, ""
        out.append({"level": level, "metric": metric, "label": label, "value": value,
                    "minimum": minimum, "sample": n, "min_sample": min_sample,
                    "met": met, "why": why})
    return out


def state():
    return store.load_json(FILE, {"level": 0, "history": []})


def level():
    return int(state().get("level") or 0)


def evaluate(month, record=True, rows=None):
    """
    Evaluate one month. Recorded only for a month that has ended, once.
    Returns the evaluation (recorded or preview).
    """
    data = state()
    done = {h["month"]: h for h in data.get("history", [])}
    if record and month in done:
        return done[month]
    ended = month < current_month()
    before = int(data.get("level") or 0)
    m = metrics(month, rows)
    nxt = before + 1
    criteria = check(nxt, m) if nxt <= 5 else []
    promoted = bool(criteria) and all(c["met"] for c in criteria)
    after = nxt if promoted else before
    holding = check(before, m) if before else []
    entry = {"month": month, "evaluated_at": store.stamp(), "level_before": before,
             "level_after": after, "promoted": promoted, "metrics": m,
             "criteria": criteria,
             "holds_current_level": all(c["met"] for c in holding) if holding else None,
             "recorded": bool(record and ended)}
    if record and ended:
        data.setdefault("history", []).append(entry)
        data["level"] = after
        data["last_evaluated"] = month
        store.save_json(FILE, data)
    return entry


def ensure_evaluated(rows=None):
    """Evaluate, in order, every ended month with events and no evaluation."""
    rows = events.all_events() if rows is None else rows
    months = sorted({store.month_of(float(r.get("epoch") or 0)) for r in rows
                     if r.get("epoch")})
    done = {h["month"] for h in state().get("history", [])}
    out = []
    for month in months:
        if month < current_month() and month not in done:
            out.append(evaluate(month, record=True, rows=rows))
    return out


def status(rows=None):
    """The level, what it rests on, and the next level's progress (a preview)."""
    ensure_evaluated(rows)
    data = state()
    lvl = int(data.get("level") or 0)
    preview = evaluate(current_month(), record=False, rows=rows)
    history = data.get("history", [])
    return {"level": lvl, "title": TITLES[lvl], "stars": stars(lvl),
            "last_evaluated": data.get("last_evaluated"),
            "next_level": lvl + 1 if lvl < 5 else None,
            "next_title": TITLES.get(lvl + 1),
            "next_criteria": preview["criteria"],
            "preview_month": preview["month"],
            "history": [{"month": h["month"], "level_before": h["level_before"],
                         "level_after": h["level_after"], "promoted": h["promoted"]}
                        for h in history][-12:]}


def review(month=None, rows=None):
    """The monthly learning review: what was learned, the weakness, the goal."""
    rows = events.all_events() if rows is None else rows
    month = month or current_month()
    start, end = _month_bounds(month)
    snap_now = learning.build([r for r in rows if float(r.get("epoch") or 0) < end])
    snap_before = learning.build([r for r in rows if float(r.get("epoch") or 0) < start])
    this = learning.month_stats(snap_now, month) or {}
    prev = learning.month_stats(snap_now, _prev(month)) or {}

    def verified_names(snap):
        return {(i["key"], s["name"]) for i in snap["issues"]
                for s in i["strategies"].values() if s["successes"] > 0}
    new_patterns = verified_names(snap_now) - verified_names(snap_before)
    recurring_now = {q["intent"] for q in snap_now["questions"] if q["count"] >= learning.RECURRING}
    recurring_before = {q["intent"] for q in snap_before["questions"]
                        if q["count"] >= learning.RECURRING}

    def rate(cell):
        n = cell.get("recovery_successes", 0) + cell.get("recovery_failures", 0)
        return cell.get("recovery_successes", 0) / n if n else None
    r_this, r_prev = rate(this), rate(prev)
    top = None
    best_count = 0
    for issue in snap_now["issues"]:
        count = sum((s["months"].get(month) or {}).get("successes", 0)
                    for s in issue["strategies"].values())
        if count > best_count:
            best_count, top = count, issue
    weakness = None
    worst = 0
    for issue in snap_now["issues"]:
        occ = issue["months"].get(month, 0)
        unresolved = occ - min(occ, issue["resolved_verified"])
        if unresolved > worst:
            worst, weakness = unresolved, issue
    human_weak = max(snap_now["human"], key=lambda h: h["timed_out"] + h["sessions_lost"],
                     default=None)
    lvl_entry = evaluate(month, record=False, rows=rows)
    data = state()
    recorded = next((h for h in data.get("history", []) if h["month"] == month), None)
    level_now = (recorded or {}).get("level_after", int(data.get("level") or 0))
    nxt = check(level_now + 1, lvl_entry["metrics"]) if level_now < 5 else []
    unmet = [c for c in nxt if not c["met"]]
    return {
        "month": month, "level": level_now, "stars": stars(level_now),
        "title": TITLES[level_now], "evaluated": bool(recorded),
        "progress": {
            "new_verified_patterns": len(new_patterns),
            "recurring_questions_learned": len(recurring_now - recurring_before),
            "new_carrier_behaviours": len(this.get("new_issues") or []),
            "verified_recovery_successes": this.get("recovery_successes", 0),
            "recovery_rate": r_this, "recovery_rate_previous": r_prev,
            "shipments": this.get("shipments", 0), "verified": this.get("verified", 0),
            "runs": len(this.get("runs") or []),
        },
        "top_learned": ({"issue": top["issue"], "carrier": top["carrier"],
                         "verified_successes": best_count, "best": top.get("best")}
                        if top else None),
        "weakness": ({"issue": weakness["issue"], "carrier": weakness["carrier"],
                      "unresolved": worst} if weakness else None),
        "human_weakness": ({"carrier": human_weak["carrier"],
                            "timed_out": human_weak["timed_out"],
                            "sessions_lost": human_weak["sessions_lost"],
                            "tasks": human_weak["tasks"]}
                           if human_weak and (human_weak["timed_out"] or
                                              human_weak["sessions_lost"]) else None),
        "next_goal": ("{0} — {1} now, {2} needed{3}".format(
            unmet[0]["label"], _fmt(unmet[0]["value"]), _fmt(unmet[0]["minimum"]),
            " ({0})".format(unmet[0]["why"]) if unmet[0]["why"] else "") if unmet else None),
        "next_level": level_now + 1 if level_now < 5 else None,
        "requirements": nxt,
    }


def _fmt(value):
    if value is None:
        return "no data"
    if isinstance(value, float) and value <= 1.0:
        return "{0:.0%}".format(value)
    return str(round(value, 2)) if isinstance(value, float) else str(value)


def render_review(r):
    p = r["progress"]
    month_name = time.strftime("%B %Y", time.strptime(r["month"], "%Y-%m"))
    lines = ["ATLAS MONTHLY REVIEW — {0}".format(month_name), "",
             "Current level: {0} {1}{2}".format(r["stars"], r["title"],
                                                "" if r["evaluated"] else
                                                " (this month is not evaluated until it ends)"),
             "", "Progress this month:",
             "• {0} new verified recovery pattern(s)".format(p["new_verified_patterns"]),
             "• {0} recurring question(s) learned".format(p["recurring_questions_learned"]),
             "• {0} new carrier behaviour(s) identified".format(p["new_carrier_behaviours"]),
             "• {0} verified recovery success(es)".format(p["verified_recovery_successes"])]
    if p["recovery_rate"] is not None and p["recovery_rate_previous"] is not None:
        lines.append("• verified recovery rate {0} (last month {1})".format(
            _fmt(p["recovery_rate"]), _fmt(p["recovery_rate_previous"])))
    elif p["recovery_rate"] is not None:
        lines.append("• verified recovery rate {0} (no previous month to compare)".format(
            _fmt(p["recovery_rate"])))
    lines.append("• {0} shipments observed, {1} verified".format(p["shipments"], p["verified"]))
    if r["top_learned"]:
        t = r["top_learned"]
        lines += ["", "Top learned behaviour: {0} {1} — {2} verified recovery success(es)"
                  "{3}".format(t["carrier"] or "", t["issue"], t["verified_successes"],
                               ", best strategy " + t["best"] if t["best"] else "")]
    if r["weakness"]:
        w = r["weakness"]
        lines.append("Biggest weakness: {0} {1} — {2} occurrence(s) not resolved to a verified "
                     "outcome".format(w["carrier"] or "", w["issue"], w["unresolved"]))
    if r["human_weakness"]:
        h = r["human_weakness"]
        lines.append("Human actions: {0} — {1} timed out, {2} session(s) lost of {3}".format(
            h["carrier"], h["timed_out"], h["sessions_lost"], h["tasks"]))
    if r["next_level"]:
        lines += ["", "Next level: {0} {1}".format(stars(r["next_level"]), TITLES[r["next_level"]]),
                  "Requirements:"]
        for c in r["requirements"]:
            lines.append("{0} {1}: {2} (needs {3}){4}".format(
                "✓" if c["met"] else "✗", c["label"], _fmt(c["value"]), _fmt(c["minimum"]),
                " — " + c["why"] if c["why"] else ""))
        if r["next_goal"]:
            lines += ["", "Next month goal: {0}.".format(r["next_goal"])]
    return "\n".join(lines)
