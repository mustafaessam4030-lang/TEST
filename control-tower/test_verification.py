"""
Real eHub verification — who may call something REAL, and on what evidence.

    TEST · SIMULATED · REAL OBSERVED · REAL VERIFIED · BLOCKED

What is pinned here:
  1. classify(): the level comes from the evidence and the channel, never
     from the level a report claims; nothing off the worker channel is REAL.
  2. The worker command's BLOCKED paths, run for real in this container
     (no credentials, a browser that cannot start, a host that does not
     resolve) — each with its stage, category and reason. Everything this
     suite produces is labelled TEST.
  3. The control plane: only an authenticated worker can report; the level
     is recomputed; the worker id is the token's; secrets are dropped; the
     health bar and /api/observations show only worker reports; the control
     plane makes no connection to eHub while doing any of it.
  4. The same browser launch as a run; OCEAN_WRITE is never switched on
     by the verification.

This suite never touches the real eHub. The real check is the worker's:
    python -m worker.verify ehub          python -m worker.verify eta --reference X
"""

import copy
import json
import os
import re
import socket
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_verify_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

from intelligence import verification as V                  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(str(detail)[:300]) if detail and not condition
                                 else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


HOST = V.ehub_host()
OBSERVED = {
    "kind": "ehub-connection", "run_id": "verify-20261006-120000-abc123",
    "ehub_host": HOST, "result": "OBSERVED",
    "browser": {"real": True, "engine": "chromium", "channel": "msedge", "version": "129.0"},
    "page": {"host": HOST, "http_status": 200},
    "shipment_list": {"rendered": True, "columns": ["bol_awb", "status"]},
    "shipment": {"reference": "MEDUAHP69377", "status": "Under Clearance"},
}
ETA = dict(copy.deepcopy(OBSERVED), kind="eta-write", result="COMPLETED",
           carrier_result={"provider": "MSC", "eta": "10/11/2026", "eta_source": "ETA",
                           "identity_checked": True},
           writes=[{"field": "ETA", "value": "10/11/2026", "page_host": HOST}],
           read_backs=[{"field": "ETA", "written": "10/11/2026", "read_back": "10/11/2026",
                        "verdict": "MATCH", "page_host": HOST}],
           shipment_outcome="updated")


def level(obs, channel="worker"):
    return V.classify(obs, channel)[0]


rule("1. classify(): THE EVIDENCE DECIDES, AND ONLY ON THE WORKER CHANNEL")
check("The five levels, in this vocabulary",
      V.LEVELS == ("TEST", "SIMULATED", "REAL OBSERVED", "REAL VERIFIED", "BLOCKED"))
check("Complete list + shipment + status, on the worker channel -> REAL OBSERVED",
      level(OBSERVED) == "REAL OBSERVED", V.classify(OBSERVED, "worker"))
check("The same evidence from the cloud -> not REAL (BLOCKED: not on the worker)",
      level(OBSERVED, "cloud") == "BLOCKED"
      and "not observed on the Windows worker" in V.classify(OBSERVED, "cloud")[1][0])
check("...or from this Linux container running the command -> not REAL",
      level(OBSERVED, "local:linux") == "BLOCKED")
check("Anything the test suite produces -> TEST", level(OBSERVED, "test") == "TEST"
      and level(dict(OBSERVED, origin="test")) == "TEST")
check("A stand-in -> SIMULATED, whatever else it says",
      level(dict(OBSERVED, simulated=True)) == "SIMULATED")
check("A claimed level is ignored", level(dict(OBSERVED, level_here="REAL VERIFIED",
                                               level="REAL VERIFIED")) == "REAL OBSERVED")
check("No real browser -> BLOCKED", level(dict(OBSERVED, browser={"real": False})) == "BLOCKED")
check("Another host -> BLOCKED", level(dict(OBSERVED, ehub_host="127.0.0.1",
                                            page={"host": "127.0.0.1"})) == "BLOCKED")
check("The list did not render -> BLOCKED",
      level(dict(OBSERVED, shipment_list={"rendered": False})) == "BLOCKED")
check("No shipment read -> BLOCKED", level(dict(OBSERVED, shipment={})) == "BLOCKED")
cleared = V.classify(dict(OBSERVED, shipment={"reference": "MEDUAHP69377", "status": "Cleared"}),
                     "worker")
check("A shipment that is not Under Clearance -> BLOCKED, with the status seen",
      cleared[0] == "BLOCKED" and "'Cleared'" in cleared[1][0], cleared)
