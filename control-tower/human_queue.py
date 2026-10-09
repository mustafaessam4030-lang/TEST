"""
Human Action Queue — shipments that need a person, held until one is free.

A carrier page that asks a human to prove they are one does not end the
shipment. The run gives a person who is watching a short window to act at
once; if nobody does, the shipment is PARKED here and the run carries on
with the rest of the list. When an operator chooses the task (Open &
Continue), the run brings that shipment back to the verification point in
its own browser, hands the tab to the person, and — once the page itself
shows the step done and the right shipment — carries on by itself:
extraction, validation, Hub write, read-back.

    WAITING_FOR_HUMAN ─ operator chooses ─> OPERATOR_OPENED
         │                                      │ run re-opens the carrier page
         │                                      v
         │                              VERIFICATION_PENDING ── window ends ──> WAITING_FOR_HUMAN
         │                                      │ the page shows it done
         │                                      v
         │                               HUMAN_COMPLETED
         │                                      │ settle, read the page again
         │                                      v
         │                           POST_VERIFICATION_CHECK ── not confirmed ──> VERIFICATION_PENDING
         │                                      │ confirmed
         │                                      v
         │                                  RESUMING ──> SUCCESS (written and read back)
         │                                           ──> FAILED
         │                                           ──> VERIFICATION_NOT_CONFIRMED
         ├──> TIMEOUT             nobody chose it before it expired
         └──> HUMAN_SESSION_LOST  the run's browser went away

This module only keeps the book. It never touches a page, and nothing in a
task is ever typed anywhere: a task has no field for a code, an answer or a
credential. The person performs the security step in the browser; the run
only reads the resulting page state.

Pure Python, no Playwright, so the state machine is tested on its own.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

WAITING = "WAITING_FOR_HUMAN"
OPENED = "OPERATOR_OPENED"
PENDING = "VERIFICATION_PENDING"
COMPLETED = "HUMAN_COMPLETED"
CHECK = "POST_VERIFICATION_CHECK"
RESUMING = "RESUMING"
SUCCESS = "SUCCESS"
TIMEOUT = "TIMEOUT"
LOST = "HUMAN_SESSION_LOST"
NOT_CONFIRMED = "VERIFICATION_NOT_CONFIRMED"
FAILED = "FAILED"

STATES = (WAITING, OPENED, PENDING, COMPLETED, CHECK, RESUMING, SUCCESS,
          TIMEOUT, LOST, NOT_CONFIRMED, FAILED)
TERMINAL = frozenset((SUCCESS, TIMEOUT, LOST, NOT_CONFIRMED, FAILED))
# An operator may choose only a task nobody is working on.
CHOOSABLE = frozenset((WAITING,))

# Every move the run is allowed to make. Anything else is refused and logged,
# never applied: a task cannot jump from waiting to SUCCESS, and nothing
# leaves a terminal state.
TRANSITIONS = {
    WAITING: frozenset((OPENED, PENDING, COMPLETED, TIMEOUT, LOST, FAILED)),
    # OPENED -> RESUMING: the re-opened carrier page did not ask again.
    OPENED: frozenset((PENDING, COMPLETED, RESUMING, WAITING, TIMEOUT, LOST,
                       FAILED, NOT_CONFIRMED)),
    PENDING: frozenset((COMPLETED, WAITING, TIMEOUT, LOST, FAILED,
                        NOT_CONFIRMED)),
    COMPLETED: frozenset((CHECK, PENDING, LOST, FAILED)),
    CHECK: frozenset((RESUMING, PENDING, NOT_CONFIRMED, LOST, FAILED)),
    # RESUMING -> PENDING / WAITING: the carrier asked a second time.
    RESUMING: frozenset((SUCCESS, FAILED, NOT_CONFIRMED, PENDING, WAITING,
                         LOST)),
}
for _terminal in TERMINAL:
    TRANSITIONS[_terminal] = frozenset()

# Plain words for the dashboard and ATLAS. One place, so the queue card, the
# chat and the audit log never describe the same state three ways.
LABELS = {
    WAITING: "Waiting for you",
    OPENED: "Preparing the carrier page",
    PENDING: "Your verification is needed in the browser",
    COMPLETED: "Verification done — checking the page",
    CHECK: "Checking the shipment page",
    RESUMING: "Continuing automatically",
    SUCCESS: "Written and read back",
    TIMEOUT: "Timed out — nothing written",
    LOST: "Browser session lost — nothing written",
    NOT_CONFIRMED: "Verification not confirmed — nothing written",
    FAILED: "Failed after verification",
}

# What a task may carry. No code, no answer, no credential, no cookie.
PUBLIC_FIELDS = ("action_id", "run_id", "reference", "carrier", "provider",
                 "step", "reason", "created_at", "created_epoch", "timeout_at",
                 "timeout_epoch", "session", "status", "label", "claimed_by",
                 "attempts", "last_detail", "updated_at", "closed_at",
                 "history")
MAX_HISTORY = 16
MAX_TASKS = 200
# The shipment fields the run needs to look the shipment up again. Hub
# identifiers only.
SHIPMENT_FIELDS = ("bol_awb", "carrier", "provider", "current_eta",
                   "table_page")


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fmt(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


class HumanQueue(object):
    """
    Thread-safe book of human tasks for one run. Persisted to `path` (JSON)
    on every change so a restarted dashboard can show what was pending, and
    so the audit trail survives the run.
    """

    def __init__(self, path=None, on_change=None, clock=time.time):
        self._lock = threading.RLock()
        self._tasks = []
        self._shipments = {}            # action_id -> shipment, in memory
        self._chosen = []               # action_ids the run has to pick up
        self.path = Path(path) if path else None
        self.on_change = on_change
        self.clock = clock

    # -- helpers ----------------------------------------------------------

    def _find(self, action_id):
        for task in self._tasks:
            if task["action_id"] == action_id:
                return task
        return None

    def _public(self, task):
        out = {key: task.get(key) for key in PUBLIC_FIELDS}
        out["history"] = list(task.get("history") or [])[-MAX_HISTORY:]
        out["session"] = dict(task.get("session") or {})
        return out

    def _changed(self):
        snapshot = [self._public(task) for task in self._tasks]
        if self.path is not None:
            try:
                tmp = self.path.with_suffix(self.path.suffix + ".tmp")
                tmp.write_text(json.dumps({"tasks": snapshot}, indent=2),
                               encoding="utf-8")
                os.replace(str(tmp), str(self.path))
            except Exception:
                pass
        if self.on_change is not None:
            try:
                self.on_change(snapshot)
            except Exception:
                pass

    def _stamp(self, task, state, detail):
        now = self.clock()
        task["status"] = state
        task["label"] = LABELS.get(state, state)
        task["updated_at"] = _fmt(now)
        if detail:
            task["last_detail"] = str(detail)[:300]
        task.setdefault("history", []).append(
            {"time": _fmt(now), "state": state, "detail": str(detail)[:200]})
        del task["history"][:-MAX_HISTORY]
        if state in TERMINAL:
            task["closed_at"] = _fmt(now)

    # -- the run's side ----------------------------------------------------

    def create(self, run_id, reference, carrier, provider=None, step=None,
               reason="human_verification_required", shipment=None,
               session=None, ttl_s=1800, action_id=None, detail=""):
        """
        Open a task for a shipment that needs a person, or return the one
        already open for it in this run — never two for the same shipment.
        """
        with self._lock:
            existing = self.for_reference(reference, run_id)
            if existing is not None:
                return existing
            now = self.clock()
            task = {
                "action_id": action_id or os.urandom(6).hex(),
                "run_id": str(run_id), "reference": str(reference),
                "carrier": carrier, "provider": provider,
                "step": step or "carrier lookup", "reason": reason,
                "created_at": _fmt(now), "created_epoch": round(now, 1),
                "timeout_at": _fmt(now + ttl_s),
                "timeout_epoch": round(now + ttl_s, 1),
                "session": {k: v for k, v in (session or {}).items()
                            if k in ("page_id", "url")},
                "claimed_by": None, "attempts": 0, "history": [],
                "closed_at": None, "last_detail": None,
            }
            self._stamp(task, WAITING, detail or "{0} needs a person".format(
                carrier or "The carrier page"))
            self._tasks.append(task)
            del self._tasks[:-MAX_TASKS]
            if shipment:
                self._shipments[task["action_id"]] = {
                    k: shipment.get(k) for k in SHIPMENT_FIELDS}
            self._changed()
            return self._public(task)

    def transition(self, action_id, state, detail="", **fields):
        """(applied, task). An illegal move is refused, not forced."""
        with self._lock:
            task = self._find(action_id)
            if task is None or state not in STATES:
                return False, None
            if state == task["status"]:
                if detail:
                    task["last_detail"] = str(detail)[:300]
                    self._changed()
                return True, self._public(task)
            if state not in TRANSITIONS.get(task["status"], ()):
                return False, self._public(task)
            for key in ("session", "claimed_by"):
                if key in fields:
                    task[key] = fields[key]
            if state == PENDING:
                task["attempts"] = int(task.get("attempts") or 0) + 1
            self._stamp(task, state, detail)
            if state in TERMINAL:
                self._shipments.pop(action_id, None)
                if action_id in self._chosen:
                    self._chosen.remove(action_id)
            self._changed()
            return True, self._public(task)

    def choose(self, action_id, run_id, client_id=None):
        """
        An operator asked to handle this task now. (accepted, message, task).
        Only a waiting task of this run, and only by one operator.
        """
        with self._lock:
            task = self._find(action_id)
            if task is None:
                return False, "That human action is not in the queue.", None
            if str(run_id) != task["run_id"]:
                return False, ("That human action belongs to run {0}. Refresh "
                               "the dashboard.".format(task["run_id"])), None
            if task["status"] in TERMINAL:
                return False, "That human action has ended: {0}.".format(
                    LABELS[task["status"]].lower()), self._public(task)
            claimed = task.get("claimed_by")
            if claimed and client_id and claimed != client_id:
                return False, ("Another operator is already handling this "
                               "shipment."), self._public(task)
            if task["status"] not in CHOOSABLE:
                return False, "Already in progress: {0}.".format(
                    LABELS[task["status"]].lower()), self._public(task)
            task["claimed_by"] = client_id or claimed
            self._stamp(task, OPENED, "chosen by the operator; bringing {0} "
                        "back to the verification point".format(
                            task["reference"]))
            if action_id not in self._chosen:
                self._chosen.append(action_id)
            self._changed()
            return True, "OK", self._public(task)

    def take_chosen(self):
        """[(task, shipment)] the operator chose and the run has not yet
        picked up, oldest choice first."""
        with self._lock:
            out = []
            for action_id in self._chosen:
                task = self._find(action_id)
                shipment = self._shipments.get(action_id)
                if task is not None and shipment and task["status"] == OPENED:
                    out.append((self._public(task), dict(shipment)))
            self._chosen = []
            return out

    def requeue(self, action_id, detail=""):
        """Back to WAITING (the operator's window ended); claim released."""
        with self._lock:
            task = self._find(action_id)
            if task is None:
                return False, None
            applied, public = self.transition(action_id, WAITING, detail)
            if applied:
                task["claimed_by"] = None
                self._changed()
                public = self._public(task)
            return applied, public

    def expire(self, now=None):
        """WAITING tasks past their deadline become TIMEOUT. Returns them."""
        now = self.clock() if now is None else now
        expired = []
        with self._lock:
            for task in list(self._tasks):
                if task["status"] == WAITING and \
                        now >= float(task.get("timeout_epoch") or 0):
                    applied, public = self.transition(
                        task["action_id"], TIMEOUT,
                        "nobody chose it before {0}".format(task["timeout_at"]))
                    if applied:
                        expired.append(public)
        return expired

    def close_open(self, state, detail=""):
        """End every open task (run over, browser gone). Returns them."""
        closed = []
        with self._lock:
            for task in list(self._tasks):
                if task["status"] not in TERMINAL:
                    applied, public = self.transition(task["action_id"], state,
                                                      detail)
                    if applied:
                        closed.append(public)
        return closed

    # -- reading -----------------------------------------------------------

    def get(self, action_id):
        with self._lock:
            task = self._find(action_id)
            return self._public(task) if task else None

    def for_reference(self, reference, run_id=None):
        """The open task for this shipment in this run, if any."""
        with self._lock:
            for task in reversed(self._tasks):
                if task["reference"] == str(reference) and \
                        task["status"] not in TERMINAL and \
                        (run_id is None or task["run_id"] == str(run_id)):
                    return self._public(task)
            return None

    def shipment(self, action_id):
        with self._lock:
            found = self._shipments.get(action_id)
            return dict(found) if found else None

    def waiting(self):
        with self._lock:
            return [self._public(t) for t in self._tasks
                    if t["status"] == WAITING]

    def open_tasks(self):
        with self._lock:
            return [self._public(t) for t in self._tasks
                    if t["status"] not in TERMINAL]

    def snapshot(self):
        with self._lock:
            return [self._public(t) for t in self._tasks]

    def clear(self):
        with self._lock:
            self._tasks, self._shipments, self._chosen = [], {}, []
            self._changed()


def summarize(tasks, now=None):
    """
    One line about the queue, from the tasks themselves: how many wait and
    for how long the oldest has. Shared by ATLAS and the queue card.
    """
    now = time.time() if now is None else now
    waiting = [t for t in (tasks or []) if t.get("status") not in TERMINAL]
    if not waiting:
        return "Nothing needs a person right now."
    oldest = min(float(t.get("created_epoch") or now) for t in waiting)
    secs = max(0, int(now - oldest))
    age = "{0} s".format(secs) if secs < 60 else (
        "{0} min".format(secs // 60) if secs < 3600 else
        "{0} h {1} min".format(secs // 3600, (secs % 3600) // 60))
    if len(waiting) == 1:
        t = waiting[0]
        return "1 human action: {0} — {1}, waiting {2}.".format(
            t.get("carrier") or "carrier", t.get("reference"), age)
    return "{0} human actions waiting. The oldest has been waiting {1}.".format(
        len(waiting), age)
