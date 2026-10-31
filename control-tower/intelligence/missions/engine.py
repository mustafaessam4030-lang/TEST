"""
The Mission Commander and the Workload Manager.

    propose(question, rows, data, allowed, by)   -> a PROPOSED mission (nothing runs) or None for NORMAL
    approve(id, by)                              -> QUEUED: its task graph is built; it runs when capacity allows
    decline(id, by) / cancel / pause / resume
    get(id), listing(), recover(), start()

Nothing runs without an approval recorded on the mission (who, when, the
plan's hash). Missions are read-only: their tasks read records and, where a
model helps, call it through models.call(); next steps are proposals only.

Workload: a persistent queue (missions.db). At most MAX_ACTIVE missions run at
once, MAX_QUEUED wait; beyond that a new one is refused, said plainly. Tasks
whose dependencies are done run in parallel on WORKERS threads, highest
priority first, with ageing so nothing waits forever. While the ATA run is
active, missions use one worker and no local model: the automation keeps the
machine. A task that fails is retried once (every task is a read); after
that the mission continues on its other branches and the report says what
failed. After a restart, unfinished missions resume from the store.
"""
import hashlib
import json
import os
import threading
import time
import uuid

from . import actions, classify, db, memory, models, plan, specialists
from .registry import SPECIALISTS, TEMPLATES

MAX_ACTIVE = int(os.environ.get("ATLAS_MISSIONS_ACTIVE") or 3)
MAX_QUEUED = int(os.environ.get("ATLAS_MISSIONS_QUEUED") or 20)
TASK_TIMEOUT_S = float(os.environ.get("ATLAS_MISSION_TASK_TIMEOUT_S") or 120)
MAX_REPLANS = 2
WORKERS = int(os.environ.get("ATLAS_MISSIONS_WORKERS") or 3)
LEVEL_WEIGHT = {"ADVANCED": 1.0, "DEEP_MISSION": 2.0, "CRITICAL_INCIDENT": 4.0}
LIVE = ("queued", "running", "paused")

_cfg = {"state": lambda: {}, "export": None}
models.LOCAL_BLOCKED = lambda: automation_busy()
_cond = threading.Condition()
_threads = []
_started = {"on": False}


def configure(state=None, export=None):
    """The server hands in how to read the run's state (and, in tests, the export)."""
    if state is not None:
        _cfg["state"] = state
    if export is not None:
        _cfg["export"] = export


def _export():
    if _cfg["export"] is not None:
        return _cfg["export"]()
    from dashboard import clearance as C
    return C.load()


def automation_busy():
    """True while the ATA run is running: missions then use one worker and no local model."""
    try:
        run = (_cfg["state"]() or {}).get("run") or {}
        return str(run.get("status") or "").lower() in ("running", "processing", "started")
    except Exception:
        return False


# ── the task context a specialist works with ──────────────────────────────
class Ctx(object):
    def __init__(self, mission, task):
        from dashboard import clearance as C
        self.C = C
        self.mission, self.task = mission, task
        self.scope = mission.get("scope") or {}
        self.profile = mission.get("profile") or "deep"
        self.budget = mission.get("budget_tokens") or 80000
        self.data = _export() or {}
        self.asof = self.data.get("asof")
        self.rows = self._scoped(C.records(self.data) if self.data else [])
        try:
            self.state = _cfg["state"]() or {}
        except Exception:
            self.state = {}

    def _scoped(self, rows):
        allowed = self.scope.get("allowed")
        return [r for r in rows if allowed is None or r.get("bu") in allowed]

    def template(self):
        return TEMPLATES.get(self.mission.get("template")) or {}

    def fanout(self):
        return bool(self.template().get("fanout"))

    def has_task(self, tid):
        return db.one("SELECT 1 AS x FROM tasks WHERE mission_id=? AND id=?", self.mission["id"], tid) is not None

    def tasks_of(self, specialist):
        return db.many("SELECT * FROM tasks WHERE mission_id=? AND specialist=?", self.mission["id"], specialist)

    def focus_rows(self):
        refs, bus = self.scope.get("refs") or [], self.scope.get("bus") or []
        if refs:
            return [r for r in self.rows if r.get("awb") in refs]
        if self.fanout():
            items = self.scope.get("items") or []
            return [r for r in self.rows if r.get("awb") in items]
        rows = [r for r in self.rows if r.get("bu") in bus and r.get("step") != "Completed" and (r.get("overdue") or 0) > 0]
        return sorted(rows, key=lambda r: -(r.get("overdue") or 0))[:8]

    def reload_rows(self):
        data = _export() or {}
        return self._scoped(self.C.records(data) if data else [])

    def run_record(self, awb):
        key = self.C.norm_awb(awb)
        for rec in (self.state.get("shipments") or []):
            if self.C.norm_awb(rec.get("reference")) == key:
                return rec
        return None

    def run_source(self):
        run = self.state.get("run") or {}
        return "ATA run {0}".format(run.get("run_id") or "(no run loaded)")

    def run_when(self):
        return (self.state.get("run") or {}).get("updated") or (self.state.get("run") or {}).get("started")

    def escalations(self):
        try:
            return self.C.escalations()
        except Exception:
            return []

    @staticmethod
    def slim(r):
        return {k: r.get(k) for k in ("awb", "bu", "mode", "step", "status", "overdue", "eta", "ata", "agent", "ffw", "due")}

    def dep(self, tid):
        row = db.one("SELECT output FROM tasks WHERE mission_id=? AND id=?", self.mission["id"], tid)
        return (row or {}).get("output") or {}

    def all_evidence(self):
        return db.many("SELECT * FROM evidence WHERE mission_id=? AND status='kept' ORDER BY captured_at", self.mission["id"])

    def evidence_ids(self, awb):
        return [e["id"] for e in self.all_evidence() if awb in (e.get("identifiers") or [])]

    def set_verification(self, eid, value):
        db.update("evidence", {"id": eid}, verification=value)

    def allow_models(self):
        return self.profile != "drill"


