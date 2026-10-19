"""
Control Tower server — standard library only, no pip install required.

Serves the dashboard and streams real automation state over Server-Sent Events.

Two ways to run it:

  1. Live (recommended). The patched update_eta.py starts this automatically.
  2. Review. `python -m dashboard.server --replay` loads the last finished run
     from tracking_results.csv and the newest log file, so you can inspect a
     completed run without launching Edge.
"""

import argparse
import csv
import io
import hmac
import json
import mimetypes
import socket
import os
import re
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

if __package__ in (None, ""):
    _HERE = Path(__file__).resolve().parent
    sys.path.insert(0, str(_HERE.parent))
    sys.path.insert(0, str(_HERE))
    try:
        from dashboard.bridge import bridge
        from dashboard import assistant
        from dashboard.control import control
        from dashboard import feedback as feedback_store
        from dashboard import mlstatus
        from dashboard import live_view
    except ImportError:
        from bridge import bridge
        import assistant
        from control import control
        import feedback as feedback_store
        import mlstatus
        import live_view
else:
    from .bridge import bridge
    from . import assistant
    from .control import control
    from . import feedback as feedback_store
    from . import mlstatus
    from . import live_view

# An iPhone ringtone (.m4r) is plain AAC in an MP4 container — the same bytes
# a browser happily plays as .m4a — but Python's mimetypes has never heard of
# the extension, so it went out as application/octet-stream and was refused.
# Registering the type is enough; the file itself is untouched.
mimetypes.add_type("audio/mp4", ".m4r")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/ogg", ".ogg")


def _find_static():
    """Locate the folder holding index.html, whatever the layout."""
    here = Path(__file__).resolve().parent
    for candidate in (
        here / "static",              # dashboard/static/  (normal)
        here / "dashboard" / "static",
        here.parent / "static",       # flattened one level up
        here,                         # everything in one folder
    ):
        if (candidate / "index.html").exists():
            return candidate
    return here / "static"            # nothing found; report 404 honestly


STATIC_DIR = _find_static()

# ============================================================
# NETWORK ACCESS
# ============================================================
#
# Default is loopback: the dashboard is reachable only from the machine
# running the automation. Set DASHBOARD_HOST = "0.0.0.0" in update_eta.py to
# let colleagues open it from their own machines.
#
# ACCESS_KEY: required whenever the dashboard leaves loopback — start() then
# resolves one (dashboard/access.py: --key, DASHBOARD_ACCESS_KEY, or this
# installation's generated key file) if the caller gave none. There is no
# built-in default key.
# The dashboard is read-only — it cannot start, stop or alter the automation —
# but it does show live shipment references, carriers and dates, and the
# assistant will answer questions about them. Anyone who can reach the port
# can read all of that.
ACCESS_KEY = None          # set via start(access_key=...)
COOKIE_NAME = "ct_key"
_shared_host = False       # True once bound to something other than loopback
_shared_port = 8787   # overwritten by start()


FIREWALL_RULE = "Mantrac Control Tower"


def ensure_firewall_rule(port):
    """
    Make sure Windows lets colleagues reach this port.

    Windows blocks inbound connections by default, which is the single reason a
    shared link "refuses to connect" from another machine. Adding the rule needs
    Administrator, so this is best-effort: if we have the rights we do it
    silently and the link just works; if we do not, we say exactly what to run.

    Scoped to domain and private profiles — never public networks.
    """
    if os.name != "nt":
        return "not-windows"

    import subprocess

    def run(args):
        return subprocess.run(
            args, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    try:
        existing = run(["netsh", "advfirewall", "firewall", "show", "rule",
                        "name={0}".format(FIREWALL_RULE)])
        if existing.returncode == 0 and str(port) in existing.stdout:
            return "already-allowed"

        added = run(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name={0}".format(FIREWALL_RULE), "dir=in", "action=allow",
                     "protocol=TCP", "localport={0}".format(port),
                     "profile=domain,private"])
        if added.returncode == 0:
            print("  Windows Firewall: inbound TCP {0} allowed automatically."
                  .format(port), flush=True)
            return "added"
        return "needs-admin"
    except Exception:
        return "needs-admin"


def local_addresses():
    """Every address a colleague could realistically use to reach this box."""
    found = []
    try:
        hostname = socket.gethostname()
        found.append(hostname)
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if address not in found and not address.startswith("127."):
                found.append(address)
    except Exception:
        pass
    if len(found) < 2:
        # Fall back to asking the OS which interface reaches the outside world.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address not in found:
                found.append(address)
        except Exception:
            pass
        finally:
            probe.close()
    return found
DEFAULT_PORT = 8787

try:
    import psutil
except ImportError:
    psutil = None


