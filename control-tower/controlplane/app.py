"""
The control plane's HTTP application.

Every request passes the same gate, in this order:

    1. security headers on every response, HSTS when served over HTTPS
    2. rate limit by client address
    3. /worker/v1/*  -> worker bearer token, or 401
       public pages  -> /healthz, /login, /set-password, /auth/*, brand assets
       everything else -> a live session, or 401 / a redirect to /login
    4. state-changing requests: the session's CSRF token in X-CSRF-Token and
       an Origin that is this site, or 403
    5. the route's permission (rbac.py), or 403 — checked here, server-side,
       whatever the page drew

The dashboard page and its assets are the existing ones (dashboard/static);
this module serves them, with the run state coming from the worker's
reports instead of an in-process bridge. ATLAS answers from that state.
"""

import json
import re
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import rbac
from .audit import Audit
from .observations import Observations
from .config import Settings
from .db import Database, loads, now
from .oidc import EntraID, SSOError
from .relay import Relay, ClaimError, clean_events
from .runs import Orchestrator, RunError, ACTIVE, ENDED, public_run
from .security import RateLimiter, page_headers, api_headers, script_hashes, same
from .users import Users, AccessError, public as public_user

from dashboard import server as tower_server
from intelligence import verification as V
from dashboard import assistant
from po import service as po_service, store as po_store, web as po_web
from dashboard.bridge import ControlTowerState

HERE = Path(__file__).resolve().parent
LOGIN_PAGE = HERE / "static" / "login.html"
MAX_JSON = 64 * 1024
MAX_STATE = 12 * 1024 * 1024
MAX_INTEL = 8 * 1024 * 1024
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_EMPTY = ControlTowerState()

PUBLIC_PREFIXES = ("/static/brand/", "/static/login/")
PUBLIC_ROUTES = ("/healthz", "/login", "/set-password", "/api/auth/login",
                 "/api/auth/config", "/api/auth/set-password", "/auth/sso/start",
                 "/auth/sso/callback")

# Which permission each authenticated API route needs. Anything not listed
# here and not handled explicitly is a 404 — there is no default allow.
ROUTE_PERMISSIONS = {
    ("GET", "/api/state"): "dashboard.view",
    ("GET", "/api/stream"): "dashboard.view",
    ("GET", "/api/atlas"): "dashboard.view",
    ("GET", "/api/atlas/learning"): "dashboard.view",
    ("GET", "/api/atlas/maturity"): "dashboard.view",
    ("GET", "/api/atlas/plan"): "dashboard.view",
    ("GET", "/api/atlas/proposals"): "dashboard.view",
    ("GET", "/api/ml"): "dashboard.view",
    ("GET", "/api/runs"): "runs.view",
    ("GET", "/api/export.csv"): "runs.view",
    ("GET", "/api/health"): "health.view",
    ("GET", "/api/observations"): "health.view",
    ("GET", "/api/evidence"): "evidence.view",
    ("GET", "/api/evidence/file"): "evidence.view",
    ("POST", "/api/evidence/upload"): "evidence.upload",
    ("POST", "/api/ask"): "atlas.chat",
    ("GET", "/api/ask/progress"): "atlas.chat",
    ("GET", "/api/atlas/llm"): "dashboard.view",
    ("POST", "/api/feedback"): "atlas.chat",
    ("POST", "/api/runs"): "runs.start",
    ("POST", "/api/human"): "human.act",
    ("GET", "/api/admin/users"): "users.manage",
    ("POST", "/api/admin/users"): "users.manage",
    ("GET", "/api/admin/audit"): "audit.view",
    ("GET", "/api/admin/workers"): "settings.manage",
    ("POST", "/api/admin/workers"): "settings.manage",
    ("GET", "/api/admin/rbac"): "users.manage",
}


