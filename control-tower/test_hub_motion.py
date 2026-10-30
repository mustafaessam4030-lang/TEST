"""
The hub's motion: every action has its own movement (the menu's selection glides,
a list grows out of the tile clicked, the theme spreads from its button, numbers
count, a thumb slides under Globe | Map and the theme). Presentation only: the
numbers end exactly as the server sent them, nothing is fetched or changed, and
with "reduce motion" nothing moves.

    python test_hub_motion.py
"""
import os
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="hub_motion_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


from dashboard import clearance as C, server as S  # noqa: E402


def row(bu, step, awb, overdue=None, ontime=1):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta="2026-10-01", overdue=overdue, ontime=ontime, awb=awb, mode="Air", status="x", due="2026-10-01")
    return r


ROWS = [row("EG", "Customs Declaration", "EG-%04d" % i, 10.0, 0) for i in range(1234)] + \
       [row("GH", "Submit to BU", "GH-%04d" % i, 5.0, 0) for i in range(3)] + [row("KE", "Completed", "KE-0001")]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA
C.escalations = lambda: []

SRC = (HERE / "dashboard" / "static" / "hub" / "motion.js").read_text(encoding="utf-8")
CSS = (HERE / "dashboard" / "static" / "hub" / "hub.css").read_text(encoding="utf-8")
HUB = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")

print("\n1. PRESENTATION ONLY")
check("Its own file, loaded once after the views", HUB.count('<script src="/static/hub/motion.js" defer></script>') == 1
      and HUB.index("hub/views.js") < HUB.index("hub/motion.js"))
check("It fetches nothing, stores nothing, changes no address", "fetch(" not in SRC and "localStorage" not in SRC
      and "location.hash =" not in SRC and "location.href" not in SRC and "XMLHttpRequest" not in SRC)
check("It never stops a click from doing what it does", "preventDefault" not in SRC and "stopPropagation" not in SRC)
check("Reduce motion: every movement asks first", SRC.count("calm()") >= 6 and "prefers-reduced-motion: reduce" in SRC
      and "@media (prefers-reduced-motion:reduce)" in CSS)
check("The theme still changes without it (the button works if motion.js is missing)",
      "if (window.HubMotion) window.HubMotion.theme(b, go); else go();" in HUB)
check("The legend's rule is a tooltip, not a line of text", 'class="legend" title="Pin number = open past due"' in HUB)

