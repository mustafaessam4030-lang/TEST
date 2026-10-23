"""
Start dry run, from the local tower's dashboard (START_TOWER.bat, or the
server's scheduled task, where no console is there to set CT_DRY_RUN).

The supervisor launches a stand-in runner that records the environment it was
started with; the button is pressed in a real browser on the real dashboard.
The PO automatic run is replaced by a recorder, so nothing real is started.

    python test_dry_run_start.py
"""

import json
import os
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_dry_"))
RUNNER = WORK / "runner.py"
SEEN = WORK / "env.json"
RUNNER.write_text(
    "import json, os, time\n"
    "open({0!r}, 'w').write(json.dumps({{'CT_DRY_RUN': os.environ.get('CT_DRY_RUN')}}))\n"
    "time.sleep(4)\n".format(str(SEEN)), encoding="utf-8")
os.environ.update(ATA_RUNNER_SCRIPT=str(RUNNER), ATA_RUNTIME_DIR=str(WORK / "runtime"),
                  PO_AUTO="1", ATLAS_INTEL_DIR=str(WORK / "intel"), ATLAS_DATA_ORIGIN="test",
                  DASHBOARD_ACCESS_KEY_FILE=str(WORK / "key"))
os.environ.pop("CT_DRY_RUN", None)

from dashboard import server as tower_server, supervisor as SUP  # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name,
                                 "  ({0})".format(str(detail)[:300]) if detail and not ok else ""))


def until(fn, seconds=20, every=0.2):
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


def seen():
    return json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else None


SWEEPS = []
SUP.supervisor.start_po_sweep = lambda *a, **k: (SWEEPS.append(1) or (True, "recorded"))
SUP.install()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    PORT = s.getsockname()[1]
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1")
BASE = "http://127.0.0.1:%d/" % PORT
until(lambda: urllib.request.urlopen(BASE + "api/state", timeout=1), 10)

try:
    from playwright.sync_api import sync_playwright
except Exception as error:                              # pragma: no cover
    print("Playwright is not available: {0}\n0 passed, 1 failed".format(error))
    sys.exit(1)
pw = sync_playwright().start()
browser = None
for option in ([{"headless": True, "executable_path": str(b)} for b in
                sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))]
               + [{"headless": True}, {"headless": True, "channel": "msedge"}]):
    try:
        browser = pw.chromium.launch(**option)
        break
    except Exception:
        continue
if browser is None:
    print("  FAIL  a real browser launched\n0 passed, 1 failed")
    sys.exit(1)
page = browser.new_page(viewport={"width": 1440, "height": 900})
page.add_init_script("sessionStorage.setItem('ct-intro','1');")
page.goto(BASE)

print("=" * 72)
print("1. THE DASHBOARD OFFERS A DRY RUN WHEN NOTHING IS RUNNING")
print("=" * 72)
check("Start dry run is shown beside Start run",
      until(lambda: page.is_visible("#btnDry") and page.is_visible("#btnStart")),
      page.inner_text("#ctlbar") if page.is_visible("#ctlbar") else "control bar hidden")

print()
print("=" * 72)
print("2. PRESSING IT STARTS A RUN THAT SAVES NOTHING")
print("=" * 72)
page.click("#btnDry")
env = until(seen)
check("The run was started with CT_DRY_RUN=1", (env or {}).get("CT_DRY_RUN") == "1", env)
check("...and the PO automatic run was not started beside it", SWEEPS == [], SWEEPS)
check("...and while it runs, neither start button is offered",
      until(lambda: not page.is_visible("#btnDry")) and page.is_disabled("#btnStart"))
until(lambda: not SUP.supervisor.is_running(), 20)

print()
print("=" * 72)
print("3. START RUN IS UNCHANGED: A LIVE RUN, WITH THE PO RUN BESIDE IT")
print("=" * 72)
SEEN.unlink()
accepted, message = SUP.supervisor.request("start")
env = until(seen)
check("Start run is accepted", accepted, message)
check("...and the run is NOT a dry run", env is not None and env.get("CT_DRY_RUN") is None, env)
check("...and the PO automatic run starts beside it, as before", SWEEPS == [1], SWEEPS)
until(lambda: not SUP.supervisor.is_running(), 20)

browser.close()
pw.stop()
print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
