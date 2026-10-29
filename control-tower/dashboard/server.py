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
import gzip
import hashlib
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

ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
from dashboard import accounts as _accounts      # noqa: E402  personal sign-in
from dashboard import conversations as _conversations  # noqa: E402  ATLAS chats, for Admins
from dashboard import clearance as _clearance    # noqa: E402  the eHub export, /clearance

# An iPhone ringtone (.m4r) is plain AAC in an MP4 container — the same bytes
# a browser happily plays as .m4a — but Python's mimetypes has never heard of
# the extension, so it went out as application/octet-stream and was refused.
# Registering the type is enough; the file itself is untouched.
mimetypes.add_type("audio/mp4", ".m4r")
mimetypes.add_type("audio/mp4", ".m4a")
mimetypes.add_type("audio/mpeg", ".mp3")
mimetypes.add_type("audio/ogg", ".ogg")
mimetypes.add_type("audio/ogg", ".oga")
mimetypes.add_type("audio/ogg", ".opus")
mimetypes.add_type("audio/flac", ".flac")
# Windows reads these from its registry, where .js is sometimes text/plain:
# a script sent as text/plain with nosniff does not run.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/webp", ".webp")


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


def _trust_proxy():
    """ATA_TRUST_PROXY=1: an HTTPS reverse proxy on this machine fronts the
    dashboard at a company domain (DEPLOY_SERVER.md)."""
    return (os.environ.get("ATA_TRUST_PROXY") or "").strip().lower() in (
        "1", "true", "yes", "on")


