"""
The ATLAS learning store — LEARN.

Computed from the event store, never typed in. Every number here can be
traced to the events behind it, and every learning item carries its
provenance: the runs it came from, the shipments it touched, when it was
first and last seen.

THE LABEL RULE — the reason this module exists:

    success     the strategy ran, its own check passed (the right shipment
                page, the recovered field), AND the shipment then ended as a
                VERIFIED SUCCESS: extracted, validated, written to the Hub and
                read back. Nothing less.
    failure     the strategy ran and did not work.
    unverified  the strategy appeared to work but the shipment did not end
                verified — read-only carrier, extraction failed later, a write
                that was not read back. Shown, labelled, NEVER counted as a
                success, never ranked on.
    skipped     never ran (budget spent). Not an attempt.

Operator feedback is counted beside outcomes, as opinion. It never turns an
outcome into a verified success.

Ranking uses the Wilson lower bound on successes / (successes + failures),
so three lucky tries never outrank thirty steady ones.
"""

import hashlib
import math
import threading
import time

from . import events, store

FAILURE_RESULTS = ("FAILED", "PARTIAL", "HUMAN_TIMEOUT")
MIN_RANKED = 3              # attempts with a verdict before a strategy is ranked
RECURRING = 3               # occurrences before an issue counts as learned
PROPOSALS_FILE = "proposals.json"

_cache = {"sig": None, "snap": None}
_lock = threading.Lock()


def wilson(successes, n, z=1.96):
    """Lower bound of the 95% Wilson interval. 0 when n is 0."""
    if n <= 0:
        return 0.0
    p = successes / float(n)
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / denom)


def confidence(n):
    """How much a rate rests on: decided attempts, not wishes."""
    if n < 5:
        return "Insufficient data"
    if n < 12:
        return "Low"
    if n < 25:
        return "Medium"
    return "High"


def _issue_key(provider, name):
    return "{0}:{1}".format(str(provider or "UNKNOWN").upper(), str(name or "UNKNOWN").upper())


def _new_issue(provider, carrier, name, kind):
    return {"key": _issue_key(provider, name), "provider": provider, "carrier": carrier,
            "issue": name, "kind": kind, "occurrences": 0, "runs": set(),
            "first_seen": None, "last_seen": None, "affected": [], "months": {},
            "resolved_verified": 0, "strategies": {}, "first_tried": {}}


def _strategy(issue, name):
    return issue["strategies"].setdefault(name, {
        "name": name, "attempts": 0, "successes": 0, "failures": 0, "unverified": 0,
        "skipped": 0, "evidence_runs": [], "last": None, "months": {}})


def _seen(issue, row):
    issue["occurrences"] += 1
    if row.get("run_id"):
        issue["runs"].add(row["run_id"])
    at = row.get("at")
    issue["first_seen"] = issue["first_seen"] or at
    issue["last_seen"] = at
    ref = row.get("reference")
    if ref and ref not in issue["affected"]:
        issue["affected"].append(ref)
        del issue["affected"][:-10]
    month = store.month_of(row.get("epoch") or time.time())
    issue["months"][month] = issue["months"].get(month, 0) + 1


def _label(strat, verdict, row):
    month = store.month_of(row.get("epoch") or time.time())
    cell = strat["months"].setdefault(month, {"successes": 0, "failures": 0, "unverified": 0})
    if verdict == "skipped":
        strat["skipped"] += 1
        return
    strat["attempts"] += 1
    strat["last"] = row.get("at")
    if verdict == "success":
        strat["successes"] += 1
        cell["successes"] += 1
        if row.get("run_id") and row["run_id"] not in strat["evidence_runs"]:
            strat["evidence_runs"].append(row["run_id"])
            del strat["evidence_runs"][:-12]
    elif verdict == "failure":
        strat["failures"] += 1
        cell["failures"] += 1
    else:
        strat["unverified"] += 1
        cell["unverified"] += 1


