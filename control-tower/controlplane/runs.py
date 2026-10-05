"""
Runs, workers and the commands between them — the control plane's record of
what is actually happening.

A run exists here before it exists anywhere else: START creates the record
(status QUEUED) and a start_run command for one worker, in one transaction,
and only if a worker is online and idle and no other run is active. The
worker starts the existing automation with that run id; from then on the
run's state is what the worker reports, stored as it arrives.

    QUEUED -> STARTING -> RUNNING -> COMPLETED | STOPPED | FAILED
       \\-> FAILED_TO_START          \\-> WORKER_DISCONNECTED -> (reconciled)

A disconnected run is reconciled only from what the worker says when it
returns — still running, or ended with an exit code. Nothing here ever marks
a shipment or a run successful on its own.
"""

import os
import threading
import time
import uuid
from datetime import datetime

from .db import dumps, loads, now
from .security import new_token, token_hash, same

ACTIVE = ("QUEUED", "STARTING", "RUNNING", "STOPPING", "WORKER_DISCONNECTED")
ENDED = ("COMPLETED", "STOPPED", "FAILED", "FAILED_TO_START", "INTERRUPTED")
WORKER_STATES = ("ONLINE", "IDLE", "BUSY", "DEGRADED", "OFFLINE")
COMMAND_KINDS = ("start_run", "stop_run", "pause_run", "resume_run", "reprocess",
                 "human", "session_attach", "session_detach")
# A command the worker has not collected in this long is withdrawn.
COMMAND_TTL_S = 120
# Delivered but never answered: offered again after this long, this often.
REDELIVER_S = 20
MAX_ATTEMPTS = 3


class RunError(Exception):
    """A refused run action, with the reason as the dashboard shows it."""

    def __init__(self, message, status=409, run=None):
        Exception.__init__(self, message)
        self.status, self.run = status, run


def new_run_id():
    return "{0:%Y%m%d-%H%M%S}-{1}".format(datetime.now(), os.urandom(3).hex())


def public_run(row):
    if not row:
        return None
    out = dict(row)
    out["options"] = loads(row.get("options"))
    out["summary"] = loads(row.get("summary"))
    out["active"] = row["status"] in ACTIVE
    return out


