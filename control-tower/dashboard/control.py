"""
Control channel — the one place the dashboard can ask the automation to act.

Everything else in the dashboard is read-only. This module is deliberately
small and deliberately indirect: the dashboard never touches the browser, the
Hub or a shipment. It appends a request to a queue, and the automation decides
— between shipments, at a safe point — whether to honour it.

That indirection is the safety property. A viewer cannot interrupt a shipment
mid-write, and a malformed request cannot corrupt a run in progress.

Disabled unless the operator explicitly turns it on.
"""

import json
import os
import re
import threading
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path

MAX_QUEUE = 50
MAX_HISTORY = 60

# ── Human-in-the-loop requests ────────────────────────────────────────
#
# When a carrier page needs a person (a security code, a "verify you are
# human" check) the run pauses INSIDE the shipment, holding the browser tab
# where it stopped. The dashboard may then ask for exactly two things, and
# only for the action the run itself opened:
#
#     open    bring that paused tab to the front of the run's Edge window
#     resume  the person is done; check the page and carry on
#
# Every request names the run and the action it is for. A stale tab, an old
# run or a second operator cannot steer a wait it was not given. Nothing in
# a request is ever typed into a page: there is no field for it.
HUMAN_OPS = ("open", "resume")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def validate_human_request(pending, op, run_id, action_id, client_id=None):
    """
    (accepted, message) for a human request against the action the run has
    published as pending. Pure; shared by the in-process channel and the
    supervisor so both refuse the same things with the same words.
    """
    if op not in HUMAN_OPS:
        return False, "Unknown human action."
    for name, value in (("run", run_id), ("action", action_id)):
        if not _ID.match(str(value or "")):
            return False, "That request does not name a valid {0}.".format(name)
    if client_id is not None and client_id != "" and \
            not _ID.match(str(client_id)):
        return False, "That request does not come from a valid dashboard tab."
    if pending and not pending.get("waiting") and \
            str(action_id) == str(pending.get("action_id")):
        return False, {
            "session_lost": "The browser session for this shipment is no "
                            "longer available. Nothing was written; the "
                            "shipment is looked up again next run.",
            "timeout": "That human action timed out. Nothing was written; "
                       "the shipment is looked up again next run.",
            "unattended": "That run is unattended; nobody can be asked.",
            "resumed": "That human action has already been resumed.",
        }.get(pending.get("state"), "That human action has ended.")
    if not pending or not pending.get("waiting"):
        return False, "Nothing is waiting for a person right now."
    if str(run_id) != str(pending.get("run_id")):
        return False, ("That request is for run {0}, but run {1} is the one "
                       "waiting. Refresh the dashboard.".format(
                           run_id, pending.get("run_id")))
    if str(action_id) != str(pending.get("action_id")):
        return False, ("That human action is no longer the current one. "
                       "Refresh the dashboard.")
    claimed = pending.get("claimed_by")
    if claimed and client_id and claimed != client_id:
        return False, ("Another operator has taken over this browser session. "
                       "Only they can resume it.")
    return True, "OK"


