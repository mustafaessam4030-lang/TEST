"""
Grimaldi GNET result page (operator's screenshot, 16 Oct, S330394405):

    Shipment ETS/ETA
    Departure: ANTWERP 14/10/2026 04:12:00
    Arrival:   TAKORADI 31/10/2026 01:23:00

Both lines are estimates: the ETA is the Arrival line (31/10), never the
departure (14/10, which the bare word 'ETA' in the heading used to pick up),
and there is no ATA. Real Chromium, a local page, no carrier site.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_eta as A                                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


A.write_log = lambda *a, **k: None
PAGE = """<html><body><h2>GNET - Container tracking</h2>
<label>Shipment #</label><input type="text" value="S330394405">
<button>Search</button>
<div><b>Shipment ETS/ETA</b><br>
<span>Departure: ANTWERP 14/10/2026 04:12:00</span>
<span style="margin-left:300px">Arrival: TAKORADI 31/10/2026 01:23:00</span></div>
<table><tr><th>Equipment</th><th>Event</th></tr><tr><td colspan=2>No records found</td></tr></table>
<p>{pad}</p></body></html>""".format(pad="x" * 150)

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    page = browser.new_page()
    page.set_content(PAGE)
    r = A._read_ocean_page(page, "GRIMALDI")
    check("ETA is the Arrival line: 31/10/2026 at TAKORADI", r and r["eta"] == "31/10/2026"
          and "TAKORADI" in (r.get("eta_source") or ""), r)
    check("The departure date 14/10 is never the ETA", r and r["eta"] != "14/10/2026", r)
    check("No ATA: the block holds estimates only", r and r["ata"] is None, r)
    check("An empty container table ('No records found') does not hide the result",
          r and not r.get("no_result"), r)
    check("The shipment number in the search box proves it is this shipment",
          A.awb_on_page(page, "S330394405"))
    check("...and another shipment's number does not", not A.awb_on_page(page, "S330394406"))
    browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