# ── proposals and approval ────────────────────────────────────────────────
def _plan_hash(plan):
    return hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def propose(question, data=None, rows=None, allowed=None, by=None, origin="chat", profile=None, force_level=None):
    """-> the mission row (PROPOSED), or None when ATLAS answers normally / the user already said no."""
    a = classify.assess(question, data, rows, allowed)
    if force_level:
        a["level"] = force_level
        if force_level == "CRITICAL_INCIDENT" and a["severity"] in ("NORMAL", "HIGH"):
            a["severity"] = "CRITICAL"
    if a["level"] == "NORMAL":
        return None
    if classify.declined(a["fingerprint"]) and origin != "manual":
        return None
    same = db.one("SELECT * FROM missions WHERE fingerprint=? AND status IN ('proposed','queued','running','paused') ORDER BY created_at DESC",
                  a["fingerprint"])
    if same:
        if force_level == "CRITICAL_INCIDENT" and same["status"] == "proposed" and same["level"] != "CRITICAL_INCIDENT":
            # Emergency Mode on a task already waiting as a deeper look: the proposal is raised, said in its timeline
            plan_ = dict(same.get("plan") or {}, level="CRITICAL_INCIDENT", approver="missions.emergency")
            db.update("missions", {"id": same["id"]}, level="CRITICAL_INCIDENT", plan=plan_,
                      severity=same["severity"] if same.get("severity") in ("CRITICAL", "SEVERE") else "CRITICAL",
                      priority=(same.get("priority") or 0) + LEVEL_WEIGHT["CRITICAL_INCIDENT"])
            db.event(same["id"], "escalation", "Raised to a critical incident (Emergency Mode) by {0}".format(by or "someone"))
            return get(same["id"])
        return same
    mid = uuid.uuid4().hex[:10]
    d = plan.draft(a, profile or ("emergency" if a["level"] == "CRITICAL_INCIDENT" else "deep"), mission_id=mid)
    a["template"] = d["template"]
    p = classify.proposal(a, profile)
    p["planner"] = {"note": d["note"], "model": d["model"], "skip": d["skip"]}
    if a["template"] in ("multi_delay",):
        p["items"] = _fan_items(a["scope"], rows)
        a["scope"] = dict(a["scope"], items=p["items"])
    scope = a["scope"]
    title = "{0}: {1}".format(p["title"], ", ".join(scope.get("refs") or scope.get("bus") or []) or "scope")
    max_over = a["signals"].get("max_overdue_days") or 0
    db.insert("missions", id=mid, fingerprint=a["fingerprint"], title=title, objective=question[:500], question=question[:500],
              level=a["level"], severity=a["severity"], template=a["template"], scope=scope, profile=p["profile"],
              status="proposed", phase="awaiting approval", priority=LEVEL_WEIGHT.get(a["level"], 1) + min(max_over / 30.0, 3),
              created_at=time.time(), created_by=str(by or "")[:80], plan=p, plan_hash=_plan_hash(p["plan"]),
              budget_tokens=p["estimate"]["budget"], reasons=a["reasons"], drill=1 if p["profile"] == "drill" else 0, origin=origin,
              autonomy_level=3 if actions.enabled() else 2, perms=None, max_calls_per_task=models.call_cap())
    db.event(mid, "proposed", "Proposed from {0}: {1}".format(origin, "; ".join(a["reasons"]) or "no reasons"))
    db.event(mid, "plan", "Plan: {0} ({1})".format(p["title"], d["note"]))
    return get(mid)