def human_request_record(op, run_id, action_id, client_id=None):
    return {"id": uuid.uuid4().hex, "op": op, "run_id": str(run_id),
            "action_id": str(action_id), "client_id": str(client_id or ""),
            "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


class ControlChannel:
    def __init__(self):
        self._lock = threading.RLock()
        self.enabled = False          # set by update_eta.py
        self.paused = False
        self.stop_requested = False
        self.reprocess = deque(maxlen=MAX_QUEUE)
        self.history = deque(maxlen=MAX_HISTORY)
        self.version = 0
        # Human-in-the-loop. Allowed independently of `enabled`: these
        # requests can only answer a wait the run itself opened.
        self.human_enabled = True
        self.human = deque(maxlen=MAX_QUEUE)
        self._human_seen = deque(maxlen=200)
        # The action the run is waiting on, as the run published it.
        self.human_pending = None
        # When the supervisor owns the dashboard, the automation runs in a
        # separate process — so requests travel through a small file rather
        # than shared memory. Same queue semantics either way.
        self.file = os.environ.get("CT_CONTROL_FILE") or None

    # -- configuration -----------------------------------------------------

    def configure(self, enabled, human_enabled=None):
        with self._lock:
            self.enabled = bool(enabled)
            if human_enabled is not None:
                self.human_enabled = bool(human_enabled)
            self.version += 1

    # -- human-in-the-loop -------------------------------------------------

    def set_human_pending(self, pending):
        """Called by the run when it opens or closes a human wait."""
        with self._lock:
            self.human_pending = dict(pending) if pending else None
            self.version += 1

    def human_request(self, op, run_id, action_id, client_id=None):
        """(accepted, message). Never raises."""
        with self._lock:
            if not self.human_enabled:
                return False, ("Human actions from the dashboard are switched "
                               "off (DASHBOARD_ALLOW_HUMAN_ACTIONS).")
            accepted, message = validate_human_request(
                self.human_pending, op, run_id, action_id, client_id)
            if not accepted:
                return False, message
            self.human.append(human_request_record(
                op, run_id, action_id, client_id))
            self._record("human_" + op, str(action_id))
            return True, {
                "open": "Asked the run to bring the paused tab to the front "
                        "of its Edge window.",
                "resume": "Resume sent. The run checks the page and carries "
                          "on if the result is there.",
            }[op]

    def take_human_requests(self):
        """Every human request not yet handed to the run, oldest first."""
        with self._lock:
            self._load_file()
            fresh = []
            while self.human:
                request = self.human.popleft()
                if request.get("id") in self._human_seen:
                    continue
                self._human_seen.append(request.get("id"))
                fresh.append(request)
            if fresh:
                self.version += 1
            return fresh

    def _record(self, action, detail=""):
        self.history.appendleft({
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "detail": detail,
        })
        self.version += 1

    # -- requests from the dashboard --------------------------------------

    def request(self, action, reference=None):
        """
        Returns (accepted, message). Never raises.

        Requests are only accepted when control is enabled; otherwise the
        caller is told plainly rather than the request being dropped.
        """
        with self._lock:
            if not self.enabled:
                return False, ("Control is switched off. Set "
                               "DASHBOARD_ALLOW_CONTROL = True in update_eta.py "
                               "to allow the dashboard to pause, resume, stop or "
                               "re-run shipments.")

            if action == "pause":
                if self.paused:
                    return False, "The run is already paused."
                self.paused = True
                self._record("pause")
                return True, ("Pausing after the current shipment finishes. "
                              "Nothing is interrupted mid-write.")

            if action == "resume":
                if not self.paused:
                    return False, "The run is not paused."
                self.paused = False
                self._record("resume")
                return True, "Resuming."

            if action == "stop":
                if self.stop_requested:
                    return False, "A stop has already been requested."
                self.stop_requested = True
                self.paused = False
                self._record("stop")
                return True, ("Stopping after the current shipment finishes. "
                              "Results already written to the Hub are kept.")

            if action == "reprocess":
                cleaned = str(reference or "").strip()
                if not cleaned:
                    return False, "Give me a BOL or AWB number to re-run."
                if len(cleaned) > 40:
                    return False, "That does not look like a shipment reference."
                if cleaned in self.reprocess:
                    return False, "{0} is already queued to be re-run.".format(cleaned)
                self.reprocess.append(cleaned)
                self._record("reprocess", cleaned)
                return True, ("{0} is queued. The automation will re-track it and "
                              "update the Hub after the current shipment."
                              .format(cleaned))

            return False, "Unknown request."

    # -- consumed by the automation ---------------------------------------

    def _load_file(self):
        """Merge requests written by the supervisor process."""
        if not self.file:
            return
        try:
            path = Path(self.file)
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return
        if data.get("paused") is not None:
            self.paused = bool(data["paused"])
        if data.get("stop"):
            self.stop_requested = True
        for reference in data.get("reprocess") or []:
            if reference not in self.reprocess:
                self.reprocess.append(reference)
        for request in data.get("human") or []:
            if isinstance(request, dict) and \
                    request.get("id") not in self._human_seen and \
                    all(r.get("id") != request.get("id") for r in self.human):
                self.human.append(request)

    def take_reprocess(self):
        """Pop the next queued reference, or None."""
        with self._lock:
            self._load_file()
            if not self.reprocess:
                return None
            reference = self.reprocess.popleft()
            self.version += 1
            return reference

    def should_stop(self):
        with self._lock:
            self._load_file()
            return self.stop_requested

    def is_paused(self):
        with self._lock:
            self._load_file()
            return self.paused and not self.stop_requested

    def clear(self):
        """Called when a run ends so the next one starts clean."""
        with self._lock:
            self.paused = False
            self.stop_requested = False
            self.reprocess.clear()
            self.human.clear()
            self.human_pending = None
            self.version += 1

    def snapshot(self):
        with self._lock:
            return {
                "enabled": self.enabled,
                "paused": self.paused,
                "stopping": self.stop_requested,
                "queued": list(self.reprocess),
                "history": list(self.history),
                "human_enabled": self.human_enabled,
            }


control = ControlChannel()
