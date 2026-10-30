"""
The hub's own views (Shipment Tracking with one shipment's fields and their
sources, To Do & Approvals), the existing pages inside it as they
are (the run page with its Human Action button, BOE / PO, Clearance, ATLAS…)
and ATLAS's chat on every page. Read-only, real numbers, only for who may see
them, and safe with hostile text in the records.

    python test_hub_views.py
"""
import http.cookiejar
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
TMP = tempfile.mkdtemp(prefix="hub_views_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS_DB": os.path.join(TMP, "accounts.db"), "DASHBOARD_ACCOUNTS": "auto",
                   "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "PO_DATA_DIR": os.path.join(TMP, "po"),
                   "PO_AUTO": "0", "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED, SKIPPED = [], [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


from dashboard import clearance as C, hub as H  # noqa: E402

EVIL = '<img src=x onerror="window.__pwned=1">'


def row(bu, step, awb, eta=None, ata=None, ontime=1, overdue=None, cycle=None, target=None, **kw):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, ata=ata, ontime=ontime, overdue=overdue, awb=awb, mode="Air", status="x",
             cycle=cycle, target=target, **kw)
    return r


ROWS = [
    row("EG", "Submit to BU", "157-1111 2222", eta="2026-10-01", overdue=12, ontime=0, due="2026-10-14", submitted="2026-10-02",
        ffw=EVIL, agent=EVIL),
    row("EG", "Customs Declaration", "EGX2", eta="2026-10-01", ata="2026-10-02", overdue=40, ontime=0),
    row("GH", "Completed", "GHX1", eta="2026-09-01", ata="2026-09-02", cycle=5, target=7, completed="2026-09-08"),
    row("GH", "Completed", "GHX2", eta="2026-09-01", ata="2026-09-02", cycle=9, target=7, completed="2026-09-12"),
    row("GH", "Duty Payment", "GHX3", eta="2026-11-09"),
]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS]}
C.load = lambda: DATA
STATE = {"run": {"run_id": "R-77", "status": "running", "dry_run": True},
         "shipments": [{"reference": "15711112222", "carrier": "DHL", "state": "updated", "provider_eta": "2026-10-01",
                        "provider_ata": "2026-10-03", "step": "Written and read back",
                        "where": {"source": "DHL timeline", "last": {"date": "03/10/2026", "time": "09:10", "text": "Arrived at Cairo"}}}]}

print("\n1. ONE SHIPMENT: THE EXPORT'S ROW AND THE RUN'S RECORD, EACH FIELD WITH ITS SOURCE")
s = H.shipment("157 1111-2222", STATE)
check("Found by AWB/BOL however it is spaced or dashed", s and s["export"] and s["run"])
check("Export fields say they come from the export, with its data date",
      s["export"]["source"] == "eHub export, data as of 2026-10-26" and any(f["key"] == "due" and f["value"] == "2026-10-14" for f in s["export"]["rows"][0]["fields"]))
check("Run fields say which run (and that it was a dry run)", s["run"]["source"] == "ATA run R-77 (dry run)"
      and any(f["key"] == "provider_ata" and f["value"] == "2026-10-03" for f in s["run"]["fields"]))
labels = [t["label"] for t in s["timeline"]]
iso = [t["date"] for t in s["timeline"] if re.match(r"^\d{4}-", str(t["date"]))]
check("The timeline: every event from those two sources, ISO dates in order",
      labels[0] == "ETA (eHub)" and "ATA (carrier)" in labels and all(t["source"] for t in s["timeline"]) and iso == sorted(iso), labels)
check("...a carrier's own date text is not reinterpreted (03/10 could be either way round): it comes last, as written",
      s["timeline"][-1]["date"] == "03/10/2026 09:10")
check("Nothing merged: the carrier's ATA is not written into the export's ATA",
      next(f for f in s["export"]["rows"][0]["fields"] if f["key"] == "ata")["value"] is None)
check("An unknown AWB/BOL is None (the server answers 404)", H.shipment("ZZZZ9999", STATE) is None)
sg = H.shipment("157 1111-2222", STATE, allowed={"GH"})
check("A BU-limited person: not another BU's shipment, and never the run's record", sg is None)
check("Without clearance.view: the run's record only", H.shipment("15711112222", STATE, can=lambda p: p == "runs.view")["export"] is None)

print("\n3. THE NAVIGATION: EVERY CONNECTED MODULE HAS ITS VIEW IN THE HUB")
js = (HERE / "dashboard" / "static" / "hub" / "views.js").read_text(encoding="utf-8")
html = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")
for mid in ("tracking", "todo"):
    if "V.{0} = {{".format(mid) not in js:
        check("A view for " + mid, False)
        break
else:
    check("A hub view for Tracking and To Do", True)