print("\n2. IN THE BROWSER")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
else:
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        PORT = so.getsockname()[1]
    S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
    BASE = "http://127.0.0.1:{0}".format(PORT)
    time.sleep(0.4)
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()

        def page(motion="no-preference", theme="light"):
            pg = br.new_page(viewport={"width": 1440, "height": 900}, reduced_motion=motion)
            pg._errs = []
            pg.on("console", lambda m: m.type == "error" and pg._errs.append(m.text))
            pg.on("pageerror", lambda e: pg._errs.append(str(e)))
            pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','%s')" % theme)
            return pg

        pg = page()
        pg.goto(BASE + "/hub"); pg.wait_for_timeout(600)
        mid = pg.locator('#strip .chip[data-list="all"] .v').inner_text()
        check("First load: the tiles and cards arrive one after the other", pg.evaluate("document.getAnimations().length") >= 6)
        pg.wait_for_timeout(2400)
        end = pg.locator('#strip .chip[data-list="all"] .v').inner_text()
        check("Numbers count up, and end exactly as the server sent them", end == "1,238" and pg.locator("#exL em").first.inner_text() == "1,237", (mid, end))
        # the menu
        pg.locator('#nav a[data-mod="tracking"]').click(); pg.wait_for_timeout(120)
        check("Menu: the yellow selection glides to the item chosen", pg.evaluate("!!document.querySelector('#side .nav-ink')"))
        check("...and the page rises into place (down the menu)", pg.evaluate("getComputedStyle(document.getElementById('content')).getPropertyValue('--nav-dy').trim()") == "18px")
        pg.wait_for_timeout(900)
        check("...then the item is selected as before, and the glide is gone",
              pg.get_attribute('#nav a[data-mod="tracking"]', "aria-current") == "page" and not pg.evaluate("!!document.querySelector('.nav-ink')")
              and "ink-wait" not in (pg.get_attribute('#nav a[data-mod="tracking"]', "class") or ""))
        pg.locator('#nav a[data-mod="overview"]').click(); pg.wait_for_timeout(80)
        check("Up the menu the page drops into place", pg.evaluate("getComputedStyle(document.getElementById('content')).getPropertyValue('--nav-dy').trim()") == "-18px")
        pg.wait_for_timeout(2200)
        # the sheet grows out of its tile
        pg.locator('#strip .chip[data-list="customs"]').click(); pg.wait_for_timeout(120)
        clip = pg.evaluate("getComputedStyle(document.querySelector('.dl-b')).clipPath")
        check("A tile: its list grows out of it", clip.startswith("inset(") and clip != "inset(0px)", clip)
        pg.wait_for_timeout(1200)
        check("...and is the same list as before", pg.locator("#dlT").inner_text() == "At customs" and pg.locator("#dlRows tr").count() == 60)
        pg.locator("#dlCsv").click(); pg.wait_for_timeout(300)
        check("Download CSV: a check is drawn on the button, its words unchanged",
              pg.evaluate("!!document.querySelector('#dlCsv .mo-ok')") and pg.locator("#dlCsv").inner_text().strip() == "Download CSV")
        pg.wait_for_timeout(1600)
        check("...and it goes", not pg.evaluate("!!document.querySelector('#dlCsv .mo-ok')"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(600)
        check("Closing: the sheet shrinks back and the focus returns to the tile", pg.evaluate("document.getElementById('dl').hidden")
              and pg.evaluate("document.activeElement.dataset.list") == "customs")
        # Globe | Map
        x0 = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('#mapSeg .seg-thumb')).left)")
        # watch the thumb through its slide (the map's first WebGL load can hold the first frames back)
        pg.evaluate("""(() => { const t = document.querySelector('#mapSeg .seg-thumb'); window.__xs = [];
            const tick = () => { window.__xs.push(parseFloat(getComputedStyle(t).left)); if (window.__xs.length < 400) requestAnimationFrame(tick); };
            requestAnimationFrame(tick); })()""")
        pg.locator('#mapSeg button[data-mv="map"]').click(); pg.wait_for_timeout(100)
        bx = pg.evaluate("document.querySelector('#mapSeg button[data-mv=map]').offsetLeft")
        for _ in range(40):          # the map's first WebGL load can slow the frames: wait for it to settle
            pg.wait_for_timeout(100)
            x2 = pg.evaluate("parseFloat(getComputedStyle(document.querySelector('#mapSeg .seg-thumb')).left)")
            if abs(x2 - bx) < 1: break
        xs = pg.evaluate("window.__xs")
        between = [x for x in xs if x0 + 1 < x < bx - 1]
        check("Globe | Map: a thumb slides under the choice (seen on its way, then at rest under it)",
              between and abs(x2 - bx) < 1, (x0, between[:5], x2, bx))
        pg.locator('#mapSeg button[data-mv="globe"]').click(); pg.wait_for_timeout(1500)
        # theme
        pg.locator('#theme button[data-t="dark"]').click(); pg.wait_for_timeout(200)
        check("Theme: it spreads from the button (a view transition), and the theme is set", pg.evaluate("document.documentElement.classList.contains('mo-theme')")
              and pg.evaluate("document.documentElement.dataset.theme") == "dark")
        pg.wait_for_timeout(900)
        check("...the theme's thumb has moved to it", abs(pg.evaluate("parseFloat(getComputedStyle(document.querySelector('#theme .seg-thumb')).left)")
              - pg.evaluate("document.querySelector('#theme button[data-t=dark]').offsetLeft")) < 1)
        pg.locator('#theme button[data-t="light"]').click(); pg.wait_for_timeout(900)
        # Ask ATLAS
        pg.locator("#askTop").click(); pg.wait_for_timeout(60)
        check("Ask ATLAS: the chat opens in a circle from the button", "circle(" in pg.evaluate("getComputedStyle(document.getElementById('chatF')).clipPath"))
        pg.wait_for_timeout(1500)
        check("No console error through all of it", not pg._errs, pg._errs)
        pg.close()

        pg = page(motion="reduce")
        pg.goto(BASE + "/hub"); pg.wait_for_timeout(2500)
        pg.locator('#nav a[data-mod="tracking"]').click(); pg.wait_for_timeout(60)
        check("Reduce motion: no glide", not pg.evaluate("!!document.querySelector('.nav-ink')"))
        pg.locator('#nav a[data-mod="overview"]').click(); pg.wait_for_timeout(1500)
        pg.locator('#strip .chip[data-list="customs"]').click(); pg.wait_for_timeout(60)
        check("...the list just appears", pg.evaluate("getComputedStyle(document.querySelector('.dl-b')).clipPath") == "none")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        pg.locator('#theme button[data-t="dark"]').click(); pg.wait_for_timeout(30)
        check("...the theme just changes", pg.evaluate("document.documentElement.dataset.theme") == "dark"
              and not pg.evaluate("document.documentElement.classList.contains('mo-theme')"))
        check("...and no console error", not pg._errs, pg._errs)
        pg.close()
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
