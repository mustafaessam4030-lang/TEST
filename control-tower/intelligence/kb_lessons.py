"""
Lessons from operation: what the runs themselves have shown, as structured
records ATLAS can show and a reviewer can turn into knowledge.

Read from the verified learning store (intelligence/learning.py), never typed
in and never from a conversation. A lesson is one carrier and one failure:

    incident and run references, carrier and workflow, the observed failure,
    every strategy tried with its outcomes (failures kept, so they are not
    blindly repeated), the verified resolution if one exists, the conditions
    it applies under, confidence (Wilson lower bound) and review status.

What a lesson never does:
  * claim a root cause — the learning store records outcomes, not causes;
    the root cause is "not established" unless a reviewer adds one
  * generalise — a strategy that worked for one carrier's failure is not a
    rule for any other carrier; with fewer than learning.MIN_RANKED verdicts
    it is not even a ranking
  * become knowledge by itself — propose_from_lesson() makes a proposal that
    a reviewer must approve (intelligence/kb_govern.py)
"""
import re

from . import learning

WORKFLOW = {"po": "PO automation"}


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:40] or "x"


def lessons(snap=None):
    snap = snap or learning.snapshot()
    out = []
    for issue in snap.get("issues") or []:
        strategies = sorted(issue.get("strategies", {}).values(), key=lambda s: -(s.get("attempts") or 0))
        attempts = [{"strategy": s["name"], "attempts": s.get("attempts", 0), "successes": s.get("successes", 0),
                     "failures": s.get("failures", 0), "unverified": s.get("unverified", 0),
                     "wilson": s.get("wilson"), "evidence_runs": s.get("evidence_runs", [])[-5:]}
                    for s in strategies]
        best = next((a for a in attempts if a["strategy"] == issue.get("best")), None)
        enough = best is not None and (best["successes"] + best["failures"]) >= learning.MIN_RANKED
        resolution = None
        if best and best["successes"] > 0:
            resolution = {"strategy": best["strategy"], "verified_successes": best["successes"],
                          "failures": best["failures"], "confidence": best.get("wilson"),
                          "ranked": enough,
                          "note": None if enough else "fewer than {0} verdicts: shown, not relied on".format(learning.MIN_RANKED)}
        failed = [a["strategy"] for a in attempts if a["failures"] and not a["successes"]]
        out.append({
            "id": "L.{0}-{1}".format(_slug(issue.get("provider")), _slug(issue.get("issue"))),
            "kind": "lesson", "carrier": issue.get("carrier"), "provider": issue.get("provider"),
            "workflow": WORKFLOW.get(str(issue.get("kind") or "").lower(), "ETA/ATA automation"),
            "observed_failure": issue.get("issue"), "occurrences": issue.get("occurrences", 0),
            "runs": list(issue.get("runs") or [])[-10:], "shipments": list(issue.get("affected") or [])[-10:],
            "first_seen": issue.get("first_seen"), "last_seen": issue.get("last_seen"),
            "root_cause": None, "root_cause_note": "not established by the record — the learning store holds outcomes, not causes",
            "attempts": attempts, "failed_strategies": failed, "verified_resolution": resolution,
            "applies_when": "carrier {0}, failure '{1}' — not other carriers or failures".format(
                issue.get("provider"), issue.get("issue")),
            "recurring": bool(issue.get("learned")),
            "review_status": "computed — not reviewed",
        })
    return out


def text(lesson):
    """A lesson in plain words."""
    lines = ["**{0} — {1}** ({2}): seen {3} time(s) in {4} run(s).".format(
        lesson["provider"], lesson["observed_failure"], lesson["workflow"], lesson["occurrences"], len(lesson["runs"]))]
    res = lesson["verified_resolution"]
    if res:
        lines.append("Verified resolution: **{0}** — {1} verified success(es), {2} failure(s){3}.".format(
            res["strategy"], res["verified_successes"], res["failures"],
            "" if res["ranked"] else " (" + res["note"] + ")"))
    else:
        lines.append("No verified resolution yet.")
    if lesson["failed_strategies"]:
        lines.append("Tried without success: " + ", ".join(lesson["failed_strategies"]) + ".")
    lines.append("Root cause: " + lesson["root_cause_note"] + ".")
    lines.append("Applies to: " + lesson["applies_when"] + ".")
    return "\n".join(lines)


def propose_from_lesson(lesson_id, by, note=None):
    """A reviewer's starting point: a proposal of kind 'observation' carrying
    the lesson. It becomes knowledge only as an item a reviewer approves."""
    from . import kb_govern
    found = next((l for l in lessons() if l["id"] == lesson_id), None)
    if found is None:
        raise kb_govern.GovernanceError("no such lesson")
    return kb_govern.propose(by=by, kind="observation", observation=text(found).replace("**", ""),
                             note=note or "from operational lesson " + lesson_id)
