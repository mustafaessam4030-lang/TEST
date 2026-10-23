"""
Run limits through the automation's own main() loop: every started shipment
counts (review, parked, failed, partial included) and is checked BEFORE it
starts; MAX_HUB_WRITES counts each saved field at the save, before its
read-back. The carrier and the Hub are stand-ins (in memory); a real Chromium
is launched as main() does. No carrier site, no Hub, no production state.

Each scenario runs in its own process (main() keeps run-level state).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


CHILD = r'''
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, sys.argv[1])
WORK = Path(tempfile.mkdtemp(prefix="ct_limits_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ["ATLAS_DATA_ORIGIN"] = "test"
import update_eta as A
scenario = sys.argv[2]
cfg = json.loads(sys.argv[3])
A.MAX_RECORDS_PER_RUN = cfg["max_shipments"]
A.MAX_HUB_WRITES = cfg["max_writes"]
A.LOG_FOLDER = WORK
A.write_log = lambda m, *a, **k: None
chromium = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
_launch = A.hub_launch_options
A.hub_launch_options = lambda: {k: v for k, v in dict(_launch(), headless=True,
    executable_path=str(chromium[0]) if chromium else None).items()
    if k != "channel" and v is not None}
A.load_credentials = lambda: ("hub.user", "never-shown")
A.login_internal = lambda *a, **k: None
A.ensure_filtered_page = lambda page, view, number: (_ for _ in ()).throw(
    A.SkipShipment("no more pages")) if number > 1 else None
refs = ["T{0:03d}".format(i) for i in range(cfg["shipments"])]
A.collect_supported_shipments = lambda page, n: [
    {"bol_awb": r, "carrier": "MSC", "provider": "MSC", "current_eta": "", "table_page": 1}
    for r in refs]
A.carrier_pacing.MIN_GAP["ocean"] = A.carrier_pacing.MIN_GAP["other"] = 0
A.DASHBOARD_ENABLED, A.ML_AVAILABLE, A.PAUSE_ON_FATAL_ERROR = False, False, False
A.save_result = lambda *a, **k: None
A.wait_between_shipments = lambda: None
A.take_screenshot = lambda *a, **k: None
A.ATLAS_DEFERRED_RETRY = False
LOOKUPS, FILLS, SAVES = [], [], []
HUB = {}
outcomes = cfg["outcomes"]

def lookup(pages, shipment):
    ref = shipment["bol_awb"]
    LOOKUPS.append(ref)
    kind = outcomes[(len(LOOKUPS) - 1) % len(outcomes)]
    if kind == "review":
        return {"provider": "MSC", "eta": None, "ata": None, "review": ["unclear (test)"]}
    if kind == "parked":
        raise A.CaptchaRequired(ref, "MSC", reason="queued")
    if kind == "failed":
        raise RuntimeError("carrier page broke (test)")
    return {"provider": "MSC", "tracking_status": "Estimated arrival", "eta": "20/12/2026",
            "eta_source": "ETA", "ata": None, "read_back_required": True}

A.get_provider_result = lookup
A.click_manage_in_view = lambda page, view, ref, table_page: 1
A.select_shipment_info_tab = lambda page, view, field: True
A.read_current_date_field = lambda page, field: HUB.get(field, "")
A.fill_date_field = lambda page, field, value: FILLS.append((field, value))
A.save_manage_page = lambda page: SAVES.append(len(FILLS))
def read_back(page, shipment, view, field, expected):
    if cfg.get("read_back_fails"):
        return False, "the Hub holds something else (test)"
    return True, "the Hub holds {0}".format(expected)
A.verify_saved_date = read_back
try:
    A.main()
except SystemExit:
    pass
print("RESULT " + json.dumps({"lookups": LOOKUPS, "fills": FILLS, "saves": len(SAVES),
                              "started": A.RUN_LIMITS.started, "writes": A.RUN_LIMITS.writes}))
'''


def run(scenario, **cfg):
    path = Path(tempfile.mkdtemp(prefix="ct_limits_child_")) / "child.py"
    path.write_text(CHILD, encoding="utf-8")
    out = subprocess.run([sys.executable, str(path), str(HERE), scenario, json.dumps(cfg)],
                         capture_output=True, text=True, timeout=300,
                         env=dict(os.environ, CARRIER_HOLD="", CARRIER_HOLD_RELEASE=""))
    line = [l for l in out.stdout.splitlines() if l.startswith("RESULT ")]
    if not line:
        print(out.stdout[-1500:], out.stderr[-2500:])
        return {}
    return json.loads(line[-1][7:])


print("1. Shipments sent to REVIEW count toward the shipment limit")
r = run("review", shipments=6, max_shipments=3, max_writes=100, outcomes=["review"])
check("Only 3 of 6 review shipments were looked up", len(r.get("lookups", [])) == 3, r)
check("...and nothing was written", r.get("fills") == [] and r.get("saves") == 0, r)

print("2. PARKED shipments (human verification queued) count too")
r = run("parked", shipments=6, max_shipments=2, max_writes=100, outcomes=["parked"])
check("Only 2 of 6 parked shipments were looked up", len(r.get("lookups", [])) == 2, r)

print("3. Mixed outcomes: every started shipment counts, checked before each")
r = run("mixed", shipments=8, max_shipments=4, max_writes=100,
        outcomes=["review", "parked", "failed", "eta"])
check("Exactly 4 started (review, parked, failed, written), the 5th never looked up",
      r.get("started") == 4 and len(r.get("lookups", [])) == 4, r)
check("The one written shipment wrote one field", r.get("saves") == 1, r)

print("4. MAX_HUB_WRITES: counted at the save, before the read-back")
r = run("writes", shipments=5, max_shipments=50, max_writes=1, outcomes=["eta"],
        read_back_fails=True)
check("A write whose read-back FAILED still used the budget", r.get("writes") == 1, r)
check("No second field was filled or saved after the cap",
      len(r.get("fills", [])) == 1 and r.get("saves") == 1, r)
check("No further shipment was even looked up once the cap was reached",
      len(r.get("lookups", [])) == 1, r)

print("5. The write cap holds on successful writes too")
r = run("writes_ok", shipments=5, max_shipments=50, max_writes=2, outcomes=["eta"])
check("Exactly 2 fields saved, 2 shipments looked up", r.get("saves") == 2
      and len(r.get("lookups", [])) == 2, r)

print("6. A limit of 0 allows nothing")
r = run("zero", shipments=3, max_shipments=0, max_writes=0, outcomes=["eta"])
check("No lookup, no write", r.get("lookups") == [] and r.get("fills") == [], r)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
