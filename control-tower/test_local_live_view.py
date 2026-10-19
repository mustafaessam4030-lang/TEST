"""
Human verification in the operator's OWN browser, from the LOCAL dashboard
(START_TOWER.bat → dashboard.supervisor) — no platform, no sign-in.

    python test_local_live_view.py

Real: the supervisor that starts the run, the run's remote_session endpoint
and CDP pump on a real Chromium tab, update_eta's Grimaldi lookup and
wait_for_human(), the dashboard server, and the dashboard page itself in a
second real browser — the operator presses Open Session, sees the carrier
page in the dashboard, clicks the code box and types into that view.
Stand-ins (as in test_remote_session.py): the carrier site, whose security
code is drawn as an image (fixtures/carrier_stub.py), and the Hub
(fixtures/remote_runner.py).
"""

import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_live_"))
os.environ.update(ATA_RUNNER_SCRIPT=str(HERE / "fixtures" / "remote_runner.py"),
                  ATA_RUNTIME_DIR=str(WORK / "runtime"), PO_AUTO="0",
                  ATLAS_INTEL_DIR=str(WORK / "intel"), ATLAS_DATA_ORIGIN="test",
                  PO_DATA_DIR=str(WORK / "po"), E2E_WORK=str(WORK / "runner"),
                  E2E_REFS="S330348776", E2E_HOLD_S="90", E2E_GRACE_MS="60000",
                  DASHBOARD_ACCESS_KEY_FILE=str(WORK / "key"))
os.environ.pop("ATA_LOCAL_LIVE_VIEW", None)

from fixtures import carrier_stub as stub            # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(str(detail)[:400]) if detail and not ok else ""))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


def until(fn, seconds=60, every=0.25):
    end = time.time() + seconds
    while time.time() < end:
        try:
            value = fn()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(every)
    return None


try:
    from playwright.sync_api import sync_playwright
    CHROME = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
except ImportError:
    print("  SKIP  Playwright is not installed\n0 passed, 0 failed, 1 skipped")
    sys.exit(0)

_server, CARRIER = stub.start()
os.environ["E2E_CARRIER_BASE"] = CARRIER
from dashboard import server as tower_server, supervisor as SUP, live_view  # noqa: E402

KEY = "local-live-view-key-2026"
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
SUP.install()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=KEY)
BASE = "http://127.0.0.1:{0}".format(PORT)


def call(method, path, body=None):
    request = urllib.request.Request(
        BASE + path + ("&" if "?" in path else "?") + "key=" + KEY, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers or {})


def state():
    return json.loads(call("GET", "/api/state")[1])


def waiting():
    a = state().get("human_action") or {}
    return a if a.get("waiting") else None


try:
    rule("1. A RUN STARTED FROM THE LOCAL DASHBOARD GETS ITS OWN LIVE-VIEW ENDPOINT")
    status, body, _ = call("POST", "/api/control", {"action": "start"})
    check("Start (the dashboard's own button) starts the run", json.loads(body)["accepted"])
    check("...with a private loopback session port and a random token (≥ 32 chars)",
          live_view.available() and len(live_view._target["token"]) >= 32)
    action = until(waiting, 90)
    check("The run pauses at Grimaldi's security code and asks for a person", bool(action),
          str(state().get("human_action")))
    if not action:
        raise SystemExit("the run never asked for a person")
    check("The dashboard says the live view is available", state().get("live_view") is True)
    aid = action["action_id"]
    check("Without Open, no frames for anyone (409)",
          call("GET", "/api/session/{0}/frame?client=x".format(aid))[0] == 409)

    rule("2. OPEN SESSION — THE CARRIER PAGE IN THE OPERATOR'S OWN BROWSER")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME[-1]) if CHROME else None)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.add_init_script("sessionStorage.setItem('ct-intro','1');")
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + "/?key=" + KEY)
        button = '[data-hq="{0}"]'.format(aid)
        page.wait_for_selector(button, timeout=30000)
        check("The paused shipment is on the dashboard with its Open button",
              page.is_visible(button))
        page.click(button)
        page.wait_for_selector("#rv:not([hidden])", timeout=10000)
        check("Open Session opens the live view in the dashboard", page.is_visible("#rv"))
        got = page.wait_for_function(
            "() => { const i = document.querySelector('#rvImg'); "
            "return i && i.src && i.naturalWidth > 0; }", timeout=30000)
        check("...showing the run's own carrier tab (JPEG frames)", bool(got))
        other = call("GET", "/api/session/{0}/frame?client=someone-else".format(aid))
        check("Another dashboard tab gets nothing (409)", other[0] == 409)
        until(lambda: stub.LAYOUT.get("code"), 20)
        L = stub.LAYOUT
        check("The page is waiting at the code, untouched by the run",
              bool(L.get("code")) and stub.SUBMITTED == [])

        def at(pt):
            """A point on the carrier page → where it is on the screen, in the view."""
            box = page.evaluate("() => { const r = document.querySelector('#rvImg')"
                                ".getBoundingClientRect(); return {x: r.left, y: r.top, "
                                "w: r.width, h: r.height}; }")
            meta = page.evaluate("() => rv.meta")
            return (box["x"] + pt["x"] / meta["width"] * box["w"],
                    box["y"] + pt["y"] / meta["height"] * box["h"])
        page.mouse.click(*at(L["code"]))
        page.keyboard.type(stub.CODE, delay=40)
        time.sleep(1.2)
        check("Typed in the dashboard's view; the run still waits for the person",
              stub.SUBMITTED == [])
        page.mouse.click(*at(L["go"]))
        done = until(lambda: stub.SUBMITTED and stub.SUBMITTED[-1], 30)
        check("The carrier accepted the code the person typed in their own browser",
              bool(done) and done["code_ok"] is True and done["ship"] == "S330348776",
              str(stub.SUBMITTED))
        record = until(lambda: next((r for r in state().get("shipments") or []
                                     if r["reference"] == "S330348776" and
                                     r.get("state") == "updated"), None), 60)
        check("The run carried on by itself: written and read back (updated)", bool(record),
              str([(r.get("reference"), r.get("state")) for r in state().get("shipments") or []]))
        check("No script errors in the dashboard", not errors, errors[:3])
        browser.close()

    rule("3. NOTHING KEPT")
    leaks = [str(p.relative_to(WORK)) for p in WORK.rglob("*") if p.is_file() and
             stub.CODE.encode() in p.read_bytes()]
    check("The security code is in no file (run state, runtime, logs, stores)", not leaks, leaks)
    check("...and not in what the dashboard receives", stub.CODE not in json.dumps(state()))
finally:
    try:
        SUP.supervisor.stop(force=True)
    except Exception:
        pass

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