class Orchestrator(object):
    def __init__(self, db, audit, settings):
        self.db, self.audit, self.settings = db, audit, settings
        self._wake = threading.Condition()
        self._state_version = 0
        self._state_cond = threading.Condition()
        self.on_state = None          # set by the app: claim follow-up

    # -- workers -------------------------------------------------------------

    def add_worker(self, name, by=None):
        """A new worker credential. The token is shown once and stored hashed."""
        worker_id = "w_" + uuid.uuid4().hex[:12]
        token = "{0}.{1}".format(worker_id, new_token())
        self.db.execute(
            "INSERT INTO workers (worker_id, name, token_hash, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (worker_id, str(name or worker_id)[:80], token_hash(token), now(),
             (by or {}).get("user_id")))
        self.audit.record("WORKER_REGISTERED", user=by, target_type="worker",
                          target_id=worker_id, metadata={"name": name})
        return worker_id, token

    def authenticate_worker(self, bearer):
        worker_id = str(bearer or "").split(".")[0]
        if not worker_id.startswith("w_"):
            return None
        row = self.db.one("SELECT * FROM workers WHERE worker_id = ?", (worker_id,))
        if row is None or not row["enabled"] or \
                not same(row["token_hash"], token_hash(bearer)):
            return None
        return row

    def worker_view(self, row):
        stamp = now()
        fresh = bool(row.get("last_heartbeat")) and \
            stamp - row["last_heartbeat"] <= self.settings.worker_offline_s
        state = row["state"] if fresh and row["state"] in WORKER_STATES else "OFFLINE"
        info = loads(row.get("info"))
        return {"worker_id": row["worker_id"], "name": row["name"],
                "enabled": bool(row["enabled"]), "state": state,
                "online": state != "OFFLINE", "version": row.get("version"),
                "last_heartbeat": row.get("last_heartbeat"),
                "heartbeat_age_s": None if not row.get("last_heartbeat")
                else round(stamp - row["last_heartbeat"], 1),
                "current_run_id": row.get("current_run_id"),
                "current_step": row.get("current_step"),
                "browser_state": row.get("browser_state"),
                "host": info.get("host"), "platform": info.get("platform"),
                "edge": info.get("edge"), "problems": info.get("problems") or []}

    def workers(self):
        return [self.worker_view(r) for r in
                self.db.all("SELECT * FROM workers ORDER BY created_at")]

    def heartbeat(self, worker, report):
        """
        One heartbeat. Reconciles a run this worker was carrying if the
        control plane had marked it disconnected.
        """
        state = str(report.get("state") or "IDLE").upper()
        if state not in ("IDLE", "BUSY", "DEGRADED", "ONLINE"):
            state = "DEGRADED"
        runner = report.get("runner") or {}
        info = {"host": str(report.get("host") or "")[:80],
                "platform": str(report.get("platform") or "")[:80],
                "edge": report.get("edge"),
                "problems": [str(p)[:160] for p in (report.get("problems") or [])][:5]}
        current = report.get("current_run_id") or None
        was_offline = self.worker_view(worker)["state"] == "OFFLINE"
        self.db.execute(
            "UPDATE workers SET version = ?, state = ?, last_heartbeat = ?, "
            "current_run_id = ?, current_step = ?, browser_state = ?, info = ? "
            "WHERE worker_id = ?",
            (str(report.get("version") or "")[:40], state, now(), current,
             str(report.get("current_step") or "")[:200] or None,
             str(report.get("browser_state") or "")[:40] or None, dumps(info),
             worker["worker_id"]))
        if was_offline and worker.get("last_heartbeat"):
            self.audit.record("WORKER_RECONNECTED", target_type="worker",
                              target_id=worker["worker_id"],
                              metadata={"current_run_id": current})
        self._reconcile(worker, current, runner, report.get("last_run") or {})

    def _reconcile(self, worker, current, runner, last_run):
        rows = self.db.all("SELECT * FROM runs WHERE worker_id = ? AND status = ?",
                           (worker["worker_id"], "WORKER_DISCONNECTED"))
        for run in rows:
            if current == run["run_id"] and runner.get("running"):
                status, detail = "RUNNING", "The worker reconnected; the run kept going."
            elif last_run.get("run_id") == run["run_id"] and \
                    last_run.get("exit_code") is not None:
                status = "COMPLETED" if last_run["exit_code"] == 0 else "FAILED"
                detail = ("The run ended while the worker was disconnected "
                          "(exit code {0}). Shipment results are as the run "
                          "recorded them.".format(last_run["exit_code"]))
            elif last_run.get("run_id") == run["run_id"] and last_run.get("ended_at"):
                state, _at = self.state_of(run["run_id"])
                reported = str(((state or {}).get("run") or {}).get("status") or "")
                status = "COMPLETED" if reported == "finished" else "INTERRUPTED"
                detail = ("The run ended while the worker was disconnected; its exit "
                          "code is unknown and it last reported '{0}'.".format(
                              reported or "nothing"))
            else:
                status = "INTERRUPTED"
                detail = ("The worker came back without this run. It did not "
                          "finish; shipments it had not completed are looked "
                          "up again next run.")
            ended = None if status == "RUNNING" else now()
            self.db.execute("UPDATE runs SET status = ?, detail = ?, ended_at = ? "
                            "WHERE run_id = ? AND status = ?",
                            (status, detail, ended, run["run_id"], "WORKER_DISCONNECTED"))
            self.audit.record("RUN_RECONCILED", target_type="run", target_id=run["run_id"],
                              run_id=run["run_id"], result=status,
                              metadata={"worker": worker["worker_id"]})
            self._bump()

    def sweep(self):
        """Mark silent workers OFFLINE and their runs WORKER_DISCONNECTED."""
        cutoff = now() - self.settings.worker_offline_s
        for row in self.db.all("SELECT * FROM workers WHERE state <> ? AND "
                               "(last_heartbeat IS NULL OR last_heartbeat < ?)",
                               ("OFFLINE", cutoff)):
            self.db.execute("UPDATE workers SET state = ? WHERE worker_id = ?",
                            ("OFFLINE", row["worker_id"]))
            for run in self.db.all(
                    "SELECT run_id FROM runs WHERE worker_id = ? AND status IN "
                    "(?, ?, ?, ?)", (row["worker_id"], "QUEUED", "STARTING",
                                     "RUNNING", "STOPPING")):
                self.db.execute(
                    "UPDATE runs SET status = ?, detail = ? WHERE run_id = ?",
                    ("WORKER_DISCONNECTED",
                     "The worker stopped calling in at {0:%H:%M:%S}. The run may "
                     "still be going on the worker; this updates when it "
                     "reconnects.".format(datetime.fromtimestamp(
                         row["last_heartbeat"] or now())), run["run_id"]))
                self.audit.record("WORKER_DISCONNECTED", target_type="worker",
                                  target_id=row["worker_id"], run_id=run["run_id"],
                                  result="RUN_MARKED_DISCONNECTED")
                self._bump()
        # Commands nobody collected are withdrawn rather than run late, and
        # so are those delivered MAX_ATTEMPTS times without an answer.
        expired = self.db.all("SELECT command_id, kind, run_id FROM commands WHERE "
                              "(status = ? AND created_at < ?) OR (status = ? AND "
                              "attempts >= ? AND delivered_at < ?)",
                              ("QUEUED", now() - COMMAND_TTL_S, "DELIVERED", MAX_ATTEMPTS,
                               now() - REDELIVER_S))
        for row in expired:
            self.db.execute("UPDATE commands SET status = ?, finished_at = ? WHERE command_id = ?",
                            ("EXPIRED", now(), row["command_id"]))
            if row["kind"] == "start_run":
                self.db.execute("UPDATE runs SET status = ?, ended_at = ?, detail = ? WHERE "
                                "run_id = ? AND status IN (?, ?)",
                                ("FAILED_TO_START", now(), "The worker never confirmed the "
                                 "start.", row["run_id"], "QUEUED", "STARTING"))
                self._bump()

    # -- runs ----------------------------------------------------------------

    def _serialise(self, conn):
        if self.db.engine == "postgres":
            self.db.execute("SELECT pg_advisory_xact_lock(?)", (4242001,), conn)

    def start_run(self, user, options=None, ip=None, worker_id=None):
        """Create the authoritative run and send it to a worker — or refuse."""
        options = {k: v for k, v in (options or {}).items()
                   if k in ("dry_run", "max_records", "max_pages", "target_status")}
        refusal = None
        with self.db.tx() as c:
            self._serialise(c)
            active = None if self.settings.allow_concurrent_runs else self.db.one(
                "SELECT * FROM runs WHERE status IN (?, ?, ?, ?, ?) "
                "ORDER BY created_at DESC", ACTIVE, c)
            candidates = [self.worker_view(w) for w in self.db.all(
                "SELECT * FROM workers WHERE enabled = 1 ORDER BY last_heartbeat DESC",
                (), c)]
            ready = [w for w in candidates if w["state"] in ("IDLE", "ONLINE") and
                     (worker_id is None or w["worker_id"] == worker_id)]
            if active:
                refusal = ("REFUSED_RUN_ACTIVE", RunError(
                    "Run {0} is already {1}. Only one run at a time.".format(
                        active["run_id"], active["status"].lower().replace("_", " ")),
                    409, public_run(active)), active["run_id"])
            elif not ready:
                busy = any(w["state"] == "BUSY" for w in candidates)
                refusal = ("REFUSED_WORKER_UNAVAILABLE", RunError(
                    "Automation worker unavailable." + (" The worker is busy." if busy
                                                        else ""), 503), None)
            if refusal is None:
                worker = ready[0]
                run_id = new_run_id()
                stamp = now()
                self.db.execute(
                    "INSERT INTO runs (run_id, worker_id, status, created_at, created_by, "
                    "created_by_email, options) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, worker["worker_id"], "QUEUED", stamp, user.get("user_id"),
                     user.get("work_email"), dumps(options)), c)
                self._enqueue(worker["worker_id"], "start_run",
                              {"run_id": run_id, "options": options}, run_id, user, c)
                self.audit.record("START_RUN", user=user, target_type="run", target_id=run_id,
                                  run_id=run_id, ip=ip, result="QUEUED",
                                  metadata={"worker": worker["worker_id"],
                                            "options": options}, conn=c)
        if refusal is not None:
            result, error, target = refusal
            self.audit.record("START_RUN", result=result, user=user, target_type="run",
                              target_id=target, run_id=target, ip=ip)
            raise error
        self._notify()
        self._bump()
        return public_run(self.run(run_id))

    def run(self, run_id):
        return self.db.one("SELECT * FROM runs WHERE run_id = ?", (run_id,))

    def runs(self, limit=50):
        return [public_run(r) for r in self.db.all(
            "SELECT * FROM runs ORDER BY created_at DESC LIMIT {0}".format(
                max(1, min(int(limit), 500))))]

    def current_run(self):
        row = self.db.one("SELECT * FROM runs WHERE status IN (?, ?, ?, ?, ?) "
                          "ORDER BY created_at DESC", ACTIVE)
        return row or self.db.one("SELECT * FROM runs ORDER BY created_at DESC")

    def control(self, user, action, run_id=None, reference=None, ip=None, force=False):
        """stop / pause / resume / reprocess for the active run."""
        run = self.run(run_id) if run_id else self.current_run()
        if not run or run["status"] not in ACTIVE:
            raise RunError("No run is in progress.", 409)
        if run["status"] == "WORKER_DISCONNECTED":
            raise RunError("The worker is disconnected; the request cannot reach the "
                           "run until it returns.", 503, public_run(run))
        kind = {"stop": "stop_run", "kill": "stop_run", "pause": "pause_run",
                "resume": "resume_run", "reprocess": "reprocess"}.get(action)
        if kind is None:
            raise RunError("Unknown request.", 400)
        payload = {"run_id": run["run_id"], "force": bool(force or action == "kill")}
        if kind == "reprocess":
            reference = str(reference or "").strip()[:40]
            if not reference:
                raise RunError("Give me a BOL or AWB number to re-run.", 400)
            payload["reference"] = reference
        self._enqueue(run["worker_id"], kind, payload, run["run_id"], user)
        if kind == "stop_run":
            self.db.execute("UPDATE runs SET status = ? WHERE run_id = ? AND status IN (?, ?)",
                            ("STOPPING", run["run_id"], "RUNNING", "STARTING"))
        self.audit.record({"stop_run": "STOP_RUN", "pause_run": "PAUSE_RUN",
                           "resume_run": "RESUME_RUN", "reprocess": "REPROCESS"}[kind],
                          user=user, target_type="run", target_id=run["run_id"],
                          run_id=run["run_id"], ip=ip, result="SENT",
                          metadata={"force": payload.get("force"),
                                    "reference": payload.get("reference")})
        self._notify()
        self._bump()
        return {"stop_run": "Stopping after the current shipment finishes. Everything "
                            "already written to the Hub is kept.",
                "pause_run": "Pausing after the current shipment finishes.",
                "resume_run": "Resuming.",
                "reprocess": "{0} is queued to be re-tracked and updated.".format(
                    payload.get("reference"))}[kind] if not payload.get("force") else \
            "Stopping the automation immediately."

    # -- what the worker reports ---------------------------------------------

    def push_state(self, worker, run_id, state):
        run = self.run(run_id)
        if run is None or run["worker_id"] != worker["worker_id"]:
            return False
        stamp = now()
        self.db.execute(
            "INSERT INTO run_state (run_id, updated_at, state) VALUES (?, ?, ?) "
            "ON CONFLICT (run_id) DO UPDATE SET updated_at = excluded.updated_at, "
            "state = excluded.state", (run_id, stamp, dumps(state)))
        counters = (state or {}).get("counters") or {}
        summary = {k: counters.get(k) for k in (
            "processed", "successful", "failed", "skipped", "needs_human",
            "total", "partial") if k in counters}
        reported = str(((state or {}).get("run") or {}).get("status") or "")
        status = run["status"]
        if status in ("QUEUED", "STARTING") or (
                status == "WORKER_DISCONNECTED" and reported == "running"):
            status = "RUNNING"
        self.db.execute("UPDATE runs SET summary = ?, last_state_at = ?, status = ?, "
                        "started_at = COALESCE(started_at, ?) WHERE run_id = ?",
                        (dumps(summary), stamp, status, stamp, run_id))
        if self.on_state:
            try:
                self.on_state(run_id, state)
            except Exception:
                pass
        self._bump()
        return True

    def run_ended(self, worker, run_id, exit_code, stopped=False, detail=None):
        run = self.run(run_id)
        if run is None or run["worker_id"] != worker["worker_id"] or \
                run["status"] in ENDED:
            return False
        if exit_code is None:
            # The exit code was lost (the agent restarted). The run's own last
            # report decides: "finished" is what the automation itself said.
            state, _at = self.state_of(run_id)
            reported = str(((state or {}).get("run") or {}).get("status") or "")
            status = "COMPLETED" if reported == "finished" else "INTERRUPTED"
            detail = detail or ("The exit code is unknown (the worker agent restarted "
                                "during the run); the run last reported '{0}'.".format(
                                    reported or "nothing"))
        elif run["status"] == "STOPPING" or stopped:
            status = "STOPPED"
        else:
            status = "COMPLETED" if int(exit_code) == 0 else "FAILED"
        self.db.execute("UPDATE runs SET status = ?, ended_at = ?, detail = ? WHERE run_id = ?",
                        (status, now(), str(detail or "")[:400] or
                         "The automation exited with code {0}.".format(exit_code), run_id))
        self._bump()
        return True

    def state_of(self, run_id):
        row = self.db.one("SELECT state, updated_at FROM run_state WHERE run_id = ?",
                          (run_id,))
        return (loads(row["state"]), row["updated_at"]) if row else (None, None)

    # -- commands ------------------------------------------------------------

    def _enqueue(self, worker_id, kind, payload, run_id=None, user=None, conn=None):
        if kind not in COMMAND_KINDS:
            raise ValueError(kind)
        command_id = "c_" + uuid.uuid4().hex[:16]
        self.db.execute(
            "INSERT INTO commands (command_id, worker_id, run_id, kind, payload, status, "
            "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (command_id, worker_id, run_id, kind, dumps(payload), "QUEUED", now(),
             (user or {}).get("user_id")), conn)
        return command_id

    def enqueue(self, worker_id, kind, payload, run_id=None, user=None):
        command_id = self._enqueue(worker_id, kind, payload, run_id, user)
        self._notify()
        return command_id

    def _notify(self):
        with self._wake:
            self._wake.notify_all()

    def take_commands(self, worker, wait_s=20):
        """Long-poll: the worker's queued commands, oldest first, marked delivered."""
        deadline = time.time() + max(0, min(wait_s, 30))
        while True:
            with self.db.tx() as c:
                # A command handed to a connection that then died (the agent
                # restarted mid-poll) never gets a result. It is offered
                # again, a few times; the agent recognises a repeat.
                self.db.execute("UPDATE commands SET status = ? WHERE worker_id = ? AND "
                                "status = ? AND delivered_at < ? AND attempts < ?",
                                ("QUEUED", worker["worker_id"], "DELIVERED",
                                 now() - REDELIVER_S, MAX_ATTEMPTS), c)
                rows = self.db.all("SELECT * FROM commands WHERE worker_id = ? AND "
                                   "status = ? ORDER BY created_at" + self.db.lock_clause,
                                   (worker["worker_id"], "QUEUED"), c)
                for row in rows:
                    self.db.execute("UPDATE commands SET status = ?, delivered_at = ?, "
                                    "attempts = attempts + 1 WHERE command_id = ?",
                                    ("DELIVERED", now(), row["command_id"]), c)
            if rows:
                for row in rows:
                    if row["kind"] == "start_run":
                        self.db.execute("UPDATE runs SET status = ? WHERE run_id = ? AND "
                                        "status = ?", ("STARTING", row["run_id"], "QUEUED"))
                return [{"command_id": r["command_id"], "kind": r["kind"],
                         "run_id": r["run_id"], "payload": loads(r["payload"])}
                        for r in rows]
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            with self._wake:
                self._wake.wait(min(remaining, 1.0))

    def command_result(self, worker, command_id, ok, message=None, detail=None):
        row = self.db.one("SELECT * FROM commands WHERE command_id = ? AND worker_id = ?",
                          (command_id, worker["worker_id"]))
        if row is None:
            return False
        self.db.execute("UPDATE commands SET status = ?, finished_at = ?, result = ? "
                        "WHERE command_id = ?",
                        ("DONE" if ok else "FAILED", now(),
                         dumps({"ok": bool(ok), "message": str(message or "")[:400]}),
                         command_id))
        if row["kind"] == "start_run" and not ok:
            self.db.execute("UPDATE runs SET status = ?, ended_at = ?, detail = ? "
                            "WHERE run_id = ? AND status IN (?, ?)",
                            ("FAILED_TO_START", now(), str(message or "")[:400],
                             row["run_id"], "QUEUED", "STARTING"))
            self._bump()
        return True

    def command(self, command_id):
        row = self.db.one("SELECT * FROM commands WHERE command_id = ?", (command_id,))
        if row:
            row["payload"], row["result"] = loads(row["payload"]), loads(row["result"])
        return row

    # -- change notification for the dashboard stream ------------------------

    def _bump(self):
        with self._state_cond:
            self._state_version += 1
            self._state_cond.notify_all()

    @property
    def version(self):
        return self._state_version

    def wait_change(self, version, timeout):
        with self._state_cond:
            if self._state_version == version:
                self._state_cond.wait(timeout)
            return self._state_version
