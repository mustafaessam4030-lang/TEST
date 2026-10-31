"""
The sign-in page with ATLAS: he greets, looks away from the password, reacts
to a refused sign-in, and none of it gets in the way of the form. Real
Chromium against a stand-in for the tower's sign-in API.

    python test_login_page.py
"""
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


POSTS = []


class Tower(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        r = self.path.split("?")[0]
        if r == "/api/auth/config":
            return self._send(200, b'{"local":true,"sso":false}', "application/json")
        if r == "/login":
            f = HERE / "controlplane/static/login.html"
        elif r.startswith("/static/login/"):
            f = HERE / "controlplane/static" / r[len("/static/"):]
        elif r.startswith("/static/brand/"):
            f = HERE / "dashboard/static" / r[len("/static/"):]
        else:
            return self._send(200, b"dashboard", "text/plain")
        types = {".css": "text/css", ".js": "application/javascript", ".png": "image/png"}
        self._send(200, f.read_bytes(), types.get(f.suffix, "text/html"))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        POSTS.append(body)
        if body.get("password") == "right-password-123":
            return self._send(200, b'{"ok":true}', "application/json")
        self._send(401, b'{"ok":false,"message":"That email or password is not right."}', "application/json")


html = (HERE / "controlplane/static/login.html").read_text(encoding="utf-8")
for el in ('id="email"', 'id="password"', 'id="go"', 'id="setpw"', 'id="note"', 'id="sso"'):
    check("the form keeps " + el, el in html)
check("the page still says Sign in", "Sign in" in html)
check("ATLAS is drawn inline: no request to anywhere else", 'id="axShell"' in html and "http" not in
      html.split('<button type="button" class="atlas"')[1].split("</button>")[0].replace("http://www.w3.org", ""))
css = (HERE / "controlplane/static/login/login.css").read_text(encoding="utf-8")
check("ATLAS's motion is only for people who have not asked for less",
      "@keyframes wave" in css.split("prefers-reduced-motion:no-preference")[1])

srv = ThreadingHTTPServer(("127.0.0.1", 0), Tower)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = "http://127.0.0.1:{0}/login?next=/overview".format(srv.server_port)

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    errors = []
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_function("document.getElementById('say').textContent.includes('ATLAS')", timeout=4000)
    check("ATLAS says hi", "Hi! I'm ATLAS" in page.text_content("#say"))
    check("the cursor starts in the email box", page.evaluate("document.activeElement.id") == "email")
    page.fill("#email", "someone@mantracgroup.com")
    check("ATLAS watches the email being typed",
          "translate" in page.evaluate("document.querySelector('.ax-look').style.transform"))
    page.click("#password")
    page.wait_for_timeout(700)
    check("ATLAS looks away from the password",
          "shy" in page.get_attribute("#atlas", "class") and "not looking" in page.text_content("#say"))
    page.fill("#password", "wrong")
    page.click("#go")
    page.wait_for_selector("#note:not([hidden])")
    page.wait_for_timeout(900)
    check("a refusal shows the tower's message", "not right" in page.text_content("#note"))
    check("ATLAS reacts to the refusal, not 'not looking'",
          "oops" in page.get_attribute("#atlas", "class") and "didn" in page.text_content("#say"))
    check("the password is cleared and focused again",
          page.input_value("#password") == "" and page.evaluate("document.activeElement.id") == "password")
    page.wait_for_timeout(1700)
    check("then, still in the password box, he looks away again", "shy" in page.get_attribute("#atlas", "class"))
    page.fill("#password", "right-password-123")
    page.click("#go")
    page.wait_for_url("**/overview", timeout=4000)
    check("a good sign-in goes to the page that was asked for", page.url.endswith("/overview"))
    check("the request carried only the email and password",
          POSTS[-1] == {"email": "someone@mantracgroup.com", "password": "right-password-123"}, POSTS[-1])
    page.close()

    calm = browser.new_page(reduced_motion="reduce")
    calm.on("pageerror", lambda e: errors.append(str(e)))
    calm.goto(url)
    calm.wait_for_timeout(300)
    check("reduced motion: the greeting appears at once, nothing animates",
          "Hi! I'm ATLAS" in calm.text_content("#say") and
          calm.evaluate("getComputedStyle(document.querySelector('.atlas svg')).animationName") == "none")
    calm.close()

    phone = browser.new_page(viewport={"width": 390, "height": 844})
    phone.goto(url)
    phone.wait_for_timeout(300)
    check("phone: no sideways scrolling",
          phone.evaluate("document.documentElement.scrollWidth <= innerWidth"))
    box = phone.locator("#go").bounding_box()
    check("phone: the Sign in button is on the first screen", box and box["y"] + box["height"] < 844, box)
    phone.close()
    check("no script errors", not errors, errors)
    browser.close()
srv.shutdown()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