check("Not-connected modules stay 'not connected' (no view, no numbers)",
      all(m["status"] == "off" for m in H.MODULES if m["id"] in ("rates", "tender", "optimization"))
      and not any("V.{0} = {{".format(i) in js for i in ("rates", "tender", "optimization")))
check("The views only read: no POST/PUT/DELETE anywhere in them", not re.search(r"method\s*:\s*['\"](POST|PUT|DELETE)", js) and "XMLHttpRequest" not in js)
EMB = {m["id"]: m.get("embed") for m in H.MODULES if m.get("embed")}
check("Every existing page is in the hub, as it is: run, run shipments, BOE / PO, Clearance, Human Action, history, ATLAS, Access, Settings",
      EMB == {"ata": "/#overview", "runs": "/#shipments", "po": "/#po", "clearance": "/clearance", "human": "/#human",
              "history": "/#analysis", "atlas": "/#atlas", "access": "/#access", "settings": "/#settings"}, EMB)
check("The hub's own copies of those pages are gone (no second run page, no second document list)",
      "V.ata = {" not in js and "V.documents = {" not in js and "/api/control" not in js)
check("The BOE page is named so it can be found", any(m["title"] == "BOE / PO Automation" for m in H.MODULES))
NAV = [m["id"] for m in H.MODULES if m.get("nav", True)]
check("Removed from the menu: Documents, Carriers, Analytics & KPIs, Reports, Optimization, Run History, Human Action, Master Data, Settings",
      not set(NAV) & {"documents", "carriers", "analytics", "reports", "optimization", "history", "human", "masterdata", "settings"}, NAV)
check("...and their hub views are gone", not any("V.{0} = {{".format(i) in js for i in ("carriers", "analytics", "reports", "documents", "ata", "masterdata")))
check("Settings and the theme are small icons in the top bar", 'id="setTop" aria-label="Settings"' in html and 'class="tb-th" role="group" aria-label="Theme"' in html
      and 'aria-label="Light theme"' in html and 'aria-label="Dark theme"' in html)
check("Human Action is reached from the run page (its parent), not the menu",
      next(m for m in H.MODULES if m["id"] == "human").get("parent") == "ata")
check("The page adds one script for them", html.count('<script src="/static/hub/views.js" defer></script>') == 1)
check("Nothing from outside", not re.findall(r"https?://", js))

print("\n4. THROUGH THE REAL SERVER")
from dashboard import accounts as A, server as S  # noqa: E402
PW = "Hub-Views-2026-Secret!"
st = A.store()
st.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
st.users.create("gh@mantrac.example", "Kwame GH", "ADMIN", password=PW)
st.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
_orig = S.Handler._acc_bus
S.Handler._acc_bus = lambda self: ({"GH"} if self.user and self.user.get("work_email") == "gh@mantrac.example" else _orig(self))
S._assistant_state = lambda: STATE
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="hub-views-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(o, path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Origin", BASE)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with o.open(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def sign_in(email):
    jar = http.cookiejar.CookieJar()
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), _NoRedirect())
    call(o, "/api/auth/login", "POST", {"email": email, "password": PW})
    return o, jar


ADM, ADM_JAR = sign_in("admin@mantrac.example")
GHA, _ = sign_in("gh@mantrac.example")
OPR, _ = sign_in("oper@mantrac.example")
code, b = call(ADM, "/api/hub/shipment?ref=157-1111-2222")
check("/api/hub/shipment: the joined shipment for an admin", code == 200 and json.loads(b)["run"]["source"] == "ATA run R-77 (dry run)")
check("...404 for an unknown AWB/BOL, 400 for something that is not one",
      call(ADM, "/api/hub/shipment?ref=ZZZZ9999")[0] == 404 and call(ADM, "/api/hub/shipment?ref=%3Cscript%3E")[0] == 400)
check("...a BU-limited account gets nothing of another BU", call(GHA, "/api/hub/shipment?ref=157-1111-2222")[0] == 404)
check("The removed views' endpoints are gone", call(ADM, "/api/hub/carriers")[0] == 404 and call(ADM, "/api/hub/kpis")[0] == 404)
check("Every new hub endpoint is closed to non-admins while the hub is in review",
      all(call(OPR, p)[0] in (401, 403) for p in ("/api/hub/shipment?ref=EGX2", "/api/hub/overview", "/api/hub/list?stage=all")))
check("...and answers GET only", call(ADM, "/api/hub/shipment?ref=EGX2", "POST", {})[0] in (403, 404, 405))

print("\n5. FROM THE SECURITY REVIEW")


class _U:
    def __init__(self, user):
        self.user = user


