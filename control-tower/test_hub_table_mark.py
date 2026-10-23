"""
The Hub answers a view or filter change with the SAME rows (19 Oct: the list
was already Under Clearance), so a content comparison never saw a change and
489 waits ran their full 12 s. The table element is new, though: that is now
the signal. Real Chromium, a page that stands in for the Hub's postback.

    python test_hub_table_mark.py
"""
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_eta as A                                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


A.write_log = lambda *a, **k: None
TABLE = ("<table id=t><tr><th>BOL/AWB</th><th>Carrier</th></tr>"
         "<tr><td>MEDUAHP69377</td><td>MSC</td></tr></table>")
HTML = ("<html><body><div id=panel>" + TABLE + "</div>"
        "<button id=same onclick=\"setTimeout(()=>{document.getElementById('panel').innerHTML="
        "document.getElementById('panel').innerHTML.replace(' data-ct-mark','x-old')},400)\">same</button>"
        "<button id=nothing>nothing</button>"
        "<button id=inplace onclick=\"setTimeout(()=>{document.querySelector('#t td').textContent="
        "'K358403'},400)\">inplace</button></body></html>")

from playwright.sync_api import sync_playwright              # noqa: E402
exe = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path=str(exe[-1]) if exe else None)
    page = browser.new_page()
    page.set_content(HTML)
    before = A.table_before(page)
    check("the table is marked before the click", "|ct-mark:" in before, before[-30:])
    page.click("#same")
    t = time.time()
    ok = A.wait_for_table_change(page, before, max_ms=12000, reason="test")
    took = time.time() - t
    check("the Hub answered with the same rows: seen at once, not after 12 s",
          ok and took < 3, "{0:.1f}s".format(took))

    page.set_content(HTML)
    before = A.table_before(page)
    page.click("#nothing")
    t = time.time()
    ok = A.wait_for_table_change(page, before, max_ms=1500, reason="test")
    check("no answer: still waits its full time, then falls back as before",
          not ok and time.time() - t >= 1.4)

    page.set_content(HTML)
    before = A.table_before(page)
    page.click("#inplace")
    ok = A.wait_for_table_change(page, before, max_ms=5000, reason="test")
    check("a table changed in place is still seen by its content", ok)
    browser.close()

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