def build(rows=None):
    """The whole learning snapshot from events (all of them, by default)."""
    rows = events.all_events() if rows is None else rows
    issues, human, questions, feedback = {}, {}, {}, {}
    shipments_by_run = {}
    months = {}

    def month_cell(row):
        m = store.month_of(row.get("epoch") or time.time())
        return months.setdefault(m, {
            "month": m, "runs": set(), "shipments": 0, "verified": 0, "failed": 0,
            "signals": 0, "recovery_successes": 0, "recovery_failures": 0,
            "human_tasks": 0, "human_completed": 0, "questions": 0, "answered": 0,
            "new_issues": set(), "feedback": {}})

    po_rows = []
    for row in rows:
        kind = row.get("kind")
        if kind == "po":
            # PO Automation outcomes are their own domain: they never count as
            # shipments, runs or signals in the monthly evaluation.
            po_rows.append(row)
            continue
        cell = month_cell(row)
        if row.get("run_id"):
            cell["runs"].add(row["run_id"])
        if kind == "shipment":
            cell["shipments"] += 1
            verified = bool(row.get("verified"))
            shipments_by_run[(row.get("run_id"), row.get("reference"))] = row
            if verified:
                cell["verified"] += 1
            result = str(row.get("result") or "").upper()
            provider, carrier = row.get("provider"), row.get("carrier")
            if result in FAILURE_RESULTS:
                cell["failed"] += 1
                name = row.get("outcome_class") or result
                issue = issues.setdefault(_issue_key(provider, name),
                                          _new_issue(provider, carrier, name, "outcome"))
                if issue["occurrences"] == 0:
                    cell["new_issues"].add(issue["key"])
                _seen(issue, row)
            strategies = row.get("strategies") or []
            if strategies and not strategies[0].get("page_verified"):
                # The first way in failed: a navigation issue, recovered or not.
                name = row.get("strategy_issue") or "navigation"
                issue = issues.setdefault(_issue_key(provider, name),
                                          _new_issue(provider, carrier, name, "navigation"))
                if issue["occurrences"] == 0:
                    cell["new_issues"].add(issue["key"])
                _seen(issue, row)
                if verified:
                    issue["resolved_verified"] += 1
                first = next((s for s in strategies[1:] if not s.get("skipped")), None)
                if first:
                    issue["first_tried"][first["strategy"]] = \
                        issue["first_tried"].get(first["strategy"], 0) + 1
                for s in strategies[1:]:
                    strat = _strategy(issue, s.get("strategy") or "strategy")
                    if s.get("skipped"):
                        verdict = "skipped"
                    elif not s.get("page_verified"):
                        verdict = "failure"
                    elif verified:
                        verdict = "success"
                    else:
                        verdict = "unverified"
                    _label(strat, verdict, row)
                    if verdict in ("success", "failure"):
                        cell["signals"] += 1
                        cell["recovery_successes" if verdict == "success"
                             else "recovery_failures"] += 1
        elif kind == "recovery":
            provider = row.get("provider")
            name = row.get("error_class") or "ERROR"
            issue = issues.setdefault(_issue_key(provider, name),
                                      _new_issue(provider, row.get("carrier"), name, "recovery"))
            if issue["occurrences"] == 0:
                cell["new_issues"].add(issue["key"])
            _seen(issue, row)
            shipment_verified = bool(row.get("shipment_verified"))
            if shipment_verified:
                issue["resolved_verified"] += 1
            attempts = row.get("attempts") or []
            if attempts:
                first = attempts[0].get("action")
                issue["first_tried"][first] = issue["first_tried"].get(first, 0) + 1
            for a in attempts:
                strat = _strategy(issue, a.get("action") or "action")
                result = str(a.get("result") or "").upper()
                if result == "FAILED" or a.get("verified") is False:
                    verdict = "failure"
                elif result in ("SUCCESS", "DONE") and a.get("verified") is True \
                        and shipment_verified:
                    verdict = "success"
                elif result in ("SUCCESS", "DONE"):
                    verdict = "unverified"
                else:
                    continue                    # still running: no verdict
                _label(strat, verdict, row)
                if verdict in ("success", "failure"):
                    cell["signals"] += 1
                    cell["recovery_successes" if verdict == "success"
                         else "recovery_failures"] += 1
        elif kind == "human_task":
            name = row.get("carrier") or row.get("provider") or "Unknown carrier"
            h = human.setdefault(name, {"carrier": name, "tasks": 0, "statuses": {},
                                        "waits": [], "runs": set(), "reasons": {},
                                        "completed_verified": 0, "months": {}})
            h["tasks"] += 1
            status = row.get("status") or "UNKNOWN"
            h["statuses"][status] = h["statuses"].get(status, 0) + 1
            reason = row.get("reason") or "unknown"
            h["reasons"][reason] = h["reasons"].get(reason, 0) + 1
            if isinstance(row.get("waited_s"), (int, float)):
                h["waits"].append(float(row["waited_s"]))
            if row.get("run_id"):
                h["runs"].add(row["run_id"])
            m = store.month_of(row.get("epoch") or time.time())
            h["months"][m] = h["months"].get(m, 0) + 1
            h.setdefault("_pending", []).append(row)
            cell["human_tasks"] += 1
        elif kind == "question":
            intent = row.get("intent") or "unrecognised"
            q = questions.setdefault(intent, {"intent": intent, "count": 0, "answered": 0,
                                              "patterns": {}, "months": {}, "last": None})
            q["count"] += 1
            q["last"] = row.get("at")
            if row.get("answered"):
                q["answered"] += 1
                cell["answered"] += 1
            pattern = row.get("pattern")
            if pattern:
                q["patterns"][pattern] = q["patterns"].get(pattern, 0) + 1
            m = store.month_of(row.get("epoch") or time.time())
            q["months"][m] = q["months"].get(m, 0) + 1
            cell["questions"] += 1
        elif kind == "feedback":
            verdict = row.get("verdict")
            feedback[verdict] = feedback.get(verdict, 0) + 1
            cell["feedback"][verdict] = cell["feedback"].get(verdict, 0) + 1

    # A human task completed only when its shipment then ended VERIFIED.
    for h in human.values():
        for row in h.pop("_pending", []):
            shipment = shipments_by_run.get((row.get("run_id"), row.get("reference")))
            if row.get("status") in ("SUCCESS", "RESUMED") and shipment and shipment.get("verified"):
                h["completed_verified"] += 1
                month_cell(row)["human_completed"] += 1

    for issue in issues.values():
        ranked = []
        for strat in issue["strategies"].values():
            n = strat["successes"] + strat["failures"]
            strat["decided"] = n
            strat["rate"] = round(strat["successes"] / n, 3) if n else None
            strat["wilson"] = round(wilson(strat["successes"], n), 3)
            strat["confidence"] = confidence(n)
            if n >= MIN_RANKED and strat["successes"] >= 1:
                ranked.append(strat)
        ranked.sort(key=lambda s: (s["wilson"], s["successes"]), reverse=True)
        issue["ranking"] = [s["name"] for s in ranked]
        issue["best"] = ranked[0]["name"] if ranked else None
        issue["current_first"] = (max(issue["first_tried"].items(), key=lambda kv: kv[1])[0]
                                  if issue["first_tried"] else None)
        issue["runs"] = sorted(issue["runs"])
        issue["learned"] = issue["occurrences"] >= RECURRING and len(issue["runs"]) >= 2

    for h in human.values():
        waits = h.pop("waits")
        h["runs"] = sorted(h["runs"])
        h["mean_wait_s"] = round(sum(waits) / len(waits), 1) if waits else None
        h["timed_out"] = h["statuses"].get("TIMEOUT", 0)
        h["sessions_lost"] = h["statuses"].get("HUMAN_SESSION_LOST", 0)

    for cell in months.values():
        cell["runs"] = sorted(cell["runs"])
        cell["new_issues"] = sorted(cell["new_issues"])

    snap = {
        "built_at": store.stamp(), "events": len(rows),
        "issues": sorted(issues.values(), key=lambda i: -i["occurrences"]),
        "human": sorted(human.values(), key=lambda h: -h["tasks"]),
        "questions": sorted(questions.values(), key=lambda q: -q["count"]),
        "feedback": feedback,
        "months": [months[m] for m in sorted(months)],
    }
    snap["po"] = _po_learning(po_rows, snap["issues"])
    snap["summary"] = summarize(snap)
    snap["proposals"] = proposals(snap)
    return snap