bus = lambda u: _orig(_U(u))
check("No BU limit on the account: every BU", bus({"role": "ADMIN"}) is None and bus(None) is None)
check("A BU limit that is set but empty means none (fails closed, never 'all')", bus({"bus": []}) == set() and bus({"bus": ""}) == set())
check("A BU limit given as text is one code or a comma list, not letters", bus({"bus": "EG"}) == {"EG"} and bus({"bus": "eg, GH"}) == {"EG", "GH"})
ovs = H.overview(STATE, lambda p_: True, {"GH"})
check("A BU-limited person's navigation has no module whose records carry no BU",
      not {m["id"] for m in ovs["modules"]} & set(H.UNSCOPED) and "tracking" in {m["id"] for m in ovs["modules"]})
check("The hub's own page and scripts also need hub.preview",
      call(OPR, "/static/hub.html")[0] in (302, 401, 403) and call(OPR, "/static/hub/views.js")[0] in (302, 401, 403)
      and call(ADM, "/static/hub/views.js")[0] == 200)
check("A BU code with a trailing newline is refused", call(ADM, "/api/hub/list?stage=all&bu=EG%0A")[0] == 400)
srt = H.stage_list("all", data={"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in
      [row("EG", "Submit to BU", "A", overdue=-3), row("EG", "Submit to BU", "B", overdue=0), row("EG", "Completed", "C")]]})
check("Lists sort a shipment due today (0) above one not yet due (-3)", [r["awb"] for r in srt["rows"]] == ["B", "A", "C"], [r["awb"] for r in srt["rows"]])
check("The overview's dates are reformatted only when ISO, otherwise shown escaped",
      ': esc(s);' in html.split("const dmy")[1].split("\n")[0])

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
if sync_playwright is None:
    SKIPPED.append("browser")
    print("  (Playwright not installed: browser checks skipped)")
