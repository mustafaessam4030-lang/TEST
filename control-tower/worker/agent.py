"""
The ATA worker agent — runs on the Windows machine that has Edge, and starts
the existing automation when the control plane says so.

    python -m worker            (reads ATA_CONTROL_PLANE_URL, ATA_WORKER_TOKEN)

It only ever makes OUTBOUND HTTPS requests to the control plane; nothing
listens on the network. It does not duplicate the automation: it drives the
same Supervisor the local Control Tower uses, which launches update_eta.py,
reads the state that run publishes and writes its requests back through the
control file. Every safety property of a local run holds unchanged.

Threads, each one loop:

    heartbeat     every 10 s: state, current run, browser, problems
    commands      long-poll; start / stop / pause / resume / re-run / human
    state         the run's published state, sent when it changes
    watcher       notices the run's process ending and reports the exit code
    intel         ATLAS's records and captures, forwarded to the control plane
    session-*     while a Human Action is open remotely: frames up, input down
    po-*          a PO job (`python -m po process`) in its own process; its
                  record and events go up as they change, and at the end the
                  PDF and the generated document, each with its SHA-256
"""

import base64
import hashlib
import http.client
import json
import os
import platform
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERSION = "1.0.0"
EDGE_PATHS = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")


class ControlPlane(object):
    """A small HTTPS client: keep-alive per thread, bearer token, TLS verified."""

    def __init__(self, url, token, ca_file=None, timeout=40):
        parsed = urllib.parse.urlparse(url.rstrip("/"))
        if parsed.scheme not in ("https", "http"):
            raise ValueError("ATA_CONTROL_PLANE_URL must be https://…")
        self.scheme, self.host, self.port = parsed.scheme, parsed.hostname, parsed.port
        self.base = parsed.path.rstrip("/")
        self.token, self.timeout = token, timeout
        self.context = ssl.create_default_context(cafile=ca_file) if parsed.scheme == "https" \
            else None
        self._local = threading.local()

    def _conn(self, fresh=False):
        conn = getattr(self._local, "conn", None)
        if conn is None or fresh:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            if self.scheme == "https":
                conn = http.client.HTTPSConnection(self.host, self.port or 443,
                                                   timeout=self.timeout, context=self.context)
            else:
                conn = http.client.HTTPConnection(self.host, self.port or 80,
                                                  timeout=self.timeout)
            self._local.conn = conn
        return conn

    def request(self, method, path, body=None, raw=None, headers=None):
        """(status, parsed JSON or bytes, headers). Raises OSError when unreachable."""
        hdrs = {"Authorization": "Bearer " + self.token,
                "User-Agent": "ata-worker/" + VERSION}
        hdrs.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body, default=str).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        elif raw is not None:
            data = raw
            hdrs.setdefault("Content-Type", "application/octet-stream")
        for attempt in (0, 1):
            conn = self._conn(fresh=attempt == 1)
            try:
                conn.request(method, self.base + path, body=data, headers=hdrs)
                response = conn.getresponse()
                payload = response.read()
                kind = response.getheader("Content-Type") or ""
                parsed = json.loads(payload or b"{}") if kind.startswith("application/json") \
                    else payload
                return response.status, parsed, dict(response.getheaders())
            except (http.client.HTTPException, OSError):
                if attempt == 1:
                    raise
        raise OSError("unreachable")


class Local(object):
    """The run's loopback session endpoint (remote_session.py)."""

    def __init__(self, port, token):
        self.port, self.token = port, token

    def request(self, method, path, body=None, timeout=12):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            data = json.dumps(body).encode("utf-8") if body is not None else None
            conn.request(method, path, body=data, headers={
                "X-Session-Token": self.token, "Content-Type": "application/json"})
            response = conn.getresponse()
            payload = response.read()
            return response.status, payload, dict(response.getheaders())
        finally:
            conn.close()


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pid_alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259            # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


class Adopted(object):
    """
    A run that kept going while this agent restarted. It is watched by its
    process id; its exit code cannot be known, and is reported as unknown.
    """

    def __init__(self, pid):
        self.pid, self.returncode = int(pid), None

    def poll(self):
        return None if pid_alive(self.pid) else -1

    def terminate(self):
        try:
            if os.name == "nt":
                import subprocess
                subprocess.run(["taskkill", "/PID", str(self.pid), "/T", "/F"],
                               capture_output=True)
            else:
                os.kill(self.pid, 15)
        except Exception:
            pass