FIREWALL_RULE = "Mantrac Control Tower"
FIREWALL_REMOTE = "remoteip=localsubnet,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def ensure_firewall_rule(port):
    """
    Make sure Windows lets colleagues reach this port.

    Windows blocks inbound connections by default, which is the single reason a
    shared link "refuses to connect" from another machine. Adding the rule needs
    Administrator, so this is best-effort: if we have the rights we do it
    silently and the link just works; if we do not, we say exactly what to run.

    Every network profile (an Azure server's network is "Public"), but only
    from private, company addresses: never from the internet.
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
            # netsh's words are translated on non-English Windows; the
            # address ranges are not. A rule with them is the current one.
            if "10.0.0.0/8" in existing.stdout:
                return "already-allowed"
            # An older rule for Domain/Private only: an Azure server's network
            # is "Public", so colleagues stay blocked. Replace it.
            run(["netsh", "advfirewall", "firewall", "delete", "rule",
                 "name={0}".format(FIREWALL_RULE)])

        added = run(["netsh", "advfirewall", "firewall", "add", "rule",
                     "name={0}".format(FIREWALL_RULE), "dir=in", "action=allow",
                     "protocol=TCP", "localport={0}".format(port),
                     "profile=any", FIREWALL_REMOTE])
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


def _knowledge_retrieval_stats():
    """How retrieval is doing, from kb_queries.jsonl: volume, hit rate, latency,
    and the questions nothing answered (redacted, for reviewers only)."""
    try:
        from intelligence import store as _st
        rows = _st.read("kb_queries.jsonl")[-2000:]
    except Exception:
        rows = []
    if not rows:
        return {"queries": 0}
    ms = sorted(r.get("ms") or 0 for r in rows)
    misses = [r.get("q") for r in rows if not r.get("hit")][-20:]
    return {"queries": len(rows), "hit_rate": round(sum(1 for r in rows if r.get("hit")) / float(len(rows)), 3),
            "median_ms": ms[len(ms) // 2], "p95_ms": ms[int(len(ms) * 0.95) - 1] if len(ms) > 1 else ms[0],
            "recent_misses": misses}


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

    # Text over 1.4 KB goes compressed when the browser accepts it: the main
    # page is 476 KB as text and 134 KB compressed, the Clearance data ~5x
    # smaller. Over a VPN that is most of the wait. Nothing else changes.
    GZIP_MIN = 1400
    GZIP_TYPES = ("text/", "application/json", "application/javascript", "image/svg+xml")

    def _gzip_ok(self, content_type, size):
        return size >= self.GZIP_MIN and content_type.startswith(self.GZIP_TYPES) and \
            "gzip" in (self.headers.get("Accept-Encoding") or "").lower()

    def _send(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        packed = self._gzip_ok(content_type, len(body))
        if packed:
            body = gzip.compress(body, 6)
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if packed:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
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

    def _send_no_icon(self):
        """The pages declare their icon inline; an old-style /favicon.ico
        request gets an empty answer instead of a 404 in every console."""
        self.send_response(204)
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()

    def _send_file(self, path, extra=None):
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
        if guessed.startswith("text/") and "charset" not in guessed:
            guessed += "; charset=utf-8"
        if not self.headers.get("Range") and guessed.startswith(self.GZIP_TYPES):
            # Pages, styles and scripts: the browser keeps them and asks
            # "changed?" (ETag) instead of downloading them again, and gets
            # them compressed. A new release changes the ETag at once.
            self._send_text_file(path, guessed, extra)
            return
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
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self._maybe_set_cookie()
        self.end_headers()
        self.wfile.write(body)

    _packed = {}                       # (path, mtime, size) -> (etag, raw, gzipped)
    _packed_lock = threading.Lock()

    def _send_text_file(self, path, content_type, extra=None):
        st = path.stat()
        key = (str(path), st.st_mtime_ns, st.st_size)
        with self._packed_lock:
            hit = self._packed.get(key)
        if hit is None:
            raw = path.read_bytes()
            hit = ('"{0}"'.format(hashlib.sha256(raw).hexdigest()[:20]), raw,
                   gzip.compress(raw, 6) if len(raw) >= self.GZIP_MIN else None)
            with self._packed_lock:
                if len(self._packed) > 200:
                    self._packed.clear()
                self._packed[key] = hit
        etag, raw, packed = hit
        extra = dict(extra or {})
        cache = extra.pop("Cache-Control", "private, no-cache")   # a route's own rule wins
        use_gzip = packed is not None and "gzip" in (self.headers.get("Accept-Encoding") or "").lower()
        if use_gzip:                       # each encoding its own validator
            etag = etag[:-1] + '-gz"'
        if (self.headers.get("If-None-Match") or "").strip() == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.send_header("Vary", "Accept-Encoding")
            for name, value in (extra or {}).items():
                self.send_header(name, value)
            self._maybe_set_cookie()
            self.end_headers()
            return
        body = packed if use_gzip else raw
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache)
        self.send_header("Vary", "Accept-Encoding")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
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

    # -- personal accounts: sign-in and access levels (dashboard/accounts.py) --
    user = None
    session = None

    def _acc_cookie_name(self):
        return "__Host-ata_session" if _accounts.store().settings.secure_cookies else "ata_session"

    def _acc_token(self):
        name = self._acc_cookie_name()
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == name:
                return value
        return None

    def _acc_cookie(self, token, max_age=None):
        acc = _accounts.store()
        attrs = ["{0}={1}".format(self._acc_cookie_name(), token), "Path=/", "HttpOnly",
                 "SameSite=Lax", "Max-Age={0}".format(acc.settings.session_max_s
                                                      if max_age is None else max_age)]
        if acc.settings.secure_cookies:
            attrs.append("Secure")
        return {"Set-Cookie": "; ".join(attrs)}

    def _acc_ip(self):
        peer = self.client_address[0] if self.client_address else None
        # Behind an HTTPS reverse proxy on this machine (ATA_TRUST_PROXY=1, the
        # control plane's setting), every request comes from the proxy, so the
        # person's address is the entry the proxy added to X-Forwarded-For —
        # the last one. Believed only from a loopback peer, so nobody on the
        # network can claim another address (sign-in rate limits, audit).
        if peer in ("127.0.0.1", "::1") and _trust_proxy():
            forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
            if forwarded:
                return forwarded.split(":")[0] if forwarded.count(":") == 1 else forwarded[:64]
        return peer

    def _acc_json(self, limit=4096):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > limit:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _acc_origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return self.headers.get("Sec-Fetch-Site") in (None, "same-origin")
        host = (self.headers.get("Host") or "").lower()
        return origin.lower().rstrip("/") in ("http://" + host, "https://" + host)

    def _acc_base(self):
        return (os.environ.get("ATA_PUBLIC_URL") or
                "http://{0}".format(self.headers.get("Host") or "localhost")).rstrip("/")

    def _gate(self, method, route):
        """
        True when the request may go on to its handler. Without accounts, the
        access key decides, as before. With accounts, a signed-in person whose
        role holds the route's permission; the sign-in page and its own routes
        are answered here.
        """
        if not _accounts.enabled():
            if self._authorised():
                return True
            self._deny()
            return False
        from controlplane import rbac
        acc = _accounts.store()
        try:
            if route in _accounts.PUBLIC_ROUTES or route.startswith(_accounts.PUBLIC_PREFIXES):
                self._acc_public(acc, method, route)
                return False
            user, session, why = acc.users.resolve(self._acc_token())
            if user is None:
                extra = {}
                if why == "expired":
                    acc.audit.record("SESSION_EXPIRED", result="EXPIRED", ip=self._acc_ip())
                    extra = self._acc_cookie("", 0)
                if route.startswith("/api/"):
                    self._send(401, json.dumps({
                        "error": "session_expired" if why == "expired" else "not_signed_in",
                        "message": "Your session has expired. Sign in again." if why == "expired"
                        else "Sign in to continue."}), extra=extra)
                elif method == "GET" and why != "expired" and self._launch_redirect(route):
                    pass
                else:
                    from urllib.parse import quote
                    extra["Location"] = "/login?next={0}{1}".format(
                        quote(self.path if self.path.startswith("/") else "/"),
                        "&expired=1" if why == "expired" else "")
                    self._send(302, b"", "text/plain", extra=extra)
                return False
            self.user, self.session = user, session
            if method not in ("GET", "HEAD") and not (
                    self._acc_origin_ok() and hmac.compare_digest(
                        str(self.headers.get("X-CSRF-Token") or ""), str(session["csrf"]))):
                self._send(403, json.dumps({"error": "csrf", "message": "This request did not "
                                            "come from the Control Tower page. Reload and try "
                                            "again."}))
                return False
            if self._acc_own(acc, method, route):
                return False
            permission = _accounts.permission_for(method, route)
            if permission is None or not rbac.allowed(user, permission):
                acc.audit.record("ACCESS_DENIED", result="FORBIDDEN", user=user,
                                 target_type="permission", target_id=permission or "none",
                                 ip=self._acc_ip(), metadata={"route": route, "method": method})
                self._send(403, json.dumps({
                    "error": "forbidden", "permission": permission,
                    "message": "Your access level ({0}) does not allow this.".format(
                        rbac.ROLE_LABELS.get(user.get("role"), "none"))}))
                return False
            return True
        except ValueError:
            self._send(400, json.dumps({"error": "bad_request"}))
            return False

    def _acc_can(self, permission):
        """With accounts on, whether the signed-in person may; without, True."""
        if self.user is None:
            return True
        from controlplane import rbac
        return rbac.allowed(self.user, permission)

    def _acc_bus(self):
        """The business units the signed-in person may see on the hub: None =
        all (today every account; no BU limit is stored yet), or a set of BU
        codes when the account carries one ("bus")."""
        if self.user is None:
            return None
        bus = self.user.get("bus") if hasattr(self.user, "get") else None
        if not bus:
            return None
        return {str(b).strip().upper() for b in bus if str(b).strip()}

    def _acc_audit(self, action, result="SUCCESS", target_type=None, target_id=None,
                   metadata=None):
        if self.user is None:
            return
        try:
            _accounts.store().audit.record(action, result=result, user=self.user,
                                           target_type=target_type, target_id=target_id,
                                           ip=self._acc_ip(), metadata=metadata)
        except Exception:
            pass

    def _acc_public(self, acc, method, route):
        from controlplane.users import AccessError, public as public_user
        if route == "/favicon.ico":
            self._send_no_icon()
            return
        if route in ("/login", "/set-password") and method in ("GET", "HEAD"):
            self._send_file(ROOT.parent / "controlplane" / "static" / "login.html")
            return
        if route.startswith("/static/login/") and method in ("GET", "HEAD"):
            base = (ROOT.parent / "controlplane" / "static").resolve()
            candidate = (base / route[len("/static/"):]).resolve()
            if base in candidate.parents:
                self._send_file(candidate)
            else:
                self._send(404, json.dumps({"error": "not_found"}))
            return
        if route.startswith("/static/brand/") and method in ("GET", "HEAD"):
            candidate = (STATIC_DIR / route[len("/static/"):]).resolve()
            if route == "/static/brand/mantrac-logo.svg" and not candidate.is_file():
                # No official SVG installed: the PNG, instead of a 404 in every console.
                candidate = (STATIC_DIR / "brand" / "mantrac-logo.png").resolve()
            if STATIC_DIR.resolve() in candidate.parents:
                self._send_file(candidate)
            else:
                self._send(404, json.dumps({"error": "not_found"}))
            return
        if route == "/api/auth/config":
            self._send(200, json.dumps({"local": True, "sso": False,
                                        "sso_label": "Sign in with Microsoft"}))
            return
        ip = self._acc_ip() or ""
        if route == "/api/auth/login" and method == "POST":
            if not self._acc_origin_ok():
                self._send(403, json.dumps({"error": "csrf"}))
                return
            data = self._acc_json()
            email = str(data.get("email") or "").strip().lower()[:254]
            if not acc.limiter.hit("login-ip:" + ip, 20, 600) or \
                    not acc.limiter.hit("login-email:" + email, 10, 600):
                acc.audit.record("LOGIN_FAILED", result="RATE_LIMITED", ip=ip,
                                 metadata={"email": email})
                self._send(429, json.dumps({"ok": False, "message": "Too many sign-in "
                                            "attempts. Wait ten minutes and try again."}))
                return
            user, reason = acc.users.authenticate(email, data.get("password"), ip)
            if user is None:
                acc.audit.record("LOGIN_FAILED", result="DENIED", ip=ip,
                                 metadata={"email": email, "reason": reason})
                self._send(401, json.dumps({"ok": False, "message": "That email or password "
                                                                    "is not right."}))
                return
            token, csrf = acc.users.start_session(user, ip, self.headers.get("User-Agent"))
            acc.audit.record("LOGIN", user=user, ip=ip, metadata={"method": "password"})
            self._send(200, json.dumps({"ok": True, "csrf": csrf, "user": public_user(
                acc.users.get(user["user_id"]))}, default=str), extra=self._acc_cookie(token))
            return
        if route == "/api/auth/set-password" and method == "POST":
            if not self._acc_origin_ok():
                self._send(403, json.dumps({"error": "csrf"}))
                return
            data = self._acc_json()
            if not acc.limiter.hit("setpw:" + ip, 10, 600):
                self._send(429, json.dumps({"error": "too_many_requests"}))
                return
            try:
                user = acc.users.set_password(str(data.get("token") or ""),
                                              str(data.get("password") or ""), ip=ip)
            except AccessError as error:
                self._send(400, json.dumps({"ok": False, "message": str(error)}))
                return
            self._send(200, json.dumps({"ok": True, "email": user["work_email"]}))
            return
        self._send(404, json.dumps({"error": "not_found"}))

    def _acc_own(self, acc, method, route):
        """The signed-in person's own routes and user management. True if handled."""
        from controlplane import rbac
        from controlplane.users import AccessError, public as public_user
        user, ip = self.user, self._acc_ip()
        if route == "/api/auth/me" and method == "GET":
            self._send(200, json.dumps({
                "user": public_user(user), "permissions": rbac.permissions_of(user),
                "csrf": self.session["csrf"], "mode": "accounts",
                # The chat window says so when conversations are being kept.
                "conversations": {"saved": _conversations.enabled(), "days": _conversations.days()},
                "session": {"expires_at": self.session["expires_at"],
                            "idle_s": acc.settings.session_idle_s}}, default=str))
            return True
        if route == "/api/auth/logout" and method == "POST":
            acc.users.end_session(self._acc_token())
            acc.audit.record("LOGOUT", user=user, ip=ip)
            self._send(200, json.dumps({"ok": True}), extra=self._acc_cookie("", 0))
            return True
        if route == "/api/me/prefs" and method in ("POST", "PUT"):
            try:
                prefs = acc.users.set_prefs(user["user_id"], self._acc_json(2048))
            except AccessError as error:
                self._send(400, json.dumps({"ok": False, "message": str(error)}))
                return True
            self._send(200, json.dumps({"ok": True, "prefs": prefs}, default=str))
            return True
        if route == "/api/admin/rbac" and method == "GET":
            self._send(200, json.dumps({"roles": [{"role": r, "label": rbac.ROLE_LABELS[r]}
                                                  for r in rbac.ROLES],
                                        "matrix": rbac.matrix()}))
            return True
        if not route.startswith("/api/admin/"):
            return False
        need = "audit.view" if route.startswith("/api/admin/audit") else "users.manage"
        if not rbac.allowed(user, need):
            return False                      # the gate refuses it, and audits that
        if route == "/api/admin/conversations" and method == "GET":
            from urllib.parse import urlparse, parse_qs
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            rows = _conversations.list_(email=q.get("user") or None,
                                        limit=int(q.get("limit") or 200) if str(q.get("limit") or "200").isdigit() else 200)
            acc.audit.record("VIEW_CONVERSATIONS", user=user, ip=ip, target_type="user",
                             target_id=q.get("user") or "all")
            self._send(200, json.dumps({"conversations": rows, "days": _conversations.days()}))
            return True
        if route == "/api/admin/audit" and method == "GET":
            from urllib.parse import urlparse, parse_qs
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            self._send(200, json.dumps({"events": acc.audit.list(
                limit=min(int(q.get("limit") or 200), 500), action=q.get("action") or None,
                user_email=q.get("user") or None)}, default=str))
            return True
        try:
            if route == "/api/admin/users" and method == "GET":
                self._send(200, json.dumps({"users": acc.users.list(), "roles": list(rbac.ROLES)},
                                           default=str))
                return True
            if route == "/api/admin/users" and method == "POST":
                data = self._acc_json()
                created, invite = acc.users.create(data.get("email"), data.get("display_name"),
                                                   data.get("role"), by=user, ip=ip)
                self._send(200, json.dumps({"ok": True, "user": public_user(created),
                                            "invite_link": _accounts.link(self._acc_base(), invite)},
                                           default=str))
                return True
            parts = route.split("/")
            uid = parts[4] if len(parts) > 4 else ""
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", uid):
                self._send(404, json.dumps({"error": "not_found"}))
                return True
            if len(parts) == 5 and method == "PATCH":
                data = self._acc_json(2048)
                changed = acc.users.update(uid, user, role=data.get("role"),
                                           active=data.get("active") if "active" in data else None,
                                           display_name=data.get("display_name"), ip=ip)
                self._send(200, json.dumps({"ok": True, "user": public_user(changed)}, default=str))
                return True
            if len(parts) == 6 and parts[5] == "reset" and method == "POST":
                invite = acc.users.reset_access(uid, user, ip=ip)
                self._send(200, json.dumps({
                    "ok": True, "invite_link": _accounts.link(self._acc_base(), invite),
                    "message": "Signed out everywhere." + (
                        " Share the new link so they can set a password." if invite else "")}))
                return True
            if len(parts) == 6 and parts[5] == "revoke" and method == "POST":
                ended = acc.users.revoke_sessions(uid)
                acc.audit.record("REVOKE_SESSIONS", user=user, target_type="user", target_id=uid,
                                 ip=ip, metadata={"sessions_ended": ended})
                self._send(200, json.dumps({"ok": True, "sessions_ended": ended}))
                return True
        except AccessError as error:
            self._send(400, json.dumps({"ok": False, "message": str(error)}))
            return True
        self._send(404, json.dumps({"error": "not_found"}))
        return True

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

    # The pre-launch page (dashboard/launch.py) is public in every access
    # mode and carries no data: a date, a greeting, a licensed soundtrack.
    # It never opens anything the gate protects.
    LAUNCH_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                  "media-src 'self' https:; connect-src 'self'; base-uri 'none'; "
                  "form-action 'none'; frame-ancestors 'none'")

    def _launch_public(self, route):
        from dashboard import launch
        if route == "/launch":
            self._send_file(launch.LAUNCH_DIR / "launch.html", extra={
                "Content-Security-Policy": self.LAUNCH_CSP, "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin"})
            return True
        if route == "/favicon.ico":
            self._send_no_icon()
            return True
        if route == "/api/launch":
            self._send(200, json.dumps(launch.config()))
            return True
        if route == "/static/brand/mantrac-logo.png":
            self._send_file(STATIC_DIR / "brand" / "mantrac-logo.png")
            return True
        if route.startswith("/static/launch/"):
            path = launch.asset(route)
            if path is None:
                self._send(404, json.dumps({"error": "not_found"}))
            else:
                self._send_file(path, extra={"X-Content-Type-Options": "nosniff"})
            return True
        return False

    def _launch_redirect(self, route):
        """
        A signed-out visitor to the dashboard's front door sees the launch page
        until launch. Only with personal sign-in: the page's way in is /login,
        which the access-key mode does not have.
        """
        if route != "/":
            return False
        from dashboard import launch
        try:
            if not launch.pending():
                return False
        except Exception:
            return False
        self._send(302, b"", "text/plain", extra={"Location": "/launch"})
        return True

    def do_GET(self):
        route = self.path.split("?")[0].rstrip("/") or "/"
        if self._launch_public(route):
            return
        if not self._gate("GET", route):
            return

        if route == "/":
            self._send_file(STATIC_DIR / "index.html")
            return

        if route == "/hub":
            # The unified Control Tower (CONTROL_TOWER_PLAN.md), next to the
            # current pages until each of its views has replaced theirs.
            self._send_file(STATIC_DIR / "hub.html")
            return

        if route == "/api/hub/list":
            # The rows behind one hub number (a stage, late, past due), read-only.
            from dashboard import hub as _hub
            from urllib.parse import urlsplit, parse_qs
            q = parse_qs(urlsplit(self.path).query)
            stage = (q.get("stage") or ["all"])[0]
            bu = (q.get("bu") or [None])[0]
            if stage not in ("all", "late", "pastdue") + tuple(x[0] for x in _hub.STAGES) or \
                    (bu is not None and not re.match(r"^[A-Z]{2,3}$", bu)):
                self._send(400, json.dumps({"error": "unknown stage or BU"}))
                return
            if not self._acc_can("clearance.view"):
                self._send(403, json.dumps({"error": "clearance.view needed"}))
                return
            allowed = self._acc_bus()
            if bu is not None and allowed is not None and bu not in allowed:
                self._send(403, json.dumps({"error": "not your business unit"}))
                return
            out = _hub.stage_list(stage, bu, allowed=allowed)
            self._send(200, json.dumps(out or {"empty": True}, default=str))
            return

        if route == "/api/hub/overview":
            # Read-only: the run's snapshot and the loaded eHub export, counted
            # once (dashboard/hub.py); each part only for who may see it.
            from dashboard import hub as _hub
            self._send(200, json.dumps(_hub.overview(_assistant_state(), self._acc_can, self._acc_bus()),
                                       default=str))
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
            if relative == "brand/mantrac-logo.svg" and not candidate.is_file():
                candidate = (STATIC_DIR / "brand" / "mantrac-logo.png").resolve()   # no SVG installed
            if STATIC_DIR.resolve() in candidate.parents or candidate == STATIC_DIR.resolve():
                self._send_file(candidate)
            else:
                self._send(403, json.dumps({"error": "forbidden"}))
            return

        if route == "/api/export.csv":
            self._export_csv()
            return

        if route == "/api/report.pdf":
            self._report_pdf()
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

        if route == "/api/atlas/perf":
            # Where ATLAS's questions spend their time (stage names and
            # milliseconds only — no question text): intelligence/trace.py.
            from intelligence import trace as _trace, kb_embed as _emb
            self._send(200, json.dumps({"recent": _trace.stats(), "last_hour": _trace.stats(3600),
                                        "semantic": _emb.status()}, default=str))
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

        if route == "/clearance":
            self._send_file(STATIC_DIR / "clearance.html")
            return

        if route == "/api/clearance/status":
            _clearance.check_inbox()
            self._send(200, json.dumps(_clearance.status(), default=str))
            return

        if route == "/api/clearance/contacts":
            self._send(200, json.dumps(_clearance.contacts()))
            return

        if route == "/api/clearance/escalations":
            self._send(200, json.dumps({"escalations": _clearance.escalations()}))
            return

        if route == "/api/clearance/draft":
            # ATLAS's email for this shipment's situation (a draft; nothing is sent).
            from urllib.parse import parse_qs, urlparse as _up
            qs = parse_qs(_up(self.path).query)
            rows = _clearance.find((qs.get("awb") or [""])[0][:60])
            bu = (qs.get("bu") or [""])[0][:20]
            row = next((r for r in rows if r["bu"] == bu), rows[0] if rows else None)
            self._send(200, json.dumps({"draft": _clearance.draft_mail(
                row, (self.user or {}).get("display_name") or "")}))
            return

        if route == "/api/clearance/activity":
            from urllib.parse import parse_qs, urlparse as _up
            qs = parse_qs(_up(self.path).query)
            awb = (qs.get("awb") or [""])[0][:60]
            bu = (qs.get("bu") or [""])[0][:20] or None
            self._send(200, json.dumps({
                "contacts": _clearance.contact_log(awb, bu=bu),
                "escalations": [e for e in _clearance.escalations()
                                if e["awb"] == awb and (bu is None or e["bu"] == bu)]}))
            return

        if route == "/api/search":
            # One search across the run, the Clearance export and PO jobs.
            from urllib.parse import urlparse, parse_qs
            from dashboard import search as _search
            q = (parse_qs(urlparse(self.path).query).get("q") or [""])[0]
            self._send(200, json.dumps(_search.search(q, self._acc_can, _assistant_state()), default=str))
            return

        if route.startswith("/api/knowledge/"):
            self._knowledge_get(route)
            return


        if route == "/api/todo":
            # What needs a person, from the records; only what this person may open.
            from dashboard import todo as _todo
            self._send(200, json.dumps(_todo.build(self._acc_can, _assistant_state()), default=str))
            return

        if route == "/api/clearance/map":
            # The Overview's map: per BU counts from the export (dashboard/clearance.py).
            summary = _clearance.map_summary()
            self._send(200, json.dumps(summary if summary else {"empty": True}, default=str))
            return

        if route == "/api/clearance/places":
            # Which BU is which country on the map (dashboard/clearance.py).
            self._send(200, json.dumps({"places": _clearance.places()}))
            return

        if route == "/api/clearance":
            # The loaded eHub export (a newer file in the inbox folder starts
            # being read first; the page polls the status and reloads).
            _clearance.check_inbox()
            path = _clearance.data_path()
            if path.exists():
                self._send(200, path.read_bytes())
            else:
                # The server folder is named only to those who may load an export.
                self._send(200, json.dumps({"empty": True, "status": _clearance.status(),
                                            "inbox": str(_clearance.inbox())
                                            if self._acc_can("clearance.upload") else None},
                                           default=str))
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

    def do_PATCH(self):
        route = self.path.split("?")[0].rstrip("/") or "/"
        if self._gate("PATCH", route):
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        route = self.path.split("?")[0].rstrip("/") or "/"
        if not self._gate("POST", route):
            return

        if route == "/api/control":
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(min(length, 2000)) or b"{}")
                action = str(payload.get("action", ""))[:20]
                reference = str(payload.get("reference", ""))[:40]
            except Exception:
                self._send(400, json.dumps({"error": "bad request"}))
                return
            if action in ("stop", "pause", "cancel") and not self._acc_can("runs.stop"):
                self._send(403, json.dumps({"error": "forbidden", "permission": "runs.stop"}))
                return
            accepted, message = control.request(action, reference)
            self._acc_audit({"start": "START_RUN", "stop": "STOP_RUN", "pause": "PAUSE_RUN",
                             "resume": "RESUME_RUN", "reprocess": "REPROCESS"}.get(
                                 action, "START_RUN"), "SUCCESS" if accepted else "REFUSED",
                            "run", reference or None, {"action": action})
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
            self._acc_audit("OPEN_HUMAN_ACTION" if op == "open" else "CONTINUE_HUMAN_ACTION",
                            "SUCCESS" if accepted else "REFUSED", "human_action", action_id,
                            {"run_id": run_id, "op": op})
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

        if route in ("/api/clearance/contacts", "/api/clearance/escalate",
                     "/api/clearance/resolve", "/api/clearance/contact-log"):
            self._clearance_action(route)
            return

        if route in ("/api/knowledge/propose", "/api/knowledge/review", "/api/knowledge/status",
                     "/api/knowledge/ingest"):
            self._knowledge_action(route)
            return

        if route == "/api/clearance/upload":
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > _clearance.MAX_UPLOAD:
                self._send(413, json.dumps({"accepted": False,
                                            "message": "Send one Excel file up to 40 MB."}))
                return
            from urllib.parse import unquote
            # The page sends the name URL-encoded (HTTP headers are Latin-1 only).
            name = unquote(str(self.headers.get("X-Filename") or "export.xlsx"))[:120]
            by = (self.user or {}).get("work_email") or "access key"
            accepted, message = _clearance.upload(self.rfile.read(length), name, by)
            self._acc_audit("UPLOAD_CLEARANCE_EXPORT", "SUCCESS" if accepted else "REFUSED",
                            "clearance_export", name[:64], {"bytes": length})
            self._send(200, json.dumps({"accepted": accepted, "message": message}))
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

        from intelligence import trace as _trace
        _trace.begin()
        _parse = _trace.span("server.parse")
        _parse.__enter__()
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
                       # Asked from the Clearance page (questions are about its export).
                       # The page the question was asked from (a known page name only).
                       "page": str(raw_context.get("page") or "") if str(raw_context.get("page") or "") in (
                           "clearance", "overview", "live", "shipments", "analysis", "exceptions", "human",
                           "logs", "systems", "atlas", "settings", "access", "po") else "",
                       # The filters on that page, so ATLAS answers about what is on
                       # screen. Short strings only; checked against the export later.
                       "view": {k: str(v)[:80] for k, v in (raw_context.get("view") or {}).items()
                                if k in ("bu", "mode", "div", "ffw", "agent", "status", "group", "month", "flag", "more")
                                and isinstance(v, str)}
                       if raw_context.get("page") == "clearance" and isinstance(raw_context.get("view"), dict)
                       else {},
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
            _trace.end()
            self._send(400, json.dumps({"error": "bad request"}))
            return
        _parse.__exit__(None, None, None)

        # The assistant only ever receives a snapshot. It has no handle on the
        # bridge, the browser or the credentials, so it cannot act on anything.
        # Untrimmed: the assistant should see the whole run, not the wire view.
        from intelligence import research as _research
        _research.progress_start(context.get("progress_id"))
        if self.user is not None:
            # ATLAS answers about PO jobs only for a role that may see them.
            context["po"] = "1" if self._acc_can("po.view") else "0"
            # The deployment guides (server names, addresses) are for Admins.
            context["admin"] = "1" if self._acc_can("users.manage") else "0"
            # Clearance figures only for a role that may open the Clearance page.
            context["clearance"] = "1" if self._acc_can("clearance.view") else "0"
        try:
            with _trace.span("server.state"):
                state = _assistant_state()
            with _trace.span("answer"):
                reply = assistant.answer(question, state, context)
        finally:
            _research.progress_done(context.get("progress_id"))
        _record = _trace.span("server.record")
        _record.__enter__()
        if self.user is not None:
            # Signed-in people are told in the chat window that this is kept.
            _conversations.record(self.user, question, reply)
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
        if wanted and not self._acc_can("runs.start"):
            reply["answer"] = ("Your access level can't start a re-run. Someone with Operator "
                               "or Admin access can.")
            reply["accepted"] = False
        elif wanted:
            accepted, message = control.request(
                wanted.get("action"), wanted.get("reference"))
            self._acc_audit("REPROCESS", "SUCCESS" if accepted else "REFUSED", "shipment",
                            wanted.get("reference"))
            reply["answer"] = message
            reply["accepted"] = accepted
        if not self._acc_can("human.act"):
            if reply.pop("operation", None):
                reply["answer"] = (reply.get("answer") or "") + (
                    "\n\nYour access level can't open Human Actions; an Operator can.")
            reply["buttons"] = [b for b in reply.get("buttons") or []
                                if (b.get("action") or {}).get("type") != "human_open"]
        if not self._acc_can("evidence.view") and reply.get("evidence"):
            reply["evidence"] = []
            reply["answer"] = (reply.get("answer") or "") + (
                "\n\nScreenshots are shown to Operators and Admins.")
        _record.__exit__(None, None, None)
        # Where this question's time went: stage names and milliseconds only.
        timing = _trace.end(reply.get("intent"))
        if timing:
            reply["timing"] = {k: timing[k] for k in ("total_ms", "first_useful_ms", "first_useful_by", "stages")}
            if timing["model"]:
                reply["timing"]["model"] = timing["model"]
        self._send(200, json.dumps(reply), extra={"Server-Timing": _trace.server_timing(timing)} if timing else None)

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
        actor = self.user["work_email"] if self.user else "local operator"
        kind, status, payload = po_web.handle(po_service.local(), method, route, body,
                                              actor, self._acc_can)
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

    def _knowledge_get(self, route):
        """ATLAS's logistics knowledge, read-only (intelligence/kb.py)."""
        from urllib.parse import urlparse, parse_qs
        from intelligence import kb as _kb, kb_govern as _gov
        qs = parse_qs(urlparse(self.path).query)
        arg = lambda k, d=None: (qs.get(k) or [d])[0]
        k = _kb.kb()
        if route == "/api/knowledge/search":
            try:
                limit = max(1, min(int(arg("limit", "10") or 10), 30))
            except ValueError:
                self._send(400, json.dumps({"error": "limit must be a number"}))
                return
            res = _kb.search(str(arg("q", ""))[:300], limit=limit,
                             domain=arg("domain"), kind=arg("kind"), scope=arg("scope"),
                             include_history=arg("history") == "1", approved_only=arg("approved") == "1")
            self._send(200, json.dumps({"results": [{"id": r["id"], "score": r["score"], "named": r["named"],
                                                     "flags": r["flags"], "term": r["item"]["term"],
                                                     "definition": r["item"]["definition"],
                                                     "review_status": r["item"].get("review_status"),
                                                     "evidence_status": r["item"].get("evidence_status")}
                                                    for r in res]}, default=str))
            return
        if route == "/api/knowledge/item":
            it = k.get(str(arg("id", ""))[:80])
            if it is None:
                self._send(404, json.dumps({"error": "not found"}))
                return
            self._send(200, json.dumps({"item": it, "citations": k.cite(it), "history": _gov.history(it["id"]),
                                        "related": [{"id": r, "type": t, "direction": d}
                                                    for r, t, d, _ in k.related(it["id"])]}, default=str))
            return
        if route == "/api/knowledge/stats":
            out = {"knowledge": k.stats(), "governance": _gov.stats(), "errors": k.errors[:20],
                   "curriculum": [{"id": d["id"], "title": d["title"], "title_ar": d.get("title_ar")}
                                  for d in k.curriculum["domains"]]}
            if self._acc_can("knowledge.review"):
                out["retrieval"] = _knowledge_retrieval_stats()
            self._send(200, json.dumps(out, default=str))
            return
        if route == "/api/knowledge/lessons":
            from intelligence import kb_lessons as _les
            self._send(200, json.dumps({"lessons": _les.lessons()}, default=str))
            return
        if route == "/api/knowledge/proposals":
            self._send(200, json.dumps({"proposals": sorted(_gov.proposals().values(),
                                                            key=lambda p: -(p.get("seq") or 0))[:200]}, default=str))
            return
        self._send(404, json.dumps({"error": "not found"}))

    def _knowledge_action(self, route):
        """Propose (knowledge.propose); review, status and ingest (knowledge.review)."""
        from intelligence import kb_govern as _gov
        try:
            body = self._acc_json(8 * 1024 * 1024 if route == "/api/knowledge/ingest" else 65536)
        except (ValueError, TypeError):
            body = None
        if not isinstance(body, dict):
            self._send(400, json.dumps({"ok": False, "message": "Bad request."}))
            return
        by = (self.user or {}).get("work_email") or "access key"
        if by == "access key" and route in ("/api/knowledge/review", "/api/knowledge/status"):
            # Everyone holding the key is the same "person": four eyes cannot be told apart.
            self._send(200, json.dumps({"ok": False, "message": "Reviewing knowledge needs named accounts "
                                        "(two different people); it is not possible with the shared access key."}))
            return
        try:
            if route == "/api/knowledge/propose":
                out = _gov.propose(body.get("item"), by=by, kind=str(body.get("kind") or "new"),
                                   note=body.get("note"), observation=body.get("observation"))
                self._acc_audit("KNOWLEDGE_PROPOSED", "SUCCESS", "knowledge", out["proposal_id"])
            elif route == "/api/knowledge/review":
                out = _gov.review(str(body.get("proposal_id") or "")[:40], str(body.get("decision") or ""), by,
                                  note=body.get("note"), acknowledge_flags=bool(body.get("acknowledge_flags")))
                self._acc_audit("KNOWLEDGE_" + str(body.get("decision") or "").upper(), "SUCCESS", "knowledge",
                                str(body.get("proposal_id") or "")[:40])
            elif route == "/api/knowledge/status":
                out = _gov.set_status(str(body.get("id") or "")[:80], str(body.get("status") or ""), by,
                                      reason=body.get("reason"), superseded_by=body.get("superseded_by"))
                self._acc_audit("KNOWLEDGE_STATUS", "SUCCESS", "knowledge", str(body.get("id") or "")[:80],
                                {"status": body.get("status")})
            else:
                import base64
                data = base64.b64decode(body["data_base64"]) if body.get("data_base64") else None
                out = _gov.ingest(str(body.get("name") or "document.md")[:200], by, text=body.get("text"),
                                  data=data, title=body.get("title"), authority=str(body.get("authority") or "internal"),
                                  jurisdiction=body.get("jurisdiction"), domain=str(body.get("domain") or "P")[:1])
                self._acc_audit("KNOWLEDGE_INGESTED", "SUCCESS", "knowledge", out["doc"]["id"],
                                {"proposals": len(out["proposals"])})
        except _gov.GovernanceError as e:
            self._send(200, json.dumps({"ok": False, "message": str(e)}))
            return
        except (ValueError, KeyError, TypeError) as e:
            self._send(400, json.dumps({"ok": False, "message": "Bad request: {0}".format(type(e).__name__)}))
            return
        except Exception as e:
            self._send(500, json.dumps({"ok": False, "message": "The knowledge action failed ({0}).".format(
                type(e).__name__)}))
            return
        self._send(200, json.dumps({"ok": True, "result": out}, default=str))

    def _clearance_action(self, route):
        """Contacts (Admin), escalations (Operator, Admin), the call / mail log (everyone)."""
        try:
            body = self._acc_json(65536)
        except (ValueError, TypeError):
            body = None
        if not isinstance(body, dict):
            self._send(400, json.dumps({"ok": False, "message": "Bad request."}))
            return
        by = (self.user or {}).get("work_email") or "access key"
        if route == "/api/clearance/contacts":
            ok, message = _clearance.save_contacts(body, by)
            if ok:
                self._acc_audit("CLEARANCE_CONTACTS_CHANGED", "SUCCESS", "clearance_contacts", None,
                                {k: len(_clearance.contacts()[k]) for k in _clearance.KINDS})
            self._send(200, json.dumps({"ok": ok, "message": message,
                                        "contacts": _clearance.contacts()}))
            return
        if route == "/api/clearance/resolve":
            ok, message, entry = _clearance.resolve(str(body.get("id") or "")[:20],
                                                    body.get("note"), by)
            if ok:
                self._acc_audit("RESOLVE_ESCALATION", "SUCCESS", "shipment", entry["awb"][:64])
            self._send(200, json.dumps({"ok": ok, "message": message, "escalation": entry}))
            return
        # The shipment is taken from the loaded export, never from what the page sent.
        rows = _clearance.find(str(body.get("awb") or "")[:60])
        row = next((r for r in rows if r["bu"] == body.get("bu")), rows[0] if rows else None)
        if route == "/api/clearance/escalate":
            ok, message, entry = _clearance.escalate(row, body.get("reason"), by)
            if ok:
                self._acc_audit("ESCALATE_SHIPMENT", "SUCCESS", "shipment", entry["awb"][:64],
                                {"bu": entry["bu"], "step": entry["step"]})
            self._send(200, json.dumps({"ok": ok, "message": message, "escalation": entry}))
            return
        kind = str(body.get("kind") or "")
        ok = _clearance.log_contact(row, kind, body.get("to"), by)
        if ok:
            self._acc_audit("CLEARANCE_CALL" if kind == "call" else "CLEARANCE_MAIL", "SUCCESS",
                            "shipment", row["awb"][:64], {"to": str(body.get("to") or "")[:120]})
        self._send(200, json.dumps({"ok": ok}))

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

    def _report_pdf(self):
        """
        A PDF report ATLAS offered in the chat, made now from the live state:
        ?kind=run|failed|review|shipment&ref=<reference>. Read-only.
        """
        from urllib.parse import urlparse, parse_qs
        from intelligence import pdf_report

        q = parse_qs(urlparse(self.path).query)
        kind = (q.get("kind") or ["run"])[0]
        ref = re.sub(r"[^A-Za-z0-9 _/-]", "", (q.get("ref") or [""])[0])[:60] or None
        try:
            body, name = pdf_report.build(kind, bridge.snapshot(), ref)
        except Exception as error:
            body, name = None, "the report could not be made: {0}".format(str(error)[:160])
        if body is None:
            self._send(404 if kind == "shipment" else 503, json.dumps({"error": name}))
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition", 'attachment; filename="{0}"'.format(name))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._maybe_set_cookie()
        self.end_headers()
        self.wfile.write(body)

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


