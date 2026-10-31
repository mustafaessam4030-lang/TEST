"""
Mission actions: a proposed step becomes an action card; a person approves it;
an existing engine does it; the engine's own persisted result says whether it
worked.

    from_report(mission, acts)     the Automation Engineer's APPROVAL_REQUIRED / PREPARATION steps -> action rows
    decide(id, approve, by, can)   approve (then execute) or reject one action; per-kind permission
    verify_pending()               the Action Verifier: reads outcomes back, never guesses
    recover()                      after a restart: EXECUTING -> AWAITING_OUTCOME (read, never re-executed)
    policies()                     Level 4: written, time-boxed, audited pre-approvals (none by default)

Lifecycle of an action:

    proposed ─approve─► executing ─engine accepted─► awaiting_outcome ─read back─► verified
        │                   └─engine refused─► failed                └─not confirmed─► not_verified
        └─reject─► rejected

Success is never reported from the engine's "accepted": a re-run counts only
when the run's bridge event for that reference, recorded after the request,
is a verified success (events.verified_success: result SUCCESS and every Hub
write read back and matched). An escalation counts when the escalation (same shipment
and BU) is in the Clearance store. A To Do task counts when a person marks it done. A non-idempotent
action is never retried: its idempotency key is unique, and after a restart
its outcome is read, not re-run.

Execution is OFF unless ATLAS_MISSION_ACTIONS=1: with it off, actions are
cards that say what would happen and who may approve them, as before.
"""
import hashlib
import json
import os
import re
import time
import uuid

from . import db, tools

KINDS = {
    "reprocess":   {"tool": "reprocess", "permission": "runs.start", "class": "APPROVAL_REQUIRED", "level": 3,
                    "criterion": "the run's next result for this reference is a verified success (Hub write read back and matched)"},
    "escalate":    {"tool": "escalate", "permission": "clearance.escalate", "class": "APPROVAL_REQUIRED", "level": 3,
                    "criterion": "an open escalation for this shipment is on the Clearance page"},
    "queue_human": {"tool": "queue_human", "permission": "missions.run", "class": "PREPARATION", "level": 2,
                    "criterion": "a person marks the To Do task done"},
}
OUTCOME_WAIT_S = float(os.environ.get("ATLAS_MISSION_OUTCOME_WAIT_S") or 6 * 3600)
LIVE = ("proposed", "executing", "awaiting_outcome")
AUDIT = {"fn": None}       # the server hands in its audit log: fn(action, result, target_id, metadata)


def _audit(action, result, a, **meta):
    fn = AUDIT["fn"]
    if fn:
        try:
            fn(action, result, a["id"], dict(meta, mission=a["mission_id"], kind=a["kind"]))
        except Exception:
            pass


def _target(kind, args):
    """What the action acts on: the same shipment (and BU) is never acted on by two missions at once."""
    return "{0}:{1}:{2}".format(kind, tools._norm(args.get("reference") or args.get("awb")), args.get("bu") or "")


def enabled():
    return str(os.environ.get("ATLAS_MISSION_ACTIONS") or "0").strip().lower() in ("1", "on", "yes", "true")


def _key(mission_id, kind, args):
    return hashlib.sha256(json.dumps([mission_id, kind, args], sort_keys=True).encode("utf-8")).hexdigest()[:24]


def _add(mission, kind, title, why, args, href=None, origin="records"):
    k = KINDS[kind]
    key = _key(mission["id"], kind, args)
    aid = uuid.uuid4().hex[:12]
    if not db.insert_new("actions", id=aid, mission_id=mission["id"], kind=kind, **{"class": k["class"]}, title=title[:200], why=why[:400],
                         args=dict(args, origin=origin), permission=k["permission"], idempotency_key=key, status="proposed",
                         proposed_at=time.time(), criterion=k["criterion"], href=href, outcome="pending"):
        return None                                    # the same card exists already: never replaced
    db.event(mission["id"], "action_proposed", "Action proposed ({0}): {1}".format(k["permission"], title))
    return aid


def from_report(mission, acts):
    """Turn the Automation Engineer's steps into action cards. READ_ONLY links stay links."""
    made = []
    for a in acts or []:
        kind, args = a.get("kind"), a.get("args") or {}
        if kind not in KINDS:
            continue
        aid = _add(mission, kind, a.get("title") or kind, a.get("why") or "", args, a.get("href"))
        if aid:
            made.append(aid)
    if made:
        db.update("missions", {"id": mission["id"]}, verification_status="awaiting_approval")
        _apply_policies(mission)
    return made