def edge_status():
    for path in EDGE_PATHS:
        if os.path.exists(path):
            return {"found": True, "path": path}
    return {"found": False, "path": None}


def po_store_active():
    from po import store as po_store
    return po_store.ACTIVE_STATES


class Agent(object):
    def __init__(self, cp, supervisor, log=print, heartbeat_s=10, intel=True,
                 runtime=None):
        self.cp, self.sup, self.log = cp, supervisor, log
        self.heartbeat_s = heartbeat_s
        self.intel_on = intel
        self.runtime = Path(runtime or os.environ.get("ATA_RUNTIME_DIR") or
                            ROOT / "dashboard" / ".runtime")
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.stop_event = threading.Event()
        self.run_id = None
        self.local = None
        self.last_run = self._load("last_run.json", {})
        self.stop_requested = False
        self.sessions = {}            # action_id -> threading.Event (stop)
        self.connected = False
        self._lock = threading.Lock()
        self._adopt()

    def _adopt(self):
        """
        A run this worker started before a restart may still be going. If its
        process is alive it is adopted — watched, reported, never doubled.
        """
        last = self.last_run
        if last.get("run_id") and last.get("exit_code") is None and \
                not last.get("ended_at") and pid_alive(last.get("pid")) and \
                not self.sup.is_running():
            self.sup.process = Adopted(last["pid"])
            self.run_id = last["run_id"]
            self.log("[worker] run {0} is still going (pid {1}); watching it".format(
                self.run_id, last["pid"]))
        elif last.get("run_id") and last.get("exit_code") is None and \
                not last.get("ended_at"):
            # It ended while nobody was watching; the exit code is unknown.
            self.last_run = dict(last, ended_at=time.time(), unknown_exit=True)
            self._save("last_run.json", self.last_run)

    # -- persistence ---------------------------------------------------------

    def _load(self, name, default):
        try:
            return json.loads((self.runtime / name).read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save(self, name, data):
        try:
            path = self.runtime / name
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(str(tmp), str(path))
        except Exception:
            pass

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        for name, target in (("heartbeat", self._heartbeat_loop),
                             ("commands", self._command_loop),
                             ("state", self._state_loop),
                             ("watcher", self._watch_loop)) + (
                (("intel", self._intel_loop),) if self.intel_on else ()):
            threading.Thread(target=target, name="ata-" + name, daemon=True).start()

    def stop(self):
        self.stop_event.set()
        for event in list(self.sessions.values()):
            event.set()

    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception as error:
            if self.connected:
                self.log("[worker] control plane unreachable: {0}".format(
                    type(error).__name__))
            self.connected = False
            return None

    # -- heartbeat -----------------------------------------------------------

    def report(self):
        running = self.sup.is_running()
        published = (self.sup._published() or {}) if running else {}
        step = None
        if published:
            step = (published.get("run") or {}).get("step") or \
                (published.get("current") or {}).get("reference")
        problems = []
        script = getattr(sys.modules.get(type(self.sup).__module__), "SCRIPT", None)
        if script is not None and not Path(script).exists():
            problems.append("update_eta.py is missing")
        edge = edge_status()
        if os.name == "nt" and not edge["found"]:
            problems.append("Microsoft Edge was not found")
        state = "BUSY" if running else ("DEGRADED" if problems else "IDLE")
        return {"version": VERSION, "state": state,
                "current_run_id": self.run_id if running else None,
                "runner": {"running": running,
                           "pid": getattr(self.sup.process, "pid", None) if running else None},
                "current_step": step,
                "browser_state": "In use" if running else (
                    "Ready" if edge["found"] or os.name != "nt" else "Not found"),
                "host": socket.gethostname(), "platform": platform.platform()[:80],
                "edge": edge, "problems": problems, "last_run": self.last_run}

    def heartbeat(self):
        status, data, _h = self.cp.request("POST", "/worker/v1/heartbeat", self.report())
        if status == 200:
            if not self.connected:
                self.log("[worker] connected to the control plane")
            self.connected = True
        elif status == 401:
            self.log("[worker] the control plane refused this worker's token")
            self.connected = False
        return status

    def _heartbeat_loop(self):
        while not self.stop_event.is_set():
            self._safe(self.heartbeat)
            self.stop_event.wait(self.heartbeat_s)

    # -- commands ------------------------------------------------------------

    def _command_loop(self):
        while not self.stop_event.is_set():
            result = self._safe(self.cp.request, "GET", "/worker/v1/commands?wait=20")
            if not result or result[0] != 200:
                self.stop_event.wait(3)
                continue
            for command in (result[1] or {}).get("commands") or []:
                ok, message = self._safe(self.execute, command) or (False, "failed")
                self._safe(self.cp.request, "POST",
                           "/worker/v1/commands/{0}/result".format(command["command_id"]),
                           {"ok": bool(ok), "message": message})

    def execute(self, command):
        kind, payload = command.get("kind"), command.get("payload") or {}
        if kind == "start_run":
            return self.start_run(payload.get("run_id"), payload.get("options") or {})
        if kind == "stop_run":
            if payload.get("run_id") != self.run_id:
                return False, "That run is not the one on this worker."
            self.stop_requested = True
            return self.sup.stop(force=bool(payload.get("force")))
        if kind in ("pause_run", "resume_run", "reprocess"):
            if payload.get("run_id") != self.run_id:
                return False, "That run is not the one on this worker."
            return self.sup.request({"pause_run": "pause", "resume_run": "resume",
                                     "reprocess": "reprocess"}[kind],
                                    payload.get("reference"))
        if kind == "human":
            if payload.get("run_id") != self.run_id:
                return False, "That run is not the one on this worker."
            return self.sup.human_request(payload.get("op"), payload.get("run_id"),
                                          payload.get("action_id"),
                                          payload.get("client_id"))
        if kind == "session_attach":
            return self.session_attach(payload.get("action_id"), payload.get("viewer"))
        if kind == "session_detach":
            return self.session_detach(payload.get("action_id"))
        if kind == "po_process":
            return self.po_process(payload.get("po_id"), payload.get("record") or {})
        return False, "Unknown command."

    # -- PO Automation -----------------------------------------------------

    def po_process(self, po_id, record, wait=False):
        """
        Run one PO job here — this machine has the Hub browser — and report it.
        The job never sends email: that is the control plane's, after it has
        the generated document and its hash.
        """
        from po import store as po_store
        if not po_id or record.get("po_id") != po_id:
            return False, "The PO job is incomplete."
        store = po_store.Store(folder=os.environ.get("PO_DATA_DIR") or
                               (self.runtime / "po"))
        if store.get(po_id) is not None:
            return True, "PO job {0} is already here.".format(po_id)
        record = dict(record, state=po_store.QUEUED)
        store.save(record)
        thread = threading.Thread(target=self._po_run, args=(store, po_id), daemon=True,
                                  name="po-" + po_id)
        thread.start()
        if wait:
            thread.join(900)
        return True, "PO job {0} started.".format(po_id)

    def po_store(self):
        from po import store as po_store
        return po_store.Store(folder=os.environ.get("PO_DATA_DIR") or (self.runtime / "po"))

    def start_po_sweep(self):
        """
        The PO automatic run beside the ETA run, in its own process and
        browser: a job for every Under Clearance record that has none. Each
        job it creates is reported to the control plane as it goes; sending
        stays the control plane's (PO_AUTO_SEND is off here).
        """
        if os.environ.get("PO_AUTO", "1").strip().lower() in ("0", "false", "no", "off"):
            return False
        store = self.po_store()
        ok, message = self.sup.start_po_sweep(env={
            "PO_DATA_DIR": str(store.folder), "PO_OUTPUT_DIR": str(store.output_dir),
            "PO_AUTO_SEND": "0"})
        self.log("[worker] " + message)
        if ok:
            threading.Thread(target=self._po_sweep_sync, args=(store, self.sup.po_sweep),
                             daemon=True, name="po-sweep").start()
        return ok

    def _po_sweep_sync(self, store, process):
        """Report the automatic run's jobs while it runs, and in full when it ends."""
        seen = {}
        while True:
            running = process.poll() is None
            for record in store.all(500):
                if (record.get("request") or {}).get("started_by") != "automatic":
                    continue
                if seen.get(record["po_id"]) == record.get("updated") and running:
                    continue
                final = not running or record["state"] not in po_store_active()
                status = self._safe(self._po_push, store, record["po_id"], final)
                if status == 200:
                    seen[record["po_id"]] = record.get("updated")
            if not running:
                break
            time.sleep(2.0)
        self.log("[worker] PO automatic run finished (exit code {0})".format(process.returncode))

    def _po_push(self, store, po_id, files=False):
        record = store.get(po_id)
        if record is None:
            return None
        body = {"record": record, "events": store.events(po_id)}
        if files:
            body["files"] = {}
            path = (record.get("output") or {}).get("path")
            if path and Path(path).is_file():
                body["files"]["output"] = base64.b64encode(Path(path).read_bytes()).decode("ascii")
            sha = (record.get("document") or {}).get("sha256")
            doc = store.folder / "documents" / "{0}.pdf".format(sha)
            if sha and doc.is_file():
                body["files"]["document"] = base64.b64encode(doc.read_bytes()).decode("ascii")
        status, data, _h = self.cp.request("POST", "/worker/v1/po/{0}".format(po_id), body)
        return status

    def _po_run(self, store, po_id):
        import subprocess
        env = dict(os.environ, PO_DATA_DIR=str(store.folder),
                   PO_OUTPUT_DIR=str(store.output_dir), PO_AUTO_SEND="0")
        try:
            process = subprocess.Popen([sys.executable, "-m", "po", "process", "--po-id", po_id],
                                       cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
        except Exception as error:
            self.log("[worker] PO job {0} could not start: {1}".format(po_id, error))
            return
        seen = None
        while process.poll() is None:
            record = store.get(po_id) or {}
            if record.get("updated") != seen:
                seen = record.get("updated")
                self._safe(self._po_push, store, po_id)
            time.sleep(1.0)
        record = store.get(po_id)
        if record and record["state"] in po_store_active():
            from po import pipeline as po_pipeline
            po_pipeline.abandon(store, record, "the PO job process ended (exit code {0})".format(
                process.returncode))
        for _ in range(4):
            status = self._safe(self._po_push, store, po_id, True)
            if status == 200:
                break
            time.sleep(3)
        self.log("[worker] PO job {0} finished: {1}".format(
            po_id, (store.get(po_id) or {}).get("state")))

    def start_run(self, run_id, options):
        with self._lock:
            if self.sup.is_running() and self.run_id == run_id:
                return True, "Run {0} is already going on this worker.".format(run_id)
            if self.last_run.get("run_id") == run_id:
                return False, "Run {0} already ran on this worker.".format(run_id)
            if self.sup.is_running():
                return False, "This worker is already running {0}.".format(self.run_id)
            port, token = _free_port(), secrets.token_urlsafe(32)
            env = {"CT_RUN_ID": run_id, "CT_SESSION_PORT": port, "CT_SESSION_TOKEN": token}
            if options.get("dry_run"):
                env["CT_DRY_RUN"] = "1"
            ok, message = self.sup.start(extra_env=env, po_sweep=False)
            if ok:
                self.run_id, self.stop_requested = run_id, False
                self.local = Local(port, token)
                self.last_run = {"run_id": run_id, "exit_code": None,
                                 "pid": getattr(self.sup.process, "pid", None),
                                 "started_at": time.time()}
                self._save("last_run.json", self.last_run)
                self.log("[worker] run {0} started".format(run_id))
                self.start_po_sweep()
                threading.Thread(target=self._safe, args=(self.heartbeat,),
                                 daemon=True).start()
            return ok, message

    # -- the run's state -------------------------------------------------------

    def _state_loop(self):
        last = None
        while not self.stop_event.is_set():
            run_id = self.run_id
            if run_id and self.sup.is_running():
                published = self.sup._published()
                if published:
                    body = json.dumps(published, sort_keys=True, default=str)
                    digest = hashlib.sha1(body.encode("utf-8")).hexdigest()
                    if digest != last:
                        result = self._safe(self.cp.request, "POST",
                                            "/worker/v1/runs/{0}/state".format(run_id),
                                            published)
                        if result and result[0] == 200:
                            last = digest
            self.stop_event.wait(0.5)

    def _watch_loop(self):
        """Report the run's end — with its exit code — once, until accepted."""
        pending = self.last_run if self.last_run.get("ended_at") and \
            not self.last_run.get("reported") else None
        while not self.stop_event.is_set():
            proc = self.sup.process
            if self.run_id and proc is not None and proc.poll() is not None and \
                    self.last_run.get("run_id") == self.run_id and \
                    not self.last_run.get("ended_at"):
                published = self.sup._published()
                if published:
                    self._safe(self.cp.request, "POST",
                               "/worker/v1/runs/{0}/state".format(self.run_id), published)
                adopted = isinstance(proc, Adopted)
                self.last_run = {"run_id": self.run_id,
                                 "exit_code": None if adopted else proc.returncode,
                                 "unknown_exit": adopted,
                                 "stopped": self.stop_requested, "ended_at": time.time()}
                self._save("last_run.json", self.last_run)
                for event in list(self.sessions.values()):
                    event.set()
                self.log("[worker] run {0} ended (exit code {1})".format(
                    self.run_id, proc.returncode))
                self._safe(self.heartbeat)
                pending = self.last_run
            if pending:
                result = self._safe(self.cp.request, "POST",
                                    "/worker/v1/runs/{0}/ended".format(pending["run_id"]),
                                    {"exit_code": pending["exit_code"],
                                     "stopped": pending.get("stopped")})
                if result and result[0] == 200:
                    pending["reported"] = True
                    self._save("last_run.json", pending)
                    pending = None
            self.stop_event.wait(1.0)

    # -- remote Human Action session -------------------------------------------

    def session_attach(self, action_id, viewer=None):
        if not action_id or self.local is None or not self.sup.is_running():
            return False, "No run on this worker can show that session."
        status, _b, _h = self.local.request("POST", "/session/attach",
                                            {"action_id": action_id, "viewer": viewer})
        if status != 200:
            return False, "The run did not accept the session ({0}).".format(status)
        old = self.sessions.pop(action_id, None)
        if old:
            old.set()
        stop = threading.Event()
        self.sessions[action_id] = stop
        for name, target in (("frames", self._frames), ("input", self._input),
                             ("status", self._status)):
            threading.Thread(target=target, args=(action_id, stop), daemon=True,
                             name="ata-session-" + name).start()
        return True, "Session view attached."

    def session_detach(self, action_id):
        stop = self.sessions.pop(action_id, None)
        if stop:
            stop.set()
        if self.local is not None:
            try:
                self.local.request("POST", "/session/detach", {"action_id": action_id})
            except OSError:
                pass
        return True, "Session view closed."

    def _end_session(self, action_id, stop):
        if not stop.is_set():
            self.session_detach(action_id)

    def _frames(self, action_id, stop):
        seq = 0
        idle_since = time.time()
        while not stop.is_set() and not self.stop_event.is_set():
            try:
                status, data, headers = self.local.request(
                    "GET", "/session/frame?action_id={0}&after={1}&wait=8".format(
                        urllib.parse.quote(action_id), seq))
            except OSError:
                stop.wait(1)
                continue
            if status == 409:
                stop.wait(1)
                continue
            if status != 200:
                continue
            seq = int(headers.get("X-Seq") or seq)
            result = self._safe(self.cp.request, "POST",
                                "/worker/v1/session/{0}/frame".format(action_id),
                                None, data, {"Content-Type": "image/jpeg",
                                             "X-Meta": headers.get("X-Meta") or "{}"})
            if result and result[0] == 200:
                if (result[1] or {}).get("viewer"):
                    idle_since = time.time()
                elif time.time() - idle_since > 90:
                    self._end_session(action_id, stop)      # nobody is watching
                    return
            elif result and result[0] == 404:
                self._end_session(action_id, stop)          # the claim is gone
                return

    def _input(self, action_id, stop):
        while not stop.is_set() and not self.stop_event.is_set():
            result = self._safe(self.cp.request, "GET",
                                "/worker/v1/session/{0}/input?wait=8".format(action_id))
            if not result:
                stop.wait(1)
                continue
            if result[0] == 404:
                self._end_session(action_id, stop)
                return
            body = result[1] or {}
            if body.get("events"):
                try:
                    self.local.request("POST", "/session/input",
                                       {"action_id": action_id, "events": body["events"]})
                except OSError:
                    pass
            if body.get("held") is False:
                self._end_session(action_id, stop)
                return

    def _status(self, action_id, stop):
        last = None
        while not stop.is_set() and not self.stop_event.is_set():
            try:
                status, data, _h = self.local.request("GET", "/session/status")
                view = json.loads(data or b"{}") if status == 200 else {}
            except (OSError, ValueError):
                view = {"attached": False, "streaming": False,
                        "reason": "The run is no longer reachable on the worker."}
            if view != last:
                self._safe(self.cp.request, "POST",
                           "/worker/v1/session/{0}/status".format(action_id), view)
                last = view
            stop.wait(1.0)

    # -- ATLAS learning store ----------------------------------------------------

    def _intel_loop(self):
        while not self.stop_event.is_set():
            self.stop_event.wait(15)
            if self.connected:
                self._safe(self.sync_intel)

    def sync_intel(self):
        """
        Forward new lines of the worker's events and evidence files. Offsets
        are kept on disk and advanced only after the control plane accepted
        the batch, so nothing is lost and a restart resumes where it left.
        """
        from intelligence import store, events, evidence
        marks = self._load("intel_sync.json", {"events": 0, "evidence": 0})
        origin = store.store_origin()
        if origin is None:
            return 0
        sent = 0
        for name, key in ((events.FILE, "events"), (evidence.FILE, "evidence")):
            path = store.folder() / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            offset = int(marks.get(key) or 0)
            if size < offset:
                offset = 0                  # the file was replaced
            if size == offset:
                continue
            with open(path, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read(4 * 1024 * 1024)
            end = chunk.rfind(b"\n")
            if end < 0:
                continue
            lines = chunk[:end + 1].splitlines()
            if key == "events":
                records = []
                for raw in lines:
                    try:
                        record = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    record["sync_id"] = hashlib.sha1(raw).hexdigest()[:20]
                    records.append(record)
                for i in range(0, len(records), 500):
                    status, body, _h = self.cp.request(
                        "POST", "/worker/v1/intel/events",
                        {"origin": origin, "records": records[i:i + 500]})
                    if status != 200:
                        return sent
                    sent += len(records[i:i + 500])
            else:
                for raw in lines:
                    try:
                        entry = json.loads(raw.decode("utf-8"))
                    except Exception:
                        continue
                    if entry.get("source") != "browser_capture" or entry.get("removed"):
                        continue
                    try:
                        image = Path(entry["path"]).read_bytes()
                    except Exception:
                        continue            # the file is gone; nothing to send
                    text = None
                    if entry.get("text_path"):
                        try:
                            text = Path(entry["text_path"]).read_text(
                                encoding="utf-8")[:200000]
                        except Exception:
                            text = None
                    status, body, _h = self.cp.request(
                        "POST", "/worker/v1/intel/evidence",
                        {"origin": origin, "entry": entry, "text": text,
                         "image": base64.b64encode(image).decode("ascii")})
                    if status >= 500 or status in (401, 409):
                        return sent
                    sent += 1
            marks[key] = offset + end + 1
            self._save("intel_sync.json", marks)
        return sent


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="ATA worker agent")
    parser.add_argument("--url", default=os.environ.get("ATA_CONTROL_PLANE_URL"))
    parser.add_argument("--token", default=os.environ.get("ATA_WORKER_TOKEN"))
    parser.add_argument("--ca", default=os.environ.get("ATA_CA_FILE"),
                        help="CA bundle for a private TLS certificate")
    args = parser.parse_args(argv)
    if not args.url or not args.token:
        print("Set ATA_CONTROL_PLANE_URL and ATA_WORKER_TOKEN (see PLATFORM.md).")
        return 2
    if args.url.startswith("http://") and not args.url.startswith(
            ("http://127.0.0.1", "http://localhost")):
        print("Refusing to send the worker token over plain HTTP. Use https://.")
        return 2
    # The worker's learning store holds real operational data.
    os.environ.setdefault("ATLAS_DATA_ORIGIN", "production")
    from dashboard import supervisor as sup_module
    try:
        heartbeat_s = max(2, min(60, int(os.environ.get("ATA_HEARTBEAT_S") or 10)))
    except ValueError:
        heartbeat_s = 10
    agent = Agent(ControlPlane(args.url, args.token, args.ca), sup_module.supervisor,
                  heartbeat_s=heartbeat_s)
    agent.start()
    print("ATA worker {0} — connecting to {1}".format(VERSION, args.url), flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        agent.stop()
        if agent.sup.is_running():
            print("A run is in progress; it keeps going. Stop it from the dashboard.")
    return 0