def _po_learning(rows, issue_list):
    """
    PO outcomes: each failure category is an issue (provider "PO"); it counts
    as resolved only when the same document later reached a CONFIRMED send —
    the verified outcome, never a send that was merely accepted.
    """
    confirmed_keys = {r.get("po_key") for r in rows if r.get("verified")}
    issues = {}
    for row in rows:
        if row.get("verified") or not row.get("category"):
            continue
        issue = issues.setdefault(_issue_key("PO", row["category"]),
                                  _new_issue("PO", row.get("doctype"), row["category"], "po"))
        _seen(issue, row)
        if row.get("po_key") in confirmed_keys:
            issue["resolved_verified"] += 1
    for issue in issues.values():
        issue["runs"] = sorted(issue["runs"])
        issue["learned"] = issue["occurrences"] >= RECURRING and len(issue["runs"]) >= 2
        issue["ranking"], issue["best"], issue["current_first"] = [], None, None
        issue_list.append(issue)
    return {"jobs": len(rows), "confirmed": sum(1 for r in rows if r.get("verified")),
            "failed": sum(1 for r in rows if not r.get("verified") and r.get("category")),
            "issues": sorted(issues)}


def snapshot():
    """The current learning, cached until the event store changes."""
    sig = store.signature(events.FILE)
    with _lock:
        if _cache["sig"] != sig or _cache["snap"] is None:
            _cache["snap"] = build()
            _cache["sig"] = sig
        return _cache["snap"]


def invalidate():
    with _lock:
        _cache["sig"] = None


