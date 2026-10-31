"""
The Hub's own sign-in page (WinLogin.aspx): the automation signs in through
it, says it succeeded only when it is past it, and fails closed with a clear
reason when the account is refused. A local imitation of the Hub, in a real
browser.

    python test_hub_signin.py
"""
import http.server
import os
import socketserver
import sys
import threading
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


LIST = "/WorkFlow/ShipmentTracking/ShipmentList.aspx"
STATE = {"form": True}
LOGIN_HTML = """<html><body><form method="post" action="/WinLogin.aspx">
<label for="ctl00_txtUserName">User Name</label><input id="ctl00_txtUserName" name="ctl00$txtUserName" type="text">
<label for="ctl00_txtPassword">Password</label><input id="ctl00_txtPassword" name="ctl00$txtPassword" type="password">
<input type="submit" name="ctl00$btnLogin" value="Login"></form></body></html>"""
APP_HTML = """<html><body><a href="#">Centralized Shipments Tracking</a><table><tr><th>BOL/AWB</th><th>Carrier</th></tr></table></body></html>"""


class Hub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _signed(self):
        return "hub=1" in (self.headers.get("Cookie") or "")

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if STATE.get("basic"):
            import base64
            got = self.headers.get("Authorization") or ""
            if got.startswith("Basic "):
                STATE["attempts"] = STATE.get("attempts", 0) + 1
            if got != "Basic " + base64.b64encode(STATE["basic"].encode()).decode():
                self.send_response(401); self.send_header("WWW-Authenticate", 'Basic realm="hub"')
                self.send_header("Content-Type", "text/html"); self.end_headers()
                self.wfile.write(b"<html><body><h2>401 - Unauthorized: Access is denied due to invalid credentials.</h2></body></html>")
                return
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(APP_HTML.encode()); return
        if STATE.get("refuse"):
            self.send_response(401); self.send_header("Content-Type", "text/html"); self.end_headers()
            self.wfile.write(b"<html><body><h1>Server Error</h1><h2>401 - Unauthorized: Access is denied due to invalid credentials.</h2></body></html>")
            return
        if path == LIST and STATE["form"] and not self._signed():
            self.send_response(302); self.send_header("Location", "/WinLogin.aspx?ReturnUrl=" + urllib.parse.quote(LIST)); self.end_headers(); return
        body = LOGIN_HTML if path == "/WinLogin.aspx" else APP_HTML
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode())

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode())
        ok = form.get("ctl00$txtUserName") == ["hubuser"] and form.get("ctl00$txtPassword") == ["right-pass"]
        self.send_response(302)
        if ok:
            self.send_header("Set-Cookie", "hub=1; Path=/")
        self.send_header("Location", LIST if ok else "/WinLogin.aspx?error=1")
        self.end_headers()


srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Hub)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % srv.server_address[1]

os.environ.setdefault("ATA_RUNTIME_DIR", str(HERE / "dashboard" / ".runtime_test_hub_signin"))
import update_eta as U  # noqa: E402

U.INTERNAL_URL = BASE + LIST
logged = []
U.write_log = lambda m, *a, **k: logged.append(str(m))

from playwright.sync_api import sync_playwright  # noqa: E402

