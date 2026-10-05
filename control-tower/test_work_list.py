"""
ATLAS's work list — every failure gets a plan, and the run never stops.

  1. Each failure lands on the work list with what happens to it:
     RETRY_THIS_RUN (transient, nothing written), NEXT_RUN, NEEDS_PERSON,
     NEEDS_DECISION. Decided by a fixed rule from the evidence.
  2. An item is resolved ONLY by verification: a later outcome written and
     read back. An unverified success leaves it open. Nobody's opinion closes
     it; a person can close it, by name.
  3. The real main(): a transient failure is retried ONCE, after every other
     shipment; the run never waits for it or stops for it; a policy block or
     an unknown failure is not retried; a second failure is not retried
     again; the counters count each shipment once.
  4. ATLAS says what the plan is, in chat and on its page.

Run:  python test_work_list.py
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_worklist_intel_")
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

import update_eta as A                                          # noqa: E402
from dashboard import assistant                                 # noqa: E402
from dashboard.bridge import ControlTowerState, INTEL_EVENTS    # noqa: E402
from intelligence import backlog, events, failures as F, store  # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(str(detail)[:500]) if detail and not condition
                                 else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


def finish(tower, ref, result, error="", outcome=None, failure=None):
    tower.shipment_finished(ref, result, error, outcome=outcome, failure=failure)


POLICY = {"category": "CARRIER_POLICY_BLOCK", "stage": "hub_write", "operation": "Hub write",
          "detail": "The Hub write was not performed: writing for this carrier is switched off "
                    "by configuration.",
          "cause": {"kind": "configuration", "name": "OCEAN_WRITE", "value": "off",
                    "decided_by": "the run's own write check"}}

# ═════════════════════════════════════════════════════════════════════════
rule("1. EVERY FAILURE GETS A PLAN — DECIDED BY RULE, FROM THE EVIDENCE")
# ═════════════════════════════════════════════════════════════════════════
t = ControlTowerState()
t.attach_intelligence(events)
t.run_started(run_id="20261005-200000-work01")
cases = [
    ("N1", "AFKL", "Air France KLM Cargo", "FAILED", "Page.goto: net::ERR_CONNECTION_RESET",
     A.TEMPORARY_WEBSITE_ISSUE, None, "RETRY_THIS_RUN", "RETRY_QUEUED"),
    ("T2", "DHL", "DHL Express", "FAILED", "Timeout 30000ms exceeded", A.TIMEOUT, None,
     "RETRY_THIS_RUN", "RETRY_QUEUED"),
    ("M3", "MSC", "MSC", "SKIPPED", "MSC read eta 10/11/2026. Not written.", A.WRITE_BLOCKED,
     POLICY, "NEEDS_DECISION", "NEEDS_DECISION"),
    ("G4", "GRIMALDI", "Grimaldi", "HUMAN_TIMEOUT", "nobody came", A.HUMAN_TIMEOUT, None,
     "NEEDS_PERSON", "NEEDS_PERSON"),
    ("X5", "DHL", "DHL Express", "FAILED", "field value mismatch", A.FAILED, None,
     "NEEDS_DECISION", "NEEDS_DECISION"),
]
for ref, provider, carrier, result, error, outcome, failure, _mode, _status in cases:
    t.shipment_started({"bol_awb": ref, "carrier": carrier, "provider": provider})
    t.step("Opening {0} tracking".format(carrier))
    finish(t, ref, result, error, outcome, failure)
snap = t.snapshot()
items = {i["reference"]: i for i in backlog.items()}
for ref, provider, carrier, result, error, outcome, failure, mode, status in cases:
    item = items.get(ref) or {}
    check("{0} ({1}) → {2}, status {3}".format(ref, outcome, mode, status),
          item.get("mode") == mode and item.get("status") == status, str(item)[:300])
check("Only the transient failures with nothing written are queued for a retry in this run",
      snap["retry_queue"] == ["N1", "T2"] and t.deferred_retries() == ["N1", "T2"],
      str(snap["retry_queue"]))
check("Each item carries its plan, its why and the failure it came from",
      all(i.get("plan_statement") and i.get("why") and i.get("last_failure_id")
          for i in items.values()), str(list(items.values())[0])[:300])
log = [e for e in snap["intel_log"] if e["event"] == "work_item_recorded"]
check("work_item_recorded for every failure, as a structured event",
      sorted(e["reference"] for e in log) == ["G4", "M3", "N1", "T2", "X5"]
      and "work_item_recorded" in INTEL_EVENTS, str(log)[:300])
check("An expected 'no result' skip is not put on the work list",
      (lambda: (t.shipment_started({"bol_awb": "Z6", "carrier": "DHL Express", "provider": "DHL"}),
                finish(t, "Z6", "SKIPPED", "DHL returned no estimated arrival", A.NO_RESULT),
                "Z6" not in {i["reference"] for i in backlog.items()})[-1])())

# ═════════════════════════════════════════════════════════════════════════
rule("2. RESOLVED ONLY BY VERIFICATION")
# ═════════════════════════════════════════════════════════════════════════
t.deferred_retry_started("N1")
check("Starting the retry moves N1 to RETRYING and takes it off the queue",
      backlog.items(reference="N1")[0]["status"] == "RETRYING" and "N1" not in t.deferred_retries())
t.shipment_started({"bol_awb": "N1", "carrier": "Air France KLM Cargo", "provider": "AFKL"})
t.provider_result({"provider": "AFKL", "tracking_status": "Arrived", "eta": "02/10/2026"})
t.view_updated("COE", "ETA", "02/10/2026", verified=True)
t.shipment_finished("N1", "SUCCESS", "", {"coe": "COE ETA updated with 02/10/2026"})
snap = t.snapshot()
rows = [r for r in snap["shipments"] if r["reference"] == "N1"]
check("One row for N1, which remembers how the first attempt ended",
      len(rows) == 1 and rows[0]["state"] == "updated"
      and (rows[0].get("deferred_retry") or {}).get("state") == "failed", str(rows)[:300])
n1 = backlog.items(reference="N1")[0]
check("Written AND read back → RESOLVED_VERIFIED, saying how",
      n1["status"] == "RESOLVED_VERIFIED" and "read back" in n1["resolved"]["by"], str(n1)[:300])
check("work_item_resolved is observed", any(e["event"] == "work_item_resolved" and
                                            e["reference"] == "N1" for e in snap["intel_log"]))

t.deferred_retry_started("T2")
t.shipment_started({"bol_awb": "T2", "carrier": "DHL Express", "provider": "DHL"})
t.view_updated("BU", "ETA", "03/10/2026", verified=None)        # read-back never ran
t.shipment_finished("T2", "SUCCESS", "", {"bu": "BU ETA updated with 03/10/2026"})
t2 = backlog.items(reference="T2")[0]
check("A success whose read-back did not confirm it stays OPEN (awaiting verification)",
      t2["status"] == "AWAITING_VERIFICATION" and t2["status"] in backlog.OPEN_STATES, t2["status"])

t.shipment_started({"bol_awb": "X5", "carrier": "DHL Express", "provider": "DHL"})
finish(t, "X5", "FAILED", "field value mismatch", A.FAILED)
check("A repeat of the same failure is one item, counted again — not a duplicate",
      len(backlog.items(reference="X5")) == 1 and backlog.items(reference="X5")[0]["occurrences"] == 2,
      str(backlog.items(reference="X5"))[:200])
check("Nobody's opinion closes an item: close() needs a person's name",
      backlog.close(backlog.items(reference="X5")[0]["key"], by="") is None)
closed = backlog.close(backlog.items(reference="X5")[0]["key"], by="m.mabrouk", note="Hub fixed")
check("A person can close it, by name, with a note — recorded in its history",
      closed and closed["status"] == "CLOSED" and closed["history"][-1]["event"] == "closed"
      and "m.mabrouk" in closed["resolved"]["by"], str(closed)[:300])
s = backlog.summary()
check("The summary counts open, resolved-by-verification and closed separately",
      s["resolved"] == 1 and s["closed"] == 1 and s["open"] == 3, str(s))

# A test store and a production store never mix.
before = len(backlog.items())
store.set_origin("production")
backlog.add(F.from_record({"reference": "P1", "carrier": "X", "provider": "X", "state": "failed",
                           "error": "Timeout 1ms", "steps": []}, run_id="r"))
store._PROCESS_ORIGIN["value"] = None
check("A process of the other origin cannot write into this store",
      len(backlog.items()) == before and not backlog.items(reference="P1"))

# ═════════════════════════════════════════════════════════════════════════
rule("3. ATLAS SAYS WHAT THE PLAN IS")
# ═════════════════════════════════════════════════════════════════════════
t3 = ControlTowerState()
t3.attach_intelligence(events)
t3.run_started(run_id="20261005-210000-work02")
for ref, provider, carrier, result, error, outcome, failure, _m, _s in cases[:3]:
    t3.shipment_started({"bol_awb": ref + "b", "carrier": carrier, "provider": provider})
    finish(t3, ref + "b", result, error, outcome, failure)
st = t3.snapshot()
r = assistant.answer("What's on your work list?", st, {})
print("\n--- ATLAS: what's on your work list? ---\n" + r["answer"] + "\n---")
check("'What's on your work list?' lists this run's failures and what happens to each",
      all(x in r["answer"] for x in ("N1b", "T2b", "M3b", "retry once", "needs a decision")),
      r["answer"])
check("...says the run did not stop", "did not stop" in r["answer"])
check("...and what is still open from earlier runs", "earlier runs" in r["answer"]
      and "T2" in r["answer"], r["answer"])
check("...and that items resolve only by verification", "written and read back" in r["answer"])
r = assistant.answer("why the error?", st, {"reference": "N1b"})
check("'Why the error?' includes the plan for it, from the same record",
      "**Work**" in r["answer"] and "retry once" in r["answer"], r["answer"][-400:])
r = assistant.answer("What will you do about it?", st, {})
check("'What will you do about it?' answers from the work list",
      r.get("intent") == "failure_work" and "N1b" in r["answer"], str(r.get("intent")))
noticed = [n["text"] for n in assistant.notices(assistant.RunData(st)) if n["level"] != "ok"]
check("ATLAS NOTICED says which failures are queued for a retry and that the run carries on",
      any("queued for one retry" in n and "N1b" in n and "carries on" in n for n in noticed),
      str(noticed))
brief = assistant.atlas_brief(st)
check("The ATLAS page receives the work list (this run and still open)",
      {w["reference"] for w in brief["work"]["run"]} == {"N1b", "T2b", "M3b"}
      and brief["work"]["summary"].get("open", 0) >= 3, str(brief["work"])[:300])
card = [c for c in brief["failures"] if c["reference"] == "N1b"][0]
check("...and each failure card carries its work mode", card["work"]["mode"] == "RETRY_THIS_RUN")
UI = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
check("The ATLAS page has the Work list card, fed by /api/atlas",
      'id="wlCard"' in UI and "FI.work = d.work" in UI and "function paintWork" in UI)

# ═════════════════════════════════════════════════════════════════════════
rule("4. THE REAL main(): THE RUN NEVER STOPS; ONE RETRY, AFTER THE OTHERS")
# ═════════════════════════════════════════════════════════════════════════
# The real loop, outcome mapping and retry pass. Only the browser, the Hub
# and the carrier are stand-ins. main() marks its store production, so it
# gets an empty store of its own.
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_worklist_main_")


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


LIST = [{"bol_awb": ref, "carrier": carrier, "provider": provider, "current_eta": "",
         "table_page": 1}
        for ref, carrier, provider in (("N1", "Air France KLM Cargo", "AFKL"),
                                       ("D2", "DHL Express", "DHL"),
                                       ("M3", "MSC", "MSC"),
                                       ("X4", "DHL Express", "DHL"),
                                       ("N5", "Air France KLM Cargo", "AFKL"))]
LOOKUPS, WRITES, RESULTS = [], [], []


def fake_lookup(pages, shipment):
    ref = shipment["bol_awb"]
    LOOKUPS.append(ref)
    tries = LOOKUPS.count(ref)
    if ref == "N1" and tries == 1:
        raise Exception("Page.goto: net::ERR_CONNECTION_RESET at the carrier")
    if ref == "N5":
        raise Exception("Page.goto: net::ERR_CONNECTION_RESET at the carrier")
    if ref == "M3":
        raise A.SkipShipment("MSC read eta 10/11/2026 for M3. Not written.", failure=POLICY)
    if ref == "X4":
        raise Exception("field value mismatch")
    return {"provider": shipment["provider"], "tracking_status": "Arrived",
            "eta": "02/10/2026", "ata": "03/10/2026"}


def fake_write(page, shipment, result):
    # The real update_internal_shipment reports each read-back to the bridge
    # this way (tower.view_updated(..., verified=...)); the stand-in Hub
    # reads back what it was given.
    WRITES.append(shipment["bol_awb"])
    A.tower.view_updated("COE", "ETA", result["eta"], verified=True)
    return {"coe": "COE ETA updated with {0} and saved".format(result["eta"]), "bu": ""}


def fake_ensure(page, view, number):
    if number > 1:
        raise A.SkipShipment("no more pages")


A.sync_playwright = lambda: _PW()
A.load_credentials = lambda: ("u", "p")
A.login_internal = lambda *a, **k: None
A.ensure_filtered_page = fake_ensure
A.collect_supported_shipments = lambda page, n: [dict(x) for x in LIST]
A.get_provider_result = fake_lookup
A.update_internal_shipment = fake_write
A.save_result = lambda shipment, result, action, outcome, details="": \
    RESULTS.append((shipment["bol_awb"], outcome))
A.wait_between_shipments = lambda: None
A.take_screenshot = lambda *a, **k: None
A.write_log = lambda *a, **k: None
A.DASHBOARD_ENABLED = False
A.ML_AVAILABLE = False
A.PAUSE_ON_FATAL_ERROR = False
A.HUMAN_QUEUE_ON = False
A.ATLAS_DEFERRED_RETRY = True
error = None
try:
    A.main()
except Exception as caught:
    error = caught
snap = A.tower.snapshot()
rows = {r["reference"]: r for r in snap["shipments"]}
check("main() ran to the end", error is None and snap["run"]["status"] == "finished", repr(error))
check("The run never stopped: every shipment was looked up before any retry",
      LOOKUPS[:5] == ["N1", "D2", "M3", "X4", "N5"], str(LOOKUPS))
check("Then ONE retry each, for the transient failures only (not the policy block, not "
      "the unknown failure)", LOOKUPS[5:] == ["N1", "N5"], str(LOOKUPS))
check("N1 succeeded on its retry: written once, one row, remembering the first attempt",
      WRITES == ["D2", "N1"] and rows["N1"]["state"] == "updated"
      and rows["N1"]["deferred_retry"]["state"] == "failed",
      "{0} {1}".format(WRITES, rows["N1"].get("deferred_retry")))
check("N5 failed again and was NOT retried a second time; it stays on the work list",
      LOOKUPS.count("N5") == 2 and rows["N5"]["state"] == "failed"
      and snap["retry_queue"] == [], str(snap["retry_queue"]))
check("Counters count each shipment once: 2 written, 2 failed, 1 skipped",
      snap["counters"]["successful"] == 2 and snap["counters"]["failed"] == 2
      and snap["counters"]["skipped"] == 1, str(snap["counters"]))
work = {i["reference"]: i for i in backlog.items()}
check("Work list after the run: N1 resolved by verification",
      work["N1"]["status"] == "RESOLVED_VERIFIED", work["N1"]["status"])
check("...N5 retried and failed again (the next run looks it up again)",
      work["N5"]["status"] == "RETRY_FAILED", work["N5"]["status"])
check("...M3 and X4 wait for a decision", work["M3"]["status"] == "NEEDS_DECISION"
      and work["X4"]["status"] == "NEEDS_DECISION")
seq = [e["event"] for e in reversed(snap["intel_log"]) if e.get("reference") == "N1"]
check("N1 observed: failure → work item → retry started → resolved",
      seq.index("failure_detected") < seq.index("work_item_recorded")
      < seq.index("deferred_retry_started") < seq.index("work_item_resolved"), str(seq))
r = assistant.answer("What's on your work list?", snap, {})
check("ATLAS reports it: N5 retried once and failed again; M3 and X4 need a decision",
      "N5" in r["answer"] and "failed again" in r["answer"] and "M3" in r["answer"]
      and "needs a decision" in r["answer"], r["answer"])

# Turned off, nothing is retried and the run still finishes.
LOOKUPS.clear(); WRITES.clear()
A.ATLAS_DEFERRED_RETRY = False
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_worklist_off_")
A.main()
check("ATLAS_DEFERRED_RETRY=0: no retry pass; the run still finishes",
      LOOKUPS == ["N1", "D2", "M3", "X4", "N5"]
      and A.tower.snapshot()["run"]["status"] == "finished", str(LOOKUPS))
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("A broken retry pass can never end the run (it is guarded)",
      "note_suppressed(\"the deferred retry pass\"" in SRC)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
