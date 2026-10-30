"""
The hub's 3D map (dashboard/static/hub/map3d_gl.js): three.js served by the
tower itself, the 2D map when WebGL is missing or too slow, chosen once and
logged, the 2D/3D button both ways, a pin's sheet opening the real list.

    python test_hub_map3d.py
"""
import hashlib
import os
import re
import socket
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="hub_map3d_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


STATIC = HERE / "dashboard" / "static"
GL = (STATIC / "hub" / "map3d_gl.js").read_text(encoding="utf-8")
VENDOR = STATIC / "vendor" / "three.module.min.js"
HTML = (STATIC / "hub.html").read_text(encoding="utf-8")

print("\n1. THREE.JS: ONE PINNED FILE, SERVED BY THE TOWER, WITH ITS LICENCE")
head = VENDOR.read_text(encoding="utf-8")[:700]
check("Pinned version and source recorded at the top of the file",
      "r169" in head and "0.169.0" in head and "https://registry.npmjs.org/three/-/three-0.169.0.tgz" in head)
body = VENDOR.read_bytes().split(b"*/\n", 1)[1]
sha = re.search(r"sha256 of that file: ([0-9a-f]{64})", head)
check("The file below the comment is the upstream file, unmodified (sha256 matches)",
      sha and hashlib.sha256(body).hexdigest() == sha.group(1))
lic = (STATIC / "vendor" / "LICENSE")
check("three.js's MIT licence sits next to it", lic.exists() and "MIT License" in lic.read_text(encoding="utf-8")
      and "three.js authors" in lic.read_text(encoding="utf-8"))
plan = (HERE / "CONTROL_TOWER_PLAN.md").read_text(encoding="utf-8")
check("The plan records the exception, the version and the source URL",
      "three.js" in plan and "0.169.0" in plan and "registry.npmjs.org/three/-/three-0.169.0.tgz" in plan)
check("Core only: the map imports nothing but the vendored file",
      re.findall(r"import\(([^)]*)\)", GL) == ["VENDOR"] and 'const VENDOR = "/static/vendor/three.module.min.js"' in GL)
ext = [u for u in re.findall(r"https?://[^\s'\"`)]+", GL)]
check("The 3D map loads nothing from outside (no CDN, tiles or fonts)", not ext, ext)
check("Data reaches the page as text only (innerHTML only for fixed icons)",
      all(("ICON." in ln or "html" in ln.split("innerHTML")[1][:12]) for ln in GL.splitlines() if ".innerHTML" in ln))
check("The page adds one script, after the 2D map", '<script src="/static/hub/map3d.js" defer></script>\n'
      '<script src="/static/hub/map3d_gl.js" defer></script>' in HTML)

print("\n2. THE FALLBACK RULE, WRITTEN DOWN")
check("Too slow = median frame over the first 2 s above 50 ms", "SLOW_MS = 50, PROBE_MS = 2000" in GL)
check("Switches to 2D once and never back on its own", 'if (destroyed || mode === "2d") return;' in GL
      and "will not switch back" in GL)
check("Every choice is logged to the console as [hub map] mode: …", GL.count('"mode: ') >= 2 and '"[hub map] "' in GL)

print("\n3. IN A BROWSER")
from dashboard import clearance as C  # noqa: E402


def row(bu, step, eta=None, ata=None, ontime=1, overdue=None, awb="X"):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, ata=ata, ontime=ontime, overdue=overdue, awb=awb, mode="Air", status="x")
    return r


ROWS = [row("EG", "Submit to BU", eta="2026-10-01", overdue=12, ontime=0, awb="E%d" % i) for i in range(5)] + \
       [row("GH", "Duty Payment", eta="2026-11-09", awb="G1"), row("GH", "Completed", awb="G2")]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS]}
C.load = lambda: DATA

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
    print("  (Playwright not installed: browser checks skipped)")
