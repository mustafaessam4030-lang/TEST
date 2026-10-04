"""
Human-in-the-loop for carrier pages that need a person — Grimaldi (GNET)
first.

    PROCESSING -> WAITING_FOR_HUMAN -> PROCESSING -> read, validate, write,
                                                     read back -> SUCCESS
                                    -> HUMAN_TIMEOUT
                                    -> FAILED (browser session lost)

Everything here runs for real except the carrier and the Hub:

  * a real Chromium, driven by the real update_eta functions, holding a real
    tab on a stand-in GNET page (labels beside the boxes, a security code, an
    "Enter code" box, Search);
  * the real dashboard server, in-process, reached over HTTP from a second
    thread playing the OPERATOR — exactly the requests the Open Browser
    Session and Resume buttons send;
  * a "person" in the page: a script that types the code and presses Search
    when the operator thread says so — standing in for a human at the
    browser, never for the automation;
  * the Hub boundary (open Manage, fill, save, read back) is replaced by a
    small in-memory Hub. update_one_view itself is the real one, so its
    refusal to report a write that did not read back is what is tested.

The per-shipment sequence in `process()` below mirrors main()'s loop (start,
look up, write, close, the same exception-to-outcome mapping). main() itself
needs a live Hub and a sign-in, so it is not driven here.

    python test_human_loop.py
"""

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_eta as A                                        # noqa: E402
from dashboard import control as C                            # noqa: E402

PASS, FAIL, SKIP = [], [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format(
        "PASS" if condition else "FAIL", name,
        "  ({0})".format(detail) if detail and not condition else ""))


def skip(name, why):
    SKIP.append(name)
    print("  SKIP  {0}  ({1})".format(name, why))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


# ── Isolation: nothing from this test reaches production files ─────────
WORK = Path(tempfile.mkdtemp(prefix="ct_human_"))
A.HUMAN_ACTION_FILE = WORK / "human_action.json"
A.HUMAN_EVENTS_FILE = WORK / "human_actions.jsonl"
LOG = []
A.write_log = lambda message, *args, **kwargs: LOG.append(str(message))
A.save_page_text = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None
A.ml_record = lambda *args, **kwargs: None            # no telemetry written
A.ml_episode_begin = lambda *args, **kwargs: None
A.ml_episode_end = lambda *args, **kwargs: None
A.PAGE_SETTLE_MAX_SECONDS = 1
A.CAPTCHA_POLL_MS = 300
A.DRY_RUN = False
A.VERIFY_AFTER_SAVE = True
CODE = "7Q4K"                       # what the person reads off the image

# ── The in-memory Hub ──────────────────────────────────────────────────
HUB, PENDING, VERIFY_CALLS = {}, {}, []
READBACK = {"lie": False}
A.click_manage_in_view = lambda page, view, bol, table_page: 1
A.select_shipment_info_tab = lambda page, view, field: True
A.fill_date_field = lambda page, field, value: PENDING.__setitem__(field, value)
A.save_manage_page = lambda page: HUB.update(PENDING)
A.ensure_filtered_page = lambda *args, **kwargs: None


def fake_read_back(page, shipment, view, field, expected):
    VERIFY_CALLS.append((view, field, expected))
    held = "01/01/1999" if READBACK["lie"] else HUB.get(field)
    return held == expected, "the Hub holds {0}".format(held)


A.verify_saved_date = fake_read_back


def process(pages, shipment, counts):
    """main()'s per-shipment sequence and outcome mapping, without the Hub."""
    reference = shipment["bol_awb"]
    before = tuple(counts[k] for k in ("ok", "failed", "skipped", "partial",
                                       "human"))
    A.tower.shipment_started(shipment)
    try:
        result = A.get_provider_result(pages, shipment)
        A.tower.provider_result(result)
        actions = A.update_internal_shipment(None, shipment, result)
        counts["ok"] += 1
        A.tower.shipment_finished(reference, "SUCCESS", "", actions)
        outcome = ("SUCCESS", result)
    except A.CaptchaRequired as error:
        counts["human"] += 1
        lost = getattr(error, "reason", "") == "session_lost"
        A.tower.shipment_finished(
            reference, "FAILED" if lost else "HUMAN_TIMEOUT", str(error),
            outcome=A.HUMAN_SESSION_LOST if lost else A.HUMAN_TIMEOUT)
        outcome = ("HUMAN", error)
    except A.SkipShipment as error:
        counts["skipped"] += 1
        A.tower.shipment_finished(reference, "SKIPPED", str(error))
        outcome = ("SKIPPED", error)
    except Exception as error:
        counts["failed"] += 1
        A.tower.shipment_finished(reference, "FAILED", str(error))
        outcome = ("FAILED", error)
    A.tower.counters(counts["ok"], counts["failed"], counts["skipped"],
                     counts["partial"], needs_human=counts["human"])
    after = tuple(counts[k] for k in ("ok", "failed", "skipped", "partial",
                                      "human"))
    A.human_shipment_closed(reference, before, after)
    return outcome