def _fan_items(scope, rows):
    """The shipments a fan-out examines: the BU's open past-due shipments, longest past due first, at most 25.
    The rest are covered by the Data Analyst's counts over the whole BU."""
    bus = scope.get("bus") or []
    pick = [r for r in rows or [] if r.get("bu") in bus and r.get("step") != "Completed" and (r.get("overdue") or 0) > 0]
    pick.sort(key=lambda r: -(r.get("overdue") or 0))
    return [r["awb"] for r in pick[:plan.FANOUT_MAX]]


def approve(mid, by, profile=None, remove=None, deadline_min=None):
    """Approve the plan as shown, or as the person edited it: tasks removed (never the required ones),
    a profile, a deadline in minutes. The edited graph is validated before anything runs."""
    m = db.one("SELECT * FROM missions WHERE id=?", mid)
    if not m:
        return False, "No such mission."
    if m["status"] != "proposed":
        return False, "This mission is already {0}.".format(m["status"])
    queued = db.one("SELECT COUNT(*) AS n FROM missions WHERE status='queued'")["n"]
    if queued >= MAX_QUEUED:
        return False, "{0} missions are already waiting; try again when one finishes.".format(queued)
    if profile and profile in models.PROFILES:
        m["profile"] = profile
    p = m.get("plan") or {}
    skip = list(remove or []) + list((p.get("planner") or {}).get("skip") or [])
    tasks = plan.build(m["template"], (m.get("scope") or {}).get("items"), skip)
    problems = plan.validate(tasks)
    if problems:
        db.event(mid, "challenged", "The plan was refused: " + "; ".join(problems))
        return False, "The plan cannot run: " + "; ".join(problems)
    for i, t in enumerate(tasks):
        db.insert("tasks", mission_id=mid, id=t["id"], objective=t["objective"], specialist=t["specialist"],
                  needs=[SPECIALISTS[t["specialist"]]["needs"]], depends_on=t["depends_on"], priority=len(tasks) - i,
                  status="queued", timeout_s=TASK_TIMEOUT_S, idempotent=1, attempts=0, params=t["params"],
                  dedupe_key=t["id"] if not t["params"] else "{0}:{1}".format(t["specialist"], t["params"].get("awb")))
    try:
        minutes = float(deadline_min) if deadline_min else plan.DEADLINE_MIN.get(m["level"], 15)
    except (TypeError, ValueError):
        minutes = plan.DEADLINE_MIN.get(m["level"], 15)
    minutes = max(2.0, min(minutes, 240.0))
    db.update("missions", {"id": mid}, status="queued", phase="waiting for capacity", approved_by=str(by or "")[:80],
              approved_at=time.time(), profile=m["profile"], drill=1 if m["profile"] == "drill" else 0,
              deadline_at=time.time() + minutes * 60)
    removed = sorted(set(remove or []) & set(plan.removable(m["template"])))
    db.event(mid, "approved", "Approved by {0} (plan {1}, profile {2}, deadline {3:.0f} min{4})".format(
        by or "someone", m["plan_hash"], m["profile"], minutes, "; removed: " + ", ".join(removed) if removed else ""))
    start()
    with _cond:
        _cond.notify_all()
    return True, "Mission started."


def decline(mid, by):
    m = db.one("SELECT * FROM missions WHERE id=?", mid)
    if not m or m["status"] != "proposed":
        return False, "Nothing to decline."
    classify.decline(m["fingerprint"], by)
    db.update("missions", {"id": mid}, status="declined", phase="declined", ended_at=time.time())
    db.event(mid, "declined", "Declined by {0}; ATLAS will not propose it again for 24 hours".format(by or "someone"))
    return True, "Understood. ATLAS continues normally."


def control(mid, action, by):
    m = db.one("SELECT * FROM missions WHERE id=?", mid)
    if not m:
        return False, "No such mission."
    if action == "cancel" and m["status"] in LIVE + ("proposed",):
        db.update("missions", {"id": mid}, status="cancelled", phase="cancelled", ended_at=time.time())
        with db._lock:
            db.db().execute("UPDATE tasks SET status='cancelled' WHERE mission_id=? AND status='queued'", (mid,))
    elif action == "pause" and m["status"] in ("queued", "running"):
        db.update("missions", {"id": mid}, status="paused", phase="paused")
    elif action == "resume" and m["status"] == "paused":
        db.update("missions", {"id": mid}, status="queued", phase="resuming")
    else:
        return False, "Cannot {0} a {1} mission.".format(action, m["status"])
    db.event(mid, action, "{0} by {1}".format(action.capitalize(), by or "someone"))
    with _cond:
        _cond.notify_all()
    return True, "Done."