def summarize(snap):
    issues = snap["issues"]
    strategies = [s for i in issues for s in i["strategies"].values()]
    return {
        "issues_seen": len(issues),
        "issues_learned": sum(1 for i in issues if i["learned"]),
        "verified_strategies": sum(1 for s in strategies if s["successes"] > 0),
        "verified_signals": sum(s["successes"] + s["failures"] for s in strategies),
        "verified_successes": sum(s["successes"] for s in strategies),
        "unverified": sum(s["unverified"] for s in strategies),
        "recurring_questions": sum(1 for q in snap["questions"] if q["count"] >= RECURRING),
        "human_carriers": len(snap["human"]),
        "runs": len({r for m in snap["months"] for r in m["runs"]}),
    }


def find_issue(snap, provider=None, name=None, carrier=None):
    """Issues matching a provider/carrier and an issue name fragment."""
    out = []
    for issue in snap["issues"]:
        hay = " ".join(str(x) for x in (issue["provider"], issue["carrier"])).casefold()
        if provider and provider.casefold() not in hay:
            continue
        if carrier and carrier.casefold() not in hay:
            continue
        if name and name.casefold().replace("_", " ") not in \
                str(issue["issue"]).casefold().replace("_", " "):
            continue
        out.append(issue)
    return out


def month_stats(snap, month):
    for cell in snap["months"]:
        if cell["month"] == month:
            return cell
    return None


def compare(snap, month_a, month_b):
    """What changed between two months, from their own events."""
    a, b = month_stats(snap, month_a) or {}, month_stats(snap, month_b) or {}

    def rate(cell):
        n = (cell.get("recovery_successes", 0) + cell.get("recovery_failures", 0))
        return (cell.get("recovery_successes", 0) / n) if n else None

    def vrate(cell):
        return (cell.get("verified", 0) / cell["shipments"]) if cell.get("shipments") else None
    per_issue = []
    for issue in snap["issues"]:
        ca, cb = issue["months"].get(month_a, 0), issue["months"].get(month_b, 0)
        if ca or cb:
            per_issue.append({"issue": issue["key"], "carrier": issue["carrier"],
                              "name": issue["issue"], "before": ca, "after": cb})
    per_issue.sort(key=lambda x: -(abs(x["after"] - x["before"])))
    return {"a": month_a, "b": month_b, "shipments": (a.get("shipments", 0), b.get("shipments", 0)),
            "verified_rate": (vrate(a), vrate(b)), "recovery_rate": (rate(a), rate(b)),
            "failures": (a.get("failed", 0), b.get("failed", 0)), "issues": per_issue[:8]}


def proposals(snap):
    """
    PROPOSE, never deploy. When the verified record says a different
    strategy should go first, say so — with the numbers — and leave the change
    to a tested, approved deployment.
    """
    approvals = store.load_json(PROPOSALS_FILE, {}).get("decisions", {})
    out = []
    for issue in snap["issues"]:
        best, first = issue.get("best"), issue.get("current_first")
        if not best or not first or best == first:
            continue
        b = issue["strategies"][best]
        if b["confidence"] in ("Insufficient data", "Low"):
            continue
        f = issue["strategies"].get(first) or {}
        pid = hashlib.sha1("{0}|{1}|{2}".format(issue["key"], best, first)
                           .encode("utf-8")).hexdigest()[:10]
        decision = approvals.get(pid) or {}
        out.append({
            "id": pid, "issue": issue["key"], "carrier": issue["carrier"],
            "change": "Try {0} before {1} for {2}".format(best, first, issue["issue"]),
            "evidence": {"best": {"name": best, "successes": b["successes"],
                                  "decided": b["decided"], "wilson": b["wilson"],
                                  "runs": b["evidence_runs"][-5:]},
                         "current": {"name": first, "successes": f.get("successes", 0),
                                     "decided": f.get("decided", 0),
                                     "wilson": f.get("wilson", 0.0)}},
            "confidence": b["confidence"],
            "status": decision.get("status", "PROPOSED"),
            "decided_by": decision.get("by"), "decided_at": decision.get("at"),
            "deploys": "Only through a tested, approved deployment. Nothing changes the "
                       "automation by itself.",
        })
    return out


def decide(proposal_id, status, by):
    """Record a person's decision on a proposal (APPROVED / REJECTED). Audit only."""
    if status not in ("APPROVED", "REJECTED"):
        return False
    data = store.load_json(PROPOSALS_FILE, {"decisions": {}})
    data.setdefault("decisions", {})[proposal_id] = {"status": status, "by": str(by)[:60],
                                                     "at": store.stamp()}
    ok = store.save_json(PROPOSALS_FILE, data)
    invalidate()
    return ok