check("A worker that could not finish -> BLOCKED with its reason",
      V.classify(dict(OBSERVED, result="BLOCKED", blocked_reason="NETWORK at sign_in: x"),
                 "worker") == ("BLOCKED", ["NETWORK at sign_in: x"]))
check("Write + equal read-back on eHub + SUCCESS -> REAL VERIFIED", level(ETA) == "REAL VERIFIED",
      V.classify(ETA, "worker"))
mismatch = copy.deepcopy(ETA)
mismatch["read_backs"][0].update(read_back=None, verdict="MISMATCH",
                                 detail="the Hub holds '05/11/2026', not '10/11/2026'")
mismatch["shipment_outcome"] = "failed"
lv, why = V.classify(mismatch, "worker")
check("Read back different -> REAL OBSERVED, NOT VERIFIED, saying what eHub held",
      lv == "REAL OBSERVED" and any("05/11/2026" in w for w in why), (lv, why))
unread = copy.deepcopy(ETA)
unread["read_backs"][0].update(read_back=None, verdict="NOT READ")
check("Read-back not performed -> not VERIFIED", level(unread) == "REAL OBSERVED")
check("No read-back at all -> not VERIFIED", level(dict(ETA, read_backs=[])) == "REAL OBSERVED")
check("Identity not confirmed on the carrier page -> not VERIFIED",
      level(dict(ETA, carrier_result=dict(ETA["carrier_result"], identity_checked=False)))
      != "REAL VERIFIED")
check("Nothing written -> BLOCKED (the proof could not be made)",
      level(dict(ETA, writes=[], read_backs=[], shipment_outcome="skipped")) == "BLOCKED")
check("The ETA proof from the cloud -> never VERIFIED", level(ETA, "cloud") == "BLOCKED")
check("The log line says REAL VERIFICATION BLOCKED for a block",
      V.line("BLOCKED", OBSERVED, ["x"]).startswith("[VERIFY] REAL VERIFICATION BLOCKED"))


rule("2. THE WORKER COMMAND'S BLOCKED PATHS, RUN FOR REAL HERE")
import update_eta as A                                       # noqa: E402
from worker import verify as W                              # noqa: E402

A.write_log = lambda *a, **k: None
real_load = A.load_credentials


def no_file():
    raise Exception("Missing credentials file: " + str(A.CREDENTIALS_FILE))


A.load_credentials = no_file
o = W.observe_ehub()
check("No credentials file -> BLOCKED, AUTHENTICATION at credentials",
      o["result"] == "BLOCKED" and o["blocked_stage"] == "credentials"
      and o["blocked_category"] == "AUTHENTICATION", o.get("blocked_reason"))
A.load_credentials = real_load
o = W.observe_ehub(credentials=("verify.user", "S3cret-Never-Shown"),
                   launch={"headless": True, "executable_path": "/nonexistent/msedge"})
check("A browser that cannot start -> BLOCKED, BROWSER at browser",
      o["result"] == "BLOCKED" and o["blocked_category"] == "BROWSER", o.get("blocked_reason"))
check("...and the password is nowhere in the observation", "S3cret-Never-Shown" not in json.dumps(o))
chromium = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")) \
    if Path("/opt/pw-browsers").is_dir() else []
if chromium:
    saved_url, saved_host = A.INTERNAL_URL, os.environ.get("EHUB_HOST")
    A.INTERNAL_URL = "https://ehub-verify.invalid/WorkFlow/ShipmentTracking/ShipmentList.aspx"
    os.environ["EHUB_HOST"] = "ehub-verify.invalid"
    try:
        o = W.observe_ehub(credentials=("verify.user", "S3cret-Never-Shown"),
                           launch={"headless": True, "executable_path": str(chromium[0])})
    finally:
        A.INTERNAL_URL = saved_url
        if saved_host is None:
            os.environ.pop("EHUB_HOST", None)
        else:
            os.environ["EHUB_HOST"] = saved_host
    check("A host that does not resolve -> BLOCKED, NETWORK at sign_in, from a real browser",
          o["result"] == "BLOCKED" and o["blocked_category"] == "NETWORK"
          and o["blocked_stage"] == "sign_in" and o["browser"].get("real") is True,
          o.get("blocked_reason"))
    check("...with Python's own DNS view attached, for the reason only",
          "dns_error" in json.dumps(o["stages"][-1].get("network_probe") or {}))
    check("...and the browser's credentials are not in it", "S3cret-Never-Shown" not in json.dumps(o))