# ── the workload manager ──────────────────────────────────────────────────
def _promote():
    running = db.one("SELECT COUNT(*) AS n FROM missions WHERE status='running'")["n"]
    if running >= MAX_ACTIVE:
        return
    for m in db.many("SELECT id FROM missions WHERE status='queued' ORDER BY priority DESC, approved_at ASC LIMIT ?", MAX_ACTIVE - running):
        db.update("missions", {"id": m["id"]}, status="running", phase="investigating", started_at=time.time())
        db.event(m["id"], "running", "Started")


def _watchdog(now):
    """Deadlines: a task running past its timeout fails (its late result is discarded); a mission past its
    deadline stops starting new work, and its report is written from what is done, said plainly."""
    for t in db.many("SELECT mission_id, id, started_at, timeout_s, specialist FROM tasks WHERE status='running'"):
        if t.get("started_at") and now - t["started_at"] > (t.get("timeout_s") or TASK_TIMEOUT_S):
            db.update("tasks", {"mission_id": t["mission_id"], "id": t["id"]}, status="failed", ended_at=now,
                      error="timed out after {0:.0f} s".format(t.get("timeout_s") or TASK_TIMEOUT_S))
            db.event(t["mission_id"], "task_failed", "{0}: timed out; the mission continues without it".format(
                SPECIALISTS.get(t["specialist"], {}).get("title", t["id"])))
            _finish_if_done(t["mission_id"])
    for m in db.many("SELECT id, deadline_at FROM missions WHERE status='running' AND deadline_at IS NOT NULL AND deadline_at < ?", now):
        left = db.many("SELECT id FROM tasks WHERE mission_id=? AND status='queued' AND specialist != 'report'", m["id"])
        if left:
            with db._lock:
                db.db().execute("UPDATE tasks SET status='cancelled', error='mission deadline' WHERE mission_id=? AND status='queued' "
                                "AND specialist != 'report'", (m["id"],))
            db.event(m["id"], "deadline", "Deadline reached: {0} step(s) not started; the report is written from what is done".format(len(left)))
            _finish_if_done(m["id"])


def _next(worker):
    """The highest-priority ready task, marked running; None when there is none."""
    with db._lock:
        _watchdog(time.time())
        _promote()
        if automation_busy() and worker > 0:
            return None                                    # the ATA run keeps the machine: one worker only
        now = time.time()
        best = None
        for m in db.many("SELECT * FROM missions WHERE status='running'"):
            tasks = {t["id"]: t for t in db.many("SELECT * FROM tasks WHERE mission_id=?", m["id"])}
            for t in tasks.values():
                if t["status"] != "queued":
                    continue
                deps = [tasks.get(d) for d in (t.get("depends_on") or [])]
                if any(d is None or d["status"] in ("queued", "running") for d in deps):
                    continue
                age = (now - (m.get("approved_at") or now)) / 600.0      # ageing: +1 per 10 minutes waited
                score = (m.get("priority") or 0) * 10 + (t.get("priority") or 0) + age
                if best is None or score > best[0]:
                    best = (score, m, t)
        if best is None:
            return None
        _, m, t = best
        db.update("tasks", {"mission_id": m["id"], "id": t["id"]}, status="running", started_at=now, attempts=(t.get("attempts") or 0) + 1)
        db.update("missions", {"id": m["id"]}, phase=SPECIALISTS[t["specialist"]]["title"])
        return m, dict(t, attempts=(t.get("attempts") or 0) + 1)


def _store_evidence(m, t, items, model):
    kept = 0
    for e in items or []:
        if not e.get("source") or not e.get("claim"):
            db.event(m["id"], "challenged", "A finding without a source was rejected: {0}".format(str(e.get("claim"))[:120]))
            continue
        eid = hashlib.sha1("{0}|{1}|{2}".format(m["id"], t["id"], e["claim"]).encode("utf-8")).hexdigest()[:12]
        db.insert("evidence", id=eid, mission_id=m["id"], task_id=t["id"], claim=e["claim"], **{"class": e["class"]},
                  source=e["source"], record=e.get("record"), captured_at=time.time(), freshness=e.get("freshness"),
                  agent=SPECIALISTS[t["specialist"]]["title"], model=model, verification=e.get("verification") or "unverified",
                  basis=e.get("basis"), identifiers=e.get("identifiers") or [], data=e.get("data") or {})
        kept += 1
    return kept


