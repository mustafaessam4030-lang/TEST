"""
Control Tower supervisor.

The dashboard normally lives inside the automation process, which means no run
= no page. This process inverts that: the dashboard stays up permanently and
the automation becomes something it starts and stops.

    python -m dashboard.supervisor            this machine only
    python -m dashboard.supervisor --share    reachable from other machines

The supervisor never touches shipments. It launches update_eta.py, reads the
state that run publishes to a file, and relays pause/stop/re-run requests back
through a second file. Every safety property of the in-process version holds:
a stop is honoured between shipments, never mid-write.
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from dashboard import server as tower_server
    from dashboard.bridge import bridge
    from dashboard import live_view
    from dashboard.control import (validate_human_request,
                                   human_request_record, human_ok_message,
                                   queue_task)
except ImportError:
    import server as tower_server
    from bridge import bridge
    import live_view
    from control import (validate_human_request, human_request_record,
                         human_ok_message, queue_task)

MAX_HUMAN_REQUESTS = 20

ROOT = Path(__file__).resolve().parent.parent
# The automation this supervisor starts, and where it keeps the files it
# shares with it. Both default to the standard install; ATA_RUNNER_SCRIPT and
# ATA_RUNTIME_DIR exist so a worker can be pointed at another install path,
# and so the platform tests can run a worker as a real separate process.
SCRIPT = Path(os.environ.get("ATA_RUNNER_SCRIPT") or ROOT / "update_eta.py")
RUNTIME = Path(os.environ.get("ATA_RUNTIME_DIR") or ROOT / "dashboard" / ".runtime")
STATE_FILE = RUNTIME / "state.json"
CONTROL_FILE = RUNTIME / "control.json"


class Supervisor:
    """Owns the automation process. One at a time, always."""

    def __init__(self):
        self.process = None
        self.po_sweep = None            # the PO Automation's automatic run, beside the ETA run
        self.started_at = None
        self.last_exit = None
        self.lock = threading.RLock()
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self._write_control({})

    # -- process ----------------------------------------------------------

    def is_running(self):
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def start(self, extra_env=None, po_sweep=True):
        """
        Launch update_eta.py. `extra_env` is how the worker agent hands the
        run its id and its loopback session settings; the supervisor's own
        start passes nothing, exactly as before.

        Beside it — never inside it — the PO Automation's automatic run
        (`python -m po sweep`) starts in its own process and browser, unless
        PO_AUTO=0. The worker agent starts its own (po_sweep=False), so it
        can report the jobs to the control plane.
        """
        with self.lock:
            if self.is_running():
                return False, "A run is already in progress."
            if not SCRIPT.exists():
                return False, "update_eta.py was not found next to the dashboard."

            self._write_control({})
            try:
                STATE_FILE.unlink()
            except Exception:
                pass

            environment = dict(os.environ)
            environment["CT_STATE_FILE"] = str(STATE_FILE)
            environment["CT_CONTROL_FILE"] = str(CONTROL_FILE)
            environment["PYTHONUNBUFFERED"] = "1"
            for key, value in (extra_env or {}).items():
                if str(key).startswith("CT_") and value is not None:
                    environment[str(key)] = str(value)
            if "CT_SESSION_PORT" not in (extra_env or {}):
                # Started here, from the local dashboard: the run gets its own
                # loopback session endpoint, so Open Session can show its
                # paused tab in the operator's browser (live_view.py). The
                # worker agent passes its own instead.
                environment.update(live_view.new_run_env())

            try:
                self.process = subprocess.Popen(
                    [sys.executable, str(SCRIPT)],
                    cwd=str(ROOT), env=environment,
                    stdin=subprocess.DEVNULL,
                )
            except Exception as error:
                return False, "Could not start the automation: {0}".format(error)

            self.started_at = datetime.now()
            if po_sweep:
                self.start_po_sweep()
            return True, "Automation started."

    def start_po_sweep(self, env=None, explicit=False):
        """The PO automatic run, in its own process. (started, message).
        `explicit`: started from the PO page's Start PO Automation, not beside
        an ETA run — PO_AUTO (which governs the latter) does not apply."""
        if not explicit and os.environ.get("PO_AUTO", "1").strip().lower() in (
                "0", "false", "no", "off"):
            return False, "PO_AUTO=0: the PO automatic run is off."
        with self.lock:
            if self.po_sweep is not None and self.po_sweep.poll() is None:
                return False, "The PO automatic run is already going."
            try:
                self.po_sweep = subprocess.Popen(
                    [sys.executable, "-m", "po", "sweep"], cwd=str(ROOT),
                    env=dict(os.environ, PYTHONUNBUFFERED="1", **(env or {})),
                    stdin=subprocess.DEVNULL)
            except Exception as error:
                return False, "The PO automatic run could not start: {0}".format(error)
            return True, ("PO Automation started: reading eHub's Shipments list."
                          if explicit else "The PO automatic run started beside the ETA run.")

    def stop(self, force=False):
        with self.lock:
            if not self.is_running():
                return False, "No run is in progress."

            if force:
                self.process.terminate()
                return True, "Automation stopped immediately."

            # Graceful: ask the run to finish the current shipment and exit.
            data = self._read_control()
            data["stop"] = True
            self._write_control(data)
            return True, ("Stopping after the current shipment finishes. "
                          "Everything already written to the Hub is kept.")

    # -- control file -----------------------------------------------------

    def _read_control(self):
        try:
            return json.loads(CONTROL_FILE.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {}

    def _write_control(self, data):
        try:
            CONTROL_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception:
            pass

    def request(self, action, reference=None):
        if action == "start":
            return self.start()
        if action == "stop":
            return self.stop()
        if action == "kill":
            return self.stop(force=True)

        if not self.is_running():
            return False, "No run is in progress, so there is nothing to {0}.".format(
                action)

        data = self._read_control()
        if action == "pause":
            data["paused"] = True
            self._write_control(data)
            return True, "Pausing after the current shipment finishes."
        if action == "resume":
            data["paused"] = False
            self._write_control(data)
            return True, "Resuming."
        if action == "reprocess":
            cleaned = str(reference or "").strip()
            if not cleaned:
                return False, "Give me a BOL or AWB number to re-run."
            queue = data.setdefault("reprocess", [])
            if cleaned in queue:
                return False, "{0} is already queued.".format(cleaned)
            queue.append(cleaned)
            self._write_control(data)
            return True, "{0} is queued to be re-tracked and updated.".format(cleaned)

        return False, "Unknown request."

    def _published(self):
        try:
            if STATE_FILE.exists():
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return None

    def human_request(self, op, run_id, action_id, client_id=None):
        """
        Relay a human-in-the-loop request to the running automation.

        Checked here against what the run last published, so a stale tab is
        told at once; checked again by the run itself, which owns the truth.
        """
        with self.lock:
            if not self.is_running():
                return False, ("No run is in progress, so the browser session "
                               "that was waiting no longer exists. The "
                               "shipment will be looked up again next run.")
            published = self._published() or {}
            pending = published.get("human_action")
            tasks = published.get("human_queue") or []
            # A choice relayed but not yet picked up by the run counts as
            # taken, so a second tab is refused before the run catches up.
            data = self._read_control()
            for request in data.get("human") or []:
                task = queue_task(tasks, request.get("action_id"))
                if task is not None and \
                        task.get("status") == "WAITING_FOR_HUMAN" and \
                        str(request.get("at") or "") >= str(
                            task.get("updated_at") or ""):
                    task["status"] = "OPERATOR_OPENED"
                    task["claimed_by"] = request.get("client_id") or None
            accepted, message = validate_human_request(
                pending, op, run_id, action_id, client_id, tasks)
            if not accepted:
                return False, message
            queue = data.setdefault("human", [])
            queue.append(human_request_record(op, run_id, action_id, client_id))
            del queue[:-MAX_HUMAN_REQUESTS]
            self._write_control(data)
            live = bool(pending and pending.get("waiting")
                        and str(pending.get("action_id")) == str(action_id))
            return True, human_ok_message(
                op, queued=not live and queue_task(tasks, action_id) is not None)

    # -- state ------------------------------------------------------------

    def snapshot(self):
        """
        The running automation's state, or an honest idle placeholder.

        Nothing here is invented: when no run has happened the counters are
        genuinely zero and the shipment list is genuinely empty.
        """
        running = self.is_running()

        published = None
        try:
            if STATE_FILE.exists():
                published = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            published = None

        if published is None:
            published = bridge.snapshot(trim=True)
            published["run"]["status"] = "idle"

        if not running and published.get("run", {}).get("status") == "running":
            # The process is gone but its last state said running — report the
            # truth rather than a run that is not happening.
            published["run"]["status"] = "finished"

        action = published.get("human_action")
        if not running and isinstance(action, dict) and action.get("waiting"):
            # The run is gone, and its browser with it. Say so instead of
            # offering a Resume that has nothing to resume.
            published["human_action"] = dict(
                action, waiting=False, state="session_lost",
                last_response="The automation process ended while waiting; "
                              "the browser session no longer exists.")

        if not running and isinstance(published.get("human_queue"), list):
            # Parked tasks lived in that run's browser. With the process gone
            # none of them can be opened any more.
            published["human_queue"] = [
                dict(t, status="HUMAN_SESSION_LOST",
                     label="Browser session lost — nothing written")
                if isinstance(t, dict) and t.get("status") not in (
                    "SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST",
                    "VERIFICATION_NOT_CONFIRMED", "FAILED") else t
                for t in published["human_queue"]]

        published["control"] = {
            "enabled": True,
            "supervised": True,
            "running": running,
            "paused": bool(self._read_control().get("paused")),
            "stopping": bool(self._read_control().get("stop")),
            "queued": self._read_control().get("reprocess") or [],
            "history": [],
        }
        return published


supervisor = Supervisor()


def install():
    """Point the dashboard server at the supervisor instead of the bridge."""
    tower_server.build_payload = _payload
    tower_server.control = supervisor      # /api/control calls supervisor.request


def _payload(trim=True, since_cold=None):
    # since_cold: the live stream asks for only what changed since its last
    # frame; the published state is always sent whole here.
    data = supervisor.snapshot()
    data["health"] = tower_server.machine_health()
    data["live_view"] = live_view.available() and supervisor.is_running()
    return data


def main():
    parser = argparse.ArgumentParser(description="Control Tower supervisor")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--share", action="store_true",
                        help="shorthand for --host 0.0.0.0")
    parser.add_argument("--key", default=None,
                        help="access key required in the link (default: the "
                             "DASHBOARD_ACCESS_KEY variable, else this installation's "
                             "generated key — see dashboard/access.py)")
    parser.add_argument("--autostart", action="store_true",
                        help="begin a run immediately on launch")
    args = parser.parse_args()

    # Questions and feedback asked here are real operational data.
    os.environ.setdefault("ATLAS_DATA_ORIGIN", "production")
    install()
    host = "0.0.0.0" if args.share else args.host
    from dashboard import access
    key, source = access.resolve(args.key)
    print(access.explain(source), flush=True)
    tower_server.start(port=args.port, open_browser=True, host=host,
                       access_key=key, learning=True)

    print("The dashboard stays up whether or not a run is in progress.", flush=True)
    print("Use Start and Stop in the dashboard header.", flush=True)

    if args.autostart:
        ok, message = supervisor.start()
        print("Autostart: {0}".format(message), flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        if supervisor.is_running():
            print("\nStopping the automation...", flush=True)
            supervisor.stop(force=True)
        print("Supervisor stopped.", flush=True)


if __name__ == "__main__":
    main()
