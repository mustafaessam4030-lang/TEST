"""
The Overview's globe: the same pins and routes as the map (nothing invented),
added beside the map (Globe | Map, remembered), readable labels that never
sit on each other, keyboard and screen-reader reachable, light and dark,
still under reduced motion, and no three.js needed.

    python test_hub_globe.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="hub_globe_")
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


ROWS = [row("EG", "Submit to BU", "EG-%04d" % i, 10.0, 0) for i in range(7)] + \
       [row("GH", "Submit to BU", "GH-%04d" % i, 5.0, 0) for i in range(3)] + \
       [row("NG", "Customs Declaration", "NG-0001", 2.0, 0), row("KE", "Completed", "KE-0001"),
        row("JR", "Submit to BU", "JR-0001", 1.0, 0)]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA
C.escalations = lambda: []

SRC = (HERE / "dashboard" / "static" / "hub" / "globe.js").read_text(encoding="utf-8")
HUB = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")

print("\n1. ADDED BESIDE THE MAP, FROM THE SAME DATA")
check("The globe is its own file, loaded beside the map's (the map is unchanged)",
      '<script src="/static/hub/globe.js" defer></script>' in HUB and '<script src="/static/hub/map3d_gl.js" defer></script>' in HUB)
check("It draws the map's own pins and routes (hub.html mapData), nothing else",
      "globe.setData(mapData(d))" in HUB and "map.setData(mapData(d))" in HUB)
check("Nothing invented: no random positions, no fetch but the world outline",
      "Math.random" not in SRC and SRC.count("fetch(") == 1 and "/static/maps/world.json" in SRC)
check("No library: Canvas 2D, no three.js import", "import(" not in SRC and "THREE" not in SRC and 'getContext("2d")' in SRC)
check("Its colours are the hub's tokens (light and dark follow the page)", "--hub-bg" in SRC and "--hub-red" in SRC and "--hub-blue" in SRC)

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

        def page(theme="light", extra="", motion="no-preference", w=1400, h=950):
            pg = br.new_page(viewport={"width": w, "height": h}, reduced_motion=motion)
            pg._errs = []
            pg.on("console", lambda m: m.type == "error" and pg._errs.append(m.text))
            pg.on("pageerror", lambda e: pg._errs.append(str(e)))
            pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','%s');%s" % (theme, extra))
            pg.goto(BASE + "/hub"); pg.wait_for_timeout(3000)
            return pg

        pg = page()
        check("The Overview opens on the globe; Map is one click away",
              pg.get_attribute("#globe", "data-map-mode") == "globe" and pg.evaluate("document.getElementById('map').hidden")
              and pg.get_attribute('#mapSeg button[data-mv="globe"]', "aria-pressed") == "true")
        dots = int(pg.get_attribute(".gb", "data-dots") or 0)
        check("Land is dots from the world outline (thousands of them)", dots > 3000, dots)
        labels = pg.eval_on_selector_all(".gb-pin", "els => els.map(e => e.textContent)")
        check("A pin for each BU with a known country, its number = open past due (EG 7, GH 3, NG 1)",
              {"EG7", "GH3", "NG1"} <= set(labels) and not any(l.startswith("JR") for l in labels), labels)
        rects = pg.eval_on_selector_all(".gb-pin:not(.tight)", """els => els.filter(e => getComputedStyle(e).visibility === 'visible')
            .map(e => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; })""")
        overlap = [(a, b) for i, a in enumerate(rects) for b in rects[i + 1:] if a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]]
        check("No label sits on another", rects and not overlap, overlap)
        card = pg.locator(".gb-card").inner_text()
        check("The glass card lists past due by business unit, from the same numbers", "Past due by business unit" in card
              and "open shipments past" not in card
              and "11 open shipments past" in (pg.get_attribute(".gb-card h3", "title") or ""), card)
        three = pg.evaluate("performance.getEntriesByType('resource').some(e => e.name.includes('three.module'))")
        check("No three.js is loaded for the globe", not three)
        empty_card = pg.evaluate("(() => { const d = document.createElement('div'); d.style.cssText = 'position:relative;width:400px;height:300px'; document.body.appendChild(d); const g = HubGlobe.mount(d, {}); const h = d.querySelector('.gb-card').hidden; g.destroy(); d.remove(); return h; })()")
        check("A globe with nothing to show has no empty card", empty_card)
        a = pg.evaluate("document.querySelector('.gb canvas').toDataURL()")
        pg.wait_for_timeout(700)
        check("It turns slowly by itself", pg.evaluate("document.querySelector('.gb canvas').toDataURL()") != a)
        # select from the card: zooms in, shows the BU, its actions open the real list
        pg.locator('.gb-row[data-id="bu:EG"]').click(); pg.wait_for_timeout(1500)
        sheet = pg.locator(".gb-card").inner_text()
        check("Choosing a BU shows its own numbers (open, past due) and marks its pin", "EG" in sheet and "PAST DUE" in sheet
              and pg.get_attribute('.gb-pin[aria-pressed="true"]', "aria-label").startswith("EG"), sheet)
        check("...and the focus is on its first action", pg.evaluate("document.activeElement.textContent") == "Open shipments")
        pg.locator(".gb-acts button", has_text="Past due").click(); pg.wait_for_timeout(1200)
        check("'Past due' opens the real list for that BU", not pg.evaluate("document.getElementById('dl').hidden")
              and pg.locator("#dlT").inner_text() == "EG · Open and past due", pg.locator("#dlT").inner_text())
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        pg.locator(".gb-x").click(); pg.wait_for_timeout(400)
        check("Closing the card goes back to the list and the focus to the pin",
              "Past due by business unit" in pg.locator(".gb-card").inner_text()
              and (pg.evaluate("document.activeElement.className") or "").startswith("gb-pin"))
        pg.keyboard.press("Enter"); pg.wait_for_timeout(800)
        check("A pin is a real button: Enter opens it", "PAST DUE" in pg.locator(".gb-card").inner_text())
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        check("Esc closes it", "Past due by business unit" in pg.locator(".gb-card").inner_text())
        # Map: one click, remembered
        pg.locator('#mapSeg button[data-mv="map"]').click(); pg.wait_for_timeout(2500)
        check("Map shows the existing map (unchanged) and hides the globe",
              pg.evaluate("document.getElementById('globe').hidden") and pg.get_attribute("#map", "data-map-mode") in ("2d", "3d", "loading"))
        check("...and the choice is remembered", pg.evaluate("localStorage.getItem('hub-map-view')") == "map")
        check("No console error with the globe or after switching", not pg._errs, pg._errs)
        pg.close()

        pg = page(extra="localStorage.setItem('hub-map-view','map');")
        check("Next visit opens on the Map when that was chosen", pg.evaluate("document.getElementById('globe').hidden"))
        pg.close()

        pg = page(theme="dark")
        bg = pg.evaluate("(() => { const c = document.querySelector('.gb canvas'), g = c.getContext('2d'); return Array.from(g.getImageData(4, 4, 1, 1).data); })()")
        check("Dark mode: a midnight globe (the page's dark tokens)", sum(bg[:3]) < 120, bg)
        pg.close()

        pg = page(motion="reduce")
        v1 = pg.evaluate("document.querySelector('.gb canvas').toDataURL()"); pg.wait_for_timeout(900)
        v2 = pg.evaluate("document.querySelector('.gb canvas').toDataURL()")
        check("Reduced motion: the globe stands still", v1 == v2)
        pg.close()

        pg = page(w=390, h=844)
        check("Phone: the globe fits, the list card steps aside until a place is chosen",
              pg.locator(".gb").bounding_box()["width"] <= 390 and not pg.locator(".gb-card").is_visible())
        pg.close()
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
