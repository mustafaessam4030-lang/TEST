"""
The Human Action Queue on its own: the state machine, who may choose a
task, what is published and persisted, the run's safe-point service, the
post-verification check, and how main() uses it.

The end-to-end flow — a real browser parked, chosen, verified by a "person"
and resumed automatically — is in test_human_loop.py, sections 12-14.

    python test_human_queue.py
"""

import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
# main() is driven below and attaches ATLAS's learning store: keep it off the
# real one, whoever runs this file.
import os as _os
_os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_queue_intel_")
import human_queue as HQ                                      # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format(
        "PASS" if condition else "FAIL", name,
        "  ({0})".format(detail) if detail and not condition else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


WORK = Path(tempfile.mkdtemp(prefix="ct_queue_"))
CLOCK = {"t": 1_800_000_000.0}
SEEN = []
q = HQ.HumanQueue(path=WORK / "queue.json", on_change=SEEN.append,
                  clock=lambda: CLOCK["t"])
SHIP = {"bol_awb": "S330348776", "carrier": "Grimaldi", "provider": "GRIMALDI",
        "current_eta": "", "table_page": 2, "password": "never"}

rule("1. A TASK IS CREATED ONCE, WITH EVERYTHING THE BRIEF NAMES")
t = q.create("run-1", "S330348776", "Grimaldi Lines", provider="GRIMALDI",
             step="security code before search", shipment=SHIP,
             session={"page_id": "tab-3", "url": "https://x/gnet",
                      "cookie": "nope"}, ttl_s=600)
for key in ("action_id", "run_id", "reference", "carrier", "step", "reason",
            "created_at", "timeout_at", "session", "status"):
    check("The task carries {0}".format(key), t.get(key) not in (None, ""))
check("It starts WAITING_FOR_HUMAN", t["status"] == HQ.WAITING)
check("The browser reference is the tab and address only",
      t["session"] == {"page_id": "tab-3", "url": "https://x/gnet"})
check("A second detection for the same shipment reuses the task",
      q.create("run-1", "S330348776", "Grimaldi Lines")["action_id"]
      == t["action_id"] and len(q.snapshot()) == 1)
check("Only Hub identifiers are kept to look the shipment up again",
      set(q.shipment(t["action_id"])) == set(HQ.SHIPMENT_FIELDS))
saved = (WORK / "queue.json").read_text(encoding="utf-8")
check("Persisted on change, with no field for a code, answer or credential",
      "S330348776" in saved and "password" not in saved and "cookie" not in saved
      and set(json.loads(saved)["tasks"][0]) <= set(HQ.PUBLIC_FIELDS))
check("Every change is published", SEEN and SEEN[-1][0]["reference"] == "S330348776")

rule("2. THE STATE MACHINE REFUSES WHAT IT DOES NOT ALLOW")
check("Every state named in the brief exists",
      set(HQ.STATES) == {"WAITING_FOR_HUMAN", "OPERATOR_OPENED",
                         "VERIFICATION_PENDING", "HUMAN_COMPLETED",
                         "POST_VERIFICATION_CHECK", "RESUMING", "SUCCESS",
                         "TIMEOUT", "HUMAN_SESSION_LOST",
                         "VERIFICATION_NOT_CONFIRMED", "FAILED"})
applied, _ = q.transition(t["action_id"], HQ.SUCCESS, "jump")
check("WAITING cannot jump to SUCCESS", not applied
      and q.get(t["action_id"])["status"] == HQ.WAITING)
check("RESUMING is reached only through the post-verification check",
      HQ.RESUMING not in HQ.TRANSITIONS[HQ.COMPLETED]
      and HQ.RESUMING in HQ.TRANSITIONS[HQ.CHECK]
      and HQ.SUCCESS not in HQ.TRANSITIONS[HQ.CHECK])
check("SUCCESS is reachable only from RESUMING",
      [s for s, nxt in HQ.TRANSITIONS.items() if HQ.SUCCESS in nxt] == [HQ.RESUMING])
check("Nothing leaves a terminal state",
      all(not HQ.TRANSITIONS[s] for s in HQ.TERMINAL))
check("Every state has a plain label", all(HQ.LABELS.get(s) for s in HQ.STATES))

rule("3. CHOOSING A TASK")
ok, msg, _ = q.choose(t["action_id"], "run-0", "opA")
check("A request from another run is refused", not ok and "run-1" in msg)
ok, msg, chosen = q.choose(t["action_id"], "run-1", "opA")
check("The operator chooses a waiting task: OPERATOR_OPENED, claimed",
      ok and chosen["status"] == HQ.OPENED and chosen["claimed_by"] == "opA")
