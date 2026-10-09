"""
The ETA automation's real main() loop with a stand-in browser, Hub and
carrier — the same harness the ETA suites use — run as its own process.
Prints one JSON line: the run's status, counters and what it wrote.

Used to prove the PO workflow cannot affect the shipment workflow: a PO job
fails beside it, and this run must finish exactly as it would alone.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_INTEL_DIR", tempfile.mkdtemp(prefix="ct_eta_fake_"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

import update_eta as A                                          # noqa: E402


class _Page(object):
    url = "about:blank"

    def is_closed(self):
        return False

    def wait_for_timeout(self, ms):
        pass


class _Context(object):
    pages = []

    def new_page(self):
        return _Page()


class _Browser(object):
    def new_context(self, **kw):
        return _Context()

    def close(self):
        pass


class _PW(object):
    class chromium(object):
        @staticmethod
        def launch(**kw):
            return _Browser()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


LIST = [{"bol_awb": ref, "carrier": "DHL Express", "provider": "DHL", "current_eta": "",
         "table_page": 1} for ref in ("E1", "E2", "E3")]
WRITES = []


def fake_ensure(page, view, number):
    if number > 1:
        raise A.SkipShipment("no more pages")


def fake_write(page, shipment, result):
    WRITES.append(shipment["bol_awb"])
    A.tower.view_updated("COE", "ETA", result["eta"], verified=True)
    return {"coe": "COE ETA updated with {0} and saved".format(result["eta"]), "bu": ""}


A.sync_playwright = lambda: _PW()
A.load_credentials = lambda: ("u", "p")
A.login_internal = lambda *a, **k: None
A.ensure_filtered_page = fake_ensure
A.collect_supported_shipments = lambda page, n: [dict(x) for x in LIST]
A.get_provider_result = lambda pages, s: {"provider": "DHL", "tracking_status": "Arrived",
                                          "eta": "02/10/2026", "ata": None}
A.update_internal_shipment = fake_write
A.save_result = lambda *a, **k: None
A.wait_between_shipments = lambda: None
A.take_screenshot = lambda *a, **k: None
A.write_log = lambda *a, **k: None
A.DASHBOARD_ENABLED = False
A.ML_AVAILABLE = False
A.PAUSE_ON_FATAL_ERROR = False
A.HUMAN_QUEUE_ON = False
A.main()
snap = A.tower.snapshot()
blob = json.dumps(snap)
print(json.dumps({"status": snap["run"]["status"], "counters": snap["counters"],
                  "writes": WRITES,
                  # A PO job id or PO event anywhere in the ETA run's state?
                  "po_in_eta_state": '"po_id"' in blob or "po-20" in blob
                  or "BILL_ENTRY" in blob or "EHUB_RECORD" in blob}))