print("\n1. THE HUB'S OWN SIGN-IN PAGE")
with sync_playwright() as pw:
    try:
        browser = pw.chromium.launch()
    except Exception:
        browser = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")

    def fresh():
        return browser.new_context().new_page()

    pg = fresh(); logged.clear()
    U.login_internal(pg, "hubuser", "right-pass")
    check("On WinLogin.aspx the stored account is entered and the Hub opens",
          pg.url.endswith(LIST) and not U.on_hub_login_page(pg), pg.url)
    check("...and 'succeeded' is said only after it is past the sign-in page",
          any("signing in" in m for m in logged) and logged[-1].startswith("Internal authentication succeeded"), logged)
    check("The password never reaches the log", not any("right-pass" in m for m in logged))

    pg = fresh(); logged.clear()
    try:
        U.login_internal(pg, "hubuser", "wrong-pass")
        raised = None
    except Exception as error:
        raised = str(error)
    check("A refused account fails closed with the reason, never 'succeeded'",
          raised is not None and "Hub sign-in failed" in raised and "WinLogin.aspx" in raised
          and not any(m.startswith("Internal authentication succeeded") for m in logged), (raised, logged))
    check("...and the reason does not contain the password", raised is not None and "wrong-pass" not in raised)

    STATE["form"] = False
    pg = fresh(); logged.clear()
    U.login_internal(pg, "hubuser", "right-pass")
    check("A Hub that needs no form (browser-level sign-in) opens directly, as before",
          logged == ["Opening internal website...", "Internal authentication succeeded."], logged)
    STATE["refuse"] = True
    U.HUB_ACCOUNT_SOURCE = "Windows Credential Manager"
    pg = fresh(); logged.clear()
    try:
        U.login_internal(pg, "hubuser", "right-pass"); raised = None
    except Exception as error:
        raised = str(error)
    check("The Hub's IIS '401 - Unauthorized' page stops the run with the reason and where the account came from",
          raised is not None and "401 Unauthorized" in raised and "Windows Credential Manager" in raised
          and "DOMAIN\\username" in raised and not any(m.startswith("Internal authentication succeeded") for m in logged), raised)
    STATE["refuse"] = False

    print("\n2. THE OLD ACCOUNT IN CREDENTIAL MANAGER, THE NEW ONE IN credentials.txt")
    STATE["basic"] = "hubuser:new-pass"
    U.hub_accounts = lambda: [("hubuser", "old-pass", "Windows Credential Manager"),
                              ("hubuser", "new-pass", r"C:\Automation\credentials.txt")]
    U.HUB_ACCOUNT_SOURCE = "Windows Credential Manager"
    ctx = browser.new_context(**U.hub_context_options("hubuser", "old-pass")); pg = ctx.new_page(); logged.clear()
    ctx2, pg2, user, secret = U.sign_in_hub(browser, ctx, pg, "hubuser", "old-pass")
    check("Credential Manager's old account is refused: the different one in credentials.txt is tried and signs in",
          secret == "new-pass" and pg2.url.endswith(LIST) and U.HUB_ACCOUNT_SOURCE.endswith("credentials.txt")
          and any("trying the account in" in m for m in logged) and any("hub_credentials move" in m for m in logged), logged)
    check("...and no password is logged", not any("pass" in m and ("old-pass" in m or "new-pass" in m) for m in logged))
    STATE["basic"] = "hubuser:another-pass"; STATE["attempts"] = 0
    U.HUB_ACCOUNT_SOURCE = "Windows Credential Manager"
    ctx = browser.new_context(**U.hub_context_options("hubuser", "old-pass")); pg = ctx.new_page()
    try:
        U.sign_in_hub(browser, ctx, pg, "hubuser", "old-pass"); raised = None
    except Exception as error:
        raised = str(error)
    check("Both refused: it stops with the reason after ONE extra attempt (never enough to lock the account)",
          raised is not None and "credentials.txt" in raised and STATE["attempts"] <= 4, (raised, STATE["attempts"]))
    U.hub_accounts = lambda: [("hubuser", "old-pass", "Windows Credential Manager")]
    ctx = browser.new_context(**U.hub_context_options("hubuser", "old-pass")); pg = ctx.new_page()
    try:
        U.sign_in_hub(browser, ctx, pg, "hubuser", "old-pass"); raised = None
    except Exception as error:
        raised = str(error)
    check("Only one stored account: refused means stop, nothing else is tried", raised is not None and "Windows Credential Manager" in raised)
    STATE["basic"] = None
    browser.close()

print("\n3. NO KEYBOARD, NO EOFError")
_stdin = sys.stdin
try:
    import io
    sys.stdin = io.StringIO("")
    U._cp_sys.stdin = sys.stdin
    U.pause_for_enter("Press ENTER")
    check("Started without a console (dashboard, scheduled task), the ENTER prompt does not raise", True)
except Exception as error:
    check("Started without a console (dashboard, scheduled task), the ENTER prompt does not raise", False, repr(error))
finally:
    sys.stdin = _stdin; U._cp_sys.stdin = _stdin
src = (HERE / "update_eta.py").read_text(encoding="utf-8")
iis = (HERE / "deploy" / "iis" / "setup_iis.ps1").read_text(encoding="utf-8")
check("The IIS setup turns anonymous access on and Windows/Basic sign-in off for the site (no IIS 401 page)",
      "anonymousAuthentication" in iis and '"windowsAuthentication", "False"' in iis and '"basicAuthentication", "False"' in iis)
check("Every ENTER prompt goes through pause_for_enter", 'input("Press ENTER' not in src and src.count('pause_for_enter("Press ENTER') == 3)

srv.shutdown()
print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