else:
    print("  SKIP  NETWORK path — no bundled Chromium on this machine (Windows uses Edge)")
saved = A.INTERNAL_URL
A.INTERNAL_URL = "http://127.0.0.1:9/list"
o = W.observe_ehub(credentials=("u", "p"))
A.INTERNAL_URL = saved
check("An eHub address on another host -> BLOCKED, CONFIGURATION, nothing opened",
      o["blocked_category"] == "CONFIGURATION" and not o["browser"].get("real"), o.get("blocked_reason"))
check("Off Windows the command's own channel is not the worker's",
      (W.channel() == "worker") == (os.name == "nt"))
check("Every observation here, classified as the suite's -> TEST",
      level(o, "test") == "TEST")
check("pick(): the named shipment, else the first Under Clearance",
      W.pick([{"reference": "A1", "status": "Cleared"}, {"reference": "B2",
                                                         "status": " Under  Clearance "}])["reference"]
      == "B2" and W.pick([{"reference": "MEDU AHP-69377", "status": "Cleared"}],
                         "MEDUAHP69377")["reference"] == "MEDU AHP-69377"
      and W.pick([{"reference": "A1", "status": "Cleared"}]) is None)


rule("3. THE CONTROL PLANE: WORKER REPORTS ONLY, LEVEL RECOMPUTED, NO eHub CONTACT")
import urllib.error                                          # noqa: E402
import urllib.request                                        # noqa: E402
from controlplane.app import App, make_server                # noqa: E402
from controlplane.config import Settings                     # noqa: E402
from fixtures.platform_client import Client                  # noqa: E402

LOOKUPS = []
_real_gai = socket.getaddrinfo


def watched_gai(host, *a, **k):
    LOOKUPS.append(str(host))
    return _real_gai(host, *a, **k)


socket.getaddrinfo = watched_gai
app = App(Settings({"DATABASE_URL": "sqlite:///{0}/cp.db".format(WORK),
                    "ATA_INSECURE_COOKIES": "1", "ATA_WORKER_OFFLINE_S": "30"}))
httpd = make_server(app, "127.0.0.1", 0)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(httpd.server_address[1])
admin = app.users.create("ada.admin@mantrac.com", "Ada Admin", "ADMIN", password="Tower-Key-2026!")[0]
viewer = app.users.create("vera.view@mantrac.com", "Vera View", "VIEWER", password="Read-Only-2026!")[0]
wid, token = app.orch.add_worker("ATA-WORKER-01")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def post(path, body, bearer=None):
    h = {"Content-Type": "application/json"}
    if bearer:
        h["Authorization"] = "Bearer " + bearer
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST", headers=h)
    try:
        r = opener.open(req, timeout=20)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()


s, _d = post("/worker/v1/observations", OBSERVED)
check("No worker token -> 401, nothing stored", s == 401
      and not app.db.all("SELECT * FROM observations"))
s, _d = post("/worker/v1/observations", OBSERVED, "w_nothere.forged")
check("A forged token -> 401", s == 401)
s, d = post("/worker/v1/observations", dict(OBSERVED, level_here="REAL VERIFIED",
                                            environment={"worker_id": "w_someone_else",
                                                         "password": "S3cret-Never-Shown"},
                                            credentials={"username": "x", "password": "y"}), token)
check("A worker's complete list observation -> REAL OBSERVED (not the REAL VERIFIED it claimed)",
      s == 200 and d["level"] == "REAL OBSERVED", d)
stored = app.observations.get(d["observation_id"])
check("The worker id is the token's, not the payload's", stored["worker_id"] == wid
      and stored["environment"]["authenticated_worker"] == wid, stored.get("environment"))
check("The claimed level is kept beside, for the audit, never as the level",
      stored["claimed_level"] == "REAL VERIFIED" and stored["level"] == "REAL OBSERVED")
check("Anything named like a secret is dropped", "S3cret-Never-Shown" not in json.dumps(stored)
      and "credentials" not in stored.get("evidence", {}))
check("run_id, timestamp, environment, host, navigation, shipment, status, real browser — all kept",
      stored["run_id"] == OBSERVED["run_id"] and stored["received_at"]
      and stored["ehub_host"] == HOST and stored["shipment_reference"] == "MEDUAHP69377"
      and stored["shipment_status"] == "Under Clearance" and stored["real_browser"] is True
      and "page" in stored and "navigation" in stored, stored)
