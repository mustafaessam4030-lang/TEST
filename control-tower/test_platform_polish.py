"""
Polish without new features: every request answered, no console error from
the platform itself, counts that match, a refresh that keeps what you were
doing, and states that say what happened. Each check here protects one fix.

    python test_platform_polish.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="polish_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


from dashboard import clearance as C, hub as H, todo as TD, server as S  # noqa: E402


def row(bu, step, awb, eta=None, overdue=None, ontime=1):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, overdue=overdue, ontime=ontime, awb=awb, mode="Air", status="x", due="2026-10-01")
    return r


ROWS = [row("EG", "Submit to BU", "E1", "2026-10-01", 12.0, 0), row("GH", "Completed", "G1", "2026-09-01")]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS]}
C.load = lambda: DATA

print("\n1. THE SERVER ANSWERS EVERY REQUEST")
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


def call(path, method="GET", body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json", "Origin": BASE})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


st, b = call("/api/auth/me")
check("Sign-in off: /api/auth/me answers 200 'local' (was a 404 in every page's console)",
      st == 200 and json.loads(b)["mode"] == "local" and json.loads(b)["user"] is None, (st, b[:80]))
_orig = H.overview
import logging  # noqa: E402
H.overview = lambda *a, **k: 1 / 0
logging.getLogger("dashboard.server").disabled = True       # the forced error is logged by design; keep the output clean
st, b = call("/api/hub/overview")
logging.getLogger("dashboard.server").disabled = False
H.overview = _orig
check("An unexpected error is answered with a 500, not a dropped connection", st == 500 and json.loads(b)["error"] == "server error", (st, b[:80]))
check("...and the server keeps serving", call("/api/hub/overview")[0] == 200)
st, _ = call("/api/feedback", "POST", raw=b"[1, 2]")
check("Feedback that is not an object is a 400, not a crash", st == 400, st)

print("\n2. COUNTS THAT MATCH")
saved = (C.escalations, C.contacts)
C.escalations = lambda: [{"awb": "E1", "bu": "EG", "status": "open", "due": "2026-09-01", "at": "2026-10-20 10:00"}]
C.contacts = lambda: {"agents": {}, "bus": {}, "forwarders": {}, "coe": {}}
items = TD._clearance_items(__import__("datetime").date(2026, 10, 30), __import__("datetime").datetime(2026, 10, 30, 9))
C.escalations, C.contacts = saved
txt = json.dumps(items)
check("To Do takes the export's own day count even when it is a decimal (12.0 -> 12 days, as on Clearance)",
      "12 day" in txt and "59 day" not in txt, txt[:300])

print("\n3. IN THE BROWSER")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
else:
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    TODO = {"items": [{"id": "a", "state": "urgent", "level": "red", "title": "Red task", "detail": "", "source": "Run", "href": "/#overview"},
                      {"id": "b", "state": "open", "level": "amber", "title": "Amber task", "detail": "", "source": "Clearance", "href": "/clearance"},
                      {"id": "c", "state": "completed", "level": "grey", "title": "Done task", "detail": "", "source": "Run", "href": "/#overview"}],
            "outstanding": 2, "count": 2, "by_state": {"urgent": 1, "open": 1, "completed": 1}}
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        pg = br.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        pg.route("**/api/todo", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(TODO)))
        pg.goto(BASE + "/hub?map=2d"); pg.wait_for_timeout(2500)
        check("Hub To Do card: the open count leaves out finished tasks", pg.locator("#tdP").inner_text() == "2 open", pg.locator("#tdP").inner_text())
        dots = pg.eval_on_selector_all("#todoL i", "els => els.map(e => e.getAttribute('style'))")
        check("...a red task has a red dot, an amber one amber, and the finished one is not listed",
              len(dots) == 2 and "hub-red" in dots[0] and "hub-amber" in dots[1], dots)
        fs = pg.evaluate("getComputedStyle(document.querySelector('.srch-b')).fontSize")
        check("Hub: the Search button keeps its own type (the token mapping was broken)", fs == "12.5px", fs)
        check("Hub: numbers keep a fixed width (tabular figures are no longer cancelled)",
              pg.evaluate("getComputedStyle(document.body).fontFeatureSettings") == "normal")
        # a refresh keeps the menu and focus
        pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
        pg.locator("#burger").click(); pg.wait_for_timeout(300)
        first = pg.evaluate("window.__a = document.querySelector('#nav a'); !!window.__a")
        pg.evaluate("window.HubCtx.refresh()"); pg.wait_for_timeout(1500)
        check("A refresh does not close the phone menu or rebuild it (links and focus kept)",
              pg.evaluate("document.getElementById('side').classList.contains('open')") and first and
              pg.evaluate("window.__a === document.querySelector('#nav a')"))
        pg.set_viewport_size({"width": 1366, "height": 768})
        pg.route("**/api/hub/list*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"server error"}'))
        pg.evaluate("location.hash = '#tracking'"); pg.wait_for_timeout(1500)
        check("Tracking: a failed load says it failed (it said 'No eHub export loaded')",
              "could not be loaded" in pg.locator("#modBody").inner_text())
        pg.unroute("**/api/hub/list*")
        check("No console error on the hub (the /api/auth/me 404 is gone)", not [e for e in errors if "500" not in e], errors)
        cl = br.new_page(viewport={"width": 1366, "height": 768})
        cerr = []
        cl.on("console", lambda m: m.type == "error" and cerr.append(m.text))
        cl.goto(BASE + "/clearance"); cl.wait_for_timeout(2500)
        check("Clearance: the closed dialog is hidden", cl.evaluate("document.getElementById('modal').hidden"))
        cl.keyboard.press("/"); cl.wait_for_timeout(500)
        check("Clearance: '/' opens the search (it never did)", cl.locator(".srch-p").is_visible() or cl.locator("#srchPanel").is_visible())
        check("No console error on Clearance", not cerr, cerr)
        idx = br.new_page(viewport={"width": 1366, "height": 768})
        ierr = []
        idx.on("console", lambda m: m.type == "error" and ierr.append(m.text))
        idx.add_init_script("sessionStorage.setItem('ct-intro','1')")
        idx.goto(BASE + "/"); idx.wait_for_timeout(2500)
        check("No console error on the run page", not ierr, ierr)
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