def listing(mission_id=None, status=None):
    sql, args = "SELECT * FROM actions", []
    where = []
    if mission_id:
        where.append("mission_id=?")
        args.append(mission_id)
    if status:
        where.append("status IN ({0})".format(",".join("?" * len(status))))
        args.extend(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.many(sql + " ORDER BY proposed_at", *args)


def decide(action_id, approve, by, can, policy=None):
    """-> (ok, message, action). The approver needs the action's own permission (runs.start for a re-run,
    clearance.escalate for an escalation). Approval executes it at once through its engine."""
    a = db.one("SELECT * FROM actions WHERE id=?", action_id)
    if not a:
        return False, "No such action.", None
    if a["status"] != "proposed":
        return False, "This action is already {0}.".format(a["status"].replace("_", " ")), a
    if not can(a["permission"]):
        return False, "Approving this needs {0}.".format(a["permission"]), a
    if not approve:
        if not db.transition("actions", {"id": action_id}, "proposed", status="rejected", decided_by=str(by)[:80],
                             decided_at=time.time(), outcome="rejected"):
            return False, "Someone else decided this action first.", db.one("SELECT * FROM actions WHERE id=?", action_id)
        db.event(a["mission_id"], "action_rejected", "Rejected by {0}: {1}".format(by, a["title"]))
        _roll_up(a["mission_id"])
        return True, "Rejected. Nothing was done.", db.one("SELECT * FROM actions WHERE id=?", action_id)
    if not enabled():
        return False, ("Mission actions are switched off on this server (ATLAS_MISSION_ACTIONS=1 allows them). "
                       "You can do this step yourself: {0}".format(a["title"])), a
    target = _target(a["kind"], a.get("args") or {})
    with db._lock:
        limit = _limit("ATLAS_MISSION_ACTIONS_DAILY", 20)
        today = executed_today()
        if len(today) >= limit:
            return False, ("Today's limit of {0} executed mission actions is reached (ATLAS_MISSION_ACTIONS_DAILY). "
                           "It resets at midnight; you can still do this step yourself.".format(limit)), a
        if policy and sum(1 for x in today if x.get("policy")) >= _limit("ATLAS_MISSION_POLICY_DAILY", 5):
            return False, "Today's limit of automatic (policy) approvals is reached; a person approves the rest.", a
        busy = [x for x in listing(status=("executing", "awaiting_outcome")) if x["id"] != action_id and x["kind"] != "queue_human"
                and _target(x["kind"], x.get("args") or {}) == target]
        if busy:
            return False, "The same step is already running from mission {0}; its outcome is awaited.".format(busy[0]["mission_id"]), a
        # Compare-and-set: of two approvals at the same moment, exactly one executes.
        if not db.transition("actions", {"id": action_id}, "proposed", status="executing", decided_by=str(by)[:80],
                             decided_at=time.time(), policy=policy, executed_at=time.time()):
            return False, "Someone else decided this action first.", db.one("SELECT * FROM actions WHERE id=?", action_id)
    if policy:
        _audit("MISSION_ACTION_APPROVED", "SUCCESS", a, policy=policy, by=str(by)[:80])
    db.event(a["mission_id"], "action_approved", "Approved by {0}{1}: {2}".format(by, " (policy {0})".format(policy) if policy else "", a["title"]))
    args = a.get("args") or {}
    try:
        if a["kind"] == "reprocess":
            ok, message, ref = tools.reprocess(args.get("reference"), by)
        elif a["kind"] == "escalate":
            ok, message, ref = tools.escalate(args.get("awb"), args.get("reason") or a["why"], by, bu=args.get("bu"))
        else:
            ok, message, ref = tools.queue_human(a["title"], by)
    except Exception as error:
        ok, message, ref = False, "{0}: {1}".format(type(error).__name__, str(error)[:160]), None
    if not ok:
        db.update("actions", {"id": action_id}, status="failed", outcome="failed", outcome_detail={"message": message}, verified_at=time.time())
        if policy:
            _audit("MISSION_ACTION_FAILED", "FAILED", a, policy=policy)
        db.event(a["mission_id"], "action_failed", "The engine refused it: {0}".format(message))
        _roll_up(a["mission_id"])
        return False, message, db.one("SELECT * FROM actions WHERE id=?", action_id)
    db.update("actions", {"id": action_id}, status="awaiting_outcome", engine_ref=ref, outcome_detail={"message": message})
    db.event(a["mission_id"], "action_executed", "{0}".format(
        {"reprocess": "The ATA run accepted it; waiting for the read-back", "escalate": "The Clearance page accepted it; reading it back",
         "queue_human": "Added to To Do; done when a person marks it done"}[a["kind"]]))
    verify_one(db.one("SELECT * FROM actions WHERE id=?", action_id))
    _roll_up(a["mission_id"])
    return True, message, db.one("SELECT * FROM actions WHERE id=?", action_id)


def _limit(name, default):
    try:
        return max(0, int(os.environ.get(name) or default))
    except ValueError:
        return default


def executed_today():
    """Actions executed since local midnight (by a person or a policy)."""
    t = time.localtime()
    midnight = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return db.many("SELECT id, policy FROM actions WHERE executed_at >= ?", midnight)


# ── the Action Verifier ───────────────────────────────────────────────────
def verify_one(a, now=None):
    """Read the engine's own record. -> the new outcome, or None while still waiting."""
    if not a or a["status"] != "awaiting_outcome":
        return None
    now = now or time.time()
    args = a.get("args") or {}
    outcome, detail = None, {}
    if a["kind"] == "reprocess":
        from intelligence import events as E
        rows = tools.run_events(args.get("reference"), since=(a.get("executed_at") or 0) - 1)
        if rows:
            last = rows[-1]
            ok = E.verified_success(last.get("result"), last.get("verification"))
            outcome = "verified" if ok else "not_verified"
            detail = {"result": last.get("result"), "read_back": last.get("verification"), "at": last.get("at")}
        elif now - (a.get("executed_at") or now) > OUTCOME_WAIT_S:
            outcome, detail = "not_verified", {"message": "no result from the run within {0:.0f} hours".format(OUTCOME_WAIT_S / 3600)}
    elif a["kind"] == "escalate":
        # the escalation this action opened (its id), or an open one for the same shipment AND BU
        esc = [e for e in tools.escalation_list() if (a.get("engine_ref") and e.get("id") == a.get("engine_ref")) or
               (tools._norm(e.get("awb")) == tools._norm(args.get("awb")) and e.get("status") == "open"
                and (not args.get("bu") or e.get("bu") == args.get("bu")))]
        outcome = "verified" if esc else "not_verified"
        detail = {"escalation": (esc[0].get("id") if esc else None)}
    else:
        return None                     # a To Do task is done only when a person marks it done (complete())
    if outcome is None:
        return None
    # Compare-and-set: of two verifiers (a request and the worker), one records the outcome.
    if not db.transition("actions", {"id": a["id"]}, "awaiting_outcome", status=outcome, outcome=outcome,
                         outcome_detail=dict(a.get("outcome_detail") or {}, **detail), verified_at=now):
        return None
    _audit("MISSION_ACTION_VERIFIED" if outcome == "verified" else "MISSION_ACTION_NOT_VERIFIED", "SUCCESS" if outcome == "verified" else "FAILED", a)
    db.event(a["mission_id"], "action_" + outcome, "{0}: {1}".format(a["title"], "confirmed by read-back" if outcome == "verified"
                                                                      else "NOT confirmed ({0})".format(detail.get("message") or detail.get("result") or "no record")))
    _roll_up(a["mission_id"])
    return outcome


def verify_pending():
    n = 0
    for a in listing(status=("awaiting_outcome",)):
        try:
            if verify_one(a):
                n += 1
        except Exception:
            pass
    return n


def _roll_up(mission_id):
    """The mission's verification status from its actions: awaiting_approval, awaiting_outcome,
    done_verified (every executed action confirmed), done_unverified (any not confirmed or failed)."""
    acts = listing(mission_id)
    if not acts:
        return
    st = {a["status"] for a in acts}
    if "proposed" in st:
        v = "awaiting_approval"
    elif st & {"executing", "awaiting_outcome"}:
        v = "awaiting_outcome"
    elif st & {"not_verified", "failed"}:
        v = "done_unverified"
    elif st & {"verified", "completed"}:
        v = "done_verified"
    else:
        v = "no_action_taken"
    db.update("missions", {"id": mission_id}, verification_status=v)
    db.bump(mission_id)


def recover():
    """After a restart: an action caught EXECUTING may or may not have reached its engine. It is never
    run again; its outcome is read like any other."""
    rows = listing(status=("executing",))
    for a in rows:
        db.update("actions", {"id": a["id"]}, status="awaiting_outcome",
                  outcome_detail=dict(a.get("outcome_detail") or {}, restart="interrupted during execution: outcome read, never re-run"))
        db.event(a["mission_id"], "recovered", "Action {0} was interrupted by a restart: its outcome will be read, it is not re-run".format(a["title"]))
    return len(rows)


def todo_items():
    """To Do source: actions waiting for a person's approval, and tasks a mission put in To Do."""
    return [a for a in listing(status=("proposed", "awaiting_outcome"))
            if a["status"] == "proposed" or a["kind"] == "queue_human"]


def complete(action_id, by):
    """A person marks a To Do task from a mission as done."""
    a = db.one("SELECT * FROM actions WHERE id=?", action_id)
    if not a or a["kind"] != "queue_human" or a["status"] != "awaiting_outcome":
        return False, "Nothing to mark done."
    if not db.transition("actions", {"id": action_id}, "awaiting_outcome", status="completed", outcome="completed", verified_at=time.time(),
                         outcome_detail=dict(a.get("outcome_detail") or {}, done_by=str(by)[:80], done_at=time.time())):
        return False, "Already marked done."
    db.event(a["mission_id"], "action_done", "Marked done by {0}: {1}".format(by, a["title"]))
    _audit("MISSION_ACTION_DONE", "SUCCESS", a, by=str(by)[:80])
    _roll_up(a["mission_id"])
    return True, "Marked done."


# ── Level 4: written policies (none by default) ───────────────────────────
GRADES = {"Insufficient": 0, "Weak": 1, "Moderate": 2, "Strong": 3}


def _policy_file():
    return os.environ.get("ATLAS_MISSION_POLICIES") or str(db.path().parent / "mission_policies.json")


def policies():
    """[{id, kind, max_per_mission, hypotheses (any of), min_grade, expires (YYYY-MM-DD, at most 30 days
    after approved_on), approved_by, approved_on}] from the policy file. Anything malformed or expired
    is ignored, and listed with the reason."""
    try:
        with open(_policy_file(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return [], []
    from datetime import date
    good, bad = [], []
    today = date.today()
    iso = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for p in raw if isinstance(raw, list) else []:
        why = None
        if not isinstance(p, dict) or p.get("kind") not in KINDS:
            why = "unknown kind"
        elif not p.get("approved_by") or not p.get("approved_on") or not p.get("expires"):
            why = "needs approved_by, approved_on and expires"
        elif not iso.match(str(p["expires"])) or not iso.match(str(p["approved_on"])):
            why = "bad dates (YYYY-MM-DD)"
        else:
            try:
                on, until = date.fromisoformat(str(p["approved_on"])), date.fromisoformat(str(p["expires"]))
            except ValueError:
                on = until = None
            if on is None:
                why = "bad dates (YYYY-MM-DD)"
            elif until < today:
                why = "expired"
            elif on > today:
                why = "approved_on is in the future"
            elif until < on:
                why = "expires before it was approved"
            elif (until - on).days > 30:
                why = "longer than 30 days"
        (bad if why else good).append(dict(p, reason=why) if why else p)
    return good, bad


def _apply_policies(mission):
    """Auto-approve only what a valid policy names exactly; everything else waits for a person."""
    if not enabled():
        return
    good, _ = policies()
    if not good:
        return
    rep = mission.get("report") or (db.one("SELECT report FROM missions WHERE id=?", mission["id"]) or {}).get("report") or {}
    grade = GRADES.get(rep.get("grade"), 0)
    supported = {h.get("id") for h in (rep.get("hypotheses") or []) if h.get("status") == "supported"}
    for p in good:
        mine = [a for a in listing(mission["id"]) if a["kind"] == p["kind"]]
        if len(mine) > int(p.get("max_per_mission") or 0):
            db.event(mission["id"], "policy", "Policy {0} not applied: {1} actions exceed its limit of {2}".format(p.get("id"), len(mine), p.get("max_per_mission")))
            continue
        if grade < GRADES.get(p.get("min_grade") or "Strong", 3):
            continue
        need = set(p.get("hypotheses") or [])
        if need and not (need & supported):
            continue
        for a in mine:
            if a["status"] == "proposed":
                ok, msg, _ = decide(a["id"], True, "policy:{0} (approved by {1})".format(p.get("id"), p.get("approved_by")),
                                    lambda perm: True, policy=p.get("id"))
                db.event(mission["id"], "policy", "Level 4 policy {0} {1} {2}: {3}".format(
                    p.get("id"), "approved" if ok else "did not approve", a["title"], msg))
                if not ok and "limit" in msg:
                    db.event(mission["id"], "guard", "Daily limit reached: the remaining actions wait for a person")
                    return
