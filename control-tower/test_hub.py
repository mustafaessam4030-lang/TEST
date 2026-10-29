"""
The Control Tower hub (CONTROL_TOWER_PLAN.md): its numbers are the records'
own, the same everywhere, and only for who may see them.

    python test_hub.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TMP = tempfile.mkdtemp(prefix="hub_")
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "DASHBOARD_ACCOUNTS_DB": os.path.join(TMP, "accounts.db"), "DASHBOARD_ACCOUNTS": "auto",
                   "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "PO_DATA_DIR": os.path.join(TMP, "po"),
                   "PO_AUTO": "0", "DASHBOARD_ACCESS_KEY_FILE": os.path.join(TMP, "access_key")})
os.makedirs(os.environ["ATA_CLEARANCE_INBOX"])

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("  {0}  {1}{2}".format("PASS" if cond else "FAIL", name, "" if cond else "  -- " + str(detail)[:300]))


from dashboard import clearance as C, hub as H  # noqa: E402

ASOF = "2026-10-26"


def row(bu, step, eta=None, ata=None, ontime=1, overdue=None, awb="X"):
    r = {k: None for k in C.FIELDS}
    r.update(bu=bu, step=step, eta=eta, ata=ata, ontime=ontime, overdue=overdue, awb=awb, mode="Air", status="x")
    return r


ROWS = [
    row("EG", "Assign Clearing Agent", eta="2026-11-02", awb="T1"),                     # transit: no ATA, ETA ahead
    row("EG", "Assign Clearing Agent", eta="2026-10-01", overdue=20, ontime=0, awb="A1"),  # arrived (ETA passed)
    row("EG", "Customs Declaration", eta="2026-10-01", ata="2026-10-02", overdue=3, awb="C1"),
    row("GH", "Awaiting Reciept", eta="2026-10-01", ata="2026-10-02", awb="W1"),
    row("GH", "Completed", eta="2026-09-01", ata="2026-09-02", awb="D1"),
    row("GH", "Duty Payment", eta="2026-11-09", awb="T2"),                              # pre-arrival: still in transit
    row("JR", "Submit to BU", eta="2026-10-01", awb="J1"),                              # a BU with no known country
    row("EG", "Some new step", ata="2026-10-01", awb="O1"),                             # open, not classifiable
]
DATA = {"asof": ASOF, "fields": C.FIELDS, "rows": [[r[k] for k in C.FIELDS] for r in ROWS]}
C.load = lambda: DATA

print("\n1. THE STAGE STRIP: DEFINED, COMPLETE, NO GUESSING")
st = H.stages(DATA)
got = {s["id"]: s["count"] for s in st["stages"]}
check("Each shipment in exactly one stage, by the written definitions",
      got == {"transit": 2, "arrived": 2, "customs": 1, "awaiting": 1, "done": 1}, got)
check("An open shipment no definition fits is counted as 'other', never forced into a stage", st["other"] == 1)
check("Stages + other = every shipment", sum(got.values()) + st["other"] == st["total"] == len(ROWS))
check("Pre-arrival work (a customs step before the ETA, no ATA) is still in transit", H._stage_of(ROWS[5], ASOF) == "transit")
check("On time / late is the export's own flag", st["on_time"] == 7 and st["late"] == 1)
check("Every stage carries its definition", all(s["definition"] for s in st["stages"]))
check("Open and past due count the whole export (a BU off the map still counts)",
      st["open"] == 7 and st["past_due"] == 2, (st["open"], st["past_due"]))
check("Booked is not a stage (no source holds bookings)", "booked" not in got)

print("\n2. THE LIST BEHIND EACH NUMBER")
for s in st["stages"]:
    lst = H.stage_list(s["id"], data=DATA)
    check("'{0}' lists exactly its {1}".format(s["label"], s["count"]), lst["total"] == s["count"], lst["total"])
check("A BU's list holds only that BU", {r["bu"] for r in H.stage_list("all", "GH", data=DATA)["rows"]} == {"GH"})
check("Past due = open and beyond the step's due date", H.stage_list("pastdue", data=DATA)["total"] == 2)
BIG = {"asof": ASOF, "fields": C.FIELDS,
       "rows": [[r[k] for k in C.FIELDS] for r in [row("EG", "Submit to BU", eta="2026-10-01", awb="B%d" % i) for i in range(2500)]]}
big = H.stage_list("all", data=BIG)
check("The list (and the CSV built from it) carries every row, not a first 2,000",
      big["total"] == 2500 and len(big["rows"]) == 2500, (big["total"], len(big["rows"])))

print("\n3. PLACES: A BU'S COUNTRY, NEVER A GUESS")
bus = {b["bu"]: b for b in H.bu_places(DATA)}
check("Egypt's BU sits in Egypt", "EG" in bus and 22 < bus["EG"]["lat"] < 32 and 25 < bus["EG"]["lon"] < 35, bus.get("EG"))
check("Ghana's BU sits in Ghana", "GH" in bus and 4 < bus["GH"]["lat"] < 11 and -4 < bus["GH"]["lon"] < 2, bus.get("GH"))
check("A BU with no known country is not placed", "JR" not in bus)
state = {"shipments": [
    {"reference": "R1", "state": "updated", "carrier": "DHL", "where": {"source": "DHL timeline",
     "last": {"date": "11/10/2026", "time": "3:30 PM", "text": "Departed", "pt": {"name": "Brussels", "cc": "BE", "lat": 50.85, "lon": 4.35}},
     "path": [{"name": "Brussels", "lat": 50.85, "lon": 4.35}], "destination": {"name": "Accra", "lat": 5.56, "lon": -0.2}}},
    {"reference": "R2", "state": "skipped", "carrier": "QATAR", "where": None},
    {"reference": "R3", "state": "failed", "carrier": "DHL", "where": {"last": {"text": "Unknown place", "pt": None}}},
]}
pl = H.run_places(state)
check("Only shipments a carrier placed are on the map", [p["ref"] for p in pl] == ["R1"], pl)
check("...with when and what the carrier reported", pl and pl[0]["when"] == "11/10/2026 3:30 PM" and pl[0]["event"] == "Departed")

print("\n4. ONLY FOR WHO MAY SEE IT")
viewer_no_clearance = lambda p: p in ("dashboard.view", "runs.view")
ov = H.overview(state, viewer_no_clearance)
check("Without clearance.view: no export counts, no BUs", "clearance" not in ov and "bus" not in ov)
check("...and no Clearance module in the navigation", not any(m["id"] == "clearance" for m in ov["modules"]))
ov_all = H.overview(state, lambda p: True)
check("With every permission: the export, the BUs, the run", ov_all["clearance"]["total"] == len(ROWS) and ov_all["bus"] and ov_all["run"]["shipments"] == 3)
off = [m for m in H.MODULES if m["status"] == "off"]
check("Modules with no data source are marked not connected, never live",
      {m["id"] for m in off} >= {"rates", "tender", "optimization"} and all("Not connected" in m.get("note", "") for m in off))

print("\n4b. A BU-RESTRICTED PERSON NEVER SEES ANOTHER BU'S NUMBERS")
GH = {"GH"}
mine = [r for r in ROWS if r["bu"] == "GH"]
sg = H.stages(DATA, allowed=GH)
check("Headline: total, open and past due count only their BU",
      sg["total"] == len(mine) and sg["open"] == sum(r["step"] != "Completed" for r in mine)
      and sg["past_due"] == sum(r["step"] != "Completed" and (r["overdue"] or 0) > 0 for r in mine),
      (sg["total"], sg["open"], sg["past_due"]))
check("Stages: every stage count is their BU's own", sg["stages"] and
      all(s["count"] == H.stage_list(s["id"], data=DATA, allowed=GH)["total"] for s in sg["stages"])
      and sum(s["count"] for s in sg["stages"]) + sg["other"] == len(mine))
check("On time / late: only their BU", sg["on_time"] + sg["late"] == len(mine))
for st_id in ("all", "late", "pastdue", "transit", "arrived", "customs", "awaiting", "done"):
    rows_ = H.stage_list(st_id, data=DATA, allowed=GH)["rows"]
    if any(r["bu"] != "GH" for r in rows_):
        check("List '{0}' (and so its CSV) holds only their BU".format(st_id), False, rows_)
        break
else:
    check("Every list (and so every CSV, built from the list) holds only their BU", True)
check("Asking for another BU's list returns nothing, not that BU",
      H.stage_list("all", "EG", data=DATA, allowed=GH)["total"] == 0)
check("Map: only their BU's pin, and no other BU in 'not on the map'",
      [b["bu"] for b in H.bu_places(DATA, GH)] == ["GH"] and H.bu_places(DATA, GH, want_unplaced=True)[1] == [])
ovg = H.overview(state, lambda p: True, GH)
check("Overview: their BU only, and no run figures or carrier places (run records carry no BU)",
      ovg["clearance"]["total"] == len(mine) and [b["bu"] for b in ovg["bus"]] == ["GH"]
      and "run" not in ovg and "places" not in ovg and ovg["run_hidden"] and ovg["scope"] == ["GH"])
check("Everyone else (no BU limit stored) still sees every BU, including the unplaced JR",
      H.overview(state, lambda p: True)["unplaced"] == [{"bu": "JR", "n": 1, "open": 1, "late": 0}])

print("\n4c. THROUGH THE REAL SERVER: ADMIN-ONLY PREVIEW, BU SCOPE ENFORCED")
import http.cookiejar  # noqa: E402
import socket  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
from controlplane import rbac  # noqa: E402
from dashboard import accounts as A, server as S  # noqa: E402
PW = "Hub-Test-2026-Secret!"
st_ = A.store()
st_.users.create("admin@mantrac.example", "Ama Admin", "ADMIN", password=PW)
st_.users.create("ghadmin@mantrac.example", "Kwame GH", "ADMIN", password=PW)
st_.users.create("oper@mantrac.example", "Kofi Operator", "OPERATOR", password=PW)
st_.users.create("view@mantrac.example", "Esi Viewer", "VIEWER", password=PW)
# No BU limit is stored on accounts yet; the test gives one account a limit
# the way a stored one would arrive (user["bus"]).
_orig_bus = S.Handler._acc_bus
S.Handler._acc_bus = lambda self: ({"GH"} if self.user and self.user.get("work_email") == "ghadmin@mantrac.example"
                                   else _orig_bus(self))
with socket.socket() as so:
    so.bind(("127.0.0.1", 0))
    PORT = so.getsockname()[1]
S.start(port=PORT, open_browser=False, host="127.0.0.1", access_key="hub-test-key-123456")
BASE = "http://127.0.0.1:{0}".format(PORT)
time.sleep(0.4)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _call(o, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Origin", BASE)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with o.open(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _as(email):
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _NoRedirect())
    _call(o, "POST", "/api/auth/login", {"email": email, "password": PW})
    return o


ADM, GHA, OPR, VW = (_as(e) for e in ("admin@mantrac.example", "ghadmin@mantrac.example",
                                     "oper@mantrac.example", "view@mantrac.example"))
check("Only ADMIN holds hub.preview", rbac.allowed({"role": "ADMIN", "active": 1}, "hub.preview")
      and not rbac.allowed({"role": "OPERATOR", "active": 1}, "hub.preview") and not rbac.allowed({"role": "VIEWER", "active": 1}, "hub.preview"))
for who, o in (("Operator", OPR), ("Viewer", VW)):
    codes = [_call(o, "GET", p)[0] for p in ("/hub", "/api/hub/overview", "/api/hub/list?stage=all")]
    check("{0}: /hub, its overview and its lists are refused while the hub is in review".format(who),
          all(c in (302, 401, 403) for c in codes) and 200 not in codes, codes)
check("Admin: /hub opens", _call(ADM, "GET", "/hub")[0] == 200)
a_ov = json.loads(_call(ADM, "GET", "/api/hub/overview")[1])
check("Admin (no BU limit): every BU, the run, and JR listed as not on the map",
      a_ov["clearance"]["total"] == len(ROWS) and "run" in a_ov and a_ov["unplaced"][0]["bu"] == "JR")
C.load = lambda: BIG
a_big = json.loads(_call(ADM, "GET", "/api/hub/list?stage=all")[1])
C.load = lambda: DATA
check("Through the server: a list of 2,500 shipments returns all 2,500 rows (the CSV is built from them)",
      a_big["total"] == 2500 and len(a_big["rows"]) == 2500, (a_big.get("total"), len(a_big.get("rows", []))))
g_ov = json.loads(_call(GHA, "GET", "/api/hub/overview")[1])
blob = json.dumps(g_ov)
check("BU-limited account: overview totals are its BU's only, no run, no other BU anywhere in the response",
      g_ov["clearance"]["total"] == len(mine) and g_ov["clearance"]["past_due"] == sg["past_due"]
      and "run" not in g_ov and "places" not in g_ov
      and all('"bu": "{0}"'.format(b) not in blob for b in ("EG", "JR")), blob[:300])
g_all = json.loads(_call(GHA, "GET", "/api/hub/list?stage=all")[1])
check("BU-limited account: the full list (the CSV's source) holds only its BU",
      g_all["total"] == len(mine) and {r["bu"] for r in g_all["rows"]} == {"GH"})
check("BU-limited account: asking for another BU's list is refused (403)",
      _call(GHA, "GET", "/api/hub/list?stage=all&bu=EG")[0] == 403)

print("\n5. THE SERVER")
src = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")
acc = (HERE / "dashboard" / "accounts.py").read_text(encoding="utf-8")
check("/hub, /api/hub/overview and /api/hub/list are served, read-only (GET only)",
      'route == "/hub"' in src and 'route == "/api/hub/overview"' in src and 'route == "/api/hub/list"' in src
      and "/api/hub" not in acc.split("WRITE_PERMISSIONS")[1])
check("The list needs clearance.view and refuses an unknown stage or BU",
      'self._acc_can("clearance.view")' in src.split('route == "/api/hub/list"')[1][:1500] and "unknown stage or BU" in src)
html = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")
check("Headline and BU pins use one metric (past due), and both say so",
      "count: b.late," in html and "past due of ${fmt(worst.open)} open" in html
      and "Number on a BU pin = open shipments past due" in html and 'countLabel: "past due"' in html)
check("The hub draws only what the server sends: no hard-coded counts",
      "/api/hub/overview" in html and "/api/hub/list" in html and "Illustration — the counts are real" in html)

print("\n5b. THE MAP AND THE SCENE: NOTHING FROM OUTSIDE, DATA AS TEXT ONLY")
import re as _re0
for name in ("map3d.js", "opsscene.js"):
    js = (HERE / "dashboard" / "static" / "hub" / name).read_text(encoding="utf-8")
    ext = [u for u in _re0.findall(r"https?://[^\s'\"`)]+", js) if u != "http://www.w3.org/2000/svg"]
    check("{0}: no external URL, font, tile or library".format(name),
          not ext and "@import" not in js and "XMLHttpRequest" not in js and "import(" not in js, ext)
    check("{0}: innerHTML only for its own fixed markup, never for data".format(name),
          all("${" not in ln for ln in js.splitlines() if "innerHTML" in ln))
mj = (HERE / "dashboard" / "static" / "hub" / "map3d.js").read_text(encoding="utf-8")
check("Map: if the browser cannot draw, it says so instead of breaking",
      "This browser cannot draw the map" in mj and "Map outline unavailable" in mj)
oj = (HERE / "dashboard" / "static" / "hub" / "opsscene.js").read_text(encoding="utf-8")
check("Scene: animation stops off-screen, in a hidden tab and with reduced motion",
      "IntersectionObserver" in oj and "visibilitychange" in oj and "prefers-reduced-motion" in oj)

print("\n6. ADDED, NOTHING REPLACED")
import re as _re
cl = (HERE / "dashboard" / "static" / "clearance.html").read_text(encoding="utf-8")
tpl = lambda t: _re.search(r'<template id="axTpl">.*?</template>', t, _re.S).group(0)
check("ATLAS keeps its own character as its icon in the hub, byte for byte the existing one", tpl(html) == tpl(cl))
check("'/' still serves the existing dashboard and '/clearance' the Clearance page; nothing redirects to the hub",
      'if route == "/":\n            self._send_file(STATIC_DIR / "index.html")' in src
      and 'if route == "/clearance":\n            self._send_file(STATIC_DIR / "clearance.html")' in src
      and "Location\", \"/hub" not in src)
check("ATLAS is in the hub's navigation", any(m["id"] == "atlas" for m in H.MODULES))

print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