class App(object):
    """Everything a request needs, built once."""

    def __init__(self, settings=None):
        self.s = settings or Settings()
        self.db = Database(self.s.database_url)
        self.audit = Audit(self.db)
        self.users = Users(self.db, self.audit, self.s)
        self.orch = Orchestrator(self.db, self.audit, self.s)
        # The real eHub, as the worker saw it. Nothing here contacts eHub.
        self.observations = Observations(self.db, self.audit)
        self.relay = Relay(self.db, self.audit, self.s)
        self.entra = EntraID(self.s, self.db) if self.s.entra_enabled else None
        self.limiter = RateLimiter()
        self.orch.on_state = self._follow_claims
        # PO Automation. Jobs go to a worker (it holds the Hub browser); email
        # goes from here, through Microsoft Graph. One service, registered so
        # ATLAS reads the same records.
        self.po = po_service.register(po_service.PoService(
            store=po_store.Store(folder=self.s.po_dir),
            launcher=po_service.WorkerLauncher(self._po_dispatch, self._po_dispatch_run),
            audit=self._po_audit))
        self._closed = {}
        self._synced = None
        self._index = None
        self._stop = threading.Event()

    # -- PO Automation -------------------------------------------------------

    def _po_dispatch(self, record):
        worker = self.orch.po_worker()
        if worker is None:
            raise RuntimeError("no worker is online")
        self.orch.enqueue(worker["worker_id"], "po_process",
                          {"po_id": record["po_id"], "record": record})
        return worker["worker_id"]

    def _po_dispatch_run(self, no_email):
        """Start PO Automation on the PO worker: the whole automatic run."""
        worker = self.orch.po_worker()
        if worker is None:
            raise RuntimeError("no worker is online")
        self.orch.enqueue(worker["worker_id"], "po_sweep", {"no_email": bool(no_email)})
        return worker["worker_id"]

    def _po_audit(self, action, result="SUCCESS", actor=None, target=None, metadata=None):
        user = actor if isinstance(actor, dict) else (
            self.users.by_email(actor) if isinstance(actor, str) and "@" in actor else None)
        meta = dict(metadata or {})
        if isinstance(actor, str) and user is None:
            meta["by"] = actor
        self.audit.record(action, result=result, user=user, target_type="po", target_id=target,
                          run_id=(metadata or {}).get("run_id") or target, metadata=meta)

    # -- background ----------------------------------------------------------

    def start_background(self):
        def sweep():
            while not self._stop.wait(5):
                try:
                    self.po.expire_stale()
                except Exception:
                    pass
                try:
                    self.orch.sweep()
                except Exception:
                    pass
        threading.Thread(target=sweep, daemon=True, name="ata-sweep").start()

    def stop(self):
        self._stop.set()

    # -- the dashboard page ---------------------------------------------------

    def index(self):
        """index.html and the CSP hash of its inline script, read once."""
        path = self.s.static_dir / "index.html"
        stamp = path.stat().st_mtime
        if self._index is None or self._index[0] != stamp:
            html = path.read_text(encoding="utf-8")
            self._index = (stamp, html.encode("utf-8"), script_hashes(html))
        return self._index[1], self._index[2]

    # -- authoritative state for the dashboard --------------------------------

    def payload(self, user, since_cold=None):
        """
        What the dashboard draws: the current run's state as the worker last
        reported it, the control plane's own record of that run, and the
        platform around it. Nothing is reconstructed in the browser.
        """
        run = self.orch.current_run()
        state, updated_at = (self.orch.state_of(run["run_id"]) if run else (None, None))
        if state is None:
            state = _EMPTY.snapshot(trim=True)
            state["run"]["status"] = "idle"
            if run:
                state["run"]["run_id"] = run["run_id"]
        state = dict(state)
        record = public_run(run)
        if run and run["status"] in ENDED:
            _close_human(state, "The run has ended; its browser session no longer "
                                "exists. Nothing was written for unfinished tasks.")
            if (state.get("run") or {}).get("status") == "running":
                state["run"] = dict(state["run"], status="finished")
        if since_cold is not None and state.get("cold_version") == since_cold:
            state.pop("shipments", None)
            state.pop("exceptions", None)
            state["unchanged"] = ["shipments", "exceptions"]
        allowed = set(rbac.permissions_of(user))
        workers = self.orch.workers()
        active = bool(run and run["status"] in ACTIVE)
        state["control"] = {
            "enabled": bool({"runs.start", "runs.stop"} & allowed),
            "supervised": True, "remote": True,
            "running": active and run["status"] != "WORKER_DISCONNECTED",
            "paused": False, "stopping": bool(run and run["status"] == "STOPPING"),
            "queued": [], "history": [],
            "can_start": "runs.start" in allowed and not active and
            any(w["state"] in ("IDLE", "ONLINE") for w in workers),
            "can_stop": "runs.stop" in allowed and active,
        }
        state["health"] = {"available": False}
        claims = []
        for task in state.get("human_queue") or []:
            if isinstance(task, dict) and task.get("action_id"):
                holder = self.relay.holder(task["action_id"])
                if holder:
                    claims.append({"action_id": task["action_id"],
                                   "user_email": holder["user_email"],
                                   "mine": holder["user_id"] == user["user_id"],
                                   "lease_until": holder["lease_until"]})
        state["platform"] = {
            "mode": "remote",
            "user": {k: v for k, v in (public_user(user) or {}).items()
                     if k in ("user_id", "work_email", "display_name", "role",
                              "role_label", "prefs")},
            "permissions": sorted(allowed),
            "run": record, "state_at": updated_at,
            "workers": workers if "health.view" in allowed else [],
            "health": self.health(workers, run) if "health.view" in allowed else None,
            "claims": claims,
        }
        return state

    def health(self, workers=None, run=None):
        workers = self.orch.workers() if workers is None else workers
        run = self.orch.current_run() if run is None else run
        online = [w for w in workers if w["online"]]
        best = online[0] if online else (workers[0] if workers else None)
        browser = "Unknown"
        if best and best["online"]:
            browser = (best.get("browser_state") or
                       ("Ready" if (best.get("edge") or {}).get("found") else "Not found"))
        from intelligence import store as intel_store
        atlas_ok = intel_store.folder().exists()
        return {
            "api": "Online",
            "storage": "Online" if self.db.ping() else "Unavailable",
            "worker": best["state"] if best else "Not registered",
            "worker_heartbeat_s": best["heartbeat_age_s"] if best else None,
            "browser": browser if best and best["online"] else "Unavailable",
            "atlas": "Ready" if atlas_ok else "Unavailable",
            "current_run": (run["status"] if run else "None"),
            "current_run_id": run["run_id"] if run else None,
            # Only what a worker reported. The control plane never checks
            # eHub itself, so before a report this is "Not verified".
            "ehub": self.observations.latest(),
        }

    def synced_ids(self):
        """sync_id of every worker record already stored — built once, then kept."""
        if self._synced is None:
            from intelligence import store as intel_store, events as intel_events
            self._synced = {str(r.get("sync_id")) for r in intel_store.read(intel_events.FILE)
                            if r.get("sync_id")}
        return self._synced

    def _follow_claims(self, run_id, state):
        """
        A claimed task reaching a final state ends its claim and is audited
        against the person who held it — with the state the RUN reported,
        never one inferred here.
        """
        for task in (state or {}).get("human_queue") or []:
            if not isinstance(task, dict):
                continue
            action_id, status = task.get("action_id"), task.get("status")
            if status in ("HUMAN_COMPLETED", "POST_VERIFICATION_CHECK", "RESUMING",
                          "SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST",
                          "VERIFICATION_NOT_CONFIRMED", "FAILED"):
                key = (action_id, status)
                if key in self._closed:
                    continue
                self._closed[key] = now()
                holder = self.db.one("SELECT * FROM claims WHERE action_id = ?",
                                     (action_id,))
                if holder is None:
                    continue
                person = {"user_id": holder["user_id"], "work_email": holder["user_email"]}
                if status in ("HUMAN_COMPLETED", "RESUMING"):
                    if (action_id, "confirmed") not in self._closed:
                        self._closed[(action_id, "confirmed")] = now()
                        self.audit.record("CONTINUE_HUMAN_ACTION", user=person,
                                          target_type="human_action", target_id=action_id,
                                          run_id=run_id, result="VERIFICATION_CONFIRMED",
                                          metadata={"reference": task.get("reference"),
                                                    "carrier": task.get("carrier")})
                elif status in ("SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST",
                                "VERIFICATION_NOT_CONFIRMED", "FAILED"):
                    self.audit.record("CONTINUE_HUMAN_ACTION", user=person,
                                      target_type="human_action", target_id=action_id,
                                      run_id=run_id, result=status,
                                      metadata={"reference": task.get("reference"),
                                                "carrier": task.get("carrier")})
                    self.db.execute("UPDATE claims SET released_at = ? WHERE action_id = ? "
                                    "AND released_at IS NULL", (now(), action_id))
                    self.relay.drop(action_id)
        if len(self._closed) > 5000:
            self._closed.clear()


def _close_human(state, why):
    action = state.get("human_action")
    if isinstance(action, dict) and action.get("waiting"):
        state["human_action"] = dict(action, waiting=False, state="session_lost",
                                     last_response=why)
    if isinstance(state.get("human_queue"), list):
        state["human_queue"] = [
            dict(t, status="HUMAN_SESSION_LOST",
                 label="Browser session lost — nothing written")
            if isinstance(t, dict) and t.get("status") not in (
                "SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST",
                "VERIFICATION_NOT_CONFIRMED", "FAILED") else t
            for t in state["human_queue"]]


