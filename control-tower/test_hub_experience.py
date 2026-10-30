"""
The hub's Apple-style experience: the Overview's Now / Recent activity,
operational exceptions counted with To Do's own rules, recent activity from
the records (no names or addresses), the compact To Do card opening the
existing slide-out, Shipment Tracking around the globe, ATA Automation's
tabs, and one look (type scale, restrained corners, one slide-out motion).

    python test_hub_experience.py
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
TMP = tempfile.mkdtemp(prefix="hub_exp_")
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


def row(bu, step, awb, overdue=None, status="x", eta="2026-10-01"):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, overdue=overdue, ontime=0 if overdue else 1, awb=awb, mode="Air", status=status, due="2026-10-01")
    return r


ROWS = [row("EG", "Submit to BU", "EG-0001", 45.0), row("EG", "Submit to BU", "EG-0002", 31.0),
        row("EG", "Customs Declaration", "EG-0003", 30.0), row("EG", "Customs Declaration", "EG-0004", 3.0),
        row("GH", "Correction Required", "GH-0001", 50.0, "RequiredCorrection"),
        row("GH", "Completed", "GH-0002"), row("NG", "Mystery step", "NG-0001", None, "x", None)]
DATA = {"asof": "2026-10-26", "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS], "dq": {}}
C.load = lambda: DATA
RT = C.runtime()
(RT / "meta.json").write_text(json.dumps({"file": "data.xlsx", "count": 7, "loaded_at": "2026-10-30T08:00:00", "by": "Jane Doe"}))
(RT / "escalations.json").write_text(json.dumps([
    {"id": "e1", "awb": "EG-0001", "bu": "EG", "step": "Submit to BU", "status": "open", "at": "2026-10-30T09:00:00", "by": "Jane Doe", "due": "2026-09-01"},
    {"id": "e2", "awb": "GH-0001", "bu": "GH", "step": "Correction Required", "status": "resolved", "at": "2026-10-29T09:00:00",
     "resolved_at": "2026-10-30T10:00:00", "by": "Jane Doe", "resolved_by": "John Roe", "due": "2026-09-01"}]))
(RT / "contact_log.jsonl").write_text(json.dumps({"awb": "EG-0002", "bu": "EG", "kind": "mail", "to": "agent@example.com",
                                                  "by": "Jane Doe", "at": "2026-10-30T11:00:00"}) + "\n")
STATE = {"run": {"run_id": "R1", "status": "finished", "started_at": "2026-10-30T07:00:00", "finished_at": "2026-10-30T07:30:00"},
         "shipments": [{"reference": "X1", "state": "failed"}, {"reference": "X2", "state": "waiting_for_human"},
                       {"reference": "X3", "state": "updated"}]}

print("\n1. OPERATIONAL EXCEPTIONS: COUNTED WITH TO DO'S OWN RULES")
ex = {e["id"]: e for e in H.exceptions(DATA, C.records(DATA), STATE)}
check("Open and past due: every open row with Performance above 0 (5)", ex["pastdue"]["count"] == 5, ex.get("pastdue"))
check("30 days or more, not escalated: To Do's own threshold and exclusion (EG-0002, EG-0003, GH-0001 = 3)",
      ex["over30"]["count"] == 3 and TD.LONG_PAST_DUE == 30, ex.get("over30"))
todo_long = next(i for i in TD.build(lambda p: True, None)["items"] if i["id"] == "clr:long-late")
check("...the same number To Do shows (never two numbers for one thing)", todo_long.get("count") == ex["over30"]["count"], todo_long)
check("Correction required: the eHub status To Do uses (1)", ex["correction"]["count"] == 1)
check("No stage: open rows the strip cannot place (1, no ETA)", ex["nostage"]["count"] == 1)
check("The run's own: failed (1) and waiting for a person (1), linking to the run and To Do",
      ex["failed"]["count"] == 1 and ex["human"]["count"] == 1 and ex["human"]["href"] == "/hub#todo")
check("Only exceptions with a count are listed", all(e["count"] > 0 for e in ex.values()))
ov = H.overview(STATE, lambda p: True, allowed={"GH"})
check("A BU-limited person: only their BUs, and no run exceptions (the run has no BU)",
      {e["id"] for e in ov["exceptions"]} == {"pastdue", "over30", "correction"} and
      next(e for e in ov["exceptions"] if e["id"] == "pastdue")["count"] == 1, ov["exceptions"])

print("\n2. RECENT ACTIVITY: FROM THE RECORDS, NO NAMES OR ADDRESSES")
act = H.activity(STATE, lambda p: True)
kinds = [a["kind"] for a in act]
check("Export load, escalation, resolution, mail and the run's start and finish, newest first",
      {"export", "escalation", "resolved", "contact", "run"} <= set(kinds) and
      [a["at"] for a in act] == sorted([a["at"] for a in act], reverse=True), kinds)
blob = json.dumps(act)
check("No person's name or address is in the list", "Jane" not in blob and "John" not in blob and "@" not in blob)
gh = H.activity(STATE, lambda p: True, allowed={"GH"})
check("BU-limited: only their BUs' events and nothing from the run",
      all(a["kind"] in ("export", "resolved", "escalation") for a in gh) and "EG-0001" not in json.dumps(gh), gh)
check("Without Clearance access: none of the export's events", all(a["kind"] == "run" for a in H.activity(STATE, lambda p: p != "clearance.view")))
check("The Overview carries both", "exceptions" in H.overview(STATE, lambda p: True) and "activity" in H.overview(STATE, lambda p: True))

print("\n3. ONE LOOK")
CSS = (HERE / "dashboard" / "static" / "hub" / "hub.css").read_text(encoding="utf-8")
HUB = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")
VIEWS = (HERE / "dashboard" / "static" / "hub" / "views.js").read_text(encoding="utf-8")
TODOJS = (HERE / "dashboard" / "static" / "todo.js").read_text(encoding="utf-8")
check("A type scale (large title to caption, and one for KPIs) and 4-point spacing",
      all("--hub-t-" + k in CSS for k in ("large", "title", "section", "headline", "body", "caption", "kpi")) and "--hub-sp-4:16px" in CSS)
check("Restrained corners: cards 16-18 px (were 20-26)", "--hub-r-lg:16px; --hub-r-xl:18px" in CSS)
check("Warm whites in light, deep charcoal in dark", "--hub-bg:#f6f5f3" in CSS and "--hub-bg:#111113" in CSS)
check("Less glass: the sidebar is solid", ".side{position:sticky;top:0;height:100vh;background:var(--hub-bg-2);" in HUB)
check("One slide-out motion (.26 s) for the To Do panel and the shipment panel",
      "transition:transform .26s var(--hub-e)" in VIEWS and "transition:transform .26s var(--e)" in TODOJS)
check("KPI numbers use the KPI type", ".chip .v{font:var(--hub-t-kpi)" in HUB)

print("\n4. IN THE BROWSER")
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
        errs = []
        pg = br.new_page(viewport={"width": 1440, "height": 950})
        pg.on("console", lambda m: m.type == "error" and errs.append(m.text)); pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','light')")
        pg.goto(BASE + "/hub"); pg.wait_for_timeout(3000)
        heads = pg.eval_on_selector_all(".sec-h h2", "els => els.map(e => e.textContent)")
        check("The Overview has a Now and a Recent activity section, in that order", heads == ["Now", "Recent activity"], heads)
        exc = pg.eval_on_selector_all("#exL b", "els => els.map(e => e.textContent)")
        check("Operational exceptions are listed with their numbers", "Open and past due" in exc and pg.locator("#exL em").first.inner_text() == "5", exc)
        pg.locator("#exL a").first.click(); pg.wait_for_timeout(1200)
        check("An exception opens the rows behind it", not pg.evaluate("document.getElementById('dl').hidden")
              and "past due" in pg.locator("#dlT").inner_text().lower(), pg.locator("#dlT").inner_text())
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        acts = pg.locator("#actL").inner_text()
        check("Recent activity shows the records (export, escalation, mail), no names", "eHub export loaded" in acts
              and "Escalated EG-0001" in acts and "Mail drafted" in acts and "Jane" not in acts, acts[:300])
        pg.locator("#tdOpen").click(); pg.wait_for_timeout(500)
        check("'Open To Do' opens the existing slide-out", pg.evaluate("document.getElementById('todoPanel').classList.contains('show')"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        check("...and Esc closes it", not pg.evaluate("document.getElementById('todoPanel').classList.contains('show')"))
        check("The To Do card is compact: three tasks at most", pg.locator("#todoL li").count() <= 3)
        # Shipment Tracking around the globe
        pg.evaluate("location.hash = '#tracking'"); pg.wait_for_timeout(3000)
        check("Shipment Tracking opens on its globe", pg.locator("#hvGlobe .hg").count() == 1 and
              "Shown by business unit" in pg.locator("#hvGlobe .hg-card").inner_text())
        check("Every row says its status (dot and word)", pg.locator("#hvBody .hv-st").count() == pg.locator("#hvBody tr[data-ref]").count() > 0)
        pg.locator('#hvGlobe .hg-row[data-id="bu:EG"]').click(); pg.wait_for_timeout(900)
        pg.locator("#hvGlobe .hg-acts button", has_text="Show only EG").click(); pg.wait_for_timeout(600)
        bus = set(pg.eval_on_selector_all("#hvBody tr[data-ref]", "els => els.map(e => e.dataset.bu)"))
        check("Choosing a BU on the globe filters the list to it", bus == {"EG"} and pg.evaluate("document.getElementById('hvBu').value") == "EG", bus)
        pg.locator("#hvBody tr[data-ref]").first.click(); pg.wait_for_timeout(900)
        check("Choosing a shipment opens its panel and turns the globe to its BU",
              pg.locator(".hv-drawer.on").count() == 1 and (pg.get_attribute('#hvGlobe .hg-pin[aria-pressed="true"]', "aria-label") or "").startswith("EG"))
        pg.keyboard.press("Escape"); pg.wait_for_timeout(400)
        pg.evaluate("location.hash = '#tracking?q=GH-0001'"); pg.wait_for_timeout(2500)
        check("A link can carry a search (?q=)", pg.evaluate("document.getElementById('hvQ').value") == "GH-0001"
              and pg.locator("#hvBody tr[data-ref]").count() == 1)
        pg.evaluate("location.hash = ''"); pg.wait_for_timeout(1200)
        check("Leaving Tracking stops its globe", pg.evaluate("!window.HubViews._globe"))
        # ATA Automation: one place
        pg.evaluate("location.hash = '#ata'"); pg.wait_for_timeout(2500)
        tabs = pg.eval_on_selector_all("#ataTabs a:not(.hum)", "els => els.map(e => e.textContent)")
        check("ATA Automation has its tabs: live run, shipments & read-back, history, activity log",
              tabs == ["Live run", "Shipments & read-back", "History", "Activity log"] and
              pg.locator('#ataTabs a[aria-current="page"]').inner_text() == "Live run", tabs)
        pg.locator("#ataTabs a", has_text="Activity log").click(); pg.wait_for_timeout(2500)
        check("Activity log opens the run page's own log", pg.evaluate("document.getElementById('embedF').contentWindow.location.hash") == "#logs"
              and pg.locator('#ataTabs a[aria-current="page"]').inner_text() == "Activity log")
        check("The tabs say whether a Human Action waits, linking to To Do", pg.get_attribute("#ataTabs a.hum", "href") == "/hub#todo")
        pg.evaluate("location.hash = '#clearance'"); pg.wait_for_timeout(1500)
        check("Other pages have no ATA tabs", pg.evaluate("document.getElementById('ataTabs').hidden"))
        check("No console error anywhere above", not [e for e in errs if "favicon" not in e], errs)
        pg.close()
        dk = br.new_page(viewport={"width": 1440, "height": 950})
        derr = []
        dk.on("pageerror", lambda e: derr.append(str(e)))
        dk.add_init_script("sessionStorage.setItem('ct-intro','1');localStorage.setItem('ct-theme','dark')")
        dk.goto(BASE + "/hub"); dk.wait_for_timeout(2500)
        bg = dk.evaluate("getComputedStyle(document.body).backgroundColor")
        check("Dark: deep charcoal, not pure black", bg == "rgb(17, 17, 19)", bg)
        check("No error in dark mode", not derr, derr)
        dk.close()
        br.close()

print("\n{0} passed, {1} failed{2}".format(len(PASSED), len(FAILED), ", {0} skipped".format(len(SKIPPED)) if SKIPPED else ""))
sys.exit(1 if FAILED else 0)
