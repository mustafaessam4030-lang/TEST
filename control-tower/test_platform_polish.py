"""
Polish without new features: every request answered, no console error from
the platform itself, counts that match, a refresh that keeps what you were
doing, and states that say what happened. Each check here protects one fix.

    python test_platform_polish.py
"""
import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="polish_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS": "0", "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"),
                   "PO_DATA_DIR": os.path.join(TMP, "po"), "PO_AUTO": "0",
                   "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


from dashboard import clearance as C, hub as H, todo as TD, server as S  # noqa: E402


def row(bu, step, awb, eta=None, overdue=None, ontime=1):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, overdue=overdue, ontime=ontime, awb=awb, mode="Air", status="x", due="2026-10-01")
    return r


ROWS = [row("EG", "Submit to BU", "EG-0001", "2026-10-01", 12.0, 0), row("GH", "Completed", "GH-0001", "2026-09-01")]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS]}
C.load = lambda: DATA

print("\n1. THE SERVER ANSWERS EVERY REQUEST")
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key=None)
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


def call(path, method="GET", body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json", "Origin": BASE})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


st, b = call("/api/auth/me")
check("Sign-in off: /api/auth/me answers 200 'local' (was a 404 in every page's console)",
      st == 200 and json.loads(b)["mode"] == "local" and json.loads(b)["user"] is None, (st, b[:80]))
_orig = H.overview
import logging  # noqa: E402
H.overview = lambda *a, **k: 1 / 0
logging.getLogger("dashboard.server").disabled = True       # the forced error is logged by design; keep the output clean
st, b = call("/api/hub/overview")
logging.getLogger("dashboard.server").disabled = False
H.overview = _orig
check("An unexpected error is answered with a 500, not a dropped connection", st == 500 and json.loads(b)["error"] == "server error", (st, b[:80]))
check("...and the server keeps serving", call("/api/hub/overview")[0] == 200)
st, _ = call("/api/feedback", "POST", raw=b"[1, 2]")
check("Feedback that is not an object is a 400, not a crash", st == 400, st)

print("\n1b. LESS WORK PER REQUEST")
dp = C.data_path(); dp.parent.mkdir(parents=True, exist_ok=True); dp.write_text(json.dumps(DATA), encoding="utf-8")
req = urllib.request.Request(BASE + "/api/clearance", headers={"Accept-Encoding": "gzip"})
with urllib.request.urlopen(req) as r:
    etag, enc = r.headers.get("ETag"), r.headers.get("Content-Encoding")
req2 = urllib.request.Request(BASE + "/api/clearance", headers={"Accept-Encoding": "gzip", "If-None-Match": etag or "x"})
try:
    st2 = urllib.request.urlopen(req2).status
except urllib.error.HTTPError as e:
    st2 = e.code
check("The export is served with an ETag and revalidated: a reload of an unchanged export is a 304 (compressed once when large)",
      etag and st2 == 304, (etag, enc, st2))
with urllib.request.urlopen(BASE + "/static/brand/mantrac-logo.png") as r:
    cc = r.headers.get("Cache-Control")
check("Images are kept by the browser for a day (they were read again on every page)", cc == "private, max-age=86400", cc)
IDX = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
check("The run page goes back to the live stream after a few reads, and does not read while hidden",
      "if (++polls >= 5){ polls = 0; connect(); return; }" in IDX and "if (!document.hidden) fetch('/api/state')" in IDX)

print("\n2. COUNTS THAT MATCH")
saved = (C.escalations, C.contacts)
C.escalations = lambda: [{"awb": "EG-0001", "bu": "EG", "status": "open", "due": "2026-09-01", "at": "2026-10-20 10:00"}]
C.contacts = lambda: {"agents": {}, "bus": {}, "forwarders": {}, "coe": {}}
items = TD._clearance_items(__import__("datetime").date(2026, 10, 30), __import__("datetime").datetime(2026, 10, 30, 9))
C.escalations, C.contacts = saved
txt = json.dumps(items)
check("To Do takes the export's own day count even when it is a decimal (12.0 -> 12 days, as on Clearance)",
      "12 day" in txt and "59 day" not in txt, txt[:300])

print("\n2b. TEXT IS READABLE")
CSS = (HERE / "dashboard" / "static" / "hub" / "hub.css").read_text(encoding="utf-8")
HUB = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def tokens(block):
    return dict(re.findall(r"--(hub-[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


LIGHT = tokens(CSS.split("@media (prefers-color-scheme:dark)")[0])
DARK = tokens(CSS.split(':root[data-theme="dark"]')[1])
low = []
for name, T in (("light", LIGHT), ("dark", DARK)):
    for ink in ("hub-ink-3", "hub-green-ink", "hub-amber-ink", "hub-red-ink"):
        for bg in ("hub-bg", "hub-bg-2", "hub-surface", "hub-surface-2"):
            r = ratio(T[ink], T[bg])
            if r < 4.5:
                low.append((name, ink, bg, round(r, 2)))
check("Muted and status text is 4.5:1 or better on every hub surface, light and dark (#86868b was 3.4:1)", not low, low)
check("Status badges use the readable text tokens, not the fill colours",
      all(re.search(r"\.hub-badge\.{0}\{{[^}}]*color:var\(--hub-{1}-ink\)".format(k, c), CSS)
          for k, c in (("live", "green"), ("part", "amber"), ("red", "red"))))
check("The hub's own small grey labels use the readable token", ".grp{" in HUB and "#86868b" not in HUB and
      "#86868b" not in (HERE / "dashboard" / "static" / "hub" / "map3d_gl.js").read_text(encoding="utf-8"))

print("\n3. IN THE BROWSER")
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
else:
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    TODO = {"items": [{"id": "a", "state": "urgent", "level": "red", "title": "Red task", "detail": "", "source": "Run", "href": "/#overview"},
                      {"id": "b", "state": "open", "level": "amber", "title": "Amber task", "detail": "", "source": "Clearance", "href": "/clearance"},
                      {"id": "c", "state": "completed", "level": "grey", "title": "Done task", "detail": "", "source": "Run", "href": "/#overview"}],
            "outstanding": 2, "count": 2, "by_state": {"urgent": 1, "open": 1, "completed": 1}}
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        pg = br.new_page(viewport={"width": 1366, "height": 768})
        errors = []
        pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        pg.route("**/api/todo", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(TODO)))
        pg.goto(BASE + "/hub?map=2d"); pg.wait_for_timeout(2500)
        check("Hub To Do card: the open count leaves out finished tasks", pg.locator("#tdP").inner_text() == "2 open", pg.locator("#tdP").inner_text())
        dots = pg.eval_on_selector_all("#todoL i", "els => els.map(e => e.getAttribute('style'))")
        check("...a red task has a red dot, an amber one amber, and the finished one is not listed",
              len(dots) == 2 and "hub-red" in dots[0] and "hub-amber" in dots[1], dots)
        logo = pg.evaluate("(() => { const i = document.querySelector('.side .corp img'); return i && [i.naturalWidth, i.getBoundingClientRect().height, i.alt]; })()")
        check("Hub: the Mantrac | CAT logo is in the sidebar, above ATLAS (official file, 30 px tall)",
              logo and logo[0] > 0 and logo[1] == 30 and logo[2] == "Mantrac | CAT" and
              pg.locator(".side .brand .axf").count() == 1, logo)
        fs = pg.evaluate("getComputedStyle(document.querySelector('.srch-b')).fontSize")
        check("Hub: the Search button keeps its own type (the token mapping was broken)", fs == "12.5px", fs)
        check("Hub: numbers keep a fixed width (tabular figures are no longer cancelled)",
              pg.evaluate("getComputedStyle(document.body).fontFeatureSettings") == "normal")
        # a refresh keeps the menu and focus
        pg.set_viewport_size({"width": 390, "height": 800}); pg.wait_for_timeout(300)
        pg.locator("#burger").click(); pg.wait_for_timeout(300)
        first = pg.evaluate("window.__a = document.querySelector('#nav a'); !!window.__a")
        pg.evaluate("window.HubCtx.refresh()"); pg.wait_for_timeout(1500)
        check("A refresh does not close the phone menu or rebuild it (links and focus kept)",
              pg.evaluate("document.getElementById('side').classList.contains('open')") and first and
              pg.evaluate("window.__a === document.querySelector('#nav a')"))
        check("The phone menu button says it is open (aria-expanded)", pg.get_attribute("#burger", "aria-expanded") == "true")
        pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        check("Esc closes the phone menu (it covers its own button) and focus returns to the button",
              not pg.evaluate("document.getElementById('side').classList.contains('open')") and
              pg.evaluate("document.activeElement.id") == "burger")
        pg.locator("#burger").click(); pg.wait_for_timeout(300)
        pg.mouse.click(370, 600); pg.wait_for_timeout(300)
        check("...the tap that closes it does not also press what was under it", pg.evaluate("document.getElementById('dl').hidden"))
        check("...and so does a tap outside it; closed, it says so and its links leave the Tab order",
              pg.get_attribute("#burger", "aria-expanded") == "false" and pg.evaluate("document.getElementById('side').inert"))
        pg.set_viewport_size({"width": 1366, "height": 768}); pg.wait_for_timeout(300)
        check("On a wide screen the menu is always reachable", not pg.evaluate("document.getElementById('side').inert"))
        pg.evaluate("location.hash = '#tracking'"); pg.wait_for_timeout(1500)
        pg.locator("tr[data-ref]").first.focus(); pg.keyboard.press("Enter"); pg.wait_for_timeout(600)
        opened = pg.locator(".hv-drawer.on").count() == 1
        check("Shipment panel: Enter on a row opens it and it stays open (the same Enter pressed its Close button)", opened)
        loaded = "from the ehub export" in pg.locator(".hv-drawer .body").inner_text().lower()
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        check("Shipment panel: Esc closes it and focus goes back to the row that opened it",
              opened and loaded and pg.locator(".hv-drawer.on").count() == 0 and
              pg.evaluate("!!document.activeElement && document.activeElement.matches('tr[data-ref]')"))
        pg.locator("tr[data-ref]").first.click(); pg.wait_for_timeout(600)
        pg.locator("#title").click(); pg.wait_for_timeout(500)
        check("Shipment panel: a click outside closes it", pg.locator(".hv-drawer.on").count() == 0)
        pg.route("**/api/hub/shipment*", lambda r: r.fulfill(status=400, content_type="application/json", body='{"error":"bad reference"}'))
        pg.locator("tr[data-ref]").first.click(); pg.wait_for_timeout(600)
        check("Shipment panel: a refused request says it could not load (it threw an error)",
              "Could not load the shipment" in pg.locator(".hv-drawer .body").inner_text())
        pg.keyboard.press("Escape"); pg.unroute("**/api/hub/shipment*")
        pg.evaluate("location.hash = ''"); pg.wait_for_timeout(800)
        pg.route("**/api/hub/list*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error":"server error"}'))
        pg.evaluate("location.hash = '#tracking'"); pg.wait_for_timeout(1500)
        check("Tracking: a failed load says it failed (it said 'No eHub export loaded')",
              "could not be loaded" in pg.locator("#modBody").inner_text())
        pg.unroute("**/api/hub/list*")
        check("No console error on the hub (the /api/auth/me 404 is gone)", not [e for e in errors if "status of 500" not in e and "status of 400" not in e], errors)
        # layout shift: what loads keeps its place
        CLS = """window.__cls=0;try{new PerformanceObserver(l=>{for(const e of l.getEntries()){if(!e.hadRecentInput)window.__cls+=e.value}}).observe({type:'layout-shift',buffered:true});}catch(e){}
sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light');"""
        for path in ("/", "/clearance", "/hub?map=2d"):
            q = br.new_page(viewport={"width": 1366, "height": 768})
            q.add_init_script(CLS)
            q.goto(BASE + path); q.wait_for_timeout(3500)
            v = q.evaluate("window.__cls")
            check("No layout shift worth noticing on {0} (CLS {1:.3f} < 0.1)".format(path, v), v < 0.1, v)
            q.close()
        lz = br.new_page(viewport={"width": 1366, "height": 768})
        lz.add_init_script("sessionStorage.setItem('ct-intro','1')")
        lz.goto(BASE + "/hub#tracking"); lz.wait_for_timeout(3000)
        three = "performance.getEntriesByType('resource').some(e => e.name.includes('three.module'))"
        got = lz.evaluate(three)
        lz.evaluate("location.hash = ''"); lz.wait_for_timeout(3000)
        on_globe = lz.evaluate(three)
        lz.locator('#mapSeg button[data-mv="map"]').click(); lz.wait_for_timeout(3000)
        later = lz.evaluate(three) or lz.locator("#map").get_attribute("data-map-mode") in ("2d", "3d", "loading")
        check("three.js loads only when the Map is shown: not on other views, not with the globe",
              not got and not on_globe and later, (got, on_globe, later))
        lz.close()
        cl = br.new_page(viewport={"width": 1366, "height": 768})
        cerr = []
        cl.on("console", lambda m: m.type == "error" and cerr.append(m.text))
        cl.goto(BASE + "/clearance"); cl.wait_for_timeout(2500)
        check("Clearance: the closed dialog is hidden", cl.evaluate("document.getElementById('modal').hidden"))
        cl.keyboard.press("/"); cl.wait_for_timeout(500)
        check("Clearance: '/' opens the search (it never did)", cl.locator(".srch-p").is_visible() or cl.locator("#srchPanel").is_visible())
        check("No console error on Clearance", not cerr, cerr)
        cl2 = br.new_page(viewport={"width": 1366, "height": 768})
        cl2.route("**/api/clearance", lambda r: r.abort())
        cl2.goto(BASE + "/clearance"); cl2.wait_for_timeout(1500)
        check("Clearance: an export that cannot be loaded says so (it stayed blank)", "could not be loaded" in cl2.locator("#src").inner_text())
        cl2.close()
        idx = br.new_page(viewport={"width": 1366, "height": 768})
        ierr = []
        idx.on("console", lambda m: m.type == "error" and ierr.append(m.text))
        idx.add_init_script("sessionStorage.setItem('ct-intro','1')")
        idx.goto(BASE + "/"); idx.wait_for_timeout(2500)
        check("No console error on the run page", not ierr, ierr)
        check("Run page: before the first update 'On the way' says Loading, not a false 'Nothing on the way'",
              'id="fleetN">—</b>' in IDX and "<span>Loading…</span>" in IDX)
        check("Run page: the Access tables and the BOE list say when they could not be loaded",
              "catch(loadFailed('acRows', 'the accounts'))" in IDX and "loadFailed('poRows', 'the BOE jobs')" in IDX)
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
