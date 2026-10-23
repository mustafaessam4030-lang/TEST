"""
END TO END - a carrier ON HOLD (CMA CGM, production configuration) is never
looked up: main() runs its real shipment loop in a real browser, with the
production hold as shipped, and CMA CGM shipments must produce zero browser
requests to the carrier, zero HTTP/API calls, zero lookups, zero Hub writes,
and a deferred-queue record for a person. A non-held carrier in the same run
is still looked up (the hold is not a general stop).

Stand-ins (labelled): the carrier site (SIMULATED, 127.0.0.1, counts every
request), the eHub boundary (sign-in, the shipment list, the write - which
only records that it was called), Chromium instead of Edge.
"""
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_hold_e2e_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ["ATLAS_DATA_ORIGIN"] = "test"
for k in ("CARRIER_HOLD", "CARRIER_HOLD_RELEASE"):      # the shipped configuration
    os.environ.pop(k, None)

import update_eta as A                                    # noqa: E402
import carrier_pacing as CP                               # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if ok else "FAIL", name, "  ({0})".format(detail) if not ok else ""))


PORT = int(os.environ.get("HOLD_E2E_PORT", "9764"))
BASE = "http://127.0.0.1:{0}".format(PORT)
REQUESTS = []


class Carrier(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        REQUESTS.append(self.path)
        data = b"<html><body><h1>Tracking</h1>" + b"x" * 300 + b"</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


server = ThreadingHTTPServer(("127.0.0.1", PORT), Carrier)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()

HELD_REFS = ("CMAU8800001", "CMAU8800002")
LOG, WRITES, LOOKUPS, API = [], [], [], []
A.write_log = lambda m, *a, **k: LOG.append(str(m))
chromium = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
_launch = A.hub_launch_options
A.hub_launch_options = lambda: {k: v for k, v in dict(
    _launch(), headless=True, executable_path=str(chromium[0]) if chromium else None).items()
    if k != "channel" and v is not None}
A.load_credentials = lambda: ("hub.user", "never-shown")
A.login_internal = lambda *a, **k: None
A.ensure_filtered_page = lambda page, view, number: (_ for _ in ()).throw(
    A.SkipShipment("no more pages")) if number > 1 else None
A.collect_supported_shipments = lambda page, n: [
    {"bol_awb": r, "carrier": "CMA CGM", "provider": "CMA_CGM", "current_eta": "05/11/2026",
     "table_page": 1} for r in HELD_REFS] + [
    {"bol_awb": "MEDU0000001", "carrier": "MSC", "provider": "MSC", "current_eta": "05/11/2026",
     "table_page": 1}]
A.LOG_FOLDER = WORK
A.carrier_pacing.MIN_GAP["ocean"] = 0
A.PORTALS["CMA_CGM"] = dict(A.PORTALS["CMA_CGM"], urls=[BASE + "/cma"], wait=2)
_real_lookup = A.get_provider_result


def counted_lookup(pages, shipment):
    LOOKUPS.append(shipment["bol_awb"])
    if shipment["provider"] == "MSC":
        return {"provider": "MSC", "tracking_status": "Estimated arrival", "eta": "12/11/2026",
                "ata": None}
    return _real_lookup(pages, shipment)


A.get_provider_result = counted_lookup
import dhl_api                                           # noqa: E402
dhl_api.fetch = lambda *a, **k: API.append(a) or {}


def hub_write(page, shipment, result):
    WRITES.append(shipment["bol_awb"])
    return {"coe": "COE ETA updated with {0} and saved".format(result.get("eta")), "bu": ""}


A.update_internal_shipment = hub_write
A.save_result = lambda *a, **k: None
A.wait_between_shipments = lambda: None
A.DASHBOARD_ENABLED, A.ML_AVAILABLE, A.PAUSE_ON_FATAL_ERROR = False, False, False

print("Production configuration in this test:", CP.hold_report())
A.main()
server.shutdown()

check("Production hold is enabled for CMA CGM", "CMA_CGM" in CP.held_carriers())
check("Zero lookups for held CMA CGM shipments",
      not [r for r in LOOKUPS if r in HELD_REFS], LOOKUPS)
check("Zero browser/HTTP requests reached the CMA CGM site", REQUESTS == [], REQUESTS)
check("Zero carrier API calls", API == [], API)
check("Zero Hub writes for CMA CGM", not [w for w in WRITES if w in HELD_REFS], WRITES)
pending = {r["bol_awb"]: r for r in CP.DeferredQueue(WORK / "carrier_deferred.json").pending()}
check("Each held shipment is in the deferred queue for a person, reason CARRIER_ON_HOLD",
      all(pending.get(r, {}).get("reason") == CP.REASON_HELD and pending[r].get("run_id") == A.RUN_ID
          for r in HELD_REFS), pending)
check("A non-held carrier in the same run is still looked up and written",
      "MEDU0000001" in LOOKUPS and WRITES == ["MEDU0000001"], (LOOKUPS, WRITES))
check("The run log states the hold in effect",
      any("Carrier hold in effect: CMA_CGM" in m for m in LOG))
check("Nothing about the hold was released", not any("RELEASED" in m for m in LOG))

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
