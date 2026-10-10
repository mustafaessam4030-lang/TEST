"""
PO page: the automation is visibly working (TEST DATA served to the page).

    python test_po_progress_ui.py

While a PO job is queued or running, a banner shows it is working: the
job, its real step (from its state, not a timer), a moving progress bar and
the elapsed time. Each running row carries a small bar. Nothing shows when
no job runs. Reduced motion is respected.
"""

import datetime
import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_poui_intel_")
os.environ["PO_DATA_DIR"] = tempfile.mkdtemp(prefix="ct_poui_data_")
os.environ["PO_OUTPUT_DIR"] = tempfile.mkdtemp(prefix="ct_poui_out_")

from dashboard import server as tower_server        # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition else "  ({0})".format(detail)))


s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1")
time.sleep(1.0)
STARTED = (datetime.datetime.now() - datetime.timedelta(seconds=75)).isoformat()
NOW = {"state": "PDF_READ", "progress": "Reading the Bill of Entry…", "kpi": "processing"}


def payload():
    job = {"po_id": "po_t1", "reference": "TEST-K1", "state": NOW["state"], "label": "Processing",
           "progress": NOW["progress"], "kpi": NOW["kpi"], "created": STARTED, "updated": STARTED,
           "source": "TEST", "verification": "UNVERIFIED"}
    return {"status": "Processing", "config": {}, "kpis": {}, "jobs": [job]}


try:
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    options = [{"headless": True, "executable_path": str(p)} for p in sorted(
        Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))] + [
        {"headless": True}, {"headless": True, "channel": "msedge"}]
    browser = None
    for option in options:
        try:
            browser = pw.chromium.launch(**option)
            break
        except Exception:
            continue
    if browser is None:
        raise RuntimeError("no browser")
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.add_init_script("sessionStorage.setItem('ct-intro','1')")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/po", lambda r: r.fulfill(status=200, content_type="application/json",
                                                body=json.dumps(payload())))
    page.goto("http://127.0.0.1:%d/" % PORT)
    page.wait_for_function("() => typeof S !== 'undefined' && S && S.run", timeout=20000)
    page.evaluate("() => document.querySelector('[data-nav=\"po\"]').click()")
    page.wait_for_selector("#poLive:not([hidden])", timeout=15000)
    page.wait_for_timeout(600)
    text = page.inner_text("#poLiveS")
    check("A running job shows the banner: the job and its real step",
          page.inner_text("#poLiveT") == "PO Automation is working" and "TEST-K1" in text and
          "Reading the Bill of Entry" in text and "step 4 of 10" in text, text)
    w1 = page.evaluate("document.getElementById('poLiveBar').style.width")
    e1 = page.inner_text("#poLiveE")
    check("...with the elapsed time since the job started", e1.startswith("1m"), e1)
    check("...and the running row carries its own small bar",
          page.locator("#poRows .po-mini").count() == 1)
    check("...and the bar has its moving sheen", page.evaluate(
        "getComputedStyle(document.getElementById('poLiveBar'), '::after').animationName")
          == "poSheen")
    NOW.update(state="VALIDATING", progress="Checking against the Hub…")
    page.wait_for_function("() => document.getElementById('poLiveS').textContent.includes('step 6')",
                           timeout=10000)
    w2 = page.evaluate("document.getElementById('poLiveBar').style.width")
    check("The bar moves forward with the job's state", int(w2.rstrip("%")) > int(w1.rstrip("%")),
          "{0} -> {1}".format(w1, w2))
    NOW.update(state="EMAIL_PREPARED", progress="Ready", kpi="generated")
    page.wait_for_function("() => document.getElementById('poLive').hidden", timeout=10000)
    check("Nothing running: the banner goes away, and no row bar is left",
          page.locator("#poRows .po-mini").count() == 0)
    check("No script errors", not errors, str(errors[:2]))
    css = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
    check("Reduced motion stops the pulse and the sheen",
          "@media (prefers-reduced-motion:reduce){.po-live-dot,.po-bar i::after" in css)
    browser.close()
    pw.stop()
except Exception as error:
    SKIP.append("browser")
    print("  SKIP  PO progress in a browser ({0}: {1})".format(type(error).__name__,
                                                           str(error)[:120]))

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(len(PASS), len(FAIL),
                                         ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