ok, msg, _ = q.choose(t["action_id"], "run-1", "opB")
check("A second operator is refused", not ok and "Another operator" in msg)
ok, msg, _ = q.choose(t["action_id"], "run-1", "opA")
check("Choosing it twice does not queue it twice", not ok and "in progress" in msg)
picked = q.take_chosen()
check("The run picks the choice up once, with its shipment",
      len(picked) == 1 and picked[0][1]["bol_awb"] == "S330348776"
      and q.take_chosen() == [])

rule("4. THE PATH TO SUCCESS, AND BACK TO THE QUEUE")
for state in (HQ.PENDING, HQ.COMPLETED, HQ.CHECK):
    q.transition(t["action_id"], state, state.lower())
applied, _ = q.transition(t["action_id"], HQ.PENDING, "second reading disagreed")
check("A check that does not confirm goes back to VERIFICATION_PENDING",
      applied and q.get(t["action_id"])["attempts"] == 2)
applied, task = q.requeue(t["action_id"], "window ended")
check("A window that ends puts it back in the queue, claim released",
      applied and task["status"] == HQ.WAITING and task["claimed_by"] is None)
q.choose(t["action_id"], "run-1", "opB")
q.take_chosen()
for state in (HQ.PENDING, HQ.COMPLETED, HQ.CHECK, HQ.RESUMING, HQ.SUCCESS):
    q.transition(t["action_id"], state, state.lower())
done = q.get(t["action_id"])
check("PENDING -> COMPLETED -> CHECK -> RESUMING -> SUCCESS",
      done["status"] == HQ.SUCCESS and done["closed_at"])
check("The history records each step, bounded",
      [h["state"] for h in done["history"]][-5:] ==
      [HQ.PENDING, HQ.COMPLETED, HQ.CHECK, HQ.RESUMING, HQ.SUCCESS]
      and len(done["history"]) <= HQ.MAX_HISTORY)
check("A finished task cannot be chosen",
      not q.choose(t["action_id"], "run-1", "opB")[0])
check("...and is no longer the shipment's open task",
      q.for_reference("S330348776", "run-1") is None)

rule("5. EXPIRY, END OF RUN, SUMMARY")
a = q.create("run-1", "ANRB1", "Grimaldi Lines", shipment=SHIP, ttl_s=60)
b = q.create("run-1", "ANRB2", "Grimaldi Lines", shipment=SHIP, ttl_s=6000)
CLOCK["t"] += 90
check("Two waiting, the oldest 90 s: the summary says so",
      HQ.summarize(q.open_tasks(), now=CLOCK["t"]) ==
      "2 human actions waiting. The oldest has been waiting 1 min.")
expired = q.expire()
check("A task past its timeout becomes TIMEOUT",
      [x["reference"] for x in expired] == ["ANRB1"])
check("One waiting reads as one line with carrier and shipment",
      HQ.summarize(q.open_tasks(), now=CLOCK["t"]) ==
      "1 human action: Grimaldi Lines — ANRB2, waiting 1 min.")
closed = q.close_open(HQ.LOST, "browser closed")
check("The run's end closes what is left", [x["reference"] for x in closed]
      == ["ANRB2"] and q.open_tasks() == [])
check("Nothing open: it says nothing needs a person",
      HQ.summarize(q.open_tasks()) == "Nothing needs a person right now.")

rule("6. THE DASHBOARD'S VALIDATION KNOWS THE QUEUE")
from dashboard import control as C                            # noqa: E402
queue = [{"action_id": "aa11", "run_id": "run-1", "reference": "S1",
          "status": "WAITING_FOR_HUMAN", "claimed_by": None},
         {"action_id": "bb22", "run_id": "run-1", "reference": "S2",
          "status": "OPERATOR_OPENED", "claimed_by": "opA"},
         {"action_id": "cc33", "run_id": "run-1", "reference": "S3",
          "status": "TIMEOUT", "claimed_by": None}]
v = C.validate_human_request
check("Open & Continue on a waiting task is accepted with nothing live",
      v(None, "open", "run-1", "aa11", "opA", queue)[0])
check("...not for another run", not v(None, "open", "run-0", "aa11", "opA", queue)[0])
check("...not for a task another operator holds",
      "Another operator" in v(None, "open", "run-1", "bb22", "opB", queue)[1])
