"""
A carrier page with two <body> elements (Lufthansa Cargo's portal nests one
inside a portlet). On 14 Oct every read of such a page failed with "strict
mode violation: locator('body') resolved to 2 elements", so the page read as
empty and the run reported "no dates on the page at all". Real Chromium, a
local page, no carrier site.
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
HTML = """<html><body id="outer"><h1>eTracking</h1><div id="portlet"></div>
<script>
  var inner = document.createElement('body');
  inner.innerText = 'AWB 020-36047734  Arrived FRA 12 Oct 2026  Delivered ACC 13 Oct 2026';
  document.getElementById('portlet').appendChild(inner);
</script></body></html>"""

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    page = browser.new_page()
    page.set_content(HTML)
    count = page.locator("body").count()
    check("The test page really has two <body> elements", count == 2, count)
    try:
        page.locator("body").inner_text(timeout=1000)
        strict = False
    except Exception as error:
        strict = "strict mode violation" in str(error)
    check("...and the old read fails on it exactly as on 14 Oct (strict mode violation)", strict)
    text = A._page_text(page)
    check("The page reader now reads it, nested body included",
          "eTracking" in text and "12 Oct 2026" in text and "13 Oct 2026" in text, text[:200])
    check("_body_text reads the outer body", "Arrived FRA" in A._body_text(page))
    page.set_content("<html><body><p>one body</p></body></html>")
    check("A normal page reads as before", A._body_text(page).strip() == "one body")
    browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
