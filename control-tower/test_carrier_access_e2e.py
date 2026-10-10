"""
END TO END — "verification completed, carrier still restricted" (6 Oct 2026).

The production case: a person completed CMA CGM's verification, and CMA CGM
still showed "Access is temporarily restricted". This drives the automation's
own main() — its shipment loop, the real carrier lookup in a real browser,
the human-verification wait, the outcome mapping, the bridge state — and
then asks ATLAS about the run that main() produced.

What is a stand-in, and labelled so:
  * the CMA CGM pages (SIMULATED, 127.0.0.1): a challenge the "person"
    completes after 5 s (a page timer — longer than the carrier's 3 s wait,
    as a real person often is), then either "Access is temporarily
    restricted" or the shipment;
  * the eHub boundary: sign-in, the list of two shipments, and the write,
    which only RECORDS that it was called (so "no Hub write" is observed);
  * Edge: this container has none, so main() launches Chromium.
Everything between is production code, unchanged.

Asserted: verification completed → carrier still restricted → extraction
blocked → no Hub write → FAILED / CARRIER ACCESS RESTRICTED with the run's
evidence → nothing learned as a success → ATLAS explains the cause from the
run → ATLAS gives a diagnose-first, safe recovery plan. And the second bug:
a run where it ended as a plain "no result" skip is still explained, never
"I don't have that information".
"""