def machine_health():
    """Only reported when psutil is installed. Otherwise the panel says so."""
    if psutil is None:
        return {"available": False}
    try:
        process = psutil.Process(os.getpid())
        return {
            "available": True,
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "memory_percent": round(psutil.virtual_memory().percent, 1),
            "process_memory_mb": round(process.memory_info().rss / (1024 * 1024), 1),
            "threads": process.num_threads(),
        }
    except Exception:
        return {"available": False}


def build_payload(trim=True, since_cold=None):
    """The browser gets the trimmed view; the assistant gets everything."""
    data = bridge.snapshot(trim=trim, since_cold=since_cold)
    data["control"] = control.snapshot()
    data["health"] = machine_health()
    # Whether Open Session can show the paused carrier tab here (live_view).
    data["live_view"] = live_view.available()
    return data


def _assistant_state():
    """
    The run state ATLAS answers from: whatever the dashboard itself is
    showing. Under the supervisor that is the running automation's published
    state, not the supervisor's own (empty) bridge — reading the bridge
    directly is what left the assistant knowing nothing in that mode.
    """
    try:
        return build_payload(trim=False)
    except TypeError:
        return build_payload()
    except Exception:
        return bridge.snapshot()


DRAIN_MAX_BYTES = 1024 * 1024


class _RequestBody:
    """
    The request body, as the handlers read it: never past Content-Length, and
    counted, so what a handler leaves unread can be dealt with before the
    connection carries the next request.
    """

    def __init__(self, raw, length):
        self.raw, self.left = raw, length

    def read(self, size=-1):
        if self.left <= 0:
            return b""
        size = self.left if size is None or size < 0 else min(size, self.left)
        data = self.raw.read(size)
        self.left -= len(data)
        return data

    def readline(self, size=-1):
        if self.left <= 0:
            return b""
        size = self.left if size is None or size < 0 else min(size, self.left)
        data = self.raw.readline(size)
        self.left -= len(data)
        return data


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # keep the automation console clean

    # -- one request per body ------------------------------------------------
    # A reply sent without reading the body (a refusal, a 409, a size limit)
    # used to leave the body on the keep-alive connection, where it was read
    # as the start of the next request: '{"client_id":...}POST' answered 501,
    # and a Sign out on that connection never reached the server.

    def parse_request(self):
        if not BaseHTTPRequestHandler.parse_request(self):
            return False
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or self.headers.get("Transfer-Encoding"):
            self.close_connection = True       # a body we cannot measure
            length = 0
        self._socket_rfile = self.rfile
        self.rfile = _RequestBody(self._socket_rfile, length)
        return True

    def handle_one_request(self):
        try:
            BaseHTTPRequestHandler.handle_one_request(self)
        finally:
            self._settle_body()

    def _settle_body(self):
        body = self.rfile
        if not isinstance(body, _RequestBody):
            return
        self.rfile = self._socket_rfile
        if body.left <= 0:
            return
        if body.left > DRAIN_MAX_BYTES:
            self.close_connection = True
            return
        try:
            while body.left > 0:
                if not body.read(min(body.left, 65536)):
                    self.close_connection = True
                    return
        except (OSError, ValueError):
            self.close_connection = True

    # -- helpers -----------------------------------------------------------

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self._maybe_set_cookie()
        self.end_headers()
        self.wfile.write(body)

    def _maybe_set_cookie(self):
        """Remember a valid ?key= so assets and the SSE stream also pass."""
        if getattr(self, "_set_cookie", False) and ACCESS_KEY:
            self.send_header(
                "Set-Cookie",
                "{0}={1}; Path=/; SameSite=Lax; Max-Age=86400".format(
                    COOKIE_NAME, ACCESS_KEY),
            )
            self._set_cookie = False

    def _send_file(self, path):
        """
        Serve a file, honouring Range.

        This used to advertise `Accept-Ranges: bytes` and then ignore the
        Range header, answering every request with a full 200. Chromium asks
        for a range when it loads media, got a whole-file 200 back instead of
        a 206, and errored — which is precisely why the intro's soundtrack
        never played. Claiming to support ranges and not supporting them is
        worse than not claiming it.
        """
        if not path.exists() or not path.is_file():
            self._send(404, json.dumps({"error": "not found"}))
            return
        guessed = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        total = len(data)

        start, end = 0, total - 1
        partial = False
        header = self.headers.get("Range") or ""
        match = re.match(r"bytes=(\d*)-(\d*)\s*$", header.strip())
        if match and total:
            first, last = match.group(1), match.group(2)
            if first:
                start = int(first)
                end = int(last) if last else total - 1
            elif last:                       # a suffix range: last N bytes
                start = max(0, total - int(last))
                end = total - 1
            if start >= total or start > end:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */{0}".format(total))
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            end = min(end, total - 1)
            partial = True

        body = data[start:end + 1]
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", guessed)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range",
                             "bytes {0}-{1}/{2}".format(start, end, total))
        self._maybe_set_cookie()
        self.end_headers()
        self.wfile.write(body)

    # -- routes ------------------------------------------------------------

    def _authorised(self):
        """
        True when the request may proceed.

        Always true when no ACCESS_KEY is configured. Otherwise the key may
        arrive as ?key=... (first visit) or as a cookie (every request after).
        """
        if not ACCESS_KEY:
            return True
        from urllib.parse import urlparse, parse_qs

        supplied = (parse_qs(urlparse(self.path).query).get("key") or [None])[0]
        if supplied and hmac.compare_digest(str(supplied), ACCESS_KEY):
            self._set_cookie = True
            return True
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME and value and hmac.compare_digest(value, ACCESS_KEY):
                return True
        return False

    def _deny(self):
        body = (
            "<!doctype html><meta charset=utf-8>"
            "<title>Control Tower</title>"
            "<style>body{background:#0A0C0E;color:#fff;font:15px/1.6 system-ui;"
            "display:grid;place-items:center;height:100vh;margin:0;text-align:center}"
            "b{color:#FF7A00}</style>"
            "<div><h2>Control Tower</h2><p>This dashboard needs an access key.</p>"
            "<p>Open the link that includes <b>?key=…</b></p></div>"
        ).encode("utf-8")
        self.send_response(401)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _live_view(self, method, route):
        """/api/session/<action_id>/(frame|input|release) — the local live view
        (live_view.py). Only the dashboard tab that opened it is served."""
        from urllib.parse import parse_qs, urlparse
        parts = route.split("/")
        if len(parts) != 5 or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", parts[3]):
            self._send(404, json.dumps({"error": "not_found"}))
            return
        action_id, verb = parts[3], parts[4]
        query = parse_qs(urlparse(self.path).query)
        if verb == "frame" and method == "GET":
            client = (query.get("client") or [""])[0][:64]
            status, headers, body = live_view.frame(action_id, client,
                                                    (query.get("after") or ["0"])[0])
            kind = "image/jpeg" if status == 200 else "application/json; charset=utf-8"
            self._send(status, body, kind, dict(headers, **{"Cache-Control": "no-store, private"})
                       if status == 200 else headers)
            return
        if method == "POST" and verb in ("input", "release"):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 32 * 1024:
                    self._send(413, json.dumps({"error": "too_large"}))
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send(400, json.dumps({"error": "bad_request"}))
                return
            client = str(payload.get("client_id") or "")[:64]
            if verb == "input":
                status, out = live_view.send_input(action_id, client, payload.get("events"))
                self._send(status, json.dumps(out))     # nothing typed is echoed
            else:
                self._send(200, json.dumps({"ok": live_view.release(action_id, client)}))
            return
        self._send(404, json.dumps({"error": "not_found"}))

    def do_GET(self):
        if not self._authorised():
            self._deny()
            return
        route = self.path.split("?")[0].rstrip("/") or "/"

        if route == "/":
            self._send_file(STATIC_DIR / "index.html")
            return

        if route == "/api/state":
            self._send(200, json.dumps(build_payload()))
            return

        if route == "/api/stream":
            self._stream()
            return

        if route.startswith("/static/"):
            relative = route[len("/static/"):]
            candidate = (STATIC_DIR / relative).resolve()
            if STATIC_DIR.resolve() in candidate.parents or candidate == STATIC_DIR.resolve():
                self._send_file(candidate)
            else:
                self._send(403, json.dumps({"error": "forbidden"}))
            return

        if route == "/api/export.csv":
            self._export_csv()
            return

        # The ATLAS film on its own page. /film stays so older links work.
        if route in ("/intro", "/film"):
            self._send_file(STATIC_DIR / "intro" / "index.html")
            return

        if route == "/api/share":
            addresses = [a for a in local_addresses()] if _shared_host else []
            key = "?key={0}".format(ACCESS_KEY) if ACCESS_KEY else ""
            self._send(200, json.dumps({
                "shared": bool(_shared_host),
                "port": _shared_port,
                "links": ["http://{0}:{1}/{2}".format(a, _shared_port, key)
                          for a in addresses],
            }))
            return

        if route == "/api/atlas/llm":
            # Whether ATLAS's optional local model and self-hosted search are
            # set up and answering. Status only — never a prompt or data.
            from intelligence import llm as _llm, research as _research
            self._send(200, json.dumps({"llm": _llm.provider().health(),
                                        "search": {"configured": _research.enabled(),
                                                   "detail": None if _research.enabled()
                                                   else _research.why_off()}}))
            return

        if route == "/api/atlas/briefing":
            # The morning briefing, carrier health and carrier-page findings —
            # all counted from recorded shipment outcomes and page checks.
            from intelligence import briefing as _briefing
            built = _briefing.build()
            self._send(200, json.dumps({
                "text": _briefing.render(built), "action": built["action"],
                "lines": built["lines"], "sources": built["sources"],
                "site_changes": built["site_changes"], "health": built["health"]},
                default=str))
            return

        if route.startswith("/api/atlas/") or route.startswith("/api/evidence"):
            self._intel_get(route)
            return

        if route == "/api/ask/progress":
            # What ATLAS is actually doing for one in-flight question: the
            # searches and pages of its research as they start. Nothing else.
            from urllib.parse import parse_qs, urlparse as _up
            from intelligence import research as _research
            pid = (parse_qs(_up(self.path).query).get("id") or [""])[0]
            self._send(200, json.dumps(_research.progress(_research.clean_progress_id(pid))))
            return

        if route == "/api/atlas":
            # ATLAS's panel header: status, counts, what it noticed, and the
            # questions worth asking — from the same state the dashboard shows.
            self._send(200, json.dumps(assistant.atlas_brief(_assistant_state())))
            return

        if route == "/api/po" or route.startswith("/api/po/"):
            self._po("GET", route)
            return

        if route.startswith("/api/session/"):
            self._live_view("GET", route)
            return

        if route == "/api/ml":
            # Real values from the live ml package. Nothing here is a demo
            # figure: an unknown is null, not zero.
            payload = mlstatus.snapshot()
            payload["feedback"] = feedback_store.stats()
            self._send(200, json.dumps(payload))
            return

        self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if not self._authorised():
            self._deny()
            return
        route = self.path.split("?")[0].rstrip("/") or "/"

        if route == "/api/control":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(min(length, 2000)) or b"{}")
                action = str(payload.get("action", ""))[:20]
                reference = str(payload.get("reference", ""))[:40]
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
            accepted, message = control.request(action, reference)
            self._send(200, json.dumps({"accepted": accepted, "message": message}))
            return

        if route == "/api/human":
            # Human-in-the-loop: open or resume the ONE browser session a run
            # paused for a person. Scoped by run and action; validated here
            # and again by the run. Nothing in it is typed into a page.
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 1000:
                    self._send(413, json.dumps({"error": "request too long"}))
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
                op = str(payload.get("op", ""))[:10]
                run_id = str(payload.get("run_id", ""))[:64]
                action_id = str(payload.get("action_id", ""))[:64]
                client_id = str(payload.get("client_id", ""))[:64]
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
            handler = getattr(control, "human_request", None)
            if handler is None:
                accepted, message = False, "Human actions are not available."
            else:
                accepted, message = handler(op, run_id, action_id, client_id)
            live = False
            if accepted and op == "open" and live_view.available():
                # The paused tab, in this operator's own browser (live_view).
                live, why = live_view.open_for(action_id, client_id)
                if why and not live:
                    message = "{0} {1}".format(message or "", why).strip()
            self._send(200, json.dumps({"accepted": accepted,
                                        "message": message, "live": live}))
            return

        if route.startswith("/api/session/"):
            self._live_view("POST", route)
            return

        if route == "/api/evidence/upload":
            self._evidence_upload()
            return

        if route == "/api/po" or route.startswith("/api/po/"):
            self._po("POST", route)
            return

        if route == "/api/feedback":
            # Feedback is stored as material for a later, deliberate training
            # and evaluation pass. It never reaches a production model on its
            # own.
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 8000:
                    self._send(413, json.dumps({"error": "feedback too long"}))
                    return
                payload = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
            accepted, message = feedback_store.record(
                question=payload.get("question"),
                answer=payload.get("answer"),
                verdict=payload.get("verdict"),
                correction=payload.get("correction"),
                sources=payload.get("sources"),
                confidence=payload.get("confidence"),
                intent=payload.get("intent"),
                reference=payload.get("reference"),
            )
            if accepted and LEARNING["on"] and INTEL_OK:
                # An opinion about an answer or a recovery — counted as one,
                # never as a verified outcome.
                intel_events.record("feedback", verdict=str(payload.get("verdict") or ""),
                                    intent=str(payload.get("intent") or "")[:40] or None,
                                    reference=str(payload.get("reference") or "")[:64] or None)
            self._send(200 if accepted else 400,
                       json.dumps({"accepted": accepted, "message": message}))
            return

        if route != "/api/ask":
            self._send(404, json.dumps({"error": "not found"}))
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4000:                      # nothing legitimate is bigger
                self._send(413, json.dumps({"error": "question too long"}))
                return
            payload = json.loads(self.rfile.read(length) or b"{}")
            question = str(payload.get("question", ""))[:1000]
            # Follow-up context is owned by the caller; the assistant keeps no
            # state between requests. Only the shipment and the human task
            # last discussed are accepted — short-term, this tab only.
            raw_context = payload.get("context") or {}
            context = {"reference": str(raw_context.get("reference") or "")[:64],
                       "action_id": str(raw_context.get("action_id") or "")[:64],
                       "evidence_id": re.sub(r"[^0-9a-f]", "", str(
                           raw_context.get("evidence_id") or ""))[:16],
                       # Asked from the PO Automation page, about which job.
                       "domain": "po" if raw_context.get("domain") == "po" else "",
                       "po_id": re.sub(r"[^0-9a-z-]", "", str(
                           raw_context.get("po_id") or ""))[:40],
                       # The tab's id for this one question, so it can ask
                       # what ATLAS is doing while it researches.
                       "progress_id": re.sub(r"[^A-Za-z0-9_-]", "", str(
                           raw_context.get("progress_id") or ""))[:64],
                       # How many rude messages this tab has sent — a count only.
                       "conduct": int(raw_context.get("conduct") or 0)
                       if str(raw_context.get("conduct") or "0").isdigit() else 0}
        except Exception:
            self._send(400, json.dumps({"error": "bad request"}))
            return

        # The assistant only ever receives a snapshot. It has no handle on the
        # bridge, the browser or the credentials, so it cannot act on anything.
        # Untrimmed: the assistant should see the whole run, not the wire view.
        from intelligence import research as _research
        _research.progress_start(context.get("progress_id"))
        try:
            reply = assistant.answer(question, _assistant_state(), context)
        finally:
            _research.progress_done(context.get("progress_id"))
        if LEARNING["on"] and INTEL_OK:
            # The intent and a reference-free pattern — never the conversation.
            # A request about a verification code keeps no pattern at all.
            intent = reply.get("intent")
            fallback = bool(reply.get("fallback")) or \
                str(reply.get("answer") or "").startswith("I don't have that information")
            # A rude message is logged by its label alone — not its words.
            intel_events.record("question", intent=intent or "unrecognised",
                                pattern=None if intent == "code_request" or reply.get("conduct")
                                else intel_events.question_pattern(question),
                                answered=bool(intent) and not fallback,
                                run_id=(bridge.snapshot(trim=True).get("run") or {}).get("run_id"))

        # The assistant may ASK for an action but can never perform one. The
        # request goes through the same control channel and the same enabled
        # check as the dashboard buttons.
        wanted = reply.pop("request", None)
        if wanted:
            accepted, message = control.request(
                wanted.get("action"), wanted.get("reference"))
            reply["answer"] = message
            reply["accepted"] = accepted

        self._send(200, json.dumps(reply))

    # -- PO Automation ---------------------------------------------------------

    def _po(self, method, route):
        """
        The PO routes (po/web.py). On a single machine whoever holds the
        dashboard's access key is the operator; there is no role to check, and
        every action is written to the PO store's own audit log.
        """
        body = {}
        if method == "POST":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 8000:
                    self._send(413, json.dumps({"error": "request too long"}))
                    return
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
        from po import service as po_service, web as po_web
        kind, status, payload = po_web.handle(po_service.local(), method, route, body,
                                              "local operator", lambda permission: True)
        if kind == "file":
            data, content_type, name = payload
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", 'attachment; filename="{0}"'.format(name))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return
        self._send(status, json.dumps(payload, default=str))

    # -- ATLAS intelligence --------------------------------------------------

    def _query(self):
        from urllib.parse import urlparse, parse_qs
        return {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}

    def _intel_get(self, route):
        if not INTEL_OK:
            self._send(503, json.dumps({"error": "the intelligence layer is not installed"}))
            return
        q = self._query()
        try:
            if route == "/api/atlas/learning":
                snap = intel_learning.snapshot()
                self._send(200, json.dumps({
                    "summary": snap["summary"], "built_at": snap["built_at"],
                    "events": snap["events"],
                    "issues": [{k: (v if k != "strategies" else sorted(
                        v.values(), key=lambda s: (s["wilson"], s["successes"]), reverse=True))
                        for k, v in i.items() if k not in ("first_tried",)}
                        for i in snap["issues"][:20]],
                    "human": snap["human"][:10], "questions": snap["questions"][:12],
                    "feedback": snap["feedback"], "proposals": snap["proposals"],
                    # REAL PRODUCTION DATA or TEST / DEMO DATA, from the store's
                    # own marker; None while nothing has been recorded.
                    "data_origin": intel_store.store_origin(),
                    "months": [{k: v for k, v in m.items() if k != "runs"}
                               for m in snap["months"][-12:]]}, default=list))
                return
            if route == "/api/atlas/maturity":
                status = intel_maturity.status()
                review = intel_maturity.review()
                self._send(200, json.dumps({"status": status, "review": review,
                                            "review_text": intel_maturity.render_review(review)}))
                return
            if route == "/api/atlas/plan":
                plan = intel_plans.build(q.get("provider"), q.get("issue"))
                plan.pop("issue", None)
                self._send(200, json.dumps(plan, default=list))
                return
            if route == "/api/evidence":
                hits = intel_evidence.search(
                    reference=q.get("reference") or None, run_id=q.get("run") or None,
                    provider=q.get("provider") or None, source=q.get("source") or None,
                    failures_only=q.get("failures") == "1",
                    limit=min(int(q.get("limit") or 20), 50))
                self._send(200, json.dumps([intel_evidence.public(h) for h in hits]))
                return
            if route == "/api/evidence/file":
                entry, path = intel_evidence.file_for(re.sub(r"[^0-9a-f]", "", q.get("id", ""))[:16])
                if path is None:
                    self._send(404, json.dumps({"error": "no such evidence, or it changed "
                                                         "since it was stored"}))
                    return
                data = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", entry.get("mime") or "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self._maybe_set_cookie()
                self.end_headers()
                self.wfile.write(data)
                return
        except Exception as error:
            self._send(500, json.dumps({"error": str(error)[:200]}))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def _evidence_upload(self):
        """An operator's image: validated, read, refused if it is a verification screen."""
        if not INTEL_OK:
            self._send(503, json.dumps({"accepted": False,
                                        "message": "The intelligence layer is not installed."}))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > intel_evidence.MAX_UPLOAD:
            self._send(413, json.dumps({"accepted": False,
                                        "message": "Send one image up to 6 MB."}))
            return
        data = self.rfile.read(length)
        name = str(self.headers.get("X-Filename") or "upload")[:80]
        holder = {}

        def read(path):
            holder["reading"] = intel_vision.read_image(path)
            return holder["reading"]
        ok, result = intel_evidence.register_upload(data, filename=name, read=read)
        if not ok:
            self._send(200, json.dumps({"accepted": False, "message": result}))
            return
        self._send(200, json.dumps({"accepted": True, "evidence": intel_evidence.public(result),
                                    "reading": holder.get("reading")}))

    EXPORT_COLUMNS = [
        ("reference", "BOL_AWB"),
        ("carrier", "Carrier"),
        ("provider", "Tracking_Provider"),
        ("hub_status", "Hub_Status_Filter"),
        ("table_page", "Hub_List_Page"),
        ("internal_eta", "Hub_ETA_Before"),
        ("provider_status", "Carrier_Status"),
        ("provider_eta", "Carrier_ETA"),
        ("provider_ata", "Carrier_ATA"),
        ("coe_action", "COE_ETA_Action"),
        ("bu_action", "BU_ATA_Action"),
        ("state", "Result"),
        ("outcome", "Outcome_Class"),
        ("duration_ms", "Processing_ms"),
        ("error", "Detail"),
        ("started_at", "Started"),
        ("updated", "Last_Updated"),
    ]

    def _export_csv(self):
        """
        Export the run's shipments as CSV.

        ?state=updated  (default) only shipments written to the hub
        ?state=all|failed|skipped|processing

        Straight from the live state — no re-derivation, no invented columns.
        An empty value stays empty rather than becoming a placeholder.
        """
        from urllib.parse import urlparse, parse_qs

        wanted = (parse_qs(urlparse(self.path).query).get("state") or ["updated"])[0]
        data = bridge.snapshot()
        rows = data.get("shipments") or []
        if wanted != "all":
            rows = [r for r in rows if r.get("state") == wanted]

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([label for _key, label in self.EXPORT_COLUMNS])
        for record in reversed(rows):          # oldest first, as processed
            writer.writerow([
                "" if record.get(key) is None else record.get(key)
                for key, _label in self.EXPORT_COLUMNS
            ])

        # Excel opens UTF-8 correctly only with a BOM.
        body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = "control_tower_{0}_{1}.csv".format(wanted, stamp)

        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition",
                         'attachment; filename="{0}"'.format(filename))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        # The version is a plain integer the bridge bumps on every mutation.
        # Reading it costs 0.00007ms; building and serialising the payload
        # costs 1.54ms for 142KB. The loop used to do the second one every
        # 0.35s and then throw the result away whenever the version had not
        # moved — which, between shipments, is most of the time.
        #
        # Checking the integer instead makes the poll interval nearly free, so
        # it can be tightened. That is where the latency went: a change had to
        # wait up to 350ms for the next look. Measured update latency was
        # 141-457ms against a 350ms floor that no amount of frontend work
        # could have improved.
        #
        # The second measurement: the payload itself. 81 pushes a minute at
        # 194KB is 16.1 MB/min for the browser to parse, and `shipments` is
        # 68% of it, growing with the run (342KB at 400 shipments). Most
        # pushes are a log line or a step, which change no shipment at all —
        # so the shipments and exceptions go out only when the bridge says
        # they actually changed, and the frame names what it left out. The
        # FIRST frame on a connection always carries everything.
        last_version = -1
        last_push = 0.0
        sent_cold = None
        try:
            while True:
                version = bridge.version
                stale = (time.time() - last_push) > 2.0
                if version != last_version or stale:
                    payload = build_payload(since_cold=sent_cold)
                    last_version = payload["version"]
                    sent_cold = payload.get("cold_version", sent_cold)
                    last_push = time.time()
                    chunk = "event: state\ndata: {0}\n\n".format(json.dumps(payload))
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                time.sleep(0.08)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return