def _run(m, t):
    fn = specialists.FUNCS[t["specialist"]]
    try:
        ctx = Ctx(m, t)
        res = fn(ctx) or {}
        # One step under the store's lock: still running (the watchdog may have failed it), its evidence,
        # replanning (before "done": verification cannot start until the plan is settled), then done.
        with db._lock:
            cur = db.one("SELECT status FROM tasks WHERE mission_id=? AND id=?", m["id"], t["id"])
            if not cur or cur["status"] != "running":
                db.event(m["id"], "challenged", "{0} finished after its deadline; its result was discarded".format(SPECIALISTS[t["specialist"]]["title"]))
                return
            n = _store_evidence(m, t, res.get("evidence"), res.get("model"))
            if t["specialist"] == "hypotheses":
                _replan(m, res.get("out") or {})
            db.update("tasks", {"mission_id": m["id"], "id": t["id"]}, status="done", ended_at=time.time(),
                      summary=res.get("summary") or "", output={"out": res.get("out") or {}, "model": res.get("model")},
                      model=res.get("model"))
        db.event(m["id"], "task", "{0}: {1}{2}".format(SPECIALISTS[t["specialist"]]["title"], res.get("summary") or "done",
                                                      " · {0} finding(s)".format(n) if n else ""))
        if t["specialist"] == "risk":
            _maybe_escalate(m, res.get("out") or {})
    except Exception as error:
        retry = (t.get("attempts") or 1) < 2 and t.get("idempotent")
        if not db.transition("tasks", {"mission_id": m["id"], "id": t["id"]}, "running", status="queued" if retry else "failed",
                             ended_at=time.time(), error=type(error).__name__ + ": " + str(error)[:200]):
            return
        db.event(m["id"], "task_failed", "{0}: {1}{2}".format(SPECIALISTS[t["specialist"]]["title"], type(error).__name__,
                                                              " — retrying once" if retry else " — the mission continues without it"))
    _finish_if_done(m["id"])


def _maybe_escalate(m, out):
    if m["level"] == "CRITICAL_INCIDENT":
        return
    if any(g == "High" for g in (out.get("grades") or {}).values()) and (m.get("severity") in ("CRITICAL", "SEVERE")):
        esc = {"suggested": "CRITICAL_INCIDENT", "why": "the records show high exposure at critical severity"}
        db.update("missions", {"id": m["id"]}, escalation=esc)
        db.event(m["id"], "escalation", "Escalation to Emergency Mode suggested: " + esc["why"])


def _replan(m, out):
    """One replanning rule: when the records support a tracking or extraction cause and the plan has no
    date reconciliation, the Commander adds one before verification (at most MAX_REPLANS times)."""
    hyp = {h["id"]: h for h in out.get("hypotheses") or []}
    if not any(hyp.get(k, {}).get("status") == "supported" for k in ("tracking", "extract")):
        return
    tasks = {t["id"]: t for t in db.many("SELECT id, status, depends_on FROM tasks WHERE mission_id=?", m["id"])}
    if "eta" in tasks or "verify" not in tasks or tasks["verify"]["status"] != "queued":
        return
    n = db.one("SELECT COUNT(*) AS n FROM events WHERE mission_id=? AND kind='replan'", m["id"])["n"]
    if n >= MAX_REPLANS:
        return
    deps = ["facts"] if "facts" in tasks else []
    db.insert("tasks", mission_id=m["id"], id="eta", objective="Compare the export's dates with the carrier's (added by replanning)",
              specialist="eta", needs=["records"], depends_on=deps, priority=50, status="queued", timeout_s=TASK_TIMEOUT_S,
              idempotent=1, attempts=0, params={}, dedupe_key="eta")
    db.update("tasks", {"mission_id": m["id"], "id": "verify"}, depends_on=list(tasks["verify"].get("depends_on") or []) + ["eta"])
    db.event(m["id"], "replan", "Replanned: a tracking or extraction cause is supported, so the carrier's and eHub's dates are compared before verification")


