"""
DHL result tabs covered by something on the page (14 Oct, DHL Express
3242667190: "Locator.click: Timeout 4000ms exceeded" on Shipment Details and
Shipment Timeline, so no event row was read). Real Chromium, a local page,
no DHL site.
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


LOG = []
A.write_log = lambda m, *a, **k: LOG.append(str(m))
A.note_suppressed = lambda what, error: LOG.append("suppressed " + what)

PAGE = """<html><body>
<div role="tablist">
  <button role="tab" onclick="show('d')">Shipment Details</button>
  <button role="tab" onclick="show('t')">Shipment Timeline</button>
</div>
<div id="out">Estimated Delivery October 19, 2026</div>
<div id="cover" style="position:fixed;inset:0;background:rgba(0,0,0,.3);z-index:9"></div>
<script>
  function show(k) { document.getElementById('out').innerText += k == 'd'
      ? '\\nDETAILS Destination DAR ES SALAAM - TANZANIA'
      : '\\nTIMELINE Arrived at DHL Delivery Facility DAR ES SALAAM - TANZANIA October 13, 2026'; }
  %s
</script></body></html>"""
ESCAPE_CLOSES = ("document.addEventListener('keydown', e => { if (e.key === 'Escape') "
                 "document.getElementById('cover').remove(); });")

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    page = browser.new_page()

    page.set_content(PAGE % ESCAPE_CLOSES)
    text = A.read_dhl_tabs(page)
    check("Tabs under an overlay that Escape closes: both tabs read",
          "DETAILS Destination" in text and "TIMELINE Arrived" in text, text[-300:])
    check("...and the log says it took a retry",
          any("opened on retry (Escape, scroll and click)" in x for x in LOG), LOG[-4:])

    del LOG[:]
    page.set_content(PAGE % "")
    text = A.read_dhl_tabs(page)
    check("An overlay that does not close: the direct click still opens the tabs",
          "TIMELINE Arrived" in text and any("direct click" in x for x in LOG), LOG[-4:])

    del LOG[:]
    page.set_content((PAGE % "").replace('<div id="cover"', '<div id="cover" hidden'))
    text = A.read_dhl_tabs(page)
    check("Nothing in the way: the first click works, no retry logged",
          "TIMELINE Arrived" in text and not any("retry" in x for x in LOG), LOG)
    browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
