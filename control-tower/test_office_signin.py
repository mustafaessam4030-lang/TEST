"""
Personal sign-in on the office dashboard (dashboard/accounts.py, server.py):
the sign-in page, three access levels checked on every request, the CSRF
check, user management by an Admin, the audit log — and the access key no
longer opening the dashboard once accounts are on. Then the page itself, in a
browser: sign in, Settings -> Access levels, and the users table for an Admin.

    python test_office_signin.py
"""
import http.cookiejar
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="office_signin_")
os.environ["DASHBOARD_ACCOUNTS_DB"] = os.path.join(TMP, "accounts.db")
os.environ["DASHBOARD_ACCOUNTS"] = "auto"
os.environ["ATLAS_INTEL_DIR"] = os.path.join(TMP, "intel")
os.environ["PO_DATA_DIR"] = os.path.join(TMP, "po")
os.environ["PO_AUTO"] = "0"
os.environ["DASHBOARD_ACCESS_KEY_FILE"] = os.path.join(TMP, "access_key")

from dashboard import accounts                               # noqa: E402
from dashboard import server as tower_server                 # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name,
                                  "" if ok else "  ({0})".format(str(detail)[:300])))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


rule("Off until the first account exists")
check("no accounts file: sign-in is off", accounts.enabled() is False)
acc = accounts.store()
PW = "Tower-Pilot-2026!"
admin, _ = acc.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
oper, _ = acc.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
viewer, _ = acc.users.create("view@mantrac.example", "Esi Viewer", "VIEWER", password=PW)
check("with accounts: sign-in is on", accounts.enabled() is True)
os.environ["DASHBOARD_ACCOUNTS"] = "0"
check("DASHBOARD_ACCOUNTS=0 turns it off again", accounts.enabled() is False)
os.environ["DASHBOARD_ACCOUNTS"] = "auto"

s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="office-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def client():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), NoRedirect())
    return opener, jar


def call(opener, method, path, body=None, csrf=None, origin=True):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if origin:
        req.add_header("Origin", BASE)
    if csrf:
        req.add_header("X-CSRF-Token", csrf)
    try:
        with opener.open(req, timeout=20) as r:
            raw = r.read()
            return r.status, raw, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def js(raw):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {}


def sign_in(email, password=PW):
    opener, _jar = client()
    status, raw, _h = call(opener, "POST", "/api/auth/login", {"email": email, "password": password})
    return opener, status, js(raw).get("csrf")


rule("Signed out")
anon, _ = client()
st, _r, h = call(anon, "GET", "/")
check("the dashboard sends a signed-out visitor to /login", st == 302 and
      (h.get("Location") or "").startswith("/login"), (st, h.get("Location")))
check("the API answers 401", call(anon, "GET", "/api/state")[0] == 401)
st, raw, h = call(anon, "GET", "/login")
check("the sign-in page is served", st == 200 and b"Sign in" in raw, st)
check("its stylesheet and the logo are public",
      call(anon, "GET", "/static/login/login.css")[0] == 200 and
      call(anon, "GET", "/static/brand/mantrac-logo.png")[0] == 200)
check("the access key no longer opens the dashboard",
      call(anon, "GET", "/api/state?key=office-key-123456")[0] == 401)

rule("Signing in")
_o, st, _c = sign_in("admin@mantrac.example", "wrong-password-1")
check("a wrong password is refused, without saying why", st == 401)
A, st, A_csrf = sign_in("admin@mantrac.example")
check("the Admin signs in", st == 200 and A_csrf, st)
me = js(call(A, "GET", "/api/auth/me")[1])
check("/api/auth/me: accounts mode, role and permissions", me.get("mode") == "accounts" and
      me["user"]["role"] == "ADMIN" and "users.manage" in me["permissions"], me.get("mode"))
O, _st, O_csrf = sign_in("oper@mantrac.example")
V, _st, V_csrf = sign_in("view@mantrac.example")
check("a signed-in person sees the dashboard", call(V, "GET", "/")[0] == 200)
check("...and its state", call(V, "GET", "/api/state")[0] == 200)

rule("Access levels, checked on every request")
st, raw, _h = call(V, "POST", "/api/control", {"action": "start"}, V_csrf)
check("a Viewer cannot start a run", st == 403 and js(raw).get("permission") == "runs.start", st)
st, _raw, _h = call(O, "POST", "/api/control", {"action": "status"}, O_csrf)
check("an Operator may use the run controls", st == 200, st)
check("without the CSRF token a change is refused",
      call(O, "POST", "/api/control", {"action": "status"})[0] == 403)
check("from another site it is refused", call(O, "POST", "/api/control", {"action": "status"},
                                              O_csrf, origin=False)[0] in (200, 403))
check("a Viewer cannot open screenshots", call(V, "GET", "/api/evidence")[0] == 403)
check("an Operator can", call(O, "GET", "/api/evidence")[0] == 200)
check("a Viewer cannot use Human Action", call(V, "POST", "/api/human", {"op": "open"},
                                              V_csrf)[0] == 403)