FILM_SLOTS = [
    "01-origin", "02-road", "03-port", "04-vessel",
    "05-arrival", "06-clearance", "07-hub", "08-close",
]
IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".avif")


def replay(base_folder):
    base = Path(base_folder)
    results = base / "tracking_results.csv"
    logs = sorted((base / "logs").glob("run_*.log")) if (base / "logs").exists() else []

    bridge.run_started(
        dry_run=None,
        target_status="Under Clearance",
        results_file=str(results),
        log_file=str(logs[-1]) if logs else None,
    )

    if logs:
        for raw in logs[-1].read_text(encoding="utf-8", errors="replace").splitlines():
            match = LOG_LINE.match(raw.strip())
            if match:
                bridge.log(match.group("msg"))

    if not results.exists():
        bridge.log("Replay: tracking_results.csv not found in {0}".format(base))
        bridge.run_finished()
        return

    successful = failed = skipped = 0
    pages = set()

    with open(results, "r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            reference = (row.get("BOL_AWB") or "").strip()
            if not reference:
                continue
            page = row.get("Table_Page") or ""
            if page and page not in pages:
                pages.add(page)
            provider = (row.get("Provider") or "").strip()
            bridge.shipment_started({
                "bol_awb": reference,
                "carrier": row.get("Carrier"),
                "provider": "QATAR" if "qatar" in provider.lower() else (provider or None),
                "current_eta": row.get("Existing_ETA"),
                "table_page": page,
            })
            bridge.provider_result({
                "provider": provider,
                "tracking_status": row.get("Provider_Status"),
                "eta": row.get("Provider_ETA"),
                "ata": row.get("Provider_ATA"),
            })
            outcome = (row.get("Result") or "").strip().upper()
            bridge.shipment_finished(
                reference,
                outcome,
                row.get("Details") or "",
                {"coe": row.get("COE_ETA_Action"), "bu": row.get("BU_ATA_Action")},
            )
            if outcome == "SUCCESS":
                successful += 1
            elif outcome == "SKIPPED":
                skipped += 1
            elif outcome == "FAILED":
                failed += 1
            bridge.counters(successful, failed, skipped)

    bridge.discovered = successful + failed + skipped
    bridge.pages_scanned = len(pages)
    bridge.run_finished()


# ============================================================
# LIFECYCLE
# ============================================================

_server = None


# ATLAS's intelligence stores (events, learning, evidence, maturity). Read by
# the API; written only by the automation, and — for questions and feedback —
# by this server when learning is switched on.
try:
    _ROOT_DIR = str(Path(__file__).resolve().parent.parent)
    if _ROOT_DIR not in sys.path:
        sys.path.insert(0, _ROOT_DIR)
    from intelligence import (events as intel_events, learning as intel_learning,
                              plans as intel_plans, evidence as intel_evidence,
                              vision as intel_vision, maturity as intel_maturity,
                              store as intel_store)
    INTEL_OK = True
except Exception:                                   # pragma: no cover
    INTEL_OK = False

# Whether operator questions and feedback go to ATLAS's learning store. Set by
# the real automation and the supervisor; off for tests, demos and tools.
LEARNING = {"on": False}


def start(port=DEFAULT_PORT, open_browser=True, host="127.0.0.1", access_key=None,
          learning=False):
    """
    Start the dashboard in a daemon thread. Never raises into the caller.

    host="127.0.0.1"  this machine only (default)
    host="0.0.0.0"    reachable from other machines on the network
    """
    global _server, ACCESS_KEY, _shared_host, _shared_port
    ACCESS_KEY = access_key or None
    if not ACCESS_KEY and host not in ("127.0.0.1", "localhost"):
        # Never serve run data to the network without a key.
        try:
            from dashboard import access as _access
        except ImportError:                     # flattened layout
            import access as _access
        try:
            ACCESS_KEY, source = _access.resolve()
        except (OSError, ValueError) as error:
            print("Control Tower did not start: a network-shared dashboard needs an access "
                  "key, and none could be set up ({0}).".format(error), flush=True)
            return None
        print(_access.explain(source), flush=True)
    LEARNING["on"] = bool(learning)
    if learning:
        # The real tower (update_eta.py or the supervisor): ATLAS uses the
        # local model and search when this machine has them. Never raises.
        try:
            from intelligence import autoconfig
            autoconfig.apply(lambda line: print(line, flush=True))
        except Exception as error:
            print("ATLAS AI check skipped: {0}".format(error), flush=True)
    _shared_host = host not in ("127.0.0.1", "localhost")
    _shared_port = port
    try:
        class QuietServer(ThreadingHTTPServer):
            """
            Browsers abort SSE streams on navigate/refresh/close. socketserver
            prints a full traceback for that, which on Windows appears as
            ConnectionAbortedError [WinError 10053] in the middle of the run
            log. It is normal client behaviour, not a fault, so it is
            swallowed here — real errors still surface.
            """

            daemon_threads = True

            def handle_error(self, request, client_address):
                import sys as _sys
                kind = _sys.exc_info()[0]
                if kind is not None and issubclass(
                    kind, (ConnectionResetError, ConnectionAbortedError,
                           BrokenPipeError, TimeoutError)
                ):
                    return
                ThreadingHTTPServer.handle_error(self, request, client_address)

        _server = QuietServer((host, port), Handler)
        _server.daemon_threads = True
        threading.Thread(target=_server.serve_forever, daemon=True).start()

        def pulse():
            while True:
                time.sleep(1.0)
                bridge.heartbeat()

        threading.Thread(target=pulse, daemon=True).start()

        suffix = "?key={0}".format(ACCESS_KEY) if ACCESS_KEY else ""
        local = "http://127.0.0.1:{0}/{1}".format(port, suffix)

        if host not in ("127.0.0.1", "localhost"):
            firewall = ensure_firewall_rule(port)
            print("", flush=True)
            print("=" * 66, flush=True)
            print("  ON THIS MACHINE:", flush=True)
            print("    {0}".format(local), flush=True)
            print("", flush=True)
            print("  SEND THIS TO COLLEAGUES  (127.0.0.1 will NOT work for them —", flush=True)
            print("  on their computer it points at their own machine):", flush=True)
            for address in local_addresses():
                print("    http://{0}:{1}/{2}".format(address, port, suffix), flush=True)
            if firewall == "needs-admin":
                print("", flush=True)
                print("  If they cannot connect, Windows Firewall is blocking it.", flush=True)
                print("  Close this, then right-click START_SHARED.bat >", flush=True)
                print("  'Run as administrator' — it opens the port once and starts", flush=True)
                print("  the run for you.", flush=True)
            print("=" * 66, flush=True)
            print("", flush=True)
        else:
            print("Control Tower running at {0}".format(local), flush=True)
            if not ACCESS_KEY:
                print(
                    "  NOTE: no access key is set, so anyone who can reach this "
                    "port can read the run. Set the DASHBOARD_ACCESS_KEY environment "
                    "variable to require one.",
                    flush=True,
                )
            print(
                "  If a colleague cannot connect, Windows Firewall is almost "
                "certainly blocking it. Allow inbound TCP on port {0} — see the "
                "README for the one-line command.".format(port),
                flush=True,
            )

        if open_browser:
            threading.Timer(0.8, lambda: webbrowser.open(local)).start()
        return local
    except OSError as error:
        if getattr(error, "errno", None) in (98, 48, 10048):
            print(
                "Control Tower could not start: port {0} is already in use.\n"
                "  Another run is probably still open at http://127.0.0.1:{0}/\n"
                "  Close it, or set DASHBOARD_PORT to a different number in update_eta.py."
                .format(port),
                flush=True,
            )
        else:
            print("Control Tower could not start: {0}".format(error), flush=True)
        return None
    except Exception as error:
        print("Control Tower could not start: {0}".format(error), flush=True)
        return None


def serve_forever(port=DEFAULT_PORT, host="127.0.0.1", access_key=None):
    start(port=port, host=host, access_key=access_key)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nControl Tower stopped.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mantrac Shipment Control Tower")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 to allow other machines on the network")
    parser.add_argument("--key", default=None,
                        help="require ?key=... to view (off loopback a key is always "
                             "required: DASHBOARD_ACCESS_KEY or the generated key file)")
    parser.add_argument("--share", action="store_true",
                        help="shorthand for --host 0.0.0.0")
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Load the last finished run from tracking_results.csv and logs",
    )
    parser.add_argument("--base", default=r"C:\Automation", help="Automation base folder")
    args = parser.parse_args()

    if args.replay:
        replay(args.base)
    serve_forever(port=args.port,
                  host="0.0.0.0" if args.share else args.host,
                  access_key=args.key)
