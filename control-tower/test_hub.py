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
os.environ.update({"ATA_RUNTIME_DIR": os.path.join(TMP, "rt"), "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox")})
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

print("\n5. THE SERVER")
src = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")
acc = (HERE / "dashboard" / "accounts.py").read_text(encoding="utf-8")
check("/hub, /api/hub/overview and /api/hub/list are served, read-only (GET only)",
      'route == "/hub"' in src and 'route == "/api/hub/overview"' in src and 'route == "/api/hub/list"' in src
      and "/api/hub" not in acc.split("WRITE_PERMISSIONS")[1])
check("The list needs clearance.view and refuses an unknown stage or BU",
      '("/api/hub/list", "clearance.view")' in acc and "unknown stage or BU" in src)
html = (HERE / "dashboard" / "static" / "hub.html").read_text(encoding="utf-8")
check("The hub draws only what the server sends: no hard-coded counts",
      "/api/hub/overview" in html and "/api/hub/list" in html and "Illustration — the counts are real" in html)

print("\n{0} passed, {1} failed".format(len(PASSED), len(FAILED)))
sys.exit(1 if FAILED else 0)