# ── Stand-in GNET ──────────────────────────────────────────────────────
CARRIER_PORT = int(os.environ.get("HUMAN_STUB_PORT", "9741"))
DASH_PORT = int(os.environ.get("HUMAN_DASH_PORT", "9742"))
CDP_PORT = int(os.environ.get("HUMAN_CDP_PORT", "9743"))
PERSON = {"go": False}
SUBMITTED = []

GNET = ("<!doctype html><html><body><h2>Container Tracking</h2>"
        "<form id='f' action='/gresult'><table>"
        "<tr><td>Equipment #</td><td><input name='equip' type='text'></td>"
        "<td>Shipment #</td><td><input name='ship' type='text'></td></tr>"
        "<tr><td>From Date</td><td><input name='from' type='text'></td>"
        "<td>To Date</td><td><input name='to' type='text'></td></tr>"
        "<tr><td>Security Code</td><td><img alt='code' src='data:,'></td>"
        "<td><input name='code' type='text' placeholder='Enter code'></td>"
        "<td><input type='hidden' name='untouched' value='?'>"
        "<button type='submit'>Search</button></td></tr></table></form>"
        "<script>"
        # THE PERSON. Acts only when the operator thread says so, and checks
        # first that the automation left the code box alone.
        # Like a person, it waits until the reference has stopped changing.
        "var done=false, last='', since=0;"
        "setInterval(function(){ if(done) return;"
        " fetch('/person').then(function(r){return r.json();}).then(function(p){"
        "  var f=document.getElementById('f'), v=f.equip.value+'|'+f.ship.value;"
        "  if(v!==last){last=v; since=Date.now(); return;}"
        "  if(!p.go||done||v==='|'||Date.now()-since<1200) return;"
        "  done=true; f.untouched.value = f.code.value==='' ? '1' : '0';"
        "  f.code.value=p.code; f.submit(); }).catch(function(){}); }, 250);"
        "</script><p>" + "x" * 200 + "</p></body></html>")


def result_page(reference):
    # Unrelated dates on purpose: a print date, a departure, an ETA at the
    # transshipment port. Only the actual arrival at the port of discharge
    # is an ATA.
    return ("<!doctype html><html><body><h2>Tracking result</h2>"
            "<p>Shipment # " + reference + "</p>"
            "<p>Report printed 01/01/2026</p>"
            "<table><tr><td>Port of Loading ANTWERP</td><td>Departure 20/09/2026</td></tr>"
            "<tr><td>Port of Transshipment ALGECIRAS</td><td>ETA 28/09/2026</td></tr>"
            "<tr><td>Port of Discharge ALEXANDRIA</td><td>ETA 02/10/2026</td>"
            "<td>Actual Arrival 03/10/2026</td></tr></table>"
            "<p>" + "x" * 200 + "</p></body></html>")


MAERSK_GOOD = "MEDUAB123456"