def _finish_if_done(mid):
    tasks = db.many("SELECT status, id, output FROM tasks WHERE mission_id=?", mid)
    if any(t["status"] in ("queued", "running") for t in tasks):
        return
    m = db.one("SELECT status FROM missions WHERE id=?", mid)
    if not m or m["status"] in ("cancelled",):
        return
    cancelled = [t["id"] for t in tasks if t["status"] == "cancelled"]
    rep = next((((t.get("output") or {}).get("out") or {}).get("report") for t in tasks if t["id"] == "report"), None) \
        if any(t["id"] == "report" and t["status"] == "done" for t in tasks) else None
    if m["status"] not in LIVE:
        return
    failed = [t["id"] for t in tasks if t["status"] == "failed"]
    if rep:
        if failed:
            rep["limits"] = rep.get("limits", []) + ["These steps failed and are missing from the report: {0}.".format(", ".join(failed))]
        if cancelled:
            rep["limits"] = rep.get("limits", []) + ["The deadline stopped these steps before they ran: {0}.".format(", ".join(cancelled))]
        status = "done" if rep.get("grade") in ("Strong", "Moderate") else "insufficient"
        # the report and its action cards first, the final status last: a finished mission never shows up
        # without the approvals it needs
        db.update("missions", {"id": mid}, report=rep, grade=rep.get("grade"), phase="preparing next steps")
        full = db.one("SELECT * FROM missions WHERE id=?", mid)
        try:
            acts = rep.get("actions") or []
            if rep.get("grade") in ("Strong", "Moderate"):     # actions only from a checked finding
                actions.from_report(full, acts)
            elif rep.get("contradictions") and not rep.get("mismatches") and any(a.get("kind") == "reprocess" for a in acts):
                # the carrier and eHub disagree: a fresh, read-back lookup is how that is settled, nothing else
                actions.from_report(full, [a for a in acts if a.get("kind") == "reprocess"])
                db.event(mid, "challenged", "The sources disagree: only a re-run (which reads the Hub back) is proposed")
            elif any(a.get("kind") for a in acts):
                db.event(mid, "challenged", "No action cards: the finding is {0}, not strong enough to act on".format(rep.get("grade")))
        except Exception as error:
            db.event(mid, "challenged", "Action cards could not be prepared ({0})".format(type(error).__name__))
        db.update("missions", {"id": mid}, status=status, phase="report ready", ended_at=time.time())
        db.event(mid, "done", "Report ready: confidence {0}".format(rep.get("grade")))
        try:
            memory.record(full, rep)
        except Exception:
            pass
    else:
        db.update("missions", {"id": mid}, status="failed", phase="failed", ended_at=time.time())
        db.event(mid, "failed", "The mission could not produce a report ({0} failed)".format(", ".join(failed) or "no report step"))


_tick = {"at": 0.0}


def _worker(i):
    while True:
        if i == 0 and time.time() - _tick["at"] > 20:
            _tick["at"] = time.time()
            try:
                actions.verify_pending()           # the Action Verifier reads outcomes back
            except Exception:
                pass
        try:
            job = _next(i)
        except Exception:
            job = None
        if job is None:
            with _cond:
                _cond.wait(1.0)
            continue
        _run(*job)
        with _cond:
            _cond.notify_all()


def recover():
    """After a restart: running tasks are reads, so they are queued again; missions carry on."""
    with db._lock:
        rows = db.many("SELECT mission_id, id FROM tasks WHERE status='running'")
        for t in rows:
            db.update("tasks", {"mission_id": t["mission_id"], "id": t["id"]}, status="queued")
        for m in db.many("SELECT id FROM missions WHERE status='running'"):
            db.update("missions", {"id": m["id"]}, status="queued", phase="resuming after a restart")
            db.event(m["id"], "recovered", "Resumed after a restart; finished work and evidence kept")
        actions.recover()
    return len(rows)


def start():
    with _cond:
        if _started["on"]:
            return
        _started["on"] = True
    recover()
    for i in range(max(1, WORKERS)):
        th = threading.Thread(target=_worker, args=(i,), name="mission-worker-%d" % i, daemon=True)
        th.start()
        _threads.append(th)


def wait(mid, timeout=60):
    """Tests and drills: block until the mission is no longer live."""
    end = time.time() + timeout
    while time.time() < end:
        m = db.one("SELECT status FROM missions WHERE id=?", mid)
        if m and m["status"] not in LIVE:
            return m["status"]
        time.sleep(0.1)
    return None


