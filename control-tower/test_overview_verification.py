"""
The Overview's ATLAS card after a run: "written and verified" only when every
written shipment's Hub read-back confirmed it — never from the success counter.

Each run is built through the bridge exactly as a run builds it (view_updated
with the read-back verdict update_one_view recorded, shipment_finished,
counters, run_finished), served by the real dashboard server, and read off
the real page in a real browser: what atlasRun() says and what the Overview
shows.

    python test_overview_verification.py
"""

import os
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_INTEL_DIR", tempfile.mkdtemp(prefix="ct_ovv_intel_"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

from dashboard import server as tower_server       # noqa: E402
from dashboard.bridge import bridge as B           # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(playwright):
    last = None
    options = [{"headless": True}]
    if Path("/opt/pw-browsers").is_dir():
        options = [{"headless": True, "executable_path": str(b)} for b in sorted(
            Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))] + options
    options += [{"headless": True, "channel": "msedge"}, {"headless": True, "channel": "chrome"}]
    for option in options:
        try:
            return playwright.chromium.launch(**option), None
        except Exception as error:
            last = str(error).split("\n")[0][:100]
    return None, last


def run(run_id, shipments, counters):
    """One finished run: (reference, {view field: read-back verdict}, result) each."""
    B.__init__()
    B.run_started(run_id=run_id, dry_run=False, target_status="Under Clearance",
                  max_records=10, max_pages=1)
    for ref, verdicts, result in shipments:
        B.shipment_started({"bol_awb": ref, "carrier": "QATAR AIRWAYS", "provider": "QATAR",
                            "table_page": 1})
        B.provider_result({"provider": "QATAR", "tracking_status": "Arrived",
                           "eta": "18/10/2026", "ata": "19/10/2026"})
        for view_field, verdict in verdicts.items():
            view, field = view_field.split()
            B.view_updated(view, field, "19/10/2026", verified=verdict)
        B.shipment_finished(ref, result, "" if result == "SUCCESS" else
                            "the Hub read back a different date than the one written",
                            {"coe": "COE ETA updated with 18/10/2026 and saved"})
    B.counters(*counters)
    B.run_finished()


def card(page):
    page.goto(BASE)
    page.wait_for_function("() => typeof S !== 'undefined' && S && S.run && "
                           "S.run.status === 'finished'", timeout=15000)
    page.wait_for_timeout(400)
    ax = page.evaluate("() => atlasRun()")
    return ax, page.inner_text("body")


PORT = free_port()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1")
BASE = "http://127.0.0.1:%d/" % PORT
for _ in range(50):
    try:
        urllib.request.urlopen(BASE + "api/state", timeout=1)
        break
    except Exception:
        time.sleep(0.1)

try:
    from playwright.sync_api import sync_playwright
except Exception as error:                              # pragma: no cover
    print("Playwright is not available: {0}".format(error))
    print("0 passed, 1 failed")
    sys.exit(1)

pw = sync_playwright().start()
browser, why = launch(pw)
if browser is None:
    print("  FAIL  a real browser launched  ({0})".format(why))
    print("0 passed, 1 failed")
    sys.exit(1)
context = browser.new_context(viewport={"width": 1440, "height": 900})
context.add_init_script("sessionStorage.setItem('ct-intro','1');")
page = context.new_page()

print("=" * 72)
print("1. EVERY WRITE READ BACK AND MATCHED")
print("=" * 72)
run("20261019-100000-aaaaaa",
    [("1570000001", {"COE ETA": True, "BU ATA": True}, "SUCCESS"),
     ("1570000002", {"COE ETA": True}, "SUCCESS")], (2, 0, 0))
ax, body = card(page)
check("All confirmed: the card says written and verified",
      ax["message"] == "Run complete. 2 Hub records written and verified."
      and ax["state"] == "success", ax)
check("...and that is what the Overview shows", ax["message"] in body)

print()
print("=" * 72)
print("2. A READ-BACK MISSING")
print("=" * 72)
run("20261019-100000-bbbbbb",
    [("1570000003", {"COE ETA": True}, "SUCCESS"),
     ("1570000004", {"COE ETA": None}, "SUCCESS")], (2, 0, 0))
ax, body = card(page)
check("One of two not read back: verified is not claimed",
      "verified" not in ax["message"] and ax["state"] != "success", ax)
check("...the card says how many were confirmed",
      ax["message"] == "Run complete: 2 written, 0 failed. Hub read-back confirmed for 1 of 2.", ax)
check("...and that is what the Overview shows", ax["message"] in body)

run("20261019-100000-cccccc",
    [("1570000005", {}, "SUCCESS")], (1, 0, 0))
ax, body = card(page)
check("No read-back recorded at all (a replayed results file): verified is not claimed",
      ax["message"] == "Run complete: 1 written, 0 failed. Hub read-back not confirmed."
      and ax["state"] != "success", ax)

print()
print("=" * 72)
print("3. A READ-BACK THAT FAILED")
print("=" * 72)
run("20261019-100000-dddddd",
    [("1570000006", {"COE ETA": True}, "SUCCESS"),
     ("1570000007", {"COE ETA": False}, "FAILED")], (1, 1, 0))
ax, body = card(page)
check("Read back different: verified is not claimed, and the mismatch is said",
      ax["message"] == "Run complete: 1 written, 1 failed. 1 read back different from what "
                       "was written." and ax["state"] == "analyzing", ax)
check("...and that is what the Overview shows", ax["message"] in body)

run("20261019-100000-eeeeee",
    [("1570000008", {"COE ETA": True, "BU ATA": False}, "SUCCESS")], (1, 0, 0))
ax, body = card(page)
check("A mismatch on a shipment counted successful still blocks the verified claim",
      "verified" not in ax["message"] and "read back different" in ax["message"], ax)

context.close()
browser.close()
pw.stop()

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