s, d = post("/worker/v1/observations", dict(OBSERVED, browser={"real": False},
                                            level_here="REAL OBSERVED"), token)
check("No real browser in the evidence -> BLOCKED, whatever was claimed", d["level"] == "BLOCKED", d)
s, d = post("/worker/v1/observations", mismatch, token)
check("An ETA write whose read-back differs -> REAL OBSERVED, NOT VERIFIED",
      d["level"] == "REAL OBSERVED" and any("NOT VERIFIED" in r for r in d["reasons"]), d)
s, d = post("/worker/v1/observations", ETA, token)
check("An ETA write read back equal on eHub -> REAL VERIFIED", d["level"] == "REAL VERIFIED", d)
s, d = post("/worker/v1/observations", dict(OBSERVED, origin="test"), token)
check("A worker sending a test observation -> TEST", d["level"] == "TEST", d)
blocked_obs = {"kind": "ehub-connection", "run_id": "verify-x", "ehub_host": HOST, "result": "BLOCKED",
               "blocked_reason": "NETWORK at sign_in: the eHub host name does not resolve"}
s, d = post("/worker/v1/observations", blocked_obs, token)
check("A worker that could not reach eHub -> BLOCKED, with its reason",
      d["level"] == "BLOCKED" and "does not resolve" in d["reasons"][0], d)

anon = Client(BASE)
s, _d, _h = anon.get("/api/observations")
check("/api/observations needs a signed-in user", s == 401)
vc = Client(BASE)
vc.login("vera.view@mantrac.com", "Read-Only-2026!")
s, d, _h = vc.get("/api/observations")
check("A viewer (health.view) sees the reports, newest first, with their levels",
      s == 200 and [o["level"] for o in d["observations"]][:2] == ["BLOCKED", "TEST"]
      and d["levels"] == list(V.LEVELS), d.get("observations", [])[:2])
s, h, _h = vc.get("/api/health")
check("The health bar's eHub entry is the latest worker report",
      s == 200 and (h.get("ehub") or {}).get("level") == "BLOCKED", h.get("ehub"))
audit = app.db.all("SELECT * FROM audit WHERE action = 'WORKER_OBSERVATION'")
check("Every report is audited", len(audit) >= 6, len(audit))
check("The control plane made no connection to eHub while receiving, classifying and showing",
      not [x for x in LOOKUPS if HOST in x or "mantracgroup" in x], LOOKUPS)
socket.getaddrinfo = _real_gai
fresh = App(Settings({"DATABASE_URL": "sqlite:///{0}/cp2.db".format(WORK)}))
check("Before any worker report, eHub is not shown as verified", fresh.health()["ehub"] is None)
CP_SRC = "".join(p.read_text(encoding="utf-8") for p in (HERE / "controlplane").glob("*.py"))
check("No control-plane code names the eHub host or fetches it",
      "logisticshub" not in CP_SRC and "mantracgroup" not in CP_SRC)
UI = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
check("The dashboard draws all five levels, and 'Not verified by the worker' before a report",
      all(x in UI for x in ("'TEST'", "'SIMULATED'", "'REAL OBSERVED'", "'REAL VERIFIED'",
                            "REAL VERIFICATION BLOCKED", "Not verified by the worker")))
httpd.shutdown()


rule("4. SAME SESSION AS A RUN; OCEAN_WRITE LEFT ALONE")
ETA_SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("main() launches its browser with hub_launch_options()",
      "playwright.chromium.launch(**hub_launch_options())" in ETA_SRC
      and "browser.new_context(**hub_context_options(username, password))" in ETA_SRC)
opts = A.hub_launch_options()
check("...headed Edge, as the operator sees it", opts["channel"] == "msedge"
      and opts["headless"] is False)
VSRC = (HERE / "worker" / "verify.py").read_text(encoding="utf-8")
check("The verification launches with the same options and signs in with the run's login",
      "A.hub_launch_options()" in VSRC and "A.login_internal(page, *credentials)" in VSRC
      and "A.hub_context_options(*credentials)" in VSRC)
check("The verification never sets OCEAN_WRITE, DRY_RUN or VERIFY_AFTER_SAVE",
      not re.search(r"A\.(OCEAN_WRITE|DRY_RUN|VERIFY_AFTER_SAVE)\s*=", VSRC))
check("...and refuses a dry run as proof", "this is a dry run" in VSRC)
check("No credential is printed: only presence, never the values",
      "values not read out" in VSRC and not re.search(r"print\([^)]*credentials", VSRC))

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