else:
    exe = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    with sync_playwright() as p:
        br = p.chromium.launch(executable_path=exe) if os.path.exists(exe) else p.chromium.launch()
        ctx = br.new_context(viewport={"width": 1400, "height": 950})
        ck = [c for c in ADM_JAR][0]
        ctx.add_cookies([{"name": ck.name, "value": ck.value, "url": BASE}])
        pg = ctx.new_page()
        errors, outside, posts = [], [], []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("request", lambda r: (outside.append(r.url) if not r.url.startswith(BASE) and not r.url.startswith(("data:", "blob:")) else None,
                                    posts.append(r.url) if r.method != "GET" and "/api/hub" in r.url else None))
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        pg.goto(BASE + "/hub?map=2d")
        pg.wait_for_timeout(2500)
        seen = {}
        for v in ("tracking", "todo"):
            pg.evaluate("location.hash = %r" % ("#" + v))
            pg.wait_for_timeout(1300)
            seen[v] = (pg.locator('[data-view="mod"]').is_visible(), pg.locator("#modBody h2").first.inner_text() if pg.locator("#modBody h2").count() else "")
        check("Every module view opens inside the hub", all(vis and title for vis, title in seen.values()), seen)
        check("...the sidebar link stays in the hub (no jump to another page)",
              pg.locator('#nav a[data-mod="tracking"]').get_attribute("href") == "/hub#tracking")
        pg.evaluate("location.hash = '#tracking'"); pg.wait_for_timeout(1200)
        txt = pg.locator("#modBody").inner_text()
        check("Tracking lists every export row with its numbers", "157-1111 2222" in txt and "Showing 5 of 5" in txt, txt[:200])
        pg.locator('tr[data-ref="157-1111 2222"]').click(); pg.wait_for_timeout(1200)
        dtxt = pg.locator(".hv-drawer").inner_text()
        check("Opening a shipment shows both sources and the timeline",
              "FROM THE EHUB EXPORT" in dtxt.upper() and "ATA run R-77 (dry run)" in dtxt and "Arrived at Cairo" in dtxt, dtxt[:300])
        pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
        check("Esc closes the shipment", pg.locator(".hv-drawer").count() == 0)
        pg.evaluate("location.hash = '#tracking?ref=EGX2'"); pg.wait_for_timeout(1600)
        check("A link with ?ref= opens that shipment", pg.locator(".hv-drawer h3").inner_text() == "EGX2")
        check("Hostile text in a record is shown as text, never run",
              pg.evaluate("window.__pwned === undefined") and pg.locator(".hv-drawer img").count() == 0
              and EVIL in pg.locator("#modBody").inner_text())
        pg.keyboard.press("Escape")
        pg.evaluate("location.hash = '#rates'"); pg.wait_for_timeout(600)
        check("A not-connected module still says so", pg.locator('[data-view="off"]').is_visible() and "not connected" in pg.locator("#offT").inner_text())
        pg.evaluate("location.hash = '#overview'"); pg.wait_for_timeout(800)
        check("Back to the Overview, unchanged", pg.locator('[data-view="overview"]').is_visible() and not pg.locator('[data-view="mod"]').is_visible())
        # ATLAS's chat on the hub's own pages: the same chat, in a layer
        pg.evaluate("location.hash = '#tracking'"); pg.wait_for_timeout(1200)
        check("The hub's own pages show Ask ATLAS", pg.locator("#askFab").is_visible())
        pg.locator("#askFab").click(); pg.wait_for_timeout(3500)
        cf = pg.frame(url=re.compile(r".*embed=chat.*"))
        check("...it opens ATLAS's own chat (the same one as on every page), and nothing else of that page",
              pg.locator("#chatF").is_visible() and cf is not None and cf.evaluate("document.getElementById('chat').classList.contains('on')")
              and cf.evaluate("getComputedStyle(document.querySelector('.main')).display") == "none")
        cf.locator("#chat .ch-x:visible").first.click(); pg.wait_for_timeout(1200)
        check("...closing the chat closes the layer", pg.locator("#chatF").is_hidden())
        # the existing pages, inside the hub
        pg.evaluate("location.hash = '#po'"); pg.wait_for_timeout(3500)
        fr = pg.frame_locator("#embedF")
        src = pg.locator("#embedF").get_attribute("src") or ""
        check("BOE / PO Automation opens the existing PO page inside the hub", pg.locator('[data-view="embed"]').is_visible()
              and src.startswith("/?embed=1") and src.endswith("#po") and fr.locator('.page[data-p="po"].on').count() == 1, src)
        check("...only its brand bar is hidden; its own title and controls are there",
              not fr.locator(".appbar").is_visible() and fr.locator("#pageTitle").inner_text().strip() == "PO Automation")
        pg.evaluate("location.hash = '#ata'"); pg.wait_for_timeout(1500)
        check("Moving between existing pages switches the page, not the frame", fr.locator('.page[data-p="overview"].on').count() == 1
              and (pg.locator("#embedF").get_attribute("src") or "").endswith("#po"))
        hb = fr.locator("#hubHuman")
        check("The run page has a Human Action button (inside the hub)", hb.is_visible() and hb.inner_text() == "Human Action")
        hb.click(); pg.wait_for_timeout(1000)
        check("...it opens the Human Action page, with a way back to the run",
              fr.locator('.page[data-p="human"].on').count() == 1 and hb.inner_text().endswith("Back to the run"))
        hb.click(); pg.wait_for_timeout(800)
        check("...and back", fr.locator('.page[data-p="overview"].on').count() == 1)
        pg.evaluate("location.hash = '#settings'"); pg.wait_for_timeout(1200)
        check("The Human Action button is only on the run page (not on Settings, BOE…)", not fr.locator("#hubHuman").is_visible())
        pg.evaluate("location.hash = '#ata'"); pg.wait_for_timeout(1200)
        check("On an existing page the hub's own Ask ATLAS button steps aside (the page has its own)", pg.locator("#askFab").is_hidden())
        pg.locator("#askTop").click(); pg.wait_for_timeout(1500)
        check("...and the top bar's Ask ATLAS opens that page's chat", fr.locator("#chat.on").count() == 1)
        fr.locator("#chat .ch-x:visible").first.click(); pg.wait_for_timeout(800)
        pg.evaluate("location.hash = '#history'"); pg.wait_for_timeout(1500)
        check("Run History opens the History · Analysis page", fr.locator('.page[data-p="analysis"].on').count() == 1)
        pg.locator('#theme button[data-t="dark"]').click(); pg.wait_for_timeout(500)
        check("The hub's theme reaches the page inside", pg.frame(url=re.compile(r".*embed=1.*")).evaluate("document.documentElement.dataset.theme") == "dark")
        pg.locator('#theme button[data-t="light"]').click(); pg.wait_for_timeout(300)
        pg.frame(url=re.compile(r".*embed=1.*")).evaluate("""() => { const a = document.createElement('a'); a.href = '/clearance#bu=EG'; a.id = 'tst';
            document.body.appendChild(a); a.click(); }""")
        pg.wait_for_timeout(2500)
        check("A link to another page opens it in the hub, not inside the frame", pg.url.endswith("/hub#clearance?bu=EG"), pg.url)
        check("Clearance inside the hub: the existing page, brand bar hidden",
              (pg.locator("#embedF").get_attribute("src") or "").startswith("/clearance?embed=1") and
              pg.frame(url=re.compile(r".*/clearance\?embed=1.*")).evaluate("getComputedStyle(document.querySelector('.appbar')).display") == "none")
        alone = ctx.new_page(); alone.goto(BASE + "/#po"); alone.wait_for_timeout(2500)
        check("Opened directly, the existing page is exactly as before (brand bar, its own menu, no added button)",
              alone.locator(".appbar").is_visible() and not alone.evaluate("document.documentElement.classList.contains('in-hub')")
              and alone.locator("#hubHuman").count() == 0)
        alone.close()
        check("No page errors, nothing from outside, no write requests", not errors and not outside and not posts, (errors, outside, posts))
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