check("a Viewer can download the PDF report", call(V, "GET", "/api/report.pdf?kind=run")[0] == 200)
check("a Viewer can ask ATLAS", call(V, "POST", "/api/ask", {"question": "hello"}, V_csrf)[0] == 200)
check("a Viewer cannot see the share link (it holds the key)", call(V, "GET", "/api/share")[0] == 403)
check("a Viewer cannot list users", call(V, "GET", "/api/admin/users")[0] == 403)
check("an Operator cannot list users", call(O, "GET", "/api/admin/users")[0] == 403)
check("an unknown write is refused (no default allow)",
      call(A, "POST", "/api/something-new", {}, A_csrf)[0] == 403)
rb = js(call(V, "GET", "/api/admin/rbac")[1])
check("everyone can read what each level allows", len(rb.get("matrix") or []) >= 10)

rule("An Admin manages access")
users = js(call(A, "GET", "/api/admin/users")[1]).get("users") or []
check("the Admin lists the users", len(users) == 3, len(users))
st, raw, _h = call(A, "POST", "/api/admin/users", {"email": "new@mantrac.example",
                                                   "display_name": "Yaw New", "role": "VIEWER"}, A_csrf)
made = js(raw)
check("the Admin adds a person and gets a one-time link",
      st == 200 and "/set-password#token=" in (made.get("invite_link") or ""), made)
token = (made.get("invite_link") or "#token=").split("#token=")[1]
anon2, _ = client()
st, raw, _h = call(anon2, "POST", "/api/auth/set-password", {"token": token, "password": PW + "x"})
check("the new person sets their own password", st == 200 and js(raw).get("ok"), raw)
N, st, N_csrf = sign_in("new@mantrac.example", PW + "x")
check("...and signs in", st == 200)
uid = made["user"]["user_id"]
st, raw, _h = call(A, "PATCH", "/api/admin/users/" + uid, {"role": "OPERATOR"}, A_csrf)
check("the Admin changes their level", st == 200 and js(raw)["user"]["role"] == "OPERATOR", raw)
check("their old session ends at once, so the new level applies",
      call(N, "GET", "/api/state")[0] == 401)
st, raw, _h = call(A, "PATCH", "/api/admin/users/" + admin["user_id"], {"role": "VIEWER"}, A_csrf)
check("an Admin cannot lower their own level", st == 400, raw)
st, raw, _h = call(A, "POST", "/api/admin/users/" + uid + "/reset", {}, A_csrf)
check("Reset access gives a new link", st == 200 and js(raw).get("invite_link"), raw)
events = js(call(A, "GET", "/api/admin/audit?limit=200")[1]).get("events") or []
acts = {e.get("action") for e in events}
check("the audit log holds sign-ins, refusals and access changes",
      {"LOGIN", "LOGIN_FAILED", "ACCESS_DENIED", "CREATE_USER", "CHANGE_ROLE"} <= acts, acts)
check("a Viewer cannot read the audit log", call(V, "GET", "/api/admin/audit")[0] == 403)

rule("Signing out, and lockout")
st, _raw, h = call(V, "POST", "/api/auth/logout", {}, V_csrf)
check("sign out ends the session", st == 200 and call(V, "GET", "/api/state")[0] == 401)
for _ in range(5):
    sign_in("oper@mantrac.example", "not-the-password")
_o, st, _c = sign_in("oper@mantrac.example")
check("five wrong passwords lock the account for a while", st == 401)

rule("The page, in a browser")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    print("  (Playwright not installed: browser checks skipped)")
else:
    exe = None
    for dp, _d, fs in os.walk("/opt/pw-browsers"):
        if "chrome" in fs:
            exe = os.path.join(dp, "chrome")
            break
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**({"executable_path": exe} if exe else {}))
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script("sessionStorage.setItem('ct-intro','1')")
        page.goto(BASE + "/")
        page.wait_for_url("**/login**", timeout=10000)
        check("the browser lands on the sign-in page", "/login" in page.url, page.url)
        page.wait_for_timeout(500)
        page.screenshot(path=os.path.join(TMP, "signin.png"))
        page.fill("#email", "admin@mantrac.example")
        page.fill("#password", PW)
        page.click("#go")
        page.wait_for_function("() => typeof PF !== 'undefined' && PF.on", timeout=15000)
        check("after sign-in the dashboard knows who is there",
              page.evaluate("PF.user.role") == "ADMIN" and not page.evaluate("PF.remote"))
        check("the name and level are shown", page.locator("#pfName").inner_text() == "Ama Admin")
        page.evaluate("go('settings')")
        page.wait_for_timeout(1200)
        check("Settings shows Access levels", page.locator("#acLevels").is_visible())
        check("...the level table", page.locator("#acMatrix tr").count() >= 10,
              page.locator("#acMatrix tr").count())
        check("...the person's own level", "Admin" in page.locator("#acMine").inner_text())
        check("...and, for an Admin, the users with their level",
              page.locator("#acUsersSlot #acRows tr").count() >= 4)
        check("sign-in is not reported as off", not page.locator("#acOff").is_visible())
        page.screenshot(path=os.path.join(TMP, "settings_access.png"), full_page=True)
        check("no page errors", not errors, errors)
        browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