# ── reading ───────────────────────────────────────────────────────────────
def get(mid):
    m = db.one("SELECT * FROM missions WHERE id=?", mid)
    if not m:
        return None
    tasks = db.many("SELECT * FROM tasks WHERE mission_id=?", mid)
    order = [x[0] for x in TEMPLATES.get(m["template"], {}).get("tasks", [])]
    fan = (TEMPLATES.get(m["template"], {}).get("fanout") or {}).get("task")

    def pos(t):
        if t["id"] in order:
            return (order.index(t["id"]), "")
        if fan and t["specialist"] == "item":
            return (order.index(fan), t["id"])
        if t["id"] == "eta" and "verify" in order:
            return (order.index("verify") - 0.5, "")
        return (99, t["id"])
    tasks.sort(key=pos)
    for t in tasks:
        t["title"] = SPECIALISTS.get(t["specialist"], {}).get("title")
        out = t.pop("output", None) or {}
        t["model"] = out.get("model") or t.get("model")
    hyp_row = db.one("SELECT output FROM tasks WHERE mission_id=? AND id='hyp'", mid)
    hyp = (((hyp_row or {}).get("output") or {}).get("out") or {}).get("hypotheses") or []
    ev = db.many("SELECT id, task_id, claim, class, source, record, freshness, agent, model, verification, basis, identifiers, captured_at "
                 "FROM evidence WHERE mission_id=? AND status='kept' ORDER BY captured_at", mid)
    led = models.ledger(mid, 200)
    by_agent = {}
    titles = {t["id"]: t["title"] for t in tasks}
    for r in led:
        who = titles.get(r.get("task_id")) or r.get("purpose") or "mission"
        a = by_agent.setdefault(who, {"calls": 0, "failed": 0, "usage": 0, "cost_estimate": 0.0, "models": []})
        a["calls" if r.get("status") == "ok" else "failed"] += 1
        a["usage"] += (r.get("usage_in") or 0) + (r.get("usage_out") or 0)
        a["cost_estimate"] = round(a["cost_estimate"] + (r.get("cost_estimate") or 0), 6)
        if r.get("model_id") not in a["models"]:
            a["models"].append(r.get("model_id"))
    v = ((db.one("SELECT output FROM tasks WHERE mission_id=? AND id='verify'", mid) or {}).get("output") or {}).get("out") or {}
    m.update(actions=actions.listing(mid), memory=memory.listing(mid),
             notes=db.many("SELECT at, by, text FROM notes WHERE mission_id=? ORDER BY at", mid),
             conflicts=[{"kind": "contradiction", "text": c} for c in v.get("contradictions") or []] +
                       [{"kind": "mismatch", "text": c} for c in v.get("mismatches") or []],
             usage_by_agent=by_agent, deadline_left=(m["deadline_at"] - time.time()) if m.get("deadline_at") and m["status"] in LIVE else None,
             removable=plan.removable(m["template"]) if m["template"] in TEMPLATES else [],
             similar=memory.similar(m["template"], m.get("scope") or {}, exclude=mid) if m["status"] == "proposed" else [])
    m.update(tasks=tasks, hypotheses=hyp, evidence=ev,
             events=db.many("SELECT at, kind, text FROM events WHERE mission_id=? ORDER BY at", mid)[-80:],
             usage={"model_calls": sum(1 for r in led if r.get("status") == "ok"), "failed_calls": sum(1 for r in led if r.get("status") != "ok"),
                    "tokens": sum((r.get("usage_in") or 0) + (r.get("usage_out") or 0) for r in led),
                    "models": sorted({r.get("model_id") for r in led if r.get("status") == "ok"})},
           progress={"done": sum(1 for t in tasks if t["status"] in ("done", "failed", "cancelled")), "total": len(tasks)})
    return m


def listing(limit=60, allowed=None):
    rows = db.many("SELECT id, title, level, severity, status, phase, created_at, started_at, ended_at, grade, origin, scope, drill, escalation, "
                   "template, deadline_at, verification_status, assigned_to, parent_id, version "
                   "FROM missions ORDER BY created_at DESC LIMIT ?", limit)
    if allowed is not None:                  # a BU-limited person sees missions made inside their BUs only
        rows = [r for r in rows if (r.get("scope") or {}).get("allowed") is not None and set(r["scope"]["allowed"]) <= set(allowed)]
    return rows


def suggest_incidents(data, rows, allowed=None):
    """Emergency Mode, automatic side: a BU over the critical threshold is PROPOSED (never started)."""
    r = classify.rules()
    by_bu = {}
    for x in rows:
        if (allowed is None or x.get("bu") in allowed) and x.get("step") != "Completed" and (x.get("overdue") or 0) > 0:
            by_bu[x.get("bu")] = by_bu.get(x.get("bu"), 0) + 1
    out = []
    for bu, n in sorted(by_bu.items(), key=lambda kv: -kv[1]):
        if n >= r["critical_bu_pastdue"]:
            m = propose("Critical: investigate the past-due backlog in {0} urgently".format(bu), data, rows, allowed, by="ATLAS (automatic)", origin="auto")
            if m:
                out.append(m)
    return out