check("...not for one already in progress",
      "in progress" in v(None, "open", "run-1", "bb22", "opA", queue)[1])
check("...not for one that timed out", "timed out" in
      v(None, "open", "run-1", "cc33", "opA", queue)[1])
check("...and no operation types anything into a page",
      not v(None, "type_code", "run-1", "aa11", "opA", queue)[0])
channel = C.ControlChannel()
channel.configure(False, human_enabled=True)
channel.set_human_queue(queue)
ok, msg = channel.human_request("open", "run-1", "aa11", "opA")
check("The in-process channel accepts and explains it", ok and "Open & Continue" in msg)
check("...and the task is taken at once, so a second tab is refused",
      not channel.human_request("open", "run-1", "aa11", "opB")[0])
check("The request is queued for the run, scoped to run and task",
      [r["action_id"] for r in channel.take_human_requests()] == ["aa11"])

rule("7. UNDER THE SUPERVISOR")
src = (HERE / "dashboard" / "supervisor.py").read_text(encoding="utf-8")
src = src.replace("\nsupervisor = Supervisor()\n", "\nsupervisor = None\n")
SUP = {"__name__": "supervisor_under_test", "__package__": None,
       "__file__": str(HERE / "dashboard" / "supervisor.py")}
exec(compile(src, "supervisor.py", "exec"), SUP)
SUP["RUNTIME"] = WORK / "runtime"
SUP["STATE_FILE"] = SUP["RUNTIME"] / "state.json"
SUP["CONTROL_FILE"] = SUP["RUNTIME"] / "control.json"
SUP["RUNTIME"].mkdir(parents=True, exist_ok=True)
sup = SUP["Supervisor"]()
SUP["STATE_FILE"].write_text(json.dumps({
    "run": {"status": "running"}, "human_action": None,
    "human_queue": [dict(queue[0], updated_at="2026-10-04 10:00:00")]}),
    encoding="utf-8")
sup.is_running = lambda: True
ok, msg = sup.human_request("open", "run-1", "aa11", "opA")
check("A parked task's Open & Continue is relayed to the run",
      ok and json.loads(SUP["CONTROL_FILE"].read_text())["human"][-1]["action_id"]
      == "aa11", msg)
check("...a second tab is refused before the run catches up",
      not sup.human_request("open", "run-1", "aa11", "opB")[0])
sup.is_running = lambda: False
gone = sup.snapshot()["human_queue"][0]
check("With the run process gone, a parked task reads as session lost",
      gone["status"] == "HUMAN_SESSION_LOST")

rule("8. THE RUN'S SIDE")
import update_eta as A                                        # noqa: E402
A.HUMAN_QUEUE.path = WORK / "run_queue.json"
A.HUMAN_EVENTS_FILE = WORK / "events.jsonl"
A.HUMAN_ACTION_FILE = WORK / "action.json"
LOG = []
A.write_log = lambda message, *a, **k: LOG.append(str(message))
A.HUMAN_CONFIRM_MS = 10


class FakePage(object):
    url = "https://carrier.invalid/track?token=abc"

    def __init__(self):
        self.closed = False

    def wait_for_timeout(self, ms):
        pass

    def is_closed(self):
        return self.closed

    def bring_to_front(self):
        pass


task = A.HUMAN_QUEUE.create(A.RUN_ID, "S77", "Grimaldi Lines",
                            shipment={"bol_awb": "S77", "table_page": 1})
# The wait's own poll saw the step done; the second reading, after the
# page settles, does not.
answers = iter([(False, "the page shows a result, but not for S77")])
action = {"reference": "S77", "queue_id": task["action_id"],
          "action_id": task["action_id"], "carrier": "Grimaldi Lines"}
confirmed, why = A._confirm_after_human(FakePage(), lambda: next(answers), action)
after = A.HUMAN_QUEUE.get(task["action_id"])
check("A page that shows it done once, then not, is NOT confirmed",
      confirmed is False and "not for S77" in why)
check("...the task goes back to VERIFICATION_PENDING through the check",
      [h["state"] for h in after["history"]][-3:] ==
      [HQ.COMPLETED, HQ.CHECK, HQ.PENDING], str(after["history"]))
check("...and VERIFICATION_NOT_CONFIRMED is in the audit log",
      "VERIFICATION_NOT_CONFIRMED" in A.HUMAN_EVENTS_FILE.read_text())