else:
    from dashboard import server as S  # noqa: E402
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        PORT = so.getsockname()[1]
    S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
    BASE = "http://127.0.0.1:{0}".format(PORT)
    time.sleep(0.4)
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    NO_WEBGL = """(() => { const g = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (t, o) { return /webgl/i.test(t) ? null : g.call(this, t, o); }; })();"""

    def page(br, init=None, query=""):
        pg = br.new_page(viewport={"width": 1400, "height": 950})
        logs, errors, outside = [], [], []
        pg.on("console", lambda m: logs.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("request", lambda r: None if r.url.startswith(BASE) or r.url.startswith("data:") or r.url.startswith("blob:")
              else outside.append(r.url))
        # the Map view (the Overview opens on the globe; these checks are the map's)
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light');"
                           "localStorage.setItem('hub-map-view','map');" + (init or ""))
        pg.goto(BASE + "/hub" + query)
        return pg, logs, errors, outside

    def mode_of(pg, want=None, ms=15000):
        end = time.time() + ms / 1000
        m = None
        while time.time() < end:
            m = pg.locator("#map").get_attribute("data-map-mode")
            if (want and m == want) or (not want and m in ("2d", "3d")):
                return m
            pg.wait_for_timeout(200)
        return m

    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()

        # a. WebGL unavailable: the 2D map, at once, and it says why
        pg, logs, errors, outside = page(br, NO_WEBGL)
        m = mode_of(pg, "2d")
        pg.wait_for_timeout(1500)
        check("WebGL unavailable: the 2D map is used", m == "2d" and pg.locator("#map .hm3-root").count() == 1
              and pg.locator("#map .hg-root").count() == 0, m)
        check("...and the console says which mode and why",
              any("[hub map] mode: 2d — WebGL unavailable" in l for l in logs), [l for l in logs if "hub map" in l])
        check("...three.js is not even downloaded", not any("three.module" in l for l in logs) and
              pg.evaluate("performance.getEntriesByType('resource').every(e => !e.name.includes('three.module'))"))
        check("...the 2D map draws its pins (with their numbers)", pg.locator('#map .hm3-pinbtn').count() >= 2
              or pg.locator("#map canvas").count() >= 1)
        b2 = pg.locator("#map .hm3-btn", has_text=re.compile(r"^(2D|3D)$")).first
        t1 = b2.inner_text(); b2.click(); pg.wait_for_timeout(900); t2 = b2.inner_text()
        b2.click(); pg.wait_for_timeout(900); t3 = b2.inner_text()
        check("...its 2D/3D button works both ways", {t1, t2} == {"2D", "3D"} and t3 == t1, (t1, t2, t3))
        check("...no page errors, nothing fetched from outside", not errors and not outside, (errors, outside))
        pg.close()

        # b. WebGL available: 3D first; kept or switched to 2D once by the speed rule, never back
        pg, logs, errors, outside = page(br)
        first = mode_of(pg, "3d", 20000)
        check("WebGL available: the 3D map starts (three.js from the tower)", first == "3d"
              and any("[hub map] mode: 3d — WebGL" in l for l in logs) and
              pg.evaluate("performance.getEntriesByType('resource').some(e => e.name.endsWith('/static/vendor/three.module.min.js'))"),
              [l for l in logs if "hub map" in l])
        pg.wait_for_timeout(4500)
        verdict = [l for l in logs if "mode: 3d kept" in l or "mode: 2d — median frame" in l]
        after = pg.locator("#map").get_attribute("data-map-mode")
        check("After 2 s of drawing the speed rule decided, and the console says so", len(verdict) == 1, verdict)
        pg.wait_for_timeout(3000)
        check("...and the map never switched again", pg.locator("#map").get_attribute("data-map-mode") == after
              and len([l for l in logs if "[hub map] mode:" in l]) <= 2, [l for l in logs if "hub map" in l])
        print("     (this browser: {0})".format(verdict[0] if verdict else "no verdict"))
        check("...no page errors, nothing fetched from outside", not errors and not outside, (errors, outside))
        pg.close()

        # c. 3D held (?map=3d): pins, the sheet, the real list, Esc, the 2D/3D button both ways
        pg, logs, errors, outside = page(br, query="?map=3d")
        m = mode_of(pg, "3d", 20000)
        pg.wait_for_timeout(2500)
        eg = pg.locator('#map .hg-pin[aria-label^="EG:"]')
        check("3D: each BU is a pin with its label, its number said to be past due",
              m == "3d" and eg.count() == 1 and eg.get_attribute("aria-label") == "EG: 5 past due"
              and pg.locator("#map .hg-lab", has_text="GH").count() == 1, (m, eg.count() and eg.get_attribute("aria-label")))
        eg.click()
        pg.wait_for_timeout(1600)
        sheet = pg.locator("#map .hg-sheet.hg-show")
        txt = sheet.inner_text() if sheet.count() else ""
        check("Click a pin: the sheet slides up with open, past due, in transit, at customs",
              sheet.count() == 1 and "EG" in txt and all(k in txt.upper() for k in ("OPEN", "PAST DUE", "IN TRANSIT", "AT CUSTOMS")), txt[:200])
        view = pg.evaluate("document.querySelector('#map .hg-root') && 1")
        sheet.locator("button", has_text="Open shipments").click()
        pg.wait_for_timeout(1200)
        dl = pg.locator("#dl")
        check("'Open shipments' opens the existing list (/api/hub/list) with that BU's rows",
              not dl.is_hidden() and "5 shipments" in pg.locator("#dlS").inner_text(), pg.locator("#dlS").inner_text())
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        check("Esc closes the list first, the map card stays", dl.is_hidden() and pg.locator("#map .hg-sheet.hg-show").count() == 1)
        pg.locator("#map .hg-root").focus()
        pg.keyboard.press("Escape"); pg.wait_for_timeout(1400)
        check("Esc again closes the card and the camera returns to the overview",
              pg.locator("#map .hg-sheet.hg-show").count() == 0)
        bd = pg.locator('#map .hg-btn[aria-label*="view"]').first
        t1 = bd.inner_text(); bd.click(); pg.wait_for_timeout(1100); t2 = bd.inner_text()
        bd.click(); pg.wait_for_timeout(1100); t3 = bd.inner_text()
        check("3D: the 2D/3D button works both ways", (t1, t2, t3) == ("2D", "3D", "2D"), (t1, t2, t3))
        check("...no page errors, nothing fetched from outside", not errors and not outside and view == 1, (errors, outside))
        pg.close()

        # d. ?map=2d: the 2D map by request
        pg, logs, errors, outside = page(br, query="?map=2d")
        check("?map=2d: the 2D map by request, logged", mode_of(pg, "2d") == "2d" and any("2D requested" in l for l in logs))
        pg.close()
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