# ── S9: replay, comparison, handoff, notes, analytics ─────────────────────
def replay(mid, by):
    """A new PROPOSED mission with the same question, scope and template, run against today's records;
    compare() then shows what changed. Nothing runs until it is approved."""
    m = db.one("SELECT * FROM missions WHERE id=?", mid)
    if not m or m["status"] in LIVE + ("proposed",):
        return None, "Only a finished mission can be replayed."
    nid = uuid.uuid4().hex[:10]
    n = db.one("SELECT COUNT(*) AS n FROM missions WHERE parent_id=?", mid)["n"]
    keep = {k: m.get(k) for k in ("title", "objective", "question", "level", "severity", "template", "scope", "profile", "plan",
                                  "plan_hash", "budget_tokens", "reasons", "drill", "autonomy_level", "max_calls_per_task")}
    if (TEMPLATES.get(m["template"]) or {}).get("fanout"):       # today's shipments, not the original list
        from dashboard import clearance as C
        data = _export() or {}
        scope = dict(m.get("scope") or {})
        rows = [r for r in (C.records(data) if data else []) if scope.get("allowed") is None or r.get("bu") in scope["allowed"]]
        scope["items"] = _fan_items(scope, rows)
        keep["scope"] = scope
        keep["plan"] = dict(m.get("plan") or {}, items=scope["items"])
    db.insert("missions", id=nid, fingerprint="{0}:replay:{1}".format(m["fingerprint"], n + 1), status="proposed",
              phase="awaiting approval", priority=m.get("priority") or 1, created_at=time.time(), created_by=str(by or "")[:80],
              origin="replay", parent_id=mid, **keep)
    db.event(nid, "proposed", "Replay of mission {0} against today's records, proposed by {1}".format(mid, by))
    db.event(mid, "replay", "Replayed as {0} by {1}".format(nid, by))
    return get(nid), "Replay proposed: approve it to run."


def compare(a_id, b_id):
    out = {}
    for key, mid in (("a", a_id), ("b", b_id)):
        m = get(mid)
        if not m:
            return None
        rep = m.get("report") or {}
        out[key] = {"id": mid, "status": m["status"], "grade": m.get("grade"), "created_at": m["created_at"],
                    "supported": sorted(h["label"] for h in rep.get("hypotheses") or [] if h.get("status") == "supported"),
                    "facts": len(rep.get("facts") or []), "evidence": len(m.get("evidence") or [])}
    out["changed"] = {"grade": out["a"]["grade"] != out["b"]["grade"],
                      "new_causes": sorted(set(out["b"]["supported"]) - set(out["a"]["supported"])),
                      "gone_causes": sorted(set(out["a"]["supported"]) - set(out["b"]["supported"]))}
    return out


def assign(mid, to, by):
    m = db.one("SELECT id FROM missions WHERE id=?", mid)
    to = " ".join(str(to or "").split())[:80]
    if not m or not to:
        return False, "Say who takes it over."
    db.update("missions", {"id": mid}, assigned_to=to)
    db.event(mid, "handoff", "Handed over to {0} by {1}".format(to, by))
    return True, "Handed over to {0}.".format(to)


def note(mid, by, text):
    text = " ".join(str(text or "").split())[:1000]
    if not text or not db.one("SELECT id FROM missions WHERE id=?", mid):
        return False, "Write a note."
    db.insert("notes", mission_id=mid, at=time.time(), by=str(by or "")[:80], text=text)
    db.bump(mid)
    return True, "Note added."


def analytics(allowed=None):
    """Counts from the mission store and the ledger only."""
    ms = listing(1000, allowed)
    ids = {m["id"] for m in ms}
    by = lambda key: dict(sorted(((k, sum(1 for m in ms if m.get(key) == k)) for k in {m.get(key) for m in ms}), key=lambda kv: -kv[1]))
    durations = sorted(m["ended_at"] - m["started_at"] for m in ms if m.get("ended_at") and m.get("started_at"))
    acts = [a for a in actions.listing() if a["mission_id"] in ids]
    executed = [a for a in acts if a.get("executed_at")]
    led = [r for r in models.ledger(None, 20000) if r.get("mission_id") in ids]
    return {"missions": len(ms), "by_status": by("status"), "by_template": by("template"), "by_grade": by("grade"),
            "median_minutes_to_report": round(durations[len(durations) // 2] / 60.0, 1) if durations else None,
            "actions": {"total": len(acts), "by_status": dict((s, sum(1 for a in acts if a["status"] == s)) for s in sorted({a["status"] for a in acts})),
                        "executed": len(executed),
                        "approval_compliance": (round(sum(1 for a in executed if a.get("decided_by")) / float(len(executed)), 3) if executed else None),
                        "verified_rate": (round(sum(1 for a in executed if a["status"] in ("verified", "completed")) / float(len(executed)), 3) if executed else None)},
            "model_calls": sum(1 for r in led if r.get("status") == "ok"), "failed_calls": sum(1 for r in led if r.get("status") != "ok"),
            "usage": sum((r.get("usage_in") or 0) + (r.get("usage_out") or 0) for r in led),
            "shared_reads": dict(tools_stats())}


def tools_stats():
    from . import tools
    return tools.STATS