page = FakePage()
confirmed, _ = A._confirm_after_human(page, lambda: (True, ""), action)
check("Two readings that agree: confirmed, RESUMING",
      confirmed is True and A.HUMAN_QUEUE.get(task["action_id"])["status"] ==
      HQ.RESUMING)

lost = A.HUMAN_QUEUE.create(A.RUN_ID, "S88", "Grimaldi Lines",
                            shipment={"bol_awb": "S88", "table_page": 1})
A.HUMAN_QUEUE.choose(lost["action_id"], A.RUN_ID, "opA")
A.HUMAN_QUEUE.take_chosen()
gone_page = FakePage()
gone_page.closed = True
A.HUMAN_QUEUE_ON = True
A.HUMAN_STATE["shipment"] = {"bol_awb": "S88", "lookup_ref": "S88"}
outcome = A.wait_for_human(gone_page, "S88", "Grimaldi Lines",
                           lambda: (False, "x"), wait_ms=2000)
check("The chosen task's browser tab is gone: HUMAN_SESSION_LOST",
      outcome == "session_lost" and A.HUMAN_QUEUE.get(lost["action_id"])["status"]
      == HQ.LOST)

# A request for a parked task that arrives during another wait is kept.
held = A.HUMAN_QUEUE.create(A.RUN_ID, "S99", "Grimaldi Lines",
                            shipment={"bol_awb": "S99", "table_page": 4})
A._HUMAN_DEFERRED.append({"op": "open", "run_id": A.RUN_ID,
                          "action_id": held["action_id"], "client_id": "opA"})
picked = A.human_queue_service()
check("The run's safe-point service applies a held choice",
      [s["bol_awb"] for _t, s in picked] == ["S99"])
A.HUMAN_QUEUE_ON = False
check("With the queue off, the service does nothing", A.human_queue_service() == [])

rule("9. MAIN() USES IT AT SAFE POINTS ONLY")
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
main_src = SRC.split("def main():")[1]
check("One per-shipment function for the Hub list and the queue",
      main_src.count("\n" + " " * 20 + "_process_shipment(shipment, table_page)") == 1
      and "_process_shipment(shipment, shipment.get(\"table_page\") or 1)" in main_src)
check("Chosen shipments join between shipments, never mid-write",
      main_src.index("reversed(_human_reruns())") < main_src.index(
          "\n" + " " * 20 + "_process_shipment(shipment, table_page)"))
check("A parked shipment is its own outcome, not a failure",
      'if getattr(error, "reason", "") == "queued":' in main_src
      and '"HUMAN_QUEUED"' in main_src)
check("The run holds for the operator before it ends, bounded",
      "HUMAN_QUEUE_HOLD_S" in main_src and "_hold_until" in main_src)
check("Whatever is open when the browser closes is closed as lost",
      "hq.LOST, \"the run ended; its browser closed with it\"" in main_src)
confirm = SRC.split("def _confirm_after_human")[1].split("\ndef ")[0]
service = SRC.split("def human_queue_service")[1].split("\ndef ")[0]
for token in (".fill(", ".type(", ".press(", ".click(", "goto(", "reload(",
              "screenshot"):
    check("The post-verification check and the queue never call {0}".format(token),
          token not in confirm and token not in service)
check("The queue module has no browser in it at all",
      "sync_api" not in (HERE / "human_queue.py").read_text()
      and "import playwright" not in (HERE / "human_queue.py").read_text())

rule("10. MAIN() END TO END, WITH A FAKE BROWSER AND HUB")
# The real main(): its loop, its outcome mapping, the queue service, the
# end-of-run hold. Only the browser, the Hub and the carrier are stand-ins.


class _Page(object):
    url = "about:blank"

    def is_closed(self):
        return False

    def wait_for_timeout(self, ms):
        pass


class _Context(object):
    def new_page(self):
        return _Page()

    pages = []


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


LIST = [{"bol_awb": ref, "carrier": carrier, "provider": provider,
         "current_eta": "", "table_page": 1}
        for ref, carrier, provider in (("G1", "Grimaldi", "GRIMALDI"),
                                       ("D2", "DHL Express", "DHL"),
                                       ("G3", "Grimaldi", "GRIMALDI"))]
LOOKUPS, WRITES, RESULTS = [], [], []


