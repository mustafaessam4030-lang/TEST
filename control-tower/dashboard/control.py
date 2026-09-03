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
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

MAX_QUEUE = 50
MAX_HISTORY = 60


class ControlChannel:
    def __init__(self):
        self._lock = threading.RLock()
        self.enabled = False          # set by update_eta.py
        self.paused = False
        self.stop_requested = False
        self.reprocess = deque(maxlen=MAX_QUEUE)
        self.history = deque(maxlen=MAX_HISTORY)
        self.version = 0
        # When the supervisor owns the dashboard, the automation runs in a
        # separate process — so requests travel through a small file rather
        # than shared memory. Same queue semantics either way.
        self.file = os.environ.get("CT_CONTROL_FILE") or None

    # -- configuration -----------------------------------------------------

    def configure(self, enabled):
        with self._lock:
            self.enabled = bool(enabled)
            self.version += 1

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
            self.version += 1

    def snapshot(self):
        with self._lock:
            return {
                "enabled": self.enabled,
                "paused": self.paused,
                "stopping": self.stop_requested,
                "queued": list(self.reprocess),
                "history": list(self.history),
            }


control = ControlChannel()
