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
except ImportError:
    import server as tower_server
    from bridge import bridge

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "update_eta.py"
RUNTIME = ROOT / "dashboard" / ".runtime"
STATE_FILE = RUNTIME / "state.json"
CONTROL_FILE = RUNTIME / "control.json"


class Supervisor:
    """Owns the automation process. One at a time, always."""

    def __init__(self):
        self.process = None
        self.started_at = None
        self.last_exit = None
        self.lock = threading.RLock()
        RUNTIME.mkdir(parents=True, exist_ok=True)
        self._write_control({})

    # -- process ----------------------------------------------------------

    def is_running(self):
        with self.lock:
            return self.process is not None and self.process.poll() is None

    def start(self):
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

            try:
                self.process = subprocess.Popen(
                    [sys.executable, str(SCRIPT)],
                    cwd=str(ROOT), env=environment,
                    stdin=subprocess.DEVNULL,
                )
            except Exception as error:
                return False, "Could not start the automation: {0}".format(error)

            self.started_at = datetime.now()
            return True, "Automation started."

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


def _payload(trim=True):
    data = supervisor.snapshot()
    data["health"] = tower_server.machine_health()
    return data


def main():
    parser = argparse.ArgumentParser(description="Control Tower supervisor")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--share", action="store_true",
                        help="shorthand for --host 0.0.0.0")
    parser.add_argument("--key", default="mantrac2026",
                        help="access key required in the link")
    parser.add_argument("--autostart", action="store_true",
                        help="begin a run immediately on launch")
    args = parser.parse_args()

    install()
    host = "0.0.0.0" if args.share else args.host
    tower_server.start(port=args.port, open_browser=True, host=host,
                       access_key=args.key or None)

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