def fake_lookup(pages, shipment):
    ref = shipment["bol_awb"]
    LOOKUPS.append(ref)
    A.HUMAN_STATE["shipment"] = dict(shipment, lookup_ref=ref)
    if ref.startswith("G"):
        task = A.HUMAN_QUEUE.for_reference(ref, A.RUN_ID)
        if task is None or task["status"] == HQ.WAITING:
            # What wait_for_human leaves behind when nobody came in time.
            A.HUMAN_QUEUE.create(A.RUN_ID, ref, "Grimaldi Lines",
                                 shipment=shipment)
            A.HUMAN_STATE["last"] = {"reference": ref, "outcome": "queued"}
            raise A.CaptchaRequired(ref, "Grimaldi Lines", reason="queued")
        # Chosen by the operator: the person verified; the page confirmed.
        for state in (HQ.PENDING, HQ.COMPLETED, HQ.CHECK, HQ.RESUMING):
            A.HUMAN_QUEUE.transition(task["action_id"], state, state)
    if ref == "D2":
        # The operator chooses G1 while the run is busy with D2.
        g1 = A.HUMAN_QUEUE.for_reference("G1", A.RUN_ID)
        A.tower_control.set_human_queue(A.HUMAN_QUEUE.snapshot())
        A.tower_control.human_request("open", A.RUN_ID, g1["action_id"], "opA")
    return {"provider": shipment["provider"], "tracking_status": "Arrived",
            "eta": "02/10/2026", "ata": "03/10/2026"}


def fake_ensure(page, view, number):
    if number > 1:
        raise A.SkipShipment("no more pages")


A.sync_playwright = lambda: _PW()
A.load_credentials = lambda: ("u", "p")
A.login_internal = lambda *a, **k: None
A.ensure_filtered_page = fake_ensure
A.collect_supported_shipments = lambda page, n: [dict(x) for x in LIST]
A.get_provider_result = fake_lookup
A.update_internal_shipment = lambda page, shipment, result: (
    WRITES.append(shipment["bol_awb"]) or {"coe": "COE ETA updated with "
                                           "02/10/2026 and saved", "bu": ""})
A.save_result = lambda shipment, result, action, outcome, details="": \
    RESULTS.append((shipment["bol_awb"], outcome))
A.wait_between_shipments = lambda: None
A.take_screenshot = lambda *a, **k: None
A.DASHBOARD_ENABLED = False
A.ML_AVAILABLE = False
A.PAUSE_ON_FATAL_ERROR = False
A.HUMAN_QUEUE_ON = True
A.HUMAN_QUEUE_HOLD_S = 1
A.tower_control.configure(False, human_enabled=True)
A.HUMAN_QUEUE.clear()
error = None
try:
    A.main()
except Exception as caught:
    error = caught
snap = A.tower.snapshot()
rows = {r["reference"]: r for r in snap["shipments"]}
tasks = {t["reference"]: t for t in A.HUMAN_QUEUE.snapshot()}
check("main() ran to the end", error is None and snap["run"]["status"] ==
      "finished", repr(error))
check("G1 was parked, then looked up again after the operator chose it",
      LOOKUPS == ["G1", "D2", "G1", "G3"], str(LOOKUPS))
check("...between shipments: D2 finished before G1 came back",
      WRITES == ["D2", "G1"], str(WRITES))
check("G1's task: chosen, verified, written and read back -> SUCCESS",
      tasks["G1"]["status"] == HQ.SUCCESS and rows["G1"]["state"] == "updated",
      "{0} {1}".format(tasks["G1"]["status"], rows["G1"]["state"]))
check("G3 nobody chose: held for, then timed out at the end of the run",
      tasks["G3"]["status"] == HQ.TIMEOUT
      and rows["G3"]["state"] == "human_timeout", str(tasks["G3"]["status"]))
check("Counters: 2 written, 1 needing a person, no failures",
      snap["counters"]["successful"] == 2 and snap["counters"]["needs_human"] == 1
      and snap["counters"]["failed"] == 0, str(snap["counters"]))
check("The results file says parked, then the real outcomes",
      RESULTS == [("G1", A.HUMAN_QUEUED), ("D2", "SUCCESS"), ("G1", "SUCCESS"),
                  ("G3", A.HUMAN_QUEUED), ("G3", A.HUMAN_TIMEOUT)], str(RESULTS))
check("One row per shipment", len([r for r in snap["shipments"]
                                   if r["reference"] == "G1"]) == 1)

print()
print("=" * 74)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 74)
sys.exit(1 if FAIL else 0)