import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
WORK = Path(tempfile.mkdtemp(prefix="ct_access_e2e_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ["ATLAS_DATA_ORIGIN"] = "test"

import update_eta as A                                       # noqa: E402
from dashboard import assistant                              # noqa: E402
from dashboard.bridge import ControlTowerState                # noqa: E402
from intelligence import failures as F                       # noqa: E402
from ml import recovery as R                                 # noqa: E402

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


RESTRICTED_REF, OK_REF = "CMAU7700001", "CMAU7700002"
PORT = int(os.environ.get("ACCESS_E2E_PORT", "9763"))
BASE = "http://127.0.0.1:{0}".format(PORT)
SEARCH = ("<!doctype html><html><head><title>Track a shipment</title></head><body>"
          "<h1>Track a shipment</h1><form onsubmit=\"event.preventDefault();location.href="
          "'/result?ref='+encodeURIComponent(document.getElementById('r').value)\">"
          "<input id='r' type='text' placeholder='Container / Bill of Lading number'>"
          "<button type='submit'>Search</button></form><p>" + "x" * 200 + "</p></body></html>")
RESTRICTED_BODY = ("<h1>Access is temporarily restricted</h1>"
                   "<p>(SIMULATED stand-in for the page the operator saw.)</p>")


def result_page(ref):
    after = RESTRICTED_BODY if ref == RESTRICTED_REF else (
        "<h1>Tracking</h1><p>Bill of Lading " + ref + "</p><p>Port of Discharge TEMA</p>"
        "<p>ETA 12/10/2026</p><p>" + "x" * 200 + "</p>")
    # The challenge, which the "person" completes after 5 s — longer than the
    # carrier's 3 s wait below.
    return ("<!doctype html><html><head><title>Security check</title></head><body>"
            "<h1>Security check</h1><p>Please verify you are human.</p><script>setTimeout("
            "function(){document.title='CMA CGM';document.body.innerHTML=" + json.dumps(after) +
            "}, 5000)</script></body></html>")


class Carrier(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        body = result_page(parse_qs(parsed.query).get("ref", [""])[0]) \
            if parsed.path == "/result" else SEARCH
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


server = ThreadingHTTPServer(("127.0.0.1", PORT), Carrier)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()

# ── the boundaries (see the docstring) ────────────────────────────────────
LOG, WRITES, ML = [], [], []
A.write_log = lambda message, *a, **k: LOG.append(str(message))
chromium = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")) \
    if Path("/opt/pw-browsers").is_dir() else []
_launch = A.hub_launch_options
A.hub_launch_options = (lambda: dict(_launch(), channel=None, headless=True,
                                     executable_path=str(chromium[0]))) if chromium else \
    (lambda: dict(_launch(), headless=True))
if chromium:
    _opts = A.hub_launch_options
    A.hub_launch_options = lambda: {k: v for k, v in _opts().items() if k != "channel"}
A.load_credentials = lambda: ("hub.user", "never-shown")
A.login_internal = lambda *a, **k: None
A.ensure_filtered_page = lambda page, view, number: (_ for _ in ()).throw(
    A.SkipShipment("no more pages")) if number > 1 else None
A.collect_supported_shipments = lambda page, n: [
    {"bol_awb": ref, "carrier": "CMA CGM", "provider": "CMA_CGM", "current_eta": "05/11/2026",
     "table_page": 1} for ref in (RESTRICTED_REF, OK_REF)]


def hub_write(page, shipment, result):
    WRITES.append((shipment["bol_awb"], dict(result)))
    A.tower.view_updated("COE", "ETA", result["eta"], verified=True)
    return {"coe": "COE ETA updated with {0} and saved".format(result["eta"]), "bu": ""}


A.update_internal_shipment = hub_write
A.save_result = lambda *a, **k: None
A.wait_between_shipments = lambda: None
def keep_text(page, ref, suffix):
    path = WORK / "{0}_{1}.txt".format(ref, suffix)
    path.write_text(page.locator("body").inner_text(timeout=3000), encoding="utf-8")
    return path


def keep_shot(page, ref, suffix, full_page=True):
    path = WORK / "{0}_{1}.png".format(ref, suffix)
    page.screenshot(path=str(path))
    return path


A.save_page_text, A.take_screenshot = keep_text, keep_shot
_ml_record = A.ml_record


def ml_record(context, strategy, success, *a, **k):
    ML.append((strategy, success, k.get("reference"), a[1] if len(a) > 1 else None))
    return _ml_record(context, strategy, success, *a, **k)


A.ml_record = ml_record
EVENTS = []
_human_event = A.human_event
A.human_event = lambda event, action, detail="", **f: (
    EVENTS.append((event, (action or {}).get("reference"))), _human_event(event, action, detail, **f))[1]
A.PORTALS["CMA_CGM"] = dict(A.PORTALS["CMA_CGM"], urls=[BASE + "/search"], wait=3)
A.PAGE_SETTLE_MAX_SECONDS = 1
A.CAPTCHA_WAIT_MS, A.CAPTCHA_POLL_MS, A.HUMAN_CONFIRM_MS = 30000, 300, 300
A.HUMAN_QUEUE_ON, A.HUMAN_AUTO_RESUME = False, True
A.DASHBOARD_ENABLED, A.ML_AVAILABLE, A.PAUSE_ON_FATAL_ERROR = False, False, False
A.HUMAN_STATE.pop("pending_clear", None)

rule("1. main(): VERIFICATION COMPLETED, CARRIER STILL RESTRICTED")
A.main()
server.shutdown()
state = A.tower.snapshot()
ships = {r["reference"]: r for r in state["shipments"]}
bad, good = ships.get(RESTRICTED_REF) or {}, ships.get(OK_REF) or {}
events_bad = [e for e, ref in EVENTS if ref == RESTRICTED_REF]
check("The person's verification was recorded as its own state: HUMAN_VERIFICATION_COMPLETED",
      "HUMAN_VERIFICATION_COMPLETED" in events_bad and bad.get("human_verification") == "COMPLETED",
      events_bad)
check("...the worker then checked the page and found it still restricted: CARRIER_ACCESS_RESTRICTED",
      "CARRIER_ACCESS_RESTRICTED" in events_bad and events_bad.index("CARRIER_ACCESS_RESTRICTED") >
      events_bad.index("HUMAN_VERIFICATION_COMPLETED") and "CARRIER_ACCESS_CONFIRMED" not in events_bad,
      events_bad)
check("...even though the person took longer than the carrier's wait (5 s > 3 s): the page was read",
      (bad.get("carrier_access") or {}).get("state") == "RESTRICTED", bad.get("carrier_access"))
check("No shipment data extracted from the restricted page", not bad.get("provider_eta")
      and not bad.get("provider_ata"), {k: bad.get(k) for k in ("provider_eta", "provider_ata")})
check("No Hub write for it", RESTRICTED_REF not in [w[0] for w in WRITES], WRITES)
check("Not a success: FAILED, outcome CARRIER ACCESS RESTRICTED",
      bad.get("state") == "failed" and bad.get("outcome") == "CARRIER ACCESS RESTRICTED",
      {k: bad.get(k) for k in ("state", "outcome")})
check("The run's evidence: carrier, verification_completed, carrier_access, extraction, hub_write, "
      "final_result", bad.get("access_check") == {
          "carrier": "CMA CGM", "verification_completed": True, "carrier_access": "restricted",
          "extraction": "not performed", "hub_write": "not performed",
          "final_result": "FAILED — RECOVERY_REQUIRED"}, bad.get("access_check"))
failure = bad.get("failure") or {}
check("...and the declared failure: category, stage, URL, and the page kept",
      failure.get("category") == "CARRIER_ACCESS_RESTRICTED"
      and failure.get("stage") == "carrier_access"
      and (failure.get("observed") or {}).get("url", "").startswith(BASE + "/result?ref=" + RESTRICTED_REF)
      and "access temporarily restricted" in (failure.get("observed") or {}).get("signals", ""),
      failure)
kept = WORK / "{0}_cma_cgm_access_restricted.txt".format(RESTRICTED_REF)
check("The restriction page's own text and a screenshot were kept as evidence",
      kept.exists() and "Access is temporarily restricted" in kept.read_text(encoding="utf-8")
      and (failure.get("observed") or {}).get("screenshot", "").endswith(
          "_cma_cgm_access_restricted.png")
      and Path((failure.get("observed") or {}).get("screenshot", "")).exists())
check("Nothing was learned as a success: no 'captcha_cleared' success for it",
      not [m for m in ML if m[2] == RESTRICTED_REF and m[1] is True]
      and [m for m in ML if m[2] == RESTRICTED_REF and m[0] == "captcha_cleared" and m[1] is False],
      [m for m in ML if m[2] == RESTRICTED_REF])
check("The recovery policy permits no automatic action for it",
      R.why_not("CARRIER_ACCESS_RESTRICTED") and R.classify(
          text="Human verification was completed, but CMA CGM restricted access")[0]
      in ("CARRIER_ACCESS_RESTRICTED",), R.why_not("CARRIER_ACCESS_RESTRICTED"))
check("Not retried in the run (it is parked, not on the deferred-retry list)",
      RESTRICTED_REF not in (A.tower.deferred_retries() or []))

rule("2. THE SAME RUN, WHERE ACCESS WAS GRANTED: ONLY THEN EXTRACTION AND WRITE")
events_good = [e for e, ref in EVENTS if ref == OK_REF]
check("Verification completed, then the shipment page: CARRIER_ACCESS_CONFIRMED after it",
      "CARRIER_ACCESS_CONFIRMED" in events_good and events_good.index("CARRIER_ACCESS_CONFIRMED") >
      events_good.index("HUMAN_VERIFICATION_COMPLETED"), events_good)
check("...extracted and written, once, after access was confirmed",
      [w[0] for w in WRITES] == [OK_REF] and WRITES[0][1].get("eta") == "12/10/2026", WRITES)
check("...SUCCESS, and the cleared verification learned as a success only now",
      good.get("state") == "updated" and [m for m in ML if m[2] == OK_REF
                                          and m[0] == "captcha_cleared" and m[1] is True])
check("The run kept going: 1 written, 1 failed", state["counters"].get("successful") == 1
      and state["counters"].get("failed") == 1, state["counters"])

rule("3. ATLAS, ON THE RUN main() PRODUCED")
NO_INFO = "I don't have that information"


def ask(question, st=None):
    return assistant.answer(question, st or state, {})


why = ask("Why did the error happen?")
check("The short answer: verification got through, the carrier still restricted access, "
      "nothing written — and the recorded reason with its URL",
      "human verification" in why["answer"] and "access-restricted" in why["answer"] and
      "nothing was written to the Hub" in why["answer"] and
      BASE + "/result?ref=" + RESTRICTED_REF in why["answer"] and "\n" not in why["answer"],
      why["answer"])
why = ask("Explain why the error happened")
print("\n--- Explain why the error happened ---\n" + why["answer"][:1800] + "\n---")
check("Answered from the run, never 'I don't have that information'",
      NO_INFO not in why["answer"] and why.get("grounded"), why["answer"][:200])
check("The cause, in the run's words: verification completed, CMA CGM still restricted access, "
      "nothing extracted, no Hub write attempted",
      "Human verification was completed, but CMA CGM continued to restrict access. The carrier "
      "page never became usable for extraction, so no shipment data was extracted and no Hub "
      "write was attempted." in why["answer"], why["answer"][:600])
check("...with the recorded evidence and the restriction URL",
      "verification_completed = true" in why["answer"] and "carrier_access = restricted" in
      why["answer"] and BASE + "/result?ref=" + RESTRICTED_REF in why["answer"])
check("...and says what is NOT established (why the carrier restricts)",
      "not established" in why["answer"])
short = ask("Why the error?")
check("'Why the error?' — the operator's exact words — is answered the same way",
      NO_INFO not in short["answer"] and "access-restricted page" in short["answer"],
      short["answer"][:300])
nxt = ask("What should I do next?")
print("\n--- What should I do next? ---\n" + nxt["answer"][:2200] + "\n---")
check("Next: diagnosis first, from this run",
      nxt["answer"].index("Diagnosis (from this run)") < nxt["answer"].index("Recovery plan")
      if "Diagnosis (from this run)" in nxt["answer"] and "Recovery plan" in nxt["answer"]
      else False, nxt["answer"][:400])
check("...a grounded plan in order: diagnose, identify session/browser/IP/carrier, apply only "
      "the supported change, re-run and verify access before extraction",
      all(s in nxt["answer"] for s in ("1. **Diagnose from this run's evidence**",
                                       "2. **Identify a session, browser, IP or carrier restriction**",
                                       "3. **Apply only the change the finding supports**",
                                       "4. **Re-run, and verify access before extraction**")),
      nxt["answer"][:800])
check("...safe: no automatic recovery, parked as an exception, with the reason",
      "No automatic recovery is permitted" in nxt["answer"] and "Parked as an exception" in
      nxt["answer"] and "not allowed" in nxt["answer"])
check("...the worker diagnostic named with this carrier and shipment",
      "worker.verify carrier --carrier CMA_CGM --reference " + RESTRICTED_REF in nxt["answer"])
check("...and nothing that would get past the carrier's controls is proposed",
      not re.search(r"bypass (the|its)|solve the captcha|rotate|stealth|change (the )?user.?agent",
                    nxt["answer"], re.I))

rule("4. THE OTHER BUG: IT ENDED AS A PLAIN 'NO RESULT' — ATLAS STILL EXPLAINS IT")
st = ControlTowerState()
st.run_started(run_id="20261006-133000-cma002", dry_run=False, target_status="Under Clearance",
               max_records=10, max_pages=1)
st.shipment_started({"bol_awb": "CMAU7700003", "carrier": "CMA CGM", "provider": "CMA_CGM",
                     "current_eta": "", "table_page": 1})
st.human_verification_required("CMAU7700003", "CMA CGM")
live = assistant.answer("Why the error?", st.snapshot(), {})
check("While it waits for the person: where it stands, not 'I don't have that information'",
      NO_INFO not in live["answer"] and "waiting for a person" in live["answer"], live["answer"])
st.human_verification_cleared("CMAU7700003", 40)
live = assistant.answer("Why the error?", st.snapshot(), {})
check("Verification completed, page not yet read: says access is NOT confirmed yet",
      NO_INFO not in live["answer"] and "Carrier access is not confirmed" in live["answer"],
      live["answer"])
# How the 6 Oct run ended before this fix: a plain skip, outcome NO RESULT.
st.shipment_finished("CMAU7700003", "SKIPPED",
                     "CMA CGM returned no arrival date that could be read.", outcome="NO RESULT")
old = assistant.answer("Why the error?", st.snapshot(), {})
print("\n--- the old ending, 'Why the error?' ---\n" + old["answer"][:900] + "\n---")
check("A skip after a completed verification with access never confirmed IS a failure",
      F.is_failure(next(r for r in st.snapshot()["shipments"] if r["reference"] == "CMAU7700003")))
check("ATLAS explains it from the run: verification completed, the shipment page never appeared",
      NO_INFO not in old["answer"] and "After the human verification, CMA CGM's shipment page "
      "never appeared" in old["answer"] and "skipped" not in old["answer"], old["answer"][:400])
old = assistant.answer("Explain why the error happened", st.snapshot(), {})
check("...and asked to explain, the full analysis says the same",
      "Human verification was completed, but CMA CGM never showed the shipment page"
      in old["answer"], old["answer"][:400])
check("...classified CARRIER_ACCESS_NOT_CONFIRMED, from the run's access state",
      F.build(st.snapshot())[0]["classification"] == "CARRIER_ACCESS_NOT_CONFIRMED"
      and F.build(st.snapshot())[0]["classification_basis"] == "access")
check("A plain NO RESULT with no verification is still not a failure (unchanged)",
      not F.is_failure({"state": "skipped", "outcome": "NO RESULT", "reference": "X"}))

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
