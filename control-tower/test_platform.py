"""
The remote Control Tower platform: the control plane, without a browser.

    AUTH        sign in, wrong password, lockout, expiry, sign out, set password
    ACCESS      every role against every protected route; 401 / 403 server-side
    SECURITY    CSRF and Origin, headers, cookies, rate limits, traversal,
                body limits, worker and user credentials kept apart
    SSO         Entra ID token checks: RS256 signature, issuer, audience,
                tenant, expiry, nonce, single-use state
    USERS       invite, roles, deactivate, reset, last admin, self-protection
    AUDIT       the events, and nothing secret in them
    WORKERS     heartbeat, offline, WORKER_DISCONNECTED, reconnect, reconcile
    RUNS        start, duplicate, worker offline, stop, failed start
    HUMAN       claim, refusal, holder-only view, release, follow-up audit
    ATLAS       chat scoped by role, no re-run from chat, proposals, origin
    THEME       per-user preference
    STREAM      live state over SSE, a second device sees the same run
    PERF        building the state the dashboard draws
    POSTGRES    the same rules on PostgreSQL, when ATA_TEST_PG_URL is set

    python test_platform.py
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_platform_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

from controlplane.config import Settings                  # noqa: E402
from controlplane.app import App, make_server              # noqa: E402
from controlplane import rbac, security, oidc               # noqa: E402
from controlplane.relay import ClaimError, clean_events     # noqa: E402
from controlplane.runs import RunError                      # noqa: E402
from controlplane.users import AccessError                  # noqa: E402
from fixtures.platform_client import Client                 # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


PASSWORDS = {"ADMIN": "Tower-Key-2026!", "OPERATOR": "Hub-Write-2026!",
             "VIEWER": "Read-Only-2026!"}


def platform(name, **extra):
    env = {"DATABASE_URL": "sqlite:///{0}/{1}.db".format(WORK, name),
           "ATA_INSECURE_COOKIES": "1", "ATA_WORKER_OFFLINE_S": "3"}
    env.update({k: str(v) for k, v in extra.items()})
    app = App(Settings(env))
    httpd = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{0}".format(httpd.server_address[1])
    return app, base, httpd


def people(app):
    out = {}
    for role, email in (("ADMIN", "ada.admin@mantrac.com"),
                        ("OPERATOR", "omar.ops@mantrac.com"),
                        ("VIEWER", "vera.view@mantrac.com")):
        out[role] = app.users.create(email, role.title() + " Person", role,
                                     password=PASSWORDS[role])[0]
    return out


def signed_in(base, user, role):
    c = Client(base)
    status, _d, _h = c.login(user["work_email"], PASSWORDS[role])
    assert status == 200, status
    return c


def worker_call(base, token, method, path, body=None, raw=None, headers=None):
    h = {"Authorization": "Bearer " + token}
    h.update(headers or {})
    data = json.dumps(body).encode() if body is not None else raw
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        r = opener.open(req, timeout=30)
        payload = r.read()
        return r.status, (json.loads(payload) if r.headers.get("Content-Type", "").startswith(
            "application/json") else payload), dict(r.headers)
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            payload = json.loads(payload)
        except ValueError:
            pass
        return e.code, payload, dict(e.headers)


app, base, _srv = platform("main")
U = people(app)

# ═════════════════════════════════════════════════════════════════════
rule("1. AUTH — sign in, wrong password, lockout, expiry, sign out")
c = Client(base)
s, d, h = c.login("omar.ops@mantrac.com", "Hub-Write-2026!")
check("A correct email and password signs in", s == 200 and d["ok"] and d["user"]["role"] == "OPERATOR")
check("...and the response carries no password or hash",
      "password" not in json.dumps(d).lower().replace("must_set_password", ""))
cookie = h.get("Set-Cookie", "")
check("The session cookie is HttpOnly and SameSite=Lax",
      "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie, cookie)
check("Only the token's hash is stored, never the token",
      cookie.split("=")[1].split(";")[0] not in json.dumps(app.db.all("SELECT * FROM sessions")))
row = app.db.one("SELECT password_hash FROM users WHERE work_email = ?", ("omar.ops@mantrac.com",))
check("Passwords are stored as scrypt hashes, never in plain text",
      row["password_hash"].startswith("scrypt$") and "Hub-Write" not in row["password_hash"])
s, d, _ = Client(base).login("omar.ops@mantrac.com", "wrong-password")
check("A wrong password is refused with one plain message", s == 401 and
      d["message"] == "That email or password is not right.")
s2, d2, _ = Client(base).login("nobody@mantrac.com", "whatever-at-all")
check("...the same message for an email that has no account", s2 == 401 and d2 == d)
s, d, _ = c.get("/api/auth/me")
check("/api/auth/me answers the signed-in person, their permissions and the CSRF token",
      s == 200 and d["user"]["work_email"] == "omar.ops@mantrac.com" and
      "human.act" in d["permissions"] and d["csrf"] == c.csrf)
s, d, _ = c.post("/api/auth/logout")
check("Sign out ends the session", s == 200 and c.get("/api/state")[0] == 401)

app.users.create("lock.me@mantrac.com", "Lock Me", "VIEWER", password="Lock-Me-Now-2026!")
for _ in range(5):
    Client(base).login("lock.me@mantrac.com", "nope-nope-nope")
s, _d, _ = Client(base).login("lock.me@mantrac.com", "Lock-Me-Now-2026!")
check("Five wrong passwords lock the account, even against the right one", s == 401)
check("...and the lock is visible to an admin",
      [u for u in app.users.list() if u["work_email"] == "lock.me@mantrac.com"][0]["locked"])

exp_app, exp_base, _ = platform("expiry", ATA_SESSION_IDLE_MIN="0")
people(exp_app)
ce = Client(exp_base)
ce.login("omar.ops@mantrac.com", PASSWORDS["OPERATOR"])
time.sleep(1.1)
s, d, _ = ce.get("/api/state")
check("An idle session expires and the API says so", s == 401 and d["error"] == "session_expired", str(d))
check("...and the expiry is audited",
      any(e["action"] == "SESSION_EXPIRED" for e in exp_app.audit.list(20)))
s, _b, h = Client(base).get("/")
check("A page request without a session goes to the sign-in page",
      s in (200, 302) and (h.get("Location", "").startswith("/login") or s == 200))

# ═════════════════════════════════════════════════════════════════════
rule("2. ACCESS — every role against every protected route, server-side")
admin, op, viewer = (signed_in(base, U[r], r) for r in ("ADMIN", "OPERATOR", "VIEWER"))
anon = Client(base)
ROUTES = [
    ("GET", "/api/state", None, {"ADMIN", "OPERATOR", "VIEWER"}),
    ("GET", "/api/runs", None, {"ADMIN", "OPERATOR", "VIEWER"}),
    ("GET", "/api/health", None, {"ADMIN", "OPERATOR", "VIEWER"}),
    ("GET", "/api/atlas", None, {"ADMIN", "OPERATOR", "VIEWER"}),
    ("POST", "/api/ask", {"question": "what needs me?"}, {"ADMIN", "OPERATOR", "VIEWER"}),
    ("POST", "/api/runs", {}, {"ADMIN", "OPERATOR"}),
    ("POST", "/api/control", {"action": "stop"}, {"ADMIN", "OPERATOR"}),
    ("POST", "/api/human", {"op": "open", "run_id": "20261005-000000-aaaaaa",
                            "action_id": "abc123"}, {"ADMIN", "OPERATOR"}),
    ("GET", "/api/evidence", None, {"ADMIN", "OPERATOR"}),
    ("GET", "/api/admin/users", None, {"ADMIN"}),
    ("POST", "/api/admin/users", {"email": "x@mantrac.com", "display_name": "X",
                                  "role": "VIEWER"}, {"ADMIN"}),
    ("GET", "/api/admin/audit", None, {"ADMIN"}),
    ("GET", "/api/admin/workers", None, {"ADMIN"}),
    ("POST", "/api/admin/workers", {"name": "w"}, {"ADMIN"}),
    ("POST", "/api/atlas/proposals/0123456789/approve", {}, {"ADMIN"}),
    ("PATCH", "/api/admin/users/" + U["VIEWER"]["user_id"], {"display_name": "Vera V"}, {"ADMIN"}),
]
bad = []
for method, path, body, allowed in ROUTES:
    for role, client in (("ADMIN", admin), ("OPERATOR", op), ("VIEWER", viewer)):
        s, d, _ = client.call(method, path, body)
        ok = (s != 403) if role in allowed else (s == 403)
        if not ok:
            bad.append("{0} {1} as {2} -> {3}".format(method, path, role, s))
    s, _d, _ = anon.call(method, path, body)
    if s != 401:
        bad.append("{0} {1} anonymous -> {2}".format(method, path, s))
check("{0} routes x 4 callers: each allowed only to its roles (403 otherwise, 401 "
      "signed out)".format(len(ROUTES)), not bad, "; ".join(bad[:6]))
s, d, _ = viewer.post("/api/runs", {})
check("A refusal names the permission and the role", s == 403 and d["permission"] == "runs.start"
      and "Viewer" in d["message"])
check("...and is written to the audit log as ACCESS_DENIED",
      any(e["action"] == "ACCESS_DENIED" and e["user_email"] == "vera.view@mantrac.com"
          for e in app.audit.list(100)))
check("The matrix: Viewer cannot start, act or approve; Operator cannot manage users or "
      "approve; Admin can do all",
      not rbac.allowed({"active": 1, "role": "VIEWER"}, "runs.start") and
      not rbac.allowed({"active": 1, "role": "VIEWER"}, "human.act") and
      not rbac.allowed({"active": 1, "role": "OPERATOR"}, "users.manage") and
      not rbac.allowed({"active": 1, "role": "OPERATOR"}, "atlas.approve") and
      all(rbac.allowed({"active": 1, "role": "ADMIN"}, p) for p in rbac.PERMISSIONS))
check("An inactive account holds no permission at all",
      rbac.permissions_of({"active": 0, "role": "ADMIN"}) == [])
s, _d, _ = admin.get("/api/does-not-exist")
check("An unknown API route is 404 — there is no default allow", s == 404)

# ═════════════════════════════════════════════════════════════════════
rule("3. SECURITY — CSRF, Origin, headers, cookies, limits, separation")
s, d, _ = op.call("POST", "/api/runs", {}, csrf=False)
check("A state change without the CSRF token is refused", s == 403 and d["error"] == "csrf")
s, d, _ = op.call("POST", "/api/runs", {}, headers={"X-CSRF-Token": "forged"})
check("...and with a wrong one", s == 403)
evil = Client(base, origin="https://evil.example")
evil.jar, evil.csrf = op.jar, op.csrf
evil.opener = op.opener
s, d, _ = evil.call("POST", "/api/runs", {})
check("A request from another site's page is refused even with the cookie and token",
      s == 403, str(s))
s, body, h = admin.get("/")
csp = h.get("Content-Security-Policy", "")
check("The dashboard is served with a strict CSP: scripts only from this origin or the "
      "page's own hashed inline blocks", "script-src 'self' 'sha256-" in csp and
      "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0] and
      "frame-ancestors 'none'" in csp, csp[:120])
check("...no inline event handler is left in the page for that CSP to block",
      not any(a in body.decode("utf-8") for a in (" onclick=", " onload=", " onerror=")))
check("Security headers on every response: nosniff, DENY framing, no referrer leak",
      h.get("X-Content-Type-Options") == "nosniff" and h.get("X-Frame-Options") == "DENY" and
      h.get("Referrer-Policy") == "same-origin")
sec_app, sec_base, _ = platform("secure", ATA_PUBLIC_URL="https://ata.mantrac.com",
                                ATA_INSECURE_COOKIES="0")
people(sec_app)
sc = Client(sec_base, origin="https://ata.mantrac.com")
s, d, h = sc.post("/api/auth/login", {"email": "omar.ops@mantrac.com",
                                      "password": PASSWORDS["OPERATOR"]})
check("Over HTTPS the cookie is __Host- prefixed and Secure", "__Host-ata_session=" in
      h.get("Set-Cookie", "") and "Secure" in h.get("Set-Cookie", ""), h.get("Set-Cookie"))
check("...and HSTS is sent", "max-age=31536000" in h.get("Strict-Transport-Security", ""))
check("Insecure cookies cannot be forced on an https public address",
      Settings({"ATA_PUBLIC_URL": "https://ata.mantrac.com",
                "ATA_INSECURE_COOKIES": "1"}).secure_cookies)
codes = [Client(base).post("/api/auth/login", {"email": "rate{0}@mantrac.com".format(i),
                                               "password": "x"})[0] for i in range(22)]
check("Sign-in attempts from one address are rate limited (429)", 429 in codes, str(codes[-4:]))
app.limiter._hits.clear()          # the rest of this file signs in from the same address
s, _d, _ = admin.get("/static/../controlplane/db.py")
s2, _d2, _ = admin.get("/static/%2e%2e/%2e%2e/update_eta.py")
check("Path traversal out of the static folder is refused", s in (403, 404) and s2 in (403, 404),
      "{0} {1}".format(s, s2))
s, _d, _ = admin.call("POST", "/api/ask", raw=b"x" * 200000,
                      headers={"Content-Type": "application/json"})
check("An oversized body is refused, not read", s in (400, 413))
s, d, _ = admin.post("/api/auth/login", {"email": "' OR 1=1 --", "password": "' OR '1'='1"})
check("SQL in the sign-in form is just a wrong email", s in (401, 429))
wid, wtoken = app.orch.add_worker("sec-worker")
s, _d, _ = worker_call(base, wtoken, "GET", "/api/state")
check("A worker token cannot read the dashboard API", s == 401)
s, _d, _ = op.get("/worker/v1/commands")
check("A user session cannot use the worker API", s == 401)
s, _d, _ = worker_call(base, wid + ".not-the-secret", "POST", "/worker/v1/heartbeat", {})
check("A forged worker token is refused", s == 401)
check("The worker token is stored hashed", wtoken not in json.dumps(
    app.db.all("SELECT * FROM workers")))
s, _d, _ = Client(base).get("/healthz")
check("/healthz answers without a session and says nothing sensitive",
      s == 200 and set(_d) == {"ok", "storage"})

# ═════════════════════════════════════════════════════════════════════
rule("4. SSO — Entra ID token checks")
OPENSSL = shutil.which("openssl")
if not OPENSSL:
    skip("RS256 verification against a real RSA key", "openssl not installed")
else:
    key = WORK / "entra.pem"
    subprocess.run([OPENSSL, "genrsa", "-out", str(key), "2048"], capture_output=True, check=True)
    modulus = subprocess.run([OPENSSL, "rsa", "-in", str(key), "-noout", "-modulus"],
                             capture_output=True, text=True, check=True).stdout.split("=")[1].strip()
    b64 = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")   # noqa: E731
    jwk = {"kty": "RSA", "kid": "k1", "n": b64(bytes.fromhex(modulus)),
           "e": b64((65537).to_bytes(3, "big"))}

    def sign(claims, header=None):
        head = b64(json.dumps(header or {"alg": "RS256", "kid": "k1", "typ": "JWT"}).encode())
        body = b64(json.dumps(claims).encode())
        data = (head + "." + body).encode()
        (WORK / "in.bin").write_bytes(data)
        sig = subprocess.run([OPENSSL, "dgst", "-sha256", "-sign", str(key), str(WORK / "in.bin")],
                             capture_output=True, check=True).stdout
        return head + "." + body + "." + b64(sig)

    T = "11111111-2222-3333-4444-555555555555"
    sso_settings = Settings({"DATABASE_URL": "sqlite:///{0}/sso.db".format(WORK),
                             "ENTRA_TENANT_ID": T, "ENTRA_CLIENT_ID": "client-abc",
                             "ENTRA_CLIENT_SECRET": "s3cret", "ATA_PUBLIC_URL": "https://ata.mantrac.com",
                             "ENTRA_ALLOWED_DOMAINS": "mantrac.com"})
    sso_app = App(sso_settings)
    state = {"nonce": None, "token": None}

    def fake_fetch(url, data=None, timeout=10):
        if url.endswith("/discovery/v2.0/keys"):
            return {"keys": [jwk]}
        if url.endswith("/oauth2/v2.0/token"):
            assert data["code_verifier"] and data["client_secret"] == "s3cret"
            return {"id_token": state["token"]}
        raise AssertionError(url)
    sso_app.entra.fetch = fake_fetch
    start = sso_app.entra.start("/")
    from urllib.parse import urlparse, parse_qs
    q = {k: v[0] for k, v in parse_qs(urlparse(start).query).items()}
    check("Sign-in goes to Microsoft with PKCE (S256), a state and a nonce",
          start.startswith("https://login.microsoftonline.com/" + T) and
          q["code_challenge_method"] == "S256" and q["state"] and q["nonce"] and
          q["redirect_uri"] == "https://ata.mantrac.com/auth/sso/callback")
    good = {"iss": "https://login.microsoftonline.com/{0}/v2.0".format(T), "aud": "client-abc",
            "tid": T, "exp": time.time() + 600, "nbf": time.time() - 5, "nonce": q["nonce"],
            "preferred_username": "Omar.Ops@Mantrac.com", "oid": "oid-1", "name": "Omar"}
    state["token"] = sign(good)
    claims, nxt = sso_app.entra.finish("code-1", q["state"])
    check("A correctly signed token for this tenant, app and nonce is accepted",
          claims["email"] == "omar.ops@mantrac.com" and claims["oid"] == "oid-1" and nxt == "/")
    try:
        sso_app.entra.finish("code-1", q["state"])
        reused = False
    except oidc.SSOError:
        reused = True
    check("The same state cannot be used twice", reused)

    def refused(change, header=None, tamper=False):
        st = sso_app.entra.start("/")
        nonce = parse_qs(urlparse(st).query)["nonce"][0]
        claims_ = dict(good, nonce=nonce)
        claims_.update(change)
        token = sign(claims_, header)
        if tamper:
            parts = token.split(".")
            body = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
            body["preferred_username"] = "attacker@mantrac.com"
            parts[1] = b64(json.dumps(body).encode())
            token = ".".join(parts)
        state["token"] = token
        try:
            sso_app.entra.finish("c", parse_qs(urlparse(st).query)["state"][0])
            return False
        except oidc.SSOError:
            return True
    check("Refused: a token whose body was altered after signing", refused({}, tamper=True))
    check("Refused: another application's audience", refused({"aud": "someone-else"}))
    check("Refused: another tenant", refused({"tid": "other", "iss": "https://login."
                                             "microsoftonline.com/other/v2.0"}))
    check("Refused: an expired token", refused({"exp": time.time() - 3600}))
    check("Refused: a nonce from another sign-in", refused({"nonce": "not-mine"}))
    check("Refused: alg 'none'", refused({}, header={"alg": "none", "kid": "k1"}))
    check("Refused: an email outside the allowed domains",
          refused({"preferred_username": "x@gmail.com"}))
    check("The client secret is never part of a page or redirect",
          "s3cret" not in start)

# ═════════════════════════════════════════════════════════════════════
rule("5. USERS — invite, roles, deactivate, reset, last admin")
s, d, _ = admin.post("/api/admin/users", {"email": "New.Hire@Mantrac.com", "display_name": "New Hire",
                                          "role": "OPERATOR"})
link = d.get("invite_link") or ""
check("An admin creates a user; they get a one-time link, not a password",
      s == 200 and d["user"]["must_set_password"] and "/set-password#token=" in link, str(d))
token = link.split("token=")[1]
check("...the link's token is not stored (only its hash)",
      token not in json.dumps(app.db.all("SELECT * FROM tokens")))
s, d, _ = Client(base).post("/api/auth/set-password", {"token": token, "password": "short"})
check("A weak password is refused with what to fix", s == 400 and "12 characters" in d["message"])
s, d, _ = Client(base).post("/api/auth/set-password", {"token": token,
                                                       "password": "Brand-New-Pass-26"})
check("The person sets their own password with the link", s == 200 and d["ok"])
s, d, _ = Client(base).post("/api/auth/set-password", {"token": token,
                                                       "password": "Another-Pass-2026"})
check("...and the link works once only", s == 400)
nh = Client(base)
check("...then signs in with it", nh.login("new.hire@mantrac.com", "Brand-New-Pass-26")[0] == 200)
uid = app.users.by_email("new.hire@mantrac.com")["user_id"]
s, d, _ = admin.call("PATCH", "/api/admin/users/" + uid, {"role": "VIEWER"})
check("Changing a role takes effect at once: their sessions end", s == 200 and
      nh.get("/api/state")[0] == 401)
check("...audited as CHANGE_ROLE with from and to", any(
    e["action"] == "CHANGE_ROLE" and e["metadata"].get("to") == "VIEWER" for e in app.audit.list(50)))
nh.login("new.hire@mantrac.com", "Brand-New-Pass-26")
s, d, _ = admin.call("PATCH", "/api/admin/users/" + uid, {"active": False})
check("Deactivating signs the person out everywhere and blocks sign-in",
      s == 200 and nh.get("/api/state")[0] == 401 and
      Client(base).login("new.hire@mantrac.com", "Brand-New-Pass-26")[0] == 401)
check("...audited as DISABLE_USER", any(e["action"] == "DISABLE_USER" for e in app.audit.list(50)))
admin.call("PATCH", "/api/admin/users/" + uid, {"active": True})
s, d, _ = admin.post("/api/admin/users/{0}/reset".format(uid))
check("Reset access ends sessions and issues a new one-time link",
      s == 200 and "/set-password#token=" in (d.get("invite_link") or ""))
s, d, _ = admin.call("PATCH", "/api/admin/users/" + U["ADMIN"]["user_id"], {"active": False})
check("An admin cannot deactivate themself", s == 400 and "own account" in d["message"])
other = app.users.create("second.admin@mantrac.com", "Second Admin", "ADMIN",
                         password="Second-Admin-26!")[0]
system = {"user_id": "u_test_harness", "role": "ADMIN"}
app.users.update(U["ADMIN"]["user_id"], system, role="OPERATOR")
try:
    app.users.update(other["user_id"], system, active=False)
    last = False
except AccessError as error:
    last = "last active admin" in str(error)
check("The last active admin cannot be removed or demoted", last)
app.users.update(U["ADMIN"]["user_id"], system, role="ADMIN")
admin = signed_in(base, U["ADMIN"], "ADMIN")
s, d, _ = admin.get("/api/admin/users")
check("The user list never contains a password or hash",
      s == 200 and "scrypt$" not in json.dumps(d) and "password_hash" not in json.dumps(d))

# ═════════════════════════════════════════════════════════════════════
rule("6. AUDIT — the trail, and nothing secret in it")
actions = {e["action"] for e in app.audit.list(1000)}
check("Recorded: LOGIN, LOGOUT, LOGIN_FAILED, CREATE_USER, CHANGE_ROLE, DISABLE_USER, "
      "ENABLE_USER, RESET_ACCESS, SET_PASSWORD, ACCESS_DENIED",
      {"LOGIN", "LOGOUT", "LOGIN_FAILED", "CREATE_USER", "CHANGE_ROLE", "DISABLE_USER",
       "ENABLE_USER", "RESET_ACCESS", "SET_PASSWORD", "ACCESS_DENIED"} <= actions,
      str(sorted(actions)))
e = app.audit.list(1)[0]
check("Each event has time, user, action, target, run, result and metadata",
      all(k in e for k in ("ts", "at", "user_id", "user_email", "action", "target_type",
                           "target_id", "run_id", "result", "metadata")))
app.audit.record("SYSTEM_SETTING_CHANGED", metadata={"password": "p@ss", "security_code": "7Q4K",
                                                      "captcha": "x", "token": "t", "field": "ok"})
dump = json.dumps(app.db.all("SELECT * FROM audit"))
check("Passwords, codes, CAPTCHA answers and tokens are dropped from audit metadata",
      "p@ss" not in dump and "7Q4K" not in dump and '"field": "ok"' in json.dumps(
          app.audit.list(1)[0]["metadata"]))
check("Failed sign-ins never record the password that was tried",
      "wrong-password" not in dump and "nope-nope-nope" not in dump)
check("There is no API to change or delete audit events",
      not any(m in ("PATCH", "DELETE") and "audit" in p for m, p, _b, _a in ROUTES))

# ═════════════════════════════════════════════════════════════════════
rule("7. WORKERS — heartbeat, offline, disconnect, reconnect, reconcile")
wapp, wbase, _ = platform("workers")
W = people(wapp)
wop = signed_in(wbase, W["OPERATOR"], "OPERATOR")
s, d, _ = wop.post("/api/runs", {})
check("With no worker registered, Start is refused: 'Automation worker unavailable.'",
      s == 503 and d["message"] == "Automation worker unavailable.")
check("...and no run record is created for it", wapp.orch.runs() == [])
wid, tok = wapp.orch.add_worker("ATA-WORKER-01")
s, d, _ = worker_call(wbase, tok, "POST", "/worker/v1/heartbeat",
                      {"state": "IDLE", "version": "1.0.0", "host": "ATAVM01",
                       "edge": {"found": True}})
check("A heartbeat marks the worker ONLINE / IDLE", s == 200 and d["worker"]["state"] == "IDLE")
health = wop.get("/api/health")[1]
check("Health shows API, worker, browser, ATLAS, storage and the current run",
      health["api"] == "Online" and health["worker"] == "IDLE" and health["browser"] == "Ready"
      and health["storage"] == "Online" and "atlas" in health and health["current_run"] == "None")
s, d, _ = wop.post("/api/runs", {})
run_id = d["run"]["run_id"]
check("Start creates the authoritative run record first (QUEUED)", s == 200 and
      d["run"]["status"] == "QUEUED" and wapp.orch.run(run_id) is not None)
s2, d2, _ = wop.post("/api/runs", {})
check("A second Start is refused and names the active run", s2 == 409 and
      d2["run"]["run_id"] == run_id)
s, d, _ = worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")
cmd = d["commands"][0]
check("The worker collects the start command with the control plane's run id",
      cmd["kind"] == "start_run" and cmd["payload"]["run_id"] == run_id and
      wapp.orch.run(run_id)["status"] == "STARTING")
worker_call(wbase, tok, "POST", "/worker/v1/commands/{0}/result".format(cmd["command_id"]),
            {"ok": True, "message": "Automation started."})
worker_call(wbase, tok, "POST", "/worker/v1/heartbeat",
            {"state": "BUSY", "current_run_id": run_id, "runner": {"running": True}})
state = {"run": {"status": "running", "run_id": run_id}, "counters": {
    "processed": 17, "successful": 14, "failed": 1, "skipped": 0, "needs_human": 1},
    "shipments": [], "human_queue": [], "cold_version": 3}
s, _d, _ = worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(run_id), state)
check("The run's state, as the worker reports it, becomes the authoritative state",
      s == 200 and wapp.orch.run(run_id)["status"] == "RUNNING" and
      wop.get("/api/state")[1]["counters"]["processed"] == 17)
other_wid, other_tok = wapp.orch.add_worker("rogue")
s, _d, _ = worker_call(wbase, other_tok, "POST", "/worker/v1/runs/{0}/state".format(run_id), state)
check("Another worker cannot report state for a run it does not carry", s == 404)
time.sleep(3.5)
wapp.orch.sweep()
check("A worker silent past ATA_WORKER_OFFLINE_S is OFFLINE and its run "
      "WORKER_DISCONNECTED — never marked successful",
      wapp.orch.worker_view(wapp.orch.authenticate_worker(tok))["state"] == "OFFLINE" and
      wapp.orch.run(run_id)["status"] == "WORKER_DISCONNECTED")
st = wop.get("/api/state")[1]
check("The dashboard still shows the run's last report, and says the worker is gone",
      st["counters"]["processed"] == 17 and st["platform"]["run"]["status"] == "WORKER_DISCONNECTED")
s, d, _ = wop.post("/api/runs", {})
check("A disconnected run still counts as active: no duplicate start", s in (409, 503))
worker_call(wbase, tok, "POST", "/worker/v1/heartbeat",
            {"state": "BUSY", "current_run_id": run_id, "runner": {"running": True}})
check("When the worker returns still running it, the run is RUNNING again (reconciled)",
      wapp.orch.run(run_id)["status"] == "RUNNING" and any(
          e["action"] == "RUN_RECONCILED" and e["result"] == "RUNNING" for e in wapp.audit.list(30)))
time.sleep(3.5)
wapp.orch.sweep()
worker_call(wbase, tok, "POST", "/worker/v1/heartbeat",
            {"state": "IDLE", "runner": {"running": False},
             "last_run": {"run_id": run_id, "exit_code": 0, "ended_at": time.time()}})
check("If it ended while disconnected, the exit code decides: COMPLETED",
      wapp.orch.run(run_id)["status"] == "COMPLETED")
s, d, _ = wop.post("/api/runs", {})
r2 = d["run"]["run_id"]
cmd = worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")[1]["commands"][0]
worker_call(wbase, tok, "POST", "/worker/v1/commands/{0}/result".format(cmd["command_id"]),
            {"ok": False, "message": "This worker is already running something."})
check("A start the worker refuses ends as FAILED_TO_START with its reason",
      wapp.orch.run(r2)["status"] == "FAILED_TO_START" and "already" in wapp.orch.run(r2)["detail"])
s, d, _ = wop.post("/api/runs", {})
r3 = d["run"]["run_id"]
worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(r3),
            dict(state, run={"status": "running", "run_id": r3}))
s, d, _ = wop.post("/api/runs/{0}/stop".format(r3), {})
cmd = worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")[1]["commands"]
check("Stop is sent to the worker and the run shows STOPPING", s == 200 and
      cmd and cmd[0]["kind"] == "stop_run" and wapp.orch.run(r3)["status"] == "STOPPING")
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/ended".format(r3), {"exit_code": 0})
check("...and STOPPED when the worker reports the end", wapp.orch.run(r3)["status"] == "STOPPED")
check("START_RUN and STOP_RUN are audited with the person", {
    (e["action"], e["user_email"]) for e in wapp.audit.list(100)} >= {
    ("START_RUN", "omar.ops@mantrac.com"), ("STOP_RUN", "omar.ops@mantrac.com")})
t0 = time.time()
s, d, _ = worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=2")
check("Commands are long-polled: an empty poll waits, then answers",
      s == 200 and d["commands"] == [] and 1.5 < time.time() - t0 < 4)

# ═════════════════════════════════════════════════════════════════════
rule("8. HUMAN ACTION — claim, refusal, holder-only view, release")
s, d, _ = wop.post("/api/runs", {})
hr = d["run"]["run_id"]
worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")
task = {"action_id": "a1b2c3d4e5f6", "run_id": hr, "reference": "S330348776",
        "carrier": "Grimaldi Lines", "status": "WAITING_FOR_HUMAN", "created_epoch": time.time()}
hstate = dict(state, run={"status": "running", "run_id": hr}, human_queue=[task])
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(hr), hstate)
op2 = Client(wbase)
wapp.users.create("olga.ops@mantrac.com", "Olga Ops", "OPERATOR", password="Hub-Write-2027!")
op2.login("olga.ops@mantrac.com", "Hub-Write-2027!")
wview = signed_in(wbase, W["VIEWER"], "VIEWER")
s, d, _ = wview.post("/api/human", {"op": "open", "run_id": hr, "action_id": task["action_id"]})
check("A Viewer cannot Open & Continue (403)", s == 403)
s, d, _ = wop.post("/api/human", {"op": "open", "run_id": "20200101-000000-aaaaaa",
                                  "action_id": task["action_id"]})
check("A request for another run is refused", s == 200 and d["accepted"] is False)
s, d, _ = wop.post("/api/human", {"op": "open", "run_id": hr, "action_id": "nope"})
check("A task that is not waiting is refused, and nothing is sent",
      d["accepted"] is False and "no longer waiting" in d["message"])
s, d, _ = wop.post("/api/human", {"op": "open", "run_id": hr, "action_id": task["action_id"]})
check("Operator A opens it: accepted, claimed for A", d["accepted"] is True and
      wapp.relay.holder(task["action_id"])["user_email"] == "omar.ops@mantrac.com")
cmds = worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")[1]["commands"]
check("...the worker gets the scoped open request and the view request, naming A",
      [c["kind"] for c in cmds] == ["human", "session_attach"] and
      cmds[0]["payload"]["client_id"] == W["OPERATOR"]["user_id"])
s, d, _ = op2.post("/api/human", {"op": "open", "run_id": hr, "action_id": task["action_id"]})
check("Operator B cannot take the same task while A holds it", d["accepted"] is False and
      d["holder"] == "omar.ops@mantrac.com")
check("...the refusal is audited", any(e["action"] == "OPEN_HUMAN_ACTION" and
      e["result"] == "REFUSED_CLAIMED" for e in wapp.audit.list(40)))
jpeg = b"\xff\xd8\xff\xe0" + b"0" * 2000 + b"\xff\xd9"
s, d, _ = worker_call(wbase, tok, "POST", "/worker/v1/session/{0}/frame".format(task["action_id"]),
                      raw=jpeg, headers={"Content-Type": "image/jpeg",
                                         "X-Meta": json.dumps({"width": 1280, "height": 800})})
check("The worker posts a frame of the run's tab", s == 200)
s, body, h = wop.get("/api/session/{0}/frame?after=0".format(task["action_id"]))
check("A, the holder, receives it (JPEG, sequence, page size)",
      s == 200 and body == jpeg and h.get("X-Seq") == "1" and "1280" in h.get("X-Meta", ""))
s, d, _ = op2.get("/api/session/{0}/frame?after=0".format(task["action_id"]))
check("B, not the holder, gets 409 — no frame", s == 409 and d["error"] == "not_holder")
s, d, _ = wview.get("/api/session/{0}/frame".format(task["action_id"]))
check("A Viewer gets 403", s == 403)
events = [{"type": "mousePressed", "x": 300, "y": 200, "button": "left", "clickCount": 1},
          {"type": "keyDown", "key": "K", "text": "K"}, {"type": "keyUp", "key": "K"}]
s, d, _ = wop.post("/api/session/{0}/input".format(task["action_id"]), {"events": events})
check("A's pointer and keys are accepted, and nothing about them is echoed back",
      s == 200 and d == {"ok": True})
s, d, _ = op2.post("/api/session/{0}/input".format(task["action_id"]), {"events": events})
check("B's input is refused", s == 409)
s, d, _ = worker_call(wbase, tok, "GET", "/worker/v1/session/{0}/input?wait=1".format(
    task["action_id"]))
check("The worker that carries the run receives A's events, in order",
      [e["type"] for e in d["events"]] == ["mousePressed", "keyDown", "keyUp"] and d["held"])
s, d, _ = worker_call(wbase, other_tok, "GET", "/worker/v1/session/{0}/input?wait=1".format(
    task["action_id"]))
check("Another worker cannot read that session's input", s == 404)
try:
    clean_events([{"type": "eval", "code": "alert(1)"}])
    shaped = False
except ValueError:
    shaped = True
check("Only pointer and key events of the expected shape pass", shaped and len(clean_events(
    [{"type": "keyDown", "key": "a", "text": "a", "evil": 1}])[0]) == 5)
secret_typed = "7Q4K-SECRET"
wop.post("/api/session/{0}/input".format(task["action_id"]), {"events": [
    {"type": "keyDown", "key": ch, "text": ch} for ch in secret_typed[:4]]})
dump = json.dumps({t: wapp.db.all("SELECT * FROM " + t) for t in
                   ("audit", "run_state", "commands", "claims", "runs", "sessions")})
check("What the person types is not in the database — audit, state, commands, claims",
      "7Q4K" not in dump)
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(hr), dict(
    hstate, human_queue=[dict(task, status="RESUMING")]))
check("When the RUN reports the verification confirmed, it is audited for A",
      any(e["action"] == "CONTINUE_HUMAN_ACTION" and e["result"] == "VERIFICATION_CONFIRMED" and
          e["user_email"] == "omar.ops@mantrac.com" for e in wapp.audit.list(40)))
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(hr), dict(
    hstate, human_queue=[dict(task, status="SUCCESS")]))
check("...and the final state (SUCCESS, from the run) closes the claim",
      wapp.relay.holder(task["action_id"]) is None and any(
          e["action"] == "CONTINUE_HUMAN_ACTION" and e["result"] == "SUCCESS"
          for e in wapp.audit.list(40)))
t2 = dict(task, action_id="b2c3d4e5f6a1", status="WAITING_FOR_HUMAN")
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(hr), dict(hstate, human_queue=[t2]))
wop.post("/api/human", {"op": "open", "run_id": hr, "action_id": t2["action_id"]})
s, d, _ = wop.post("/api/session/{0}/release".format(t2["action_id"]))
check("Leaving releases the claim; another operator can then take it", s == 200 and
      op2.post("/api/human", {"op": "open", "run_id": hr,
                              "action_id": t2["action_id"]})[1]["accepted"] is True)
lease_app, lease_base, _ = platform("lease", ATA_SESSION_LEASE_S="1")
la = people(lease_app)
lease_app.relay.claim(la["OPERATOR"], "r", "lease1")
time.sleep(1.2)
try:
    lease_app.relay.claim(la["ADMIN"], "r", "lease1")
    taken = True
except ClaimError:
    taken = False
check("A claim nobody renews lapses after ATA_SESSION_LEASE_S", taken)
wapp.orch.run_ended(wapp.orch.authenticate_worker(tok), hr, 0)
st = wop.get("/api/state")[1]
check("Once the run has ended, its waiting tasks show HUMAN_SESSION_LOST — nothing to open",
      all(t["status"] in ("HUMAN_SESSION_LOST", "SUCCESS") for t in st["human_queue"]))
s, d, _ = wop.post("/api/human", {"op": "open", "run_id": hr, "action_id": t2["action_id"]})
check("...and Open & Continue is refused", d["accepted"] is False)

# ═════════════════════════════════════════════════════════════════════
rule("9. ATLAS — chat by role, no re-run from chat, proposals, data origin")
s, d, _ = wview.post("/api/ask", {"question": "what needs me?"})
check("A Viewer can ask ATLAS; it answers from the authoritative state",
      s == 200 and d.get("answer"))
from intelligence import events as intel_events, store as intel_store, learning as intel_learning
for i in range(60):
    e = time.time() - 86400 + i * 60
    v = True
    # Every shipment's first way in fails. The automation tries "fresh
    # context" next, which works two times in three; when it fails, "clean
    # edge" is tried, and it has worked every time.
    s_ = [{"attempt": 1, "strategy": "existing page", "page_verified": False, "skipped": False,
           "error": "ERR_HTTP2"},
          {"attempt": 2, "strategy": "fresh context", "page_verified": i % 3 != 0,
           "skipped": False, "error": None}]
    if i % 3 == 0:
        s_.append({"attempt": 3, "strategy": "clean edge, HTTP/2 disabled",
                   "page_verified": True, "skipped": False, "error": None})
    intel_events.record("shipment", run_id="r%d" % (i % 6), reference="074-%08d" % i,
                        provider="AFKL", carrier="Air France KLM Cargo",
                        result="SUCCESS" if v else "FAILED",
                        outcome_class=None if v else "AFKL NAVIGATION ERROR", verified=v,
                        strategy_issue="AFKL navigation", strategies=s_, epoch=e,
                        at=intel_store.stamp(e))
intel_learning.invalidate()
props = wop.get("/api/atlas/proposals")[1]["proposals"]
if not props:
    skip("Proposal approval", "the test record produced no proposal")
else:
    pid = props[0]["id"]
    s, d, _ = wop.post("/api/atlas/proposals/{0}/approve".format(pid), {})
    check("An Operator cannot approve a proposal", s == 403)
    wadmin = signed_in(wbase, W["ADMIN"], "ADMIN")
    s, d, _ = wadmin.post("/api/atlas/proposals/{0}/approve".format(pid), {})
    check("An Admin approves it: recorded, and the automation unchanged",
          s == 200 and d["ok"] and "unchanged" in d["message"])
    after = wop.get("/api/atlas/proposals")[1]["proposals"][0]
    check("...the decision names who made it", after["status"] == "APPROVED" and
          after["decided_by"] == "ada.admin@mantrac.com")
    check("...audited as APPROVE_PROPOSAL, deployed: false", any(
        e["action"] == "APPROVE_PROPOSAL" and e["metadata"].get("deployed") is False
        for e in wapp.audit.list(20)))
    s, d, _ = wadmin.post("/api/atlas/proposals/ffffffffff/approve", {})
    check("An unknown proposal cannot be approved", s == 404)
lrn = wop.get("/api/atlas/learning")[1]
check("The learning page says whose data it is: TEST here", lrn.get("data_origin") == "test")
s, d, _ = worker_call(wbase, tok, "POST", "/worker/v1/intel/events",
                      {"origin": "production", "records": [{"kind": "question", "intent": "x"}]})
check("Production records are refused by a test store (and the reverse)", s == 409)
before = len(intel_events.all_events())
rec = {"kind": "question", "intent": "attention", "sync_id": "abc123abc123"}
worker_call(wbase, tok, "POST", "/worker/v1/intel/events", {"origin": "test", "records": [rec]})
worker_call(wbase, tok, "POST", "/worker/v1/intel/events", {"origin": "test", "records": [rec]})
check("Worker records sync once — the same line twice is stored once",
      len(intel_events.all_events()) == before + 1)
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
entry = {"id": "0123456789ab", "source": "browser_capture", "sha256": hashlib.sha256(png).hexdigest(),
         "run_id": hr, "reference": "074-1", "event": "afkl_error"}
s, d, _ = worker_call(wbase, tok, "POST", "/worker/v1/intel/evidence",
                      {"origin": "test", "entry": dict(entry, sha256="0" * 64),
                       "image": base64.b64encode(png).decode()})
check("A capture whose bytes do not match its hash is refused", s == 400)
s, d, _ = worker_call(wbase, tok, "POST", "/worker/v1/intel/evidence",
                      {"origin": "test", "entry": entry, "image": base64.b64encode(png).decode()})
check("A real capture is stored on the control plane and can be viewed",
      s == 200 and wop.get("/api/evidence/file?id=0123456789ab")[0] == 200)
check("...and viewing it is audited (VIEW_EVIDENCE)", any(
    e["action"] == "VIEW_EVIDENCE" for e in wapp.audit.list(10)))
s, d, _ = worker_call(wbase, tok, "POST", "/worker/v1/intel/evidence",
                      {"origin": "test", "entry": dict(entry, id="ba9876543210", source="user_upload"),
                       "image": base64.b64encode(png).decode()})
check("Only automation captures sync — never an upload", s == 400)

# ═════════════════════════════════════════════════════════════════════
rule("10. THEME — the choice is the person's, kept on the server")
s, d, _ = wop.post("/api/me/prefs", {"theme": "dark"})
check("Choosing Dark is saved for the account", s == 200 and d["prefs"]["theme"] == "dark")
fresh = signed_in(wbase, W["OPERATOR"], "OPERATOR")
check("...and comes back on another device", fresh.get("/api/auth/me")[1]["user"]["prefs"]["theme"]
      == "dark")
s, d, _ = wop.post("/api/me/prefs", {"theme": "purple"})
check("Only light, dark or system", s == 400)
wop.post("/api/me/prefs", {"theme": "system"})
check("System is a choice of its own", fresh.get("/api/auth/me")[1]["user"]["prefs"]["theme"]
      == "system")

# ═════════════════════════════════════════════════════════════════════
rule("11. STREAM — live state, and the same run from a second device")
worker_call(wbase, tok, "POST", "/worker/v1/heartbeat", {"state": "IDLE"})
s, d, _ = wop.post("/api/runs", {})
sr = d["run"]["run_id"]
worker_call(wbase, tok, "POST", "/worker/v1/heartbeat", {"state": "BUSY", "current_run_id": sr,
                                                         "runner": {"running": True}})
worker_call(wbase, tok, "GET", "/worker/v1/commands?wait=1")
got = []


def listen(client):
    req = urllib.request.Request(wbase + "/api/stream")
    with client.opener.open(req, timeout=10) as r:
        buf = b""
        while len(got) < 3:
            buf += r.read1(65536)
            while b"\n\n" in buf:
                chunk, buf = buf.split(b"\n\n", 1)
                if chunk.startswith(b"event: state"):
                    got.append((time.time(), json.loads(chunk.split(b"data: ", 1)[1])))


tl = threading.Thread(target=listen, args=(wop,), daemon=True)
tl.start()
time.sleep(0.5)
sent = time.time()
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(sr), dict(
    state, run={"status": "running", "run_id": sr}, counters=dict(state["counters"], processed=18)))
tl.join(6)
latest = [g for g in got if (g[1].get("counters") or {}).get("processed") == 18]
check("A state the worker reports reaches an open dashboard over SSE",
      bool(latest), str([g[1].get("counters") for g in got]))
if latest:
    check("...within half a second (measured {0:.0f} ms)".format((latest[0][0] - sent) * 1000),
          latest[0][0] - sent < 0.5)
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(sr), dict(
    state, run={"status": "running", "run_id": sr}, counters=dict(state["counters"], processed=18),
    shipments=[{"reference": "S330348776", "carrier": "Grimaldi", "provider": "GRIMALDI",
                "state": "failed", "error": "AFKL"}]))
s, d, _ = wop.post("/api/ask", {"question": "re-run S330348776"})
check("A re-run asked for in chat is not taken from chat — only Open & Continue is",
      d.get("accepted") is False and "not started from chat" in d["answer"], str(d)[:200])
phone = signed_in(wbase, W["VIEWER"], "VIEWER")
check("Another device, signed in later, sees the same run and the same numbers",
      phone.get("/api/state")[1]["counters"]["processed"] == 18 and
      phone.get("/api/state")[1]["platform"]["run"]["run_id"] == sr)
check("A Viewer's state carries no run controls", phone.get("/api/state")[1]["control"]["enabled"]
      is False and phone.get("/api/state")[1]["control"]["can_start"] is False)

# ═════════════════════════════════════════════════════════════════════
rule("12. PERF — building what the dashboard draws")
big = dict(state, run={"status": "running", "run_id": sr}, shipments=[
    {"reference": "REF%05d" % i, "carrier": "Maersk", "provider": "MAERSK", "state": "updated",
     "provider_eta": "02/10/2026", "transport_mode": "ocean", "timeline": [{"t": "x"}] * 6}
    for i in range(300)])
worker_call(wbase, tok, "POST", "/worker/v1/runs/{0}/state".format(sr), big)
user = wapp.users.by_email("omar.ops@mantrac.com")
t0 = time.perf_counter()
for _ in range(50):
    wapp.payload(user)
per = (time.perf_counter() - t0) / 50 * 1000
check("The authoritative payload for 300 shipments builds in under 25 ms "
      "(measured {0:.1f} ms)".format(per), per < 25)
t0 = time.perf_counter()
for _ in range(50):
    wapp.payload(user, since_cold=3)
per2 = (time.perf_counter() - t0) / 50 * 1000
check("...and an update that leaves the shipments unchanged omits them "
      "(measured {0:.1f} ms)".format(per2), "unchanged" in wapp.payload(user, since_cold=3))

# ═════════════════════════════════════════════════════════════════════
rule("13. LOCAL — the run's own dashboard is unchanged")
from dashboard import server as tower_server
local = tower_server.start(port=0 or 18911, open_browser=False, host="127.0.0.1")
lc = Client("http://127.0.0.1:18911")
s, _d, _ = lc.get("/api/auth/me")
check("Without the control plane there is no sign-in: /api/auth/me is 404, so the "
      "page stays in local mode", s == 404)
check("...and the local dashboard still serves state with no session", lc.get("/api/state")[0] == 200)
page = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
check("The page waits for the platform check before connecting, in both modes",
      "platformReady.then(connect)" in page)
check("Light, Dark and System are offered; Dark is a designed token set, not an inversion",
      all('data-theme-set="{0}"'.format(t) in page for t in ("light", "dark", "system")) and
      ':root[data-theme="dark"]{' in page and "invert(" not in page)
check("The theme is applied before the first paint (no flash)",
      page.index("dataset.theme=d?'dark':'light'") < page.index("<body"))

# ═════════════════════════════════════════════════════════════════════
rule("14. POSTGRES — the same rules on PostgreSQL")
PG = os.environ.get("ATA_TEST_PG_URL")
try:
    import psycopg  # noqa: F401
    have_pg = bool(PG)
except ImportError:
    have_pg = False
if not have_pg:
    skip("PostgreSQL backend", "set ATA_TEST_PG_URL (and pip install psycopg[binary]) to run")
else:
    import psycopg
    with psycopg.connect(PG, autocommit=True) as conn:
        for table in ("meta", "users", "sessions", "tokens", "sso_states", "audit", "workers",
                      "runs", "run_state", "commands", "claims"):
            conn.execute("DROP TABLE IF EXISTS {0}".format(table))
    papp = App(Settings({"DATABASE_URL": PG, "ATA_INSECURE_COOKIES": "1"}))
    pu = people(papp)
    check("Accounts and sign-in work on PostgreSQL",
          papp.users.authenticate("omar.ops@mantrac.com", PASSWORDS["OPERATOR"])[0] is not None)
    pw, ptok = papp.orch.add_worker("pg")
    papp.orch.heartbeat(papp.orch.authenticate_worker(ptok), {"state": "IDLE"})
    check("Heartbeat timestamps keep full precision (8-byte)",
          abs(papp.db.one("SELECT last_heartbeat FROM workers")["last_heartbeat"] - time.time()) < 5)
    wins = []

    def go(i):
        try:
            papp.orch.start_run(pu["OPERATOR"])
            wins.append(i)
        except RunError:
            pass
    ts = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("Ten simultaneous Starts create exactly one run", len(wins) == 1, str(len(wins)))
    res = {"ok": 0, "refused": 0, "other": 0}

    def grab(i):
        try:
            papp.relay.claim({"user_id": "u%d" % i, "work_email": "u%d@x.com" % i}, "r", "pg-act")
            res["ok"] += 1
        except ClaimError:
            res["refused"] += 1
        except Exception:
            res["other"] += 1
    ts = [threading.Thread(target=grab, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    check("Twelve operators racing for one Human Action: one holds it, eleven are told who",
          res == {"ok": 1, "refused": 11, "other": 0}, str(res))

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(len(PASS), len(FAIL),
                                         ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