class Handler(tower_server.Handler):
    """
    The dashboard server's handler, behind the platform's gate. Reused for
    what it already does well — static files with Range, the ATLAS learning
    and evidence endpoints — and overridden wherever state or control is
    involved.
    """

    server_version = "ATA"
    sys_version = ""
    app = None                 # set by make_server

    # -- plumbing ------------------------------------------------------------

    def log_message(self, *args):
        pass

    def setup(self):
        tower_server.Handler.setup(self)
        self.connection.settimeout(120)

    @property
    def secure(self):
        if self.app.s.trust_proxy:
            return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        return bool(self.app.s.public_url and self.app.s.public_url.startswith("https://"))

    def client_ip(self):
        if self.app.s.trust_proxy:
            forwarded = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if forwarded:
                return forwarded.split(":")[0] if forwarded.count(":") == 1 else forwarded
        return self.client_address[0]

    def _sent_names(self):
        names = set()
        for line in getattr(self, "_headers_buffer", []):
            name = line.split(b":", 1)[0].strip().lower()
            if name:
                names.add(name.decode("latin-1"))
        return names

    def _headers(self, extra=None):
        """Security headers, plus `extra`, never repeating one already sent."""
        sent = self._sent_names()
        merged = dict(api_headers(self.secure))
        merged.update(extra or {})
        for name, value in merged.items():
            if name.lower() not in sent:
                self.send_header(name, value)

    def _send(self, status, body, content_type="application/json; charset=utf-8",
              extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._headers(extra)
        for cookie in getattr(self, "_cookies", []):
            self.send_header("Set-Cookie", cookie)
        self._cookies = []
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _maybe_set_cookie(self):
        """Headers for files sent by the parent class's _send_file."""
        self._headers({"Cache-Control": "private, max-age=300"})

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._headers()
        for cookie in getattr(self, "_cookies", []):
            self.send_header("Set-Cookie", cookie)
        self._cookies = []
        self.end_headers()

    def _json(self, limit=MAX_JSON):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0 or length > limit:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length else b""
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        return data

    def _raw(self, limit):
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > limit:
            raise ValueError("body too large")
        return self.rfile.read(length) if length else b""

    def _query(self):
        return {k: v[0] for k, v in urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query).items()}

    # -- cookies -------------------------------------------------------------

    @property
    def cookie_name(self):
        return "__Host-ata_session" if self.app.s.secure_cookies else "ata_session"

    def _session_token(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self.cookie_name:
                return value
        return None

    def _set_session_cookie(self, token, max_age=None):
        attrs = ["{0}={1}".format(self.cookie_name, token), "Path=/", "HttpOnly",
                 "SameSite=Lax"]
        if self.app.s.secure_cookies:
            attrs.append("Secure")
        attrs.append("Max-Age={0}".format(self.app.s.session_max_s
                                          if max_age is None else max_age))
        self._cookies = getattr(self, "_cookies", []) + ["; ".join(attrs)]

    # -- the gate ------------------------------------------------------------

    def _route(self):
        return urllib.parse.urlparse(self.path).path.rstrip("/") or "/"

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PATCH(self):
        self._handle("PATCH")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        self.method = method
        self._cookies = []
        route = self._route()
        ip = self.client_ip()
        heavy = route.startswith(("/worker/v1/session/", "/api/session/")) or \
            route in ("/api/stream",)
        if not heavy and not self.app.limiter.hit("ip:" + ip, 600, 60):
            self._send(429, {"error": "too_many_requests",
                             "message": "Too many requests. Wait a minute."},
                       extra={"Retry-After": "60"})
            return
        try:
            if route.startswith("/worker/v1/"):
                self._worker(method, route)
                return
            if route in PUBLIC_ROUTES or route.startswith(PUBLIC_PREFIXES):
                self._public(method, route)
                return
            user, session, why = self.app.users.resolve(self._session_token())
            if user is None:
                if why == "expired":
                    self.app.audit.record("SESSION_EXPIRED", result="EXPIRED", ip=ip)
                    self._set_session_cookie("", 0)
                if route.startswith("/api/"):
                    self._send(401, {"error": "session_expired" if why == "expired"
                                     else "not_signed_in",
                                     "message": "Your session has expired. Sign in again."
                                     if why == "expired" else "Sign in to continue."})
                else:
                    self._redirect("/login?next={0}{1}".format(
                        urllib.parse.quote(self.path if self.path.startswith("/") else "/"),
                        "&expired=1" if why == "expired" else ""))
                return
            self.user, self.session = user, session
            if method not in ("GET", "HEAD") and not self._csrf_ok(session):
                self._send(403, {"error": "csrf", "message": "This request did not come "
                                 "from the Control Tower page. Reload and try again."})
                return
            self._authed(method, route)
        except (ValueError, json.JSONDecodeError):
            self._send(400, {"error": "bad_request"})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:
            try:
                self._send(500, {"error": "internal", "message": type(error).__name__})
            except Exception:
                pass

    def _origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            # Same-origin fetch from older browsers omits Origin on POST only
            # rarely; Sec-Fetch-Site says the rest.
            site = self.headers.get("Sec-Fetch-Site")
            return site in (None, "same-origin")
        allowed = set()
        if self.app.s.public_url:
            allowed.add(self.app.s.public_url.lower())
        host = self.headers.get("Host")
        if host:
            allowed.add(("https://" if self.secure else "http://") + host.lower())
        return origin.lower().rstrip("/") in allowed

    def _csrf_ok(self, session):
        return self._origin_ok() and same(self.headers.get("X-CSRF-Token"), session["csrf"])

    def _need(self, permission):
        if rbac.allowed(self.user, permission):
            return True
        self.app.audit.record("ACCESS_DENIED", result="FORBIDDEN", user=self.user,
                              target_type="permission", target_id=permission,
                              ip=self.client_ip(), metadata={"route": self._route(),
                                                             "method": self.method})
        self._send(403, {"error": "forbidden", "permission": permission,
                         "message": "Your role ({0}) does not allow this.".format(
                             rbac.ROLE_LABELS.get(self.user.get("role"), "none"))})
        return False

    # -- public routes -------------------------------------------------------

    def _public(self, method, route):
        app = self.app
        if route == "/healthz":
            self._send(200, {"ok": True, "storage": app.db.ping()})
            return
        if route in ("/login", "/set-password") and method in ("GET", "HEAD"):
            body = LOGIN_PAGE.read_bytes()
            self._send(200, body, "text/html; charset=utf-8",
                       extra=page_headers(script_hashes(body.decode("utf-8")), self.secure))
            return
        if route.startswith(PUBLIC_PREFIXES) and method in ("GET", "HEAD"):
            if route.startswith("/static/login/"):
                base = HERE / "static"
                candidate = (base / route[len("/static/"):]).resolve()
            else:
                base = app.s.static_dir
                candidate = (base / route[len("/static/"):]).resolve()
            if base.resolve() in candidate.parents:
                self._send_file(candidate)
            else:
                self._send(404, {"error": "not_found"})
            return
        if route == "/api/auth/config":
            self._send(200, {"local": app.s.allow_local_login, "sso": bool(app.entra),
                             "sso_label": "Sign in with Microsoft"})
            return
        if route == "/api/auth/login" and method == "POST":
            self._login()
            return
        if route == "/api/auth/set-password" and method == "POST":
            if not self._origin_ok():
                self._send(403, {"error": "csrf"})
                return
            data = self._json()
            if not app.limiter.hit("setpw:" + self.client_ip(), 10, 600):
                self._send(429, {"error": "too_many_requests"})
                return
            try:
                user = app.users.set_password(str(data.get("token") or ""),
                                              str(data.get("password") or ""),
                                              ip=self.client_ip())
            except AccessError as error:
                self._send(400, {"ok": False, "message": str(error)})
                return
            self._send(200, {"ok": True, "email": user["work_email"]})
            return
        if route == "/auth/sso/start" and method == "GET":
            if not app.entra:
                self._send(404, {"error": "sso_not_configured"})
                return
            self._redirect(app.entra.start(self._query().get("next") or "/"))
            return
        if route == "/auth/sso/callback" and method == "GET":
            self._sso_callback()
            return
        self._send(404, {"error": "not_found"})

    def _login(self):
        app, ip = self.app, self.client_ip()
        if not self._origin_ok():
            self._send(403, {"error": "csrf"})
            return
        if not app.s.allow_local_login:
            self._send(403, {"ok": False, "message": "Sign in with Microsoft."})
            return
        data = self._json(4096)
        email = str(data.get("email") or "").strip().lower()[:254]
        if not app.limiter.hit("login-ip:" + ip, 20, 600) or \
                not app.limiter.hit("login-email:" + email, 10, 600):
            app.audit.record("LOGIN_FAILED", result="RATE_LIMITED", ip=ip,
                             metadata={"email": email})
            self._send(429, {"ok": False, "message": "Too many sign-in attempts. "
                                                     "Wait ten minutes and try again."})
            return
        user, reason = app.users.authenticate(email, data.get("password"), ip)
        if user is None:
            app.audit.record("LOGIN_FAILED", result="DENIED", ip=ip,
                             metadata={"email": email, "reason": reason})
            self._send(401, {"ok": False, "message": "That email or password is not "
                                                     "right."})
            return
        token, csrf = app.users.start_session(user, ip, self.headers.get("User-Agent"))
        app.audit.record("LOGIN", user=user, ip=ip, metadata={"method": "password"})
        self._set_session_cookie(token)
        self._send(200, {"ok": True, "user": public_user(app.users.get(user["user_id"])),
                         "csrf": csrf})

    def _sso_callback(self):
        app, ip = self.app, self.client_ip()
        q = self._query()
        if not app.entra:
            self._send(404, {"error": "sso_not_configured"})
            return
        if q.get("error"):
            app.audit.record("LOGIN_FAILED", result="SSO_ERROR", ip=ip,
                             metadata={"error": q.get("error")[:60]})
            self._redirect("/login?sso_error=1")
            return
        try:
            claims, next_path = app.entra.finish(q.get("code"), q.get("state"))
        except SSOError as error:
            app.audit.record("LOGIN_FAILED", result="SSO_REJECTED", ip=ip,
                             metadata={"reason": str(error)})
            self._redirect("/login?sso_error=1")
            return
        user = app.users.by_email(claims["email"])
        if user is None and app.s.entra_auto_provision:
            user, _ = app.users.create(claims["email"], claims["name"], rbac.VIEWER,
                                       auth_source="entra", entra_oid=claims.get("oid"),
                                       ip=ip)
        if user is None or not user["active"]:
            app.audit.record("LOGIN_FAILED", result="NO_ACCESS", ip=ip,
                             metadata={"email": claims["email"], "method": "entra"})
            self._redirect("/login?no_access=1")
            return
        if user["auth_source"] != "entra":
            app.db.execute("UPDATE users SET auth_source = ?, entra_oid = ?, "
                           "password_hash = NULL, must_set_password = 0 WHERE user_id = ?",
                           ("entra", claims.get("oid"), user["user_id"]))
        elif user.get("entra_oid") and claims.get("oid") and \
                user["entra_oid"] != claims["oid"]:
            app.audit.record("LOGIN_FAILED", result="OID_MISMATCH", ip=ip,
                             metadata={"email": claims["email"]})
            self._redirect("/login?no_access=1")
            return
        if app.s.entra_role_claims:
            mapped = [rbac.ENTRA_APP_ROLES[r] for r in claims["roles"]
                      if r in rbac.ENTRA_APP_ROLES]
            role = next((r for r in rbac.ROLES if r in mapped), None)
            if role is None:
                app.audit.record("LOGIN_FAILED", result="NO_APP_ROLE", ip=ip,
                                 metadata={"email": claims["email"]})
                self._redirect("/login?no_access=1")
                return
            if role != user["role"]:
                app.db.execute("UPDATE users SET role = ? WHERE user_id = ?",
                               (role, user["user_id"]))
                app.audit.record("CHANGE_ROLE", target_type="user", target_id=user["user_id"],
                                 ip=ip, metadata={"email": user["work_email"],
                                                  "from": user["role"], "to": role,
                                                  "by": "entra_app_role"})
        user = app.users.get(user["user_id"])
        token, _csrf = app.users.start_session(user, ip, self.headers.get("User-Agent"),
                                               method="entra")
        app.audit.record("LOGIN", user=user, ip=ip, metadata={"method": "entra"})
        self._set_session_cookie(token)
        self._redirect(next_path)

    # -- signed-in routes ----------------------------------------------------

    def _authed(self, method, route):
        app, user = self.app, self.user
        key = (method if method != "HEAD" else "GET", route)

        if route == "/" and method in ("GET", "HEAD"):
            body, hashes = app.index()
            self._send(200, body, "text/html; charset=utf-8",
                       extra=page_headers(hashes, self.secure))
            return
        if route in ("/intro", "/film") and method == "GET":
            body = (app.s.static_dir / "intro" / "index.html").read_bytes()
            self._send(200, body, "text/html; charset=utf-8",
                       extra=page_headers(script_hashes(body.decode("utf-8")), self.secure))
            return
        if route.startswith("/static/") and method in ("GET", "HEAD"):
            relative = route[len("/static/"):]
            candidate = (app.s.static_dir / relative).resolve()
            if app.s.static_dir.resolve() in candidate.parents:
                self._send_file(candidate)
            else:
                self._send(404, {"error": "not_found"})
            return

        if route == "/api/auth/me" and method == "GET":
            self._send(200, {"user": public_user(user),
                             "permissions": rbac.permissions_of(user),
                             "csrf": self.session["csrf"],
                             "session": {"expires_at": self.session["expires_at"],
                                         "idle_s": app.s.session_idle_s},
                             "mode": "remote"})
            return
        if route == "/api/auth/logout" and method == "POST":
            app.users.end_session(self._session_token())
            app.audit.record("LOGOUT", user=user, ip=self.client_ip())
            self._set_session_cookie("", 0)
            self._send(200, {"ok": True})
            return
        if route == "/api/me/prefs" and method in ("POST", "PUT"):
            try:
                prefs = app.users.set_prefs(user["user_id"], self._json(2048))
            except AccessError as error:
                self._send(400, {"ok": False, "message": str(error)})
                return
            self._send(200, {"ok": True, "prefs": prefs})
            return

        # Session (remote browser) and admin routes carry ids in the path.
        if route.startswith("/api/session/"):
            self._session_route(method, route)
            return
        if route.startswith("/api/admin/users/"):
            self._admin_user(method, route)
            return
        if route.startswith("/api/atlas/proposals/") and method == "POST":
            self._proposal(route)
            return
        if route.startswith("/api/runs/"):
            self._run_route(method, route)
            return
        if route == "/api/po" or route.startswith("/api/po/"):
            self._po(method, route)
            return

        permission = ROUTE_PERMISSIONS.get(key)
        if permission is None:
            if method == "POST" and route == "/api/control":
                self._control()
                return
            self._send(404, {"error": "not_found"})
            return
        if not self._need(permission):
            return

        if route == "/api/state":
            self._send(200, app.payload(user))
        elif route == "/api/stream":
            self._stream()
        elif route == "/api/health":
            self._send(200, app.health())
        elif route == "/api/observations":
            q = self._query()
            if q.get("id"):
                record = app.observations.get(str(q["id"])[:40])
                self._send(200 if record else 404, record or {"error": "not_found"})
            else:
                self._send(200, {"observations": app.observations.recent(
                    int(q.get("limit") or 30)), "levels": list(V.LEVELS)})
        elif route == "/api/runs" and method == "GET":
            self._send(200, {"runs": app.orch.runs(int(self._query().get("limit") or 50))})
        elif route == "/api/runs" and method == "POST":
            self._start_run()
        elif route == "/api/human":
            self._human()
        elif route == "/api/ask":
            self._ask()
        elif route == "/api/atlas/llm":
            from intelligence import llm as _llm, research as _research
            self._send(200, {"llm": _llm.provider().health(),
                             "search": {"configured": _research.enabled(),
                                        "detail": None if _research.enabled()
                                        else _research.why_off()}})
        elif route == "/api/ask/progress":
            from intelligence import research as _research
            self._send(200, _research.progress(_research.clean_progress_id(
                self._query().get("id"))))
        elif route == "/api/feedback":
            tower_server.Handler.do_POST(self)
        elif route == "/api/atlas":
            self._send(200, assistant.atlas_brief(app.payload(user)))
        elif route == "/api/atlas/proposals":
            self._proposals()
        elif route in ("/api/atlas/learning", "/api/atlas/maturity", "/api/atlas/plan",
                       "/api/evidence"):
            self._intel_get(route)
        elif route == "/api/evidence/file":
            self.app.audit.record("VIEW_EVIDENCE", user=user, target_type="evidence",
                                  target_id=re.sub(r"[^0-9a-f]", "",
                                                   self._query().get("id", ""))[:16],
                                  ip=self.client_ip())
            self._intel_get(route)
        elif route == "/api/evidence/upload":
            self.app.audit.record("UPLOAD_EVIDENCE", user=user, target_type="evidence",
                                  ip=self.client_ip())
            self._evidence_upload()
        elif route == "/api/ml":
            tower_server.Handler.do_GET(self)
        elif route == "/api/export.csv":
            self._export()
        elif route == "/api/admin/users" and method == "GET":
            self._send(200, {"users": app.users.list(), "roles": list(rbac.ROLES)})
        elif route == "/api/admin/users" and method == "POST":
            self._create_user()
        elif route == "/api/admin/audit":
            q = self._query()
            self._send(200, {"events": app.audit.list(
                limit=int(q.get("limit") or 200), before=q.get("before"),
                action=q.get("action") or None, user_email=q.get("user") or None,
                run_id=q.get("run") or None)})
        elif route == "/api/admin/workers" and method == "GET":
            self._send(200, {"workers": app.orch.workers()})
        elif route == "/api/admin/workers" and method == "POST":
            data = self._json(2048)
            worker_id, token = app.orch.add_worker(str(data.get("name") or "")[:80], user)
            self._send(200, {"ok": True, "worker_id": worker_id, "token": token,
                             "message": "Copy this token to the worker now — it is not "
                                        "shown again."})
        elif route == "/api/admin/rbac":
            self._send(200, {"matrix": rbac.matrix(), "roles": list(rbac.ROLES)})
        else:
            self._send(404, {"error": "not_found"})

    # -- PO Automation -------------------------------------------------------

    def _po(self, method, route):
        """po/web.py's routes, each checked against the RBAC table here."""
        body = self._json(8192) if method == "POST" else {}
        kind, status, payload = po_web.handle(self.app.po, method, route, body, self.user,
                                              lambda permission: rbac.allowed(self.user,
                                                                              permission))
        if kind == "forbidden":
            self._need(status)              # records ACCESS_DENIED and answers 403
            return
        if kind == "file":
            data, content_type, name = payload
            self.app.audit.record("PO_OUTPUT_DOWNLOADED", user=self.user, target_type="po",
                                  target_id=route.split("/")[3], ip=self.client_ip(),
                                  metadata={"filename": name})
            self._send(200, data, content_type,
                       extra={"Content-Disposition": 'attachment; filename="{0}"'.format(name)})
            return
        self._send(status, payload)

    # -- runs ----------------------------------------------------------------

    def _start_run(self):
        data = self._json(4096)
        options = {}
        if "dry_run" in data:
            options["dry_run"] = bool(data["dry_run"])
        for key in ("max_records", "max_pages"):
            if data.get(key) is not None:
                options[key] = max(1, min(int(data[key]), 5000))
        try:
            run = self.app.orch.start_run(self.user, options, ip=self.client_ip())
        except RunError as error:
            self._send(error.status, {"accepted": False, "message": str(error),
                                      "run": error.run})
            return
        self._send(200, {"accepted": True, "run": run,
                         "message": "Run {0} created and sent to the worker.".format(
                             run["run_id"])})

    def _run_route(self, method, route):
        parts = route.split("/")
        run_id = parts[3] if len(parts) > 3 else ""
        if not _ID.match(run_id):
            self._send(404, {"error": "not_found"})
            return
        if len(parts) == 4 and method == "GET":
            if not self._need("runs.view"):
                return
            run = public_run(self.app.orch.run(run_id))
            if run is None:
                self._send(404, {"error": "not_found"})
                return
            state, at = self.app.orch.state_of(run_id)
            self._send(200, {"run": run, "state_at": at,
                             "counters": (state or {}).get("counters")})
            return
        if len(parts) == 5 and parts[4] in ("stop", "pause", "resume") and method == "POST":
            if not self._need("runs.stop"):
                return
            data = self._json(1024)
            try:
                message = self.app.orch.control(self.user, parts[4], run_id,
                                                ip=self.client_ip(),
                                                force=bool(data.get("force")))
            except RunError as error:
                self._send(error.status, {"accepted": False, "message": str(error)})
                return
            self._send(200, {"accepted": True, "message": message})
            return
        self._send(404, {"error": "not_found"})

    def _control(self):
        """The dashboard's existing Start / Pause / Stop / Re-run request."""
        data = self._json(2000)
        action = str(data.get("action") or "")[:20]
        needed = {"start": "runs.start", "reprocess": "runs.start", "stop": "runs.stop",
                  "kill": "runs.stop", "pause": "runs.stop", "resume": "runs.stop"}.get(action)
        if needed is None:
            self._send(400, {"accepted": False, "message": "Unknown request."})
            return
        if not self._need(needed):
            return
        try:
            if action == "start":
                run = self.app.orch.start_run(self.user, {}, ip=self.client_ip())
                message = "Run {0} created and sent to the worker.".format(run["run_id"])
            else:
                message = self.app.orch.control(self.user, action,
                                                reference=data.get("reference"),
                                                ip=self.client_ip())
        except RunError as error:
            self._send(200, {"accepted": False, "message": str(error)})
            return
        self._send(200, {"accepted": True, "message": message})

    # -- Human Action --------------------------------------------------------

    def _task(self, state, action_id):
        for task in (state or {}).get("human_queue") or []:
            if isinstance(task, dict) and str(task.get("action_id")) == str(action_id):
                return task
        pending = (state or {}).get("human_action")
        if isinstance(pending, dict) and str(pending.get("action_id")) == str(action_id):
            return pending
        return None

    def _human(self):
        """
        Open & Continue. Checked here against the run's last report — the run
        checks again, because it owns the truth — then claimed for this one
        person and sent to the worker together with the browser view request.
        """
        app = self.app
        data = self._json(1000)
        op = str(data.get("op") or "")[:10]
        run_id = str(data.get("run_id") or "")[:64]
        action_id = str(data.get("action_id") or "")[:64]
        if op not in ("open", "resume") or not _ID.match(run_id) or \
                not _ID.match(action_id):
            self._send(400, {"accepted": False, "message": "That request is not complete."})
            return
        run = app.orch.run(run_id)
        audit_args = dict(user=self.user, target_type="human_action", target_id=action_id,
                          run_id=run_id, ip=self.client_ip())
        if run is None or run["status"] not in ("RUNNING", "STOPPING"):
            app.audit.record("OPEN_HUMAN_ACTION", result="REFUSED_RUN_NOT_ACTIVE", **audit_args)
            self._send(200, {"accepted": False, "message": (
                "The worker is disconnected; this task cannot be opened until it "
                "returns." if run and run["status"] == "WORKER_DISCONNECTED" else
                "That run is not in progress, so its browser session no longer exists. "
                "The shipment will be looked up again next run.")})
            return
        state, _at = app.orch.state_of(run_id)
        task = self._task(state, action_id)
        live = (state or {}).get("human_action") or {}
        waiting = task is not None and (
            task.get("status") in ("WAITING_FOR_HUMAN", "OPERATOR_OPENED",
                                   "VERIFICATION_PENDING") or
            (live.get("waiting") and str(live.get("action_id")) == action_id))
        if not waiting:
            app.audit.record("OPEN_HUMAN_ACTION", result="REFUSED_NOT_PENDING", **audit_args)
            self._send(200, {"accepted": False, "message": (
                "That Human Action is no longer waiting{0}. Nothing was sent.".format(
                    " (it is {0})".format(str(task.get("status")).replace("_", " ").lower())
                    if task and task.get("status") else ""))})
            return
        try:
            holder = app.relay.claim(self.user, run_id, action_id)
        except ClaimError as error:
            app.audit.record("OPEN_HUMAN_ACTION", result="REFUSED_CLAIMED",
                             metadata={"holder": error.holder}, **audit_args)
            self._send(200, {"accepted": False, "message": str(error),
                             "holder": error.holder})
            return
        app.orch.enqueue(run["worker_id"], "human", {
            "op": op, "run_id": run_id, "action_id": action_id,
            "client_id": self.user["user_id"]}, run_id, self.user)
        if op == "open":
            app.orch.enqueue(run["worker_id"], "session_attach", {
                "run_id": run_id, "action_id": action_id,
                "viewer": self.user["user_id"]}, run_id, self.user)
        app.audit.record("OPEN_HUMAN_ACTION" if op == "open" else "CONTINUE_HUMAN_ACTION",
                         result="SENT", metadata={"reference": (task or {}).get("reference"),
                                                  "carrier": (task or {}).get("carrier"),
                                                  "op": op}, **audit_args)
        self._send(200, {"accepted": True, "action_id": action_id,
                         "lease_until": holder["lease_until"] if holder else None,
                         "message": "Opening the {0} browser session for {1}. Complete the "
                                    "verification in the browser view; ATA continues by "
                                    "itself once the carrier page confirms it.".format(
                                        (task or {}).get("carrier") or "carrier",
                                        (task or {}).get("reference") or "this shipment")
                         if op == "open" else "Sent. The run checks the page and carries "
                                              "on if the result is there."})

    def _session_route(self, method, route):
        """/api/session/<action_id>/(frame|input|status|release)"""
        app = self.app
        parts = route.split("/")
        if len(parts) != 5 or not _ID.match(parts[3]):
            self._send(404, {"error": "not_found"})
            return
        action_id, verb = parts[3], parts[4]
        if not self._need("human.act"):
            return
        holder = app.relay.holder(action_id)
        if verb == "status" and method == "GET":
            self._send(200, {"holder": holder["user_email"] if holder else None,
                             "mine": bool(holder and holder["user_id"] == self.user["user_id"]),
                             "view": app.relay.status(action_id)})
            return
        if not holder or holder["user_id"] != self.user["user_id"]:
            self._send(409, {"error": "not_holder", "holder": holder["user_email"]
                             if holder else None,
                             "message": "{0} is handling this Human Action.".format(
                                 holder["user_email"]) if holder else
                             "This browser session is not open for you. Choose Open & "
                             "Continue first."})
            return
        if verb == "frame" and method == "GET":
            app.relay.renew(self.user, action_id)
            after = int(self._query().get("after") or 0)
            frame = app.relay.frame(action_id, after, wait_s=8)
            if frame is None:
                self._send(204, b"", "text/plain", extra={
                    "X-View": json.dumps(app.relay.status(action_id) or {})})
                return
            self._send(200, frame["data"], "image/jpeg", extra={
                "X-Seq": str(frame["seq"]), "X-Meta": json.dumps(frame["meta"]),
                "Cache-Control": "no-store, private"})
            return
        if verb == "input" and method == "POST":
            data = self._json(32 * 1024)
            try:
                events = clean_events(data.get("events"))
            except (ValueError, TypeError):
                self._send(400, {"error": "bad_events"})
                return
            app.relay.renew(self.user, action_id)
            app.relay.push_input(action_id, events)
            self._send(200, {"ok": True})      # nothing about the events is echoed
            return
        if verb == "release" and method == "POST":
            app.relay.release(self.user, action_id)
            run_id = holder["run_id"]
            run = app.orch.run(run_id)
            if run:
                app.orch.enqueue(run["worker_id"], "session_detach",
                                 {"run_id": run_id, "action_id": action_id}, run_id,
                                 self.user)
            app.audit.record("RELEASE_HUMAN_ACTION", user=self.user,
                             target_type="human_action", target_id=action_id,
                             run_id=run_id, ip=self.client_ip())
            self._send(200, {"ok": True})
            return
        self._send(404, {"error": "not_found"})

    # -- ATLAS ---------------------------------------------------------------

    def _ask(self):
        data = self._json(4096)
        question = str(data.get("question") or "")[:1000]
        raw = data.get("context") or {}
        context = {"reference": str(raw.get("reference") or "")[:64],
                   "action_id": str(raw.get("action_id") or "")[:64],
                   "evidence_id": re.sub(r"[^0-9a-f]", "", str(raw.get("evidence_id") or ""))[:16],
                   "domain": "po" if raw.get("domain") == "po" else "",
                   "po_id": re.sub(r"[^0-9a-z-]", "", str(raw.get("po_id") or ""))[:40],
                   "progress_id": re.sub(r"[^A-Za-z0-9_-]", "", str(raw.get("progress_id")
                                                                   or ""))[:64],
                   # ATLAS answers about PO jobs only for a role that may see them.
                   "po": "1" if rbac.allowed(self.user, "po.view") else "0",
                   "conduct": int(raw.get("conduct") or 0)
                   if str(raw.get("conduct") or "0").isdigit() else 0}
        state = self.app.payload(self.user)
        from intelligence import research as _research
        _research.progress_start(context.get("progress_id"))
        try:
            reply = assistant.answer(question, state, context)
        finally:
            _research.progress_done(context.get("progress_id"))
        try:
            from intelligence import events as intel_events
            intent = reply.get("intent")
            fallback = bool(reply.get("fallback")) or \
                str(reply.get("answer") or "").startswith("I don't have that information")
            intel_events.record("question", intent=intent or "unrecognised",
                                pattern=None if intent == "code_request" or reply.get("conduct")
                                else intel_events.question_pattern(question),
                                answered=bool(intent) and not fallback,
                                run_id=(state.get("run") or {}).get("run_id"))
        except Exception:
            pass
        # From chat, the one automation action is Open & Continue — and only
        # for someone whose role allows it. The page sends it through
        # /api/human, which checks again. A re-run is not taken from chat.
        if reply.pop("request", None):
            reply["answer"] = ("Re-runs are not started from chat. Someone allowed to "
                               "start runs can use Start Automation.")
            reply["accepted"] = False
        if not rbac.allowed(self.user, "human.act"):
            if reply.pop("operation", None):
                reply["answer"] = (reply.get("answer") or "") + (
                    "\n\nYour role ({0}) cannot open Human Actions; an operator "
                    "can.".format(rbac.ROLE_LABELS.get(self.user["role"])))
            reply["buttons"] = [b for b in reply.get("buttons") or []
                                if (b.get("action") or {}).get("type") != "human_open"]
        self._send(200, reply)

    def _proposals(self):
        from intelligence import learning as intel_learning
        snap = intel_learning.snapshot()
        self._send(200, {"proposals": snap.get("proposals") or []})

    def _proposal(self, route):
        """Approve or reject an ATLAS proposal. It is recorded, never deployed."""
        parts = route.split("/")
        if len(parts) != 6 or parts[5] not in ("approve", "reject"):
            self._send(404, {"error": "not_found"})
            return
        if not self._need("atlas.approve"):
            return
        from intelligence import learning as intel_learning
        proposal_id = re.sub(r"[^0-9a-f]", "", parts[4])[:16]
        data = self._json(2048)
        approve = parts[5] == "approve"
        known = {p["id"]: p for p in intel_learning.snapshot().get("proposals") or []}
        if proposal_id not in known:
            self._send(404, {"ok": False, "message": "No such proposal."})
            return
        ok = intel_learning.decide(proposal_id, "APPROVED" if approve else "REJECTED",
                                   by=self.user["work_email"])
        self.app.audit.record("APPROVE_PROPOSAL" if approve else "REJECT_PROPOSAL",
                              user=self.user, target_type="proposal", target_id=proposal_id,
                              ip=self.client_ip(), result="SUCCESS" if ok else "FAILED",
                              metadata={"change": known[proposal_id].get("change"),
                                        "deployed": False})
        self._send(200 if ok else 500, {
            "ok": ok, "message": ("{0}. Recorded only — the automation is unchanged until "
                                  "a tested, approved deployment.".format(
                                      "Approved" if approve else "Rejected"))
            if ok else "The decision could not be saved."})

    # -- admin ---------------------------------------------------------------

    def _create_user(self):
        data = self._json(4096)
        try:
            user, invite = self.app.users.create(
                data.get("email"), data.get("display_name"), data.get("role"),
                by=self.user, auth_source="entra" if data.get("auth_source") == "entra"
                else "local", ip=self.client_ip())
        except AccessError as error:
            self._send(400, {"ok": False, "message": str(error)})
            return
        self._send(200, {"ok": True, "user": public_user(user),
                         "invite_link": self._link(invite)})

    def _link(self, token):
        if not token:
            return None
        base = self.app.s.public_url or "{0}://{1}".format(
            "https" if self.secure else "http", self.headers.get("Host") or "")
        return "{0}/set-password#token={1}".format(base, token)

    def _admin_user(self, method, route):
        if not self._need("users.manage"):
            return
        parts = route.split("/")
        user_id = parts[4] if len(parts) > 4 else ""
        if not _ID.match(user_id):
            self._send(404, {"error": "not_found"})
            return
        try:
            if len(parts) == 5 and method == "PATCH":
                data = self._json(2048)
                user = self.app.users.update(
                    user_id, self.user, role=data.get("role"),
                    active=data.get("active") if "active" in data else None,
                    display_name=data.get("display_name"), ip=self.client_ip())
                self._send(200, {"ok": True, "user": public_user(user)})
                return
            if len(parts) == 6 and parts[5] == "reset" and method == "POST":
                invite = self.app.users.reset_access(user_id, self.user, ip=self.client_ip())
                self._send(200, {"ok": True, "invite_link": self._link(invite),
                                 "message": "Signed out everywhere." + (
                                     " Share the new link so they can set a password."
                                     if invite else "")})
                return
            if len(parts) == 6 and parts[5] == "revoke" and method == "POST":
                ended = self.app.users.revoke_sessions(user_id)
                self.app.audit.record("REVOKE_SESSIONS", user=self.user, target_type="user",
                                      target_id=user_id, ip=self.client_ip(),
                                      metadata={"sessions_ended": ended})
                self._send(200, {"ok": True, "sessions_ended": ended})
                return
        except AccessError as error:
            self._send(400, {"ok": False, "message": str(error)})
            return
        self._send(404, {"error": "not_found"})

    # -- the live stream -----------------------------------------------------

    def _stream(self):
        """
        Server-Sent Events: the authoritative state whenever the control plane
        records a change, and a comment every 15 s to keep proxies from closing
        it. The session is checked again every 30 s; a revoked or expired
        session ends the stream.
        """
        app = self.app
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("X-Accel-Buffering", "no")
        self._headers({"Cache-Control": "no-cache"})
        self.end_headers()
        version, sent_cold, last_push, checked = -1, None, 0.0, time.time()
        token = self._session_token()
        try:
            while True:
                current = app.orch.wait_change(version, 2.0)
                stamp = time.time()
                if stamp - checked > 30:
                    checked = stamp
                    user, _s, _why = app.users.resolve(token)
                    if user is None:
                        self.wfile.write(b"event: auth\ndata: {\"signed_out\": true}\n\n")
                        self.wfile.flush()
                        return
                    self.user = user
                if current != version or stamp - last_push > 15:
                    payload = app.payload(self.user, since_cold=sent_cold)
                    sent_cold = payload.get("cold_version", sent_cold)
                    version = current
                    last_push = stamp
                    self.wfile.write("event: state\ndata: {0}\n\n".format(
                        json.dumps(payload, default=str)).encode("utf-8"))
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, TimeoutError):
            return

    def _export(self):
        """The current run's shipments as CSV, from the authoritative state."""
        import csv
        import io
        from datetime import datetime
        wanted = self._query().get("state") or "updated"
        state = self.app.payload(self.user)
        rows = state.get("shipments") or []
        if wanted != "all":
            rows = [r for r in rows if r.get("state") == wanted]
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([label for _k, label in self.EXPORT_COLUMNS])
        for record in reversed(rows):
            writer.writerow(["" if record.get(k) is None else record.get(k)
                             for k, _l in self.EXPORT_COLUMNS])
        body = ("﻿" + buffer.getvalue()).encode("utf-8")
        name = "control_tower_{0}_{1:%Y%m%d_%H%M%S}.csv".format(wanted, datetime.now())
        self._send(200, body, "text/csv; charset=utf-8",
                   extra={"Content-Disposition": 'attachment; filename="{0}"'.format(name)})

    # -- the worker API ------------------------------------------------------

    def _worker(self, method, route):
        app = self.app
        auth = self.headers.get("Authorization") or ""
        worker = app.orch.authenticate_worker(auth[7:].strip()) \
            if auth.startswith("Bearer ") else None
        if worker is None:
            if not app.limiter.hit("worker-bad:" + self.client_ip(), 30, 600):
                self._send(429, {"error": "too_many_requests"})
                return
            self._send(401, {"error": "unauthorized"})
            return
        parts = route.split("/")          # ['', 'worker', 'v1', ...]
        tail = parts[3:]
        if tail == ["heartbeat"] and method == "POST":
            app.orch.heartbeat(worker, self._json(64 * 1024))
            fresh = app.orch.authenticate_worker(auth[7:].strip())
            self._send(200, {"ok": True, "server_time": now(),
                             "worker": app.orch.worker_view(fresh)})
            return
        if tail == ["commands"] and method == "GET":
            wait = float(self._query().get("wait") or 20)
            self._send(200, {"commands": app.orch.take_commands(worker, wait)})
            return
        if len(tail) == 3 and tail[0] == "commands" and tail[2] == "result" and \
                method == "POST":
            data = self._json(16 * 1024)
            ok = app.orch.command_result(worker, tail[1][:64], bool(data.get("ok")),
                                         data.get("message"))
            self._send(200 if ok else 404, {"ok": ok})
            return
        if len(tail) == 3 and tail[0] == "runs" and _ID.match(tail[1]):
            run_id = tail[1]
            if tail[2] == "state" and method == "POST":
                ok = app.orch.push_state(worker, run_id, self._json(MAX_STATE))
                self._send(200 if ok else 404, {"ok": ok})
                return
            if tail[2] == "ended" and method == "POST":
                data = self._json(16 * 1024)
                ok = app.orch.run_ended(worker, run_id, data.get("exit_code"),
                                        stopped=bool(data.get("stopped")),
                                        detail=data.get("detail"))
                self._send(200, {"ok": ok})
                return
        if len(tail) == 2 and tail[0] == "po" and method == "POST":
            ok, message = app.po.import_from_worker(worker["worker_id"], tail[1][:64],
                                                    self._json(40 * 1024 * 1024))
            self._send(200 if ok else 409, {"ok": ok, "message": message})
            return
        if len(tail) == 3 and tail[0] == "session" and _ID.match(tail[1]):
            action_id = tail[1]
            if not self._worker_owns_action(worker, action_id):
                self._send(404, {"error": "not_found"})
                return
            if tail[2] == "frame" and method == "POST":
                data = self._raw(1600 * 1024)
                meta = loads(self.headers.get("X-Meta"))
                ok = app.relay.put_frame(action_id, data, meta if isinstance(meta, dict) else {})
                self._send(200 if ok else 400, {"ok": ok,
                                                "viewer": app.relay.viewer_active(action_id)})
                return
            if tail[2] == "input" and method == "GET":
                wait = float(self._query().get("wait") or 10)
                self._send(200, {"events": app.relay.take_input(action_id, wait),
                                 "viewer": app.relay.viewer_active(action_id),
                                 "held": app.relay.holder(action_id) is not None})
                return
            if tail[2] == "status" and method == "POST":
                app.relay.set_status(action_id, self._json(4096))
                self._send(200, {"ok": True})
                return
        if tail == ["observations"] and method == "POST":
            record = app.observations.accept(worker, self._json(512 * 1024))
            self._send(200, {"ok": True, "observation_id": record["observation_id"],
                             "level": record["level"], "reasons": record["reasons"]})
            return
        if len(tail) == 2 and tail[0] == "intel" and method == "POST":
            self._intel_sync(worker, tail[1])
            return
        self._send(404, {"error": "not_found"})

    def _worker_owns_action(self, worker, action_id):
        claim = self.app.db.one("SELECT run_id FROM claims WHERE action_id = ?", (action_id,))
        if claim is None:
            return False
        run = self.app.orch.run(claim["run_id"])
        return bool(run and run["worker_id"] == worker["worker_id"])

    def _intel_sync(self, worker, kind):
        """
        The worker's learning records, forwarded line by line into the control
        plane's store (which re-cleans them). Evidence images come with their
        index line and are kept only if their SHA-256 matches it.
        """
        from intelligence import store as intel_store, events as intel_events
        from intelligence import evidence as intel_evidence
        if kind == "events":
            data = self._json(MAX_INTEL)
            if data.get("origin") != intel_store.origin():
                self._send(409, {"ok": False, "message": "The worker's learning store holds "
                                 "{0} data; this one takes {1} data only.".format(
                                     data.get("origin"), intel_store.origin())})
                return
            accepted = duplicates = 0
            seen = self.app.synced_ids()
            for record in (data.get("records") or [])[:5000]:
                if not isinstance(record, dict) or record.get("kind") not in intel_events.KINDS:
                    continue
                sync_id = str(record.get("sync_id") or "")[:40]
                if sync_id and sync_id in seen:
                    duplicates += 1
                    continue
                if intel_store.append(intel_events.FILE, dict(record, via=worker["worker_id"])):
                    accepted += 1
                    if sync_id:
                        seen.add(sync_id)
            self._send(200, {"ok": True, "accepted": accepted, "duplicates": duplicates,
                             "origin": intel_store.store_origin()})
            return
        if kind == "evidence":
            import base64
            data = self._json(MAX_INTEL + 2 * 1024 * 1024)
            if data.get("origin") != intel_store.origin():
                self._send(409, {"ok": False, "message": "Data origin differs."})
                return
            try:
                image = base64.b64decode(str(data.get("image") or ""), validate=True)
            except Exception:
                self._send(400, {"ok": False, "message": "The image is not valid base64."})
                return
            ok, message = intel_evidence.register_synced(
                data.get("entry"), image, worker["worker_id"],
                text=str(data.get("text") or "")[:200000] or None)
            self._send(200 if ok else 400, {"ok": ok, "message": message})
            return
        self._send(404, {"error": "not_found"})


def make_server(app, host=None, port=None):
    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request, client_address):
            import sys
            kind = sys.exc_info()[0]
            if kind is not None and issubclass(kind, (ConnectionResetError,
                                                      ConnectionAbortedError,
                                                      BrokenPipeError, TimeoutError)):
                return
            ThreadingHTTPServer.handle_error(self, request, client_address)

    handler = type("BoundHandler", (Handler,), {"app": app})
    return Server((host or app.s.host, port if port is not None else app.s.port), handler)


def serve(settings=None):
    app = App(settings)
    app.start_background()
    httpd = make_server(app)
    print("ATA Control Tower control plane on http://{0}:{1}/".format(
        *httpd.server_address[:2]), flush=True)
    if app.s.public_url:
        print("Public address: {0}".format(app.s.public_url), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        httpd.server_close()
