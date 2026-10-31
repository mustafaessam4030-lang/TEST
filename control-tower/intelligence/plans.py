"""
Recovery plans — PLAN.

A plan is built from two real sources and nothing else:

    the policy   ml/recovery.py's fixed vocabulary of safe actions for an
                 error class, and the AFKL navigation ladder's strategies —
                 the only things the automation can execute
    the record   learning.py's verified history for that issue

Each step says what it is, what the verified record says about it, and —
when a live recovery is running or has run — what actually happened: status,
start time, result. A step that has not run is NOT EXECUTED; it is never
reported as done.

The ranking is ATLAS's RECOMMENDATION. The automation runs its own
deterministic order (ATLAS is in SHADOW); promoting a recommendation into
that order is a proposal for a tested, approved deployment.
"""

from . import learning

try:
    from ml import recovery as policy
except Exception:                                   # pragma: no cover
    policy = None

# The AFKL ladder, as update_eta.open_afkl_detail runs it, in its order.
AFKL_LADDER = [
    ("fresh context", "a fresh browser context on the same browser — clears cached "
                      "connection state"),
    ("clean edge, HTTP/2 disabled", "a clean Edge with HTTP/2 off — only when the "
                                    "failures look like transport errors"),
    ("bundled chromium", "a different browser build, the last hypothesis"),
]
AFKL_HYPOTHESES = [
    ("Transport", "The carrier accepted the connection but the shipment page never "
                  "finished loading (protocol or proxy interference)."),
    ("Session state", "Cached state on the existing page or profile blocks the load."),
    ("Browser build", "The installed Edge build fails where another build works."),
]
NOT_EXECUTED = "NOT EXECUTED"


def _stat_line(strat):
    if not strat:
        return "no verified history yet"
    if strat["decided"] == 0:
        return "{0} unverified, no verified verdict yet".format(strat["unverified"])
    return "{0}/{1} verified ({2:.0%}), confidence {3}{4}".format(
        strat["successes"], strat["decided"], strat["rate"] or 0.0, strat["confidence"],
        ", {0} unverified".format(strat["unverified"]) if strat["unverified"] else "")


def build(provider, issue_name, snap=None, live=None):
    """
    {problem, issue, hypotheses, steps[], recommendation, executes, similar}.
    `live` is the bridge's recovery dict (or an episode) for the step states.
    """
    snap = snap or learning.snapshot()
    matches = learning.find_issue(snap, provider=provider, name=issue_name)
    issue = matches[0] if matches else None
    ladder = "navigation" in str(issue_name or "").casefold() and \
        "afkl" in str(provider or "").casefold()
    hypotheses, actions = [], []
    if ladder:
        hypotheses = [{"name": n, "cause": c, "kind": "hypothesis"} for n, c in AFKL_HYPOTHESES]
        actions = list(AFKL_LADDER)
    elif policy is not None:
        cls = str(issue_name or "").upper()
        if cls in policy.ERROR_CLASSES:
            if cls in policy.NOT_RECOVERABLE:
                return {"problem": cls, "issue": issue, "hypotheses": [], "steps": [],
                        "recommendation": None, "executes": "nothing",
                        "not_recoverable": policy.NOT_RECOVERABLE[cls],
                        "similar": _similar(issue)}
            try:
                hypotheses = [{"name": h.name, "cause": h.cause,
                               "confidence": round(float(h.confidence), 2),
                               "kind": "hypothesis"} for h in policy.hypotheses(cls)[:4]]
            except Exception:
                hypotheses = []
            actions = [(a.name, a.detail) for a in policy.candidates(cls)]
    strategies = (issue or {}).get("strategies", {})
    attempts = {}
    for a in (live or {}).get("attempts") or []:
        attempts[a.get("action")] = a
    # The verified ranking first, then the rest of the safe vocabulary, then
    # anything that actually ran but is not in either — a step that ran is
    # always shown, never hidden because the plan did not expect it.
    learned_order = list((issue or {}).get("ranking", []))
    ordered = learned_order + [n for n, _ in actions if n not in learned_order]
    ordered += [n for n in attempts if n and n not in ordered]
    if policy is not None:
        for name in ordered:
            if name not in dict(actions) and name in getattr(policy, "ACTIONS_BY_NAME", {}):
                actions.append((name, policy.ACTIONS_BY_NAME[name].detail))
    steps = []
    for index, name in enumerate(ordered, start=1):
        detail = dict(actions).get(name, "")
        run = attempts.get(name)
        status = NOT_EXECUTED
        if run is not None:
            status = {"SUCCESS": "SUCCEEDED (verified)" if run.get("verified") else "COMPLETED (unverified)",
                      "DONE": "COMPLETED (unverified)", "FAILED": "FAILED",
                      "RUNNING": "RUNNING"}.get(str(run.get("result")).upper(), str(run.get("result")))
        steps.append({"n": index, "action": name, "detail": detail,
                      "history": _stat_line(strategies.get(name)),
                      "status": status, "started": (run or {}).get("time"),
                      "result": (run or {}).get("result"),
                      "verified": (run or {}).get("verified")})
    steps.append({"n": len(steps) + 1, "action": "verify",
                  "detail": "confirm the right shipment page, then extraction, validation, "
                            "Hub write and read-back", "history": "the existing verification "
                            "pipeline — the only thing that makes an outcome a success",
                  "status": NOT_EXECUTED if not live else (
                      "PASSED" if live.get("verified") is True else
                      "FAILED" if live.get("verified") is False else NOT_EXECUTED),
                  "started": None, "result": None, "verified": None})
    steps.append({"n": len(steps) + 1, "action": "stop safely",
                  "detail": "if nothing verifies: stop, write nothing, record the outcome",
                  "history": "", "status": "APPLIED" if (live or {}).get("status") == "EXHAUSTED"
                  else NOT_EXECUTED, "started": None, "result": None, "verified": None})
    best = (issue or {}).get("best")
    rec = None
    if best:
        rec = {"action": best, "why": _stat_line(strategies.get(best)),
               "runs": strategies[best]["evidence_runs"][-5:]}
    return {"problem": issue_name, "provider": provider, "issue": issue,
            "hypotheses": hypotheses, "steps": steps, "recommendation": rec,
            "executes": "the automation's own deterministic order (ATLAS is in SHADOW)",
            "similar": _similar(issue)}


def _similar(issue):
    if not issue:
        return {"count": 0, "runs": [], "resolved_verified": 0}
    return {"count": issue["occurrences"], "runs": issue["runs"][-6:],
            "resolved_verified": issue["resolved_verified"],
            "first_seen": issue["first_seen"], "last_seen": issue["last_seen"]}