# ============================================================
# REPLAY — rebuild state from a real finished run
# ============================================================

LOG_LINE = re.compile(r"^\[(?P<ts>[\d\-: ]+)\]\s(?P<msg>.*)$")


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
    # Behind a domain (ATA_PUBLIC_URL / ATA_TRUST_PROXY) a proxy on this
    # machine forwards the network to loopback: shared, whatever the bind.
    behind_proxy = bool((os.environ.get("ATA_PUBLIC_URL") or "").strip()) or _trust_proxy()
    if not ACCESS_KEY and (host not in ("127.0.0.1", "localhost") or behind_proxy):
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
    # Load the knowledge once, and build the optional semantic index
    # (ATLAS_EMBED_MODEL), in the background: the first question never pays.
    def _warm_knowledge():
        try:
            from intelligence import kb as _kbm, kb_embed as _emb
            _emb.warm(_kbm.kb())
        except Exception:
            pass
    threading.Thread(target=_warm_knowledge, name="atlas-knowledge-warm", daemon=True).start()
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
            if os.name == "nt":
                # Windows lets a second process bind a port another one is
                # listening on when SO_REUSEADDR is set (socketserver's
                # default). Two towers then share port 8787 and connections go
                # to either at random — an old copy answered the browser while
                # the gateway's health probe timed out. Exclusive use instead:
                # a second tower now fails with "port already in use".
                allow_reuse_address = False

                def server_bind(self):
                    if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    ThreadingHTTPServer.server_bind(self)

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

        signin = False
        try:
            signin = _accounts.enabled()
        except Exception:
            pass
        # With personal sign-in on, the link is the sign-in page; the key is not used.
        suffix = "login" if signin else ("?key={0}".format(ACCESS_KEY) if ACCESS_KEY else "")
        local = "http://127.0.0.1:{0}/{1}".format(port, suffix)
        if signin:
            print("  Personal sign-in is ON: everyone signs in with their own account "
                  "(Settings -> Access levels).", flush=True)

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
            if not ACCESS_KEY and not signin:
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
                "  Another Control Tower is probably still running (Task Scheduler →\n"
                "  Mantrac Tower, or a START_TOWER window). Stop it first: only one may run."
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
