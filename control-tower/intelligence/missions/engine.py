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

from . import classify, db, models, specialists
from .registry import SPECIALISTS, TEMPLATES

MAX_ACTIVE = int(os.environ.get("ATLAS_MISSIONS_ACTIVE") or 3)
MAX_QUEUED = int(os.environ.get("ATLAS_MISSIONS_QUEUED") or 20)
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

    def focus_rows(self):
        refs, bus = self.scope.get("refs") or [], self.scope.get("bus") or []
        if refs:
            return [r for r in self.rows if r.get("awb") in refs]
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
        return same
    p = classify.proposal(a, profile)
    mid = uuid.uuid4().hex[:10]
    scope = a["scope"]
    title = "{0}: {1}".format(p["title"], ", ".join(scope.get("refs") or scope.get("bus") or []) or "scope")
    max_over = a["signals"].get("max_overdue_days") or 0
    db.insert("missions", id=mid, fingerprint=a["fingerprint"], title=title, objective=question[:500], question=question[:500],
              level=a["level"], severity=a["severity"], template=a["template"], scope=scope, profile=p["profile"],
              status="proposed", phase="awaiting approval", priority=LEVEL_WEIGHT.get(a["level"], 1) + min(max_over / 30.0, 3),
              created_at=time.time(), created_by=str(by or "")[:80], plan=p, plan_hash=_plan_hash(p["plan"]),
              budget_tokens=p["estimate"]["budget"], reasons=a["reasons"], drill=1 if p["profile"] == "drill" else 0, origin=origin)
    db.event(mid, "proposed", "Proposed from {0}: {1}".format(origin, "; ".join(a["reasons"]) or "no reasons"))
    return get(mid)


def approve(mid, by, profile=None):
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
    tpl = TEMPLATES[m["template"]]
    for i, (tid, spec, objective, deps) in enumerate(tpl["tasks"]):
        db.insert("tasks", mission_id=mid, id=tid, objective=objective, specialist=spec, needs=[SPECIALISTS[spec]["needs"]],
                  depends_on=deps, priority=len(tpl["tasks"]) - i, status="queued", timeout_s=120, idempotent=1, attempts=0)
    db.update("missions", {"id": mid}, status="queued", phase="waiting for capacity", approved_by=str(by or "")[:80],
              approved_at=time.time(), profile=m["profile"], drill=1 if m["profile"] == "drill" else 0)
    db.event(mid, "approved", "Approved by {0} (plan {1}, profile {2})".format(by or "someone", m["plan_hash"], m["profile"]))
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


def _next(worker):
    """The highest-priority ready task, marked running; None when there is none."""
    with db._lock:
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
        n = _store_evidence(m, t, res.get("evidence"), res.get("model"))
        db.update("tasks", {"mission_id": m["id"], "id": t["id"]}, status="done", ended_at=time.time(),
                  summary=res.get("summary") or "", output={"out": res.get("out") or {}, "model": res.get("model")},
                  model=res.get("model"))
        db.event(m["id"], "task", "{0}: {1}{2}".format(SPECIALISTS[t["specialist"]]["title"], res.get("summary") or "done",
                                                      " · {0} finding(s)".format(n) if n else ""))
        if t["specialist"] == "risk":
            _maybe_escalate(m, res.get("out") or {})
    except Exception as error:
        retry = (t.get("attempts") or 1) < 2 and t.get("idempotent")
        db.update("tasks", {"mission_id": m["id"], "id": t["id"]}, status="queued" if retry else "failed",
                  ended_at=time.time(), error=type(error).__name__ + ": " + str(error)[:200])
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


def _finish_if_done(mid):
    tasks = db.many("SELECT status, id, output FROM tasks WHERE mission_id=?", mid)
    if any(t["status"] in ("queued", "running") for t in tasks):
        return
    m = db.one("SELECT status FROM missions WHERE id=?", mid)
    if not m or m["status"] in ("cancelled",):
        return
    rep = next(((t.get("output") or {}).get("out") or {}).get("report") for t in tasks if t["id"] == "report") if any(t["id"] == "report" and t["status"] == "done" for t in tasks) else None
    failed = [t["id"] for t in tasks if t["status"] == "failed"]
    if rep:
        if failed:
            rep["limits"] = rep.get("limits", []) + ["These steps failed and are missing from the report: {0}.".format(", ".join(failed))]
        status = "done" if rep.get("grade") in ("Strong", "Moderate") else "insufficient"
        db.update("missions", {"id": mid}, status=status, phase="report ready", ended_at=time.time(), report=rep, grade=rep.get("grade"))
        db.event(mid, "done", "Report ready: confidence {0}".format(rep.get("grade")))
    else:
        db.update("missions", {"id": mid}, status="failed", phase="failed", ended_at=time.time())
        db.event(mid, "failed", "The mission could not produce a report ({0} failed)".format(", ".join(failed) or "no report step"))


def _worker(i):
    while True:
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
    tasks.sort(key=lambda t: order.index(t["id"]) if t["id"] in order else 99)
    for t in tasks:
        t["title"] = SPECIALISTS.get(t["specialist"], {}).get("title")
        out = t.pop("output", None) or {}
        t["model"] = out.get("model") or t.get("model")
    hyp_row = db.one("SELECT output FROM tasks WHERE mission_id=? AND id='hyp'", mid)
    hyp = (((hyp_row or {}).get("output") or {}).get("out") or {}).get("hypotheses") or []
    ev = db.many("SELECT id, task_id, claim, class, source, record, freshness, agent, model, verification, basis, identifiers, captured_at "
                 "FROM evidence WHERE mission_id=? AND status='kept' ORDER BY captured_at", mid)
    led = models.ledger(mid, 200)
    m.update(tasks=tasks, hypotheses=hyp, evidence=ev,
             events=db.many("SELECT at, kind, text FROM events WHERE mission_id=? ORDER BY at", mid)[-80:],
             usage={"model_calls": sum(1 for r in led if r.get("status") == "ok"), "failed_calls": sum(1 for r in led if r.get("status") != "ok"),
                    "tokens": sum((r.get("usage_in") or 0) + (r.get("usage_out") or 0) for r in led),
                    "models": sorted({r.get("model_id") for r in led if r.get("status") == "ok"})},
           progress={"done": sum(1 for t in tasks if t["status"] in ("done", "failed", "cancelled")), "total": len(tasks)})
    return m


def listing(limit=60, allowed=None):
    rows = db.many("SELECT id, title, level, severity, status, phase, created_at, started_at, ended_at, grade, origin, scope, drill, escalation "
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