class Carrier(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        kind = "text/html; charset=utf-8"
        if parsed.path == "/person":
            body, kind = json.dumps({"go": PERSON["go"], "code": CODE}), \
                "application/json"
        elif parsed.path.startswith("/gnet"):
            body = GNET
        elif parsed.path.startswith("/gresult"):
            query = {k: v[0] for k, v in parse_qs(
                parsed.query, keep_blank_values=True).items()}
            SUBMITTED.append(query)
            body = result_page(query.get("ship") or query.get("equip"))
        elif parsed.path.startswith("/tracking/"):
            reference = parsed.path.split("/tracking/", 1)[1]
            body = ("<html><body><p>Bill of Lading " + reference + "</p>"
                    "<p>Port of Discharge ALEXANDRIA ETA 12/10/2026</p>"
                    "<p>" + "x" * 200 + "</p></body></html>")
        else:
            body = "<html><body>" + "x" * 200 + "</body></html>"
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


# ── The operator: plain HTTP to the dashboard, from another thread ─────
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path, body=None, port=None):
    request = urllib.request.Request(
        "http://127.0.0.1:{0}{1}".format(port or DASH_PORT, path),
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with _OPENER.open(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def until(predicate, seconds=20.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except Exception:
            pass
        time.sleep(0.15)
    return None


def state():
    return api("/api/state")


def events(name):
    return [e for e in (state().get("human_events") or [])
            if e.get("event") == name]


def launch(playwright):
    last = None
    args = ["--remote-debugging-port={0}".format(CDP_PORT)]
    options = [{"headless": True, "args": args}]
    if Path("/opt/pw-browsers").is_dir():
        options += [{"headless": True, "args": args,
                     "executable_path": str(binary)}
                    for binary in sorted(Path("/opt/pw-browsers").glob(
                        "chromium-*/chrome-linux/chrome"))]
    for option in options:
        try:
            return playwright.chromium.launch(**option), None
        except Exception as error:
            last = str(error).split("\n")[0][:100]
    return None, last


# ═════════════════════════════════════════════════════════════════════
rule("1. THE CONTROL CHANNEL ONLY ANSWERS THE WAIT THE RUN OPENED")
pending = {"waiting": True, "run_id": "run-1", "action_id": "act-1",
           "claimed_by": None}
v = C.validate_human_request
check("A request for the open action is accepted",
      v(pending, "resume", "run-1", "act-1", "tabA")[0])
check("...a request for another run is refused",
      not v(pending, "resume", "run-0", "act-1", "tabA")[0])
check("...a request for an older action is refused",
      not v(pending, "resume", "run-1", "act-0", "tabA")[0])
check("...an unknown operation is refused",
      not v(pending, "type_code", "run-1", "act-1", "tabA")[0])
check("...an injected identifier is refused",
      not v(pending, "resume", "run-1'; drop", "act-1", "tabA")[0])
check("...a second operator cannot resume a claimed session",
      not v(dict(pending, claimed_by="tabA"), "resume", "run-1", "act-1",
            "tabB")[0])
check("...nothing waiting means nothing to resume",
      not v(None, "resume", "run-1", "act-1", "tabA")[0])
closed = dict(pending, waiting=False, state="session_lost")
accepted, message = v(closed, "resume", "run-1", "act-1", "tabA")
check("A Resume after the session was lost says so plainly",
      not accepted and "no longer available" in message, message)
check("A request carries no field that could be typed into a page",
      set(C.human_request_record("resume", "r", "a", "c")) ==
      {"id", "op", "run_id", "action_id", "client_id", "at"})

rule("1b. UNDER THE SUPERVISOR (DASHBOARD IN ITS OWN PROCESS)")
# Loaded from source with its paths pointed at a temp folder, so the real
# dashboard/.runtime is never touched.
_sup_src = (HERE / "dashboard" / "supervisor.py").read_text(encoding="utf-8")
_sup_src = _sup_src.replace("\nsupervisor = Supervisor()\n", "\nsupervisor = None\n")
SUP = {"__name__": "supervisor_under_test", "__package__": None,
       "__file__": str(HERE / "dashboard" / "supervisor.py")}
exec(compile(_sup_src, "supervisor.py", "exec"), SUP)
SUP["RUNTIME"] = WORK / "runtime"
SUP["STATE_FILE"] = SUP["RUNTIME"] / "state.json"
SUP["CONTROL_FILE"] = SUP["RUNTIME"] / "control.json"
sup = SUP["Supervisor"]()
SUP["STATE_FILE"].write_text(json.dumps({
    "run": {"status": "running"},
    "human_action": dict(pending, reference="S1", carrier="Grimaldi Lines")}),
    encoding="utf-8")
sup.is_running = lambda: True
accepted, message = sup.human_request("resume", "run-1", "act-1", "tabA")
relayed = json.loads(SUP["CONTROL_FILE"].read_text(encoding="utf-8"))
check("A valid Resume is relayed to the run through the control file",
      accepted and relayed["human"][-1]["op"] == "resume"
      and relayed["human"][-1]["action_id"] == "act-1", message)
channel = C.ControlChannel()
channel.file = str(SUP["CONTROL_FILE"])
taken = channel.take_human_requests()
check("...the run picks it up exactly once",
      len(taken) == 1 and channel.take_human_requests() == [])
check("...a stale one is refused before it is relayed",
      not sup.human_request("resume", "run-0", "act-1", "tabA")[0])
sup.is_running = lambda: False
accepted, message = sup.human_request("resume", "run-1", "act-1", "tabA")
check("With the run process gone, Resume says the session no longer exists",
      not accepted and "no longer exists" in message, message)
gone = sup.snapshot()["human_action"]
check("...and the dashboard stops offering a Resume for it",
      gone["waiting"] is False and gone["state"] == "session_lost", str(gone))

rule("2. THE STATE MACHINE ON THE DASHBOARD")
from dashboard.bridge import ControlTowerState                # noqa: E402
board = ControlTowerState()
board.run_started(run_id="run-1")
board.shipment_started({"bol_awb": "S1", "carrier": "Grimaldi",
                        "provider": "GRIMALDI"})
board.human_action_opened({"run_id": "run-1", "action_id": "act-1",
                           "reference": "S1", "carrier": "Grimaldi Lines",
                           "reason": "human_verification_required"})
snap = board.snapshot()
row = [r for r in snap["shipments"] if r["reference"] == "S1"][0]
check("WAITING_FOR_HUMAN is its own state, distinct from every outcome",
      row["state"] == "waiting_for_human"
      and row["state"] not in ("failed", "skipped", "updated", "processing"))
check("The pending action is published with run, shipment and reason",
      snap["human_action"]["waiting"] and snap["human_action"]["run_id"] == "run-1"
      and snap["human_action"]["reference"] == "S1"
      and snap["human_action"]["reason"] == "human_verification_required")
check("The run id is on the run itself", snap["run"]["run_id"] == "run-1")
board.human_action_closed("resumed")
row = [r for r in board.snapshot()["shipments"] if r["reference"] == "S1"][0]
check("Resumed goes back to PROCESSING — it is not a success",
      row["state"] == "processing")
board.shipment_finished("S1", "HUMAN_TIMEOUT", "nobody came")
row = [r for r in board.snapshot()["shipments"] if r["reference"] == "S1"][0]
check("HUMAN_TIMEOUT is recorded as such", row["state"] == "human_timeout")
check("The bridge cannot raise into the automation",
      board.human_action_opened(None) is None
      and board.human_action_event(object()) is None)

rule("3. A RESUME AGAINST A TAB THAT IS GONE")


class GonePage(object):
    url = "https://example.invalid/gnet?token=secret"

    def is_closed(self):
        return True


action = {"run_id": A.RUN_ID, "action_id": "abc123", "reference": "S9",
          "carrier": "Grimaldi Lines", "claimed_by": None}
answer = A._handle_human_request(
    GonePage(), action, {"op": "resume", "run_id": A.RUN_ID,
                         "action_id": "abc123", "client_id": "tabA"},
    lambda: (True, ""))
check("Resume with the browser session missing is session_lost, not resumed",
      answer == "session_lost", str(answer))
check("...and is logged as HUMAN_RESUME_FAILED",
      any("HUMAN_RESUME_FAILED" in line for line in LOG))
check("A page address is logged without its query string",
      A._page_url_for_log(GonePage()) == "https://example.invalid/gnet")
stale = A._handle_human_request(
    GonePage(), action, {"op": "resume", "run_id": "another-run",
                         "action_id": "abc123", "client_id": "tabA"},
    lambda: (True, ""))
check("A request from another run is ignored, not obeyed", stale is None)

rule("4. DATES READ AFTER A HUMAN STEP ARE VALIDATED")
checked = A.validate_arrival_result(
    {"eta": "02/10/2026", "ata": "31/12/2999", "tracking_status": "Arrived"},
    "Grimaldi Lines", "S1")
check("An 'actual arrival' in the future is dropped, not written",
      checked["ata"] is None and "future" in checked["ata_rejected"])
check("...and the ETA beside it still stands", checked["eta"] == "02/10/2026")
check("A non-date is dropped",
      A.validate_arrival_result({"ata": "31/02/2026"}, "G", "S")["ata"] is None)
check("A real past arrival is kept",
      A.validate_arrival_result({"ata": "03/10/2026"}, "G", "S")["ata"]
      == "03/10/2026")

# ═════════════════════════════════════════════════════════════════════
try:
    from playwright.sync_api import sync_playwright
except Exception as error:
    sync_playwright = None
    WHY = str(error)[:80]

E2E = ("dashboard resume", "read-back mismatch", "session lost", "timeout",
       "other carriers")
if sync_playwright is None:
    for name in E2E:
        skip(name, WHY)
else:
    server = ThreadingHTTPServer(("127.0.0.1", CARRIER_PORT), Carrier)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    BASE = "http://127.0.0.1:{0}".format(CARRIER_PORT)
    A.tower_server.start(port=DASH_PORT, open_browser=False, host="127.0.0.1")
    until(lambda: state(), 10)
    A.tower_control.configure(False, human_enabled=True)
    A.tower.run_started(run_id=A.RUN_ID)
    A.PORTALS["GRIMALDI"] = dict(A.PORTALS["GRIMALDI"],
                                 urls=[BASE + "/gnet"], wait=6)
    A.PORTALS["MAERSK"] = dict(A.PORTALS["MAERSK"], urls=[BASE + "/tracking/"],
                               deep_link=BASE + "/tracking/{0}", wait=6)
    A.OCEAN_WRITE = True
    counts = {"ok": 0, "failed": 0, "skipped": 0, "partial": 0, "human": 0}

    with sync_playwright() as playwright:
        browser, why = launch(playwright)
        if browser is None:
            for name in E2E:
                skip(name, why)
        else:
            context = browser.new_context()
            pages = {"DHL": context.new_page()}

            # ── A. The operator resumes the same run from the dashboard ──
            rule("5. GRIMALDI: WAITING_FOR_HUMAN -> OPEN -> RESUME -> SUCCESS")
            A.HUMAN_AUTO_RESUME = False        # only the Resume button moves it
            os.environ["HUMAN_WAIT_MS"] = "40000"
            PERSON["go"] = False
            SUBMITTED[:] = []
            seen = {}

            def operator_a():
                s = until(lambda: (state().get("human_action") or {}).get(
                    "waiting") and state())
                if not s:
                    seen["error"] = "never saw WAITING_FOR_HUMAN"
                    return
                act = s["human_action"]
                seen["action"] = act
                seen["row"] = [r for r in s["shipments"]
                               if r["reference"] == "S330348776"][0]
                seen["stale"] = api("/api/human", {
                    "op": "resume", "run_id": "20200101-000000-aaaaaa",
                    "action_id": act["action_id"], "client_id": "opA"})
                seen["open"] = api("/api/human", {
                    "op": "open", "run_id": act["run_id"],
                    "action_id": act["action_id"], "client_id": "opA"})
                until(lambda: events("HUMAN_SESSION_OPENED"))
                until(lambda: state()["human_action"].get("claimed_by") == "opA")
                seen["other"] = api("/api/human", {
                    "op": "resume", "run_id": act["run_id"],
                    "action_id": act["action_id"], "client_id": "opB"})
                # Resume pressed BEFORE the person finished: must not move.
                seen["early"] = api("/api/human", {
                    "op": "resume", "run_id": act["run_id"],
                    "action_id": act["action_id"], "client_id": "opA"})
                until(lambda: events("HUMAN_RESUME_FAILED"))
                seen["still_waiting"] = state()["human_action"]["waiting"]
                PERSON["go"] = True            # the person types and searches
                until(lambda: SUBMITTED)
                seen["resume"] = api("/api/human", {
                    "op": "resume", "run_id": act["run_id"],
                    "action_id": act["action_id"], "client_id": "opA"})

            ship = {"bol_awb": "S330348776", "carrier": "Grimaldi",
                    "provider": "GRIMALDI", "current_eta": "",
                    "table_page": 1}
            operator = threading.Thread(target=operator_a, daemon=True)
            operator.start()
            tabs_before = None
            kind, result = process(pages, ship, counts)
            operator.join(10)
            grimaldi_tab = pages.get("GRIMALDI")
            final = state()
            act = seen.get("action") or {}
            row = [r for r in final["shipments"]
                   if r["reference"] == "S330348776"][0]
            names = [e["event"] for e in reversed(final["human_events"])]

            check("1. Human verification detected",
                  "HUMAN_VERIFICATION_DETECTED" in names, str(names))
            check("2. The shipment went to WAITING_FOR_HUMAN",
                  seen.get("row", {}).get("state") == "waiting_for_human",
                  str(seen.get("row", {}).get("state")))
            check("3. The dashboard received the pending action",
                  act.get("carrier") == "Grimaldi Lines"
                  and act.get("reference") == "S330348776"
                  and act.get("reason") == "human_verification_required", str(act))
            check("4. The run id is preserved end to end",
                  act.get("run_id") == A.RUN_ID == final["run"]["run_id"])
            check("...a request naming an older run is refused at the server",
                  seen.get("stale", {}).get("accepted") is False,
                  str(seen.get("stale")))
            check("Open Browser Session is accepted and logged",
                  seen.get("open", {}).get("accepted") is True
                  and "HUMAN_SESSION_OPENED" in names)
            check("...and a second operator cannot resume a session another "
                  "took over", seen.get("other", {}).get("accepted") is False,
                  str(seen.get("other")))
            check("A Resume before the step is done does NOT resume the run",
                  seen.get("still_waiting") is True
                  and "HUMAN_RESUME_FAILED" in names)
            check("5. The operator's Resume continued the same run",
                  seen.get("resume", {}).get("accepted") is True
                  and A.HUMAN_STATE["last"]["resumed_via"] == "dashboard"
                  and A.HUMAN_STATE["last"]["action_id"] == act.get("action_id"))
            check("6. The same browser tab was used before and after",
                  grimaldi_tab is not None and not grimaldi_tab.is_closed()
                  and "/gresult" in grimaldi_tab.url
                  and len(context.pages) == 2, grimaldi_tab and grimaldi_tab.url)
            check("...and the reference was typed once, by the run, into "
                  "Shipment #", SUBMITTED and SUBMITTED[-1]["ship"] == "S330348776"
                  and SUBMITTED[-1]["equip"] == "", str(SUBMITTED))
            check("...the security code box was untouched until the person",
                  SUBMITTED and SUBMITTED[-1]["untouched"] == "1")
            check("7. The automation continued after Resume",
                  "ATA_EXTRACTION_AFTER_HUMAN" in names)
            check("8. The ATA is the actual arrival at the port of discharge, "
                  "not any other date on the page",
                  kind == "SUCCESS" and result.get("ata") == "03/10/2026"
                  and result.get("eta") == "02/10/2026",
                  "{0} {1}".format(kind, result))
            check("9. The existing read-back verification ran for both writes",
                  ("BU", "ATA", "03/10/2026") in VERIFY_CALLS
                  and ("COE", "ETA", "02/10/2026") in VERIFY_CALLS,
                  str(VERIFY_CALLS))
            check("10. Verified writes, and only then, gave SUCCESS",
                  row["state"] == "updated" and names[-1] == "SUCCESS",
                  "{0} {1}".format(row["state"], names[-1:]))
            check("The human action closed as resumed",
                  final["human_action"]["waiting"] is False
                  and final["human_action"]["state"] == "resumed")
            saved = json.loads(A.HUMAN_ACTION_FILE.read_text(encoding="utf-8"))
            check("The resume context is persisted, without secrets",
                  saved["run_id"] == A.RUN_ID and saved["reference"] == "S330348776"
                  and saved["reason"] == "human_verification_required"
                  and set(saved) <= set(A._HUMAN_PERSISTED))
            trail = A.HUMAN_EVENTS_FILE.read_text(encoding="utf-8")
            check("Every event is in the audit log with the run id",
                  all(json.loads(line)["run_id"] == A.RUN_ID
                      for line in trail.splitlines()))
            everything = trail + json.dumps(final) + "\n".join(LOG) + \
                A.HUMAN_ACTION_FILE.read_text(encoding="utf-8")
            check("The security code appears nowhere the run wrote",
                  CODE not in everything)

            # ── B. Verification fails after a human step ──
            rule("6. NO FALSE SUCCESS: THE READ-BACK DISAGREES")
            A.HUMAN_AUTO_RESUME = True         # person finishes at the browser
            PERSON["go"] = True
            READBACK["lie"] = True
            kind, error = process(pages, {"bol_awb": "ANRB76464",
                                          "carrier": "Grimaldi",
                                          "provider": "GRIMALDI",
                                          "current_eta": "", "table_page": 1},
                                  counts)
            READBACK["lie"] = False
            final = state()
            row = [r for r in final["shipments"]
                   if r["reference"] == "ANRB76464"][0]
            names = [e["event"] for e in final["human_events"]]
            check("13. A write that does not read back is FAILED, not SUCCESS",
                  kind == "FAILED" and row["state"] == "failed",
                  "{0} {1} {2}".format(kind, row["state"], error))
            check("...and the human trail says it did not succeed",
                  names[0] == "NOT_SUCCESS_AFTER_HUMAN", str(names[:3]))

            # ── C. The browser session disappears while waiting ──
            rule("7. THE PAUSED BROWSER SESSION IS LOST")
            A.HUMAN_AUTO_RESUME = False
            PERSON["go"] = False
            seen = {}

            def operator_c():
                s = until(lambda: (state().get("human_action") or {}).get(
                    "waiting") and state())
                if not s:
                    return
                act = s["human_action"]
                # Close the tab from OUTSIDE the automation — as a person or a
                # crash would — through the browser's own debugging endpoint.
                targets = json.loads(_OPENER.open(
                    "http://127.0.0.1:{0}/json/list".format(CDP_PORT),
                    timeout=5).read().decode("utf-8"))
                for target in targets:
                    if "/gnet" in target.get("url", ""):
                        _OPENER.open("http://127.0.0.1:{0}/json/close/{1}".format(
                            CDP_PORT, target["id"]), timeout=5).read()
                until(lambda: state()["human_action"].get("state") == "session_lost")
                seen["resume"] = api("/api/human", {
                    "op": "resume", "run_id": act["run_id"],
                    "action_id": act["action_id"], "client_id": "opA"})

            calls = len(VERIFY_CALLS)
            operator = threading.Thread(target=operator_c, daemon=True)
            operator.start()
            kind, error = process(pages, {"bol_awb": "S330221931",
                                          "carrier": "Grimaldi",
                                          "provider": "GRIMALDI",
                                          "current_eta": "", "table_page": 1},
                                  counts)
            operator.join(10)
            final = state()
            row = [r for r in final["shipments"]
                   if r["reference"] == "S330221931"][0]
            check("11. Session missing: the run reports it and moves on",
                  kind == "HUMAN" and getattr(error, "reason", "") == "session_lost"
                  and row["state"] == "failed"
                  and row["outcome"] == A.HUMAN_SESSION_LOST,
                  "{0} {1} {2}".format(kind, getattr(error, "reason", ""), row))
            check("...and a Resume after that gets a clear, recoverable error",
                  seen.get("resume", {}).get("accepted") is False
                  and "no longer available" in seen.get("resume", {}).get(
                      "message", ""), str(seen.get("resume")))
            check("...nothing was written for it", len(VERIFY_CALLS) == calls)
            pages.pop("GRIMALDI", None)        # a fresh tab for the next one

            # ── D. Nobody comes ──
            rule("8. HUMAN TIMEOUT")
            os.environ["HUMAN_WAIT_MS"] = "2500"
            calls = len(VERIFY_CALLS)
            kind, error = process(pages, {"bol_awb": "S330000001",
                                          "carrier": "Grimaldi",
                                          "provider": "GRIMALDI",
                                          "current_eta": "", "table_page": 1},
                                  counts)
            final = state()
            row = [r for r in final["shipments"]
                   if r["reference"] == "S330000001"][0]
            check("12. WAITING_FOR_HUMAN -> HUMAN_TIMEOUT when nobody acts",
                  kind == "HUMAN" and row["state"] == "human_timeout"
                  and final["human_action"]["state"] == "timeout",
                  "{0} {1}".format(kind, row["state"]))
            check("...nothing was written to the Hub", len(VERIFY_CALLS) == calls)
            check("...and the timeout is the configured one, not hard-coded",
                  final["human_action"]["timeout_s"] == 2)
            check("The dashboard counts it apart from skipped and failed",
                  final["counters"]["needs_human"] == 2, str(final["counters"]))
            os.environ.pop("HUMAN_WAIT_MS", None)

            # ── E. Everyone else ──
            rule("9. CARRIERS THAT NEED NO PERSON ARE UNAFFECTED")
            before = len(final["human_events"])
            last = A.HUMAN_STATE["last"]
            kind, result = process(pages, {"bol_awb": MAERSK_GOOD,
                                           "carrier": "Maersk",
                                           "provider": "MAERSK",
                                           "current_eta": "", "table_page": 1},
                                   counts)
            final = state()
            row = [r for r in final["shipments"]
                   if r["reference"] == MAERSK_GOOD][0]
            check("14. Maersk runs straight through: no human step at all",
                  kind == "SUCCESS" and row["state"] == "updated"
                  and len(final["human_events"]) == before
                  and A.HUMAN_STATE["last"] is last,
                  "{0} {1}".format(kind, row["state"]))
            check("...and its date is not put through the human-step "
                  "validation path", "eta_rejected" not in (result or {}))

            rule("11. THE DASHBOARD SHOWS IT AND THE BUTTONS WORK")
            ui_action = {"run_id": A.RUN_ID, "action_id": "uiaction01",
                         "reference": "S330999999", "carrier": "Grimaldi Lines",
                         "reason": "human_verification_required",
                         "opened_at": A._human_now(), "deadline": A._human_now(),
                         "timeout_s": 180,
                         "instructions": "Type the security code and press Search."}
            A.tower.shipment_started({"bol_awb": "S330999999",
                                      "carrier": "Grimaldi",
                                      "provider": "GRIMALDI"})
            A.tower.human_action_opened(ui_action)
            A.tower_control.set_human_pending({
                "waiting": True, "run_id": A.RUN_ID, "action_id": "uiaction01"})
            errors = []
            ui = context.new_page()
            ui.add_init_script(
                "try { sessionStorage.setItem('ct-intro', '1'); } catch (e) {}")
            ui.on("pageerror", lambda error: errors.append(str(error)))
            ui.goto("http://127.0.0.1:{0}/".format(DASH_PORT))
            ui.wait_for_selector("#hbox:not([hidden])", timeout=15000)
            text = ui.inner_text("#hbox")
            check("HUMAN ACTION REQUIRED is on screen with carrier, shipment, "
                  "run and reason", "HUMAN ACTION REQUIRED" in text
                  and "Grimaldi Lines" in text and "S330999999" in text
                  and A.RUN_ID in text and "Human verification required" in text,
                  text[:200])
            check("Open Browser Session and Resume are offered",
                  ui.is_visible("#hOpen") and ui.is_visible("#hResume"))
            ui.click("#hResume")
            ui.wait_for_function(
                "document.getElementById('hMsg').textContent.indexOf('Resume sent') >= 0",
                timeout=10000)
            queued = A.tower_control.take_human_requests()
            check("Resume posts a request scoped to this run and action",
                  len(queued) == 1 and queued[0]["run_id"] == A.RUN_ID
                  and queued[0]["action_id"] == "uiaction01"
                  and queued[0]["op"] == "resume", str(queued))
            ui.goto("http://127.0.0.1:{0}/".format(DASH_PORT))
            ui.wait_for_selector("#shipRows tr, #hbox", timeout=10000)
            check("The page runs without script errors", not errors, str(errors))
            A.tower.human_action_closed("timeout", "test over")
            browser.close()
    server.shutdown()

rule("10. THE WAIT ONLY WAITS")
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
body = SRC.split("def wait_for_human")[1].split("\ndef human_shipment_closed")[0]
handler = SRC.split("def _handle_human_request")[1].split("\ndef wait_for_human")[0]
for token in (".fill(", ".type(", ".press(", ".click(", "goto(", "reload("):
    check("The human wait never calls {0}".format(token),
          token not in body and token not in handler)
check("Opening the session only brings the existing tab forward",
      "page.bring_to_front()" in handler and "new_page" not in handler)

print()
print("=" * 74)
print("{0} passed, {1} failed{2}".format(
    len(PASS), len(FAIL), ", {0} skipped".format(len(SKIP)) if SKIP else ""))
print("=" * 74)
sys.exit(1 if FAIL else 0)
