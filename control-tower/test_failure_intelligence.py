"""
ATLAS failure intelligence — "why the error?" answered from the run's own
records, end to end.

Nothing here hands ATLAS a prepared answer. Each scenario drives the real
code path and then asks ATLAS:

  1. MSC, read-only. A real Chromium page serves an MSC result; the real
     get_provider_result() reads it and raises the real SkipShipment; the
     outcome is mapped exactly as main() maps it; the real bridge records it.
     Then "why the error?" must be answered from that record — identity,
     carrier, the ETA that was read, the read-only condition — with facts
     and inferences kept apart, no invented root cause, and no "I don't
     have that information".
  2. The operator questions the brief lists, against the same run.
  3. The generic fallback is refused whenever a failure exists.
  4. One source: what ATLAS noticed, the chat, the panel's card agree.
  5. Observability: the structured events, in the run log, with no secrets.
  6. Recovery with a strategy that VERIFIES: a real page that is not ready,
     the real atlas_recover() with the production action table, the
     caller's own check as verification. Learning credits the strategy only
     when the shipment then verifies; five such runs make it a verified
     strategy that the next failure's plan names.
  7. Recovery that FAILS: no success claim, no positive credit, the failure
     remains unverified.
  8. Classification and root-cause rules on their own.

The Hub is not available here: where a scenario needs the Hub's read-back,
the bridge's own view_updated(..., verified=...) is called with the result
a read-back would report, and the test says so. The carrier pages and the
recovery are real browser work.

Run:  python test_failure_intelligence.py
"""

import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

TMP = Path(tempfile.mkdtemp(prefix="ct_failure_intel_"))
os.environ.setdefault("ATLAS_INTEL_DIR", str(TMP / "intel"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")
os.environ["ML_TELEMETRY_PATH"] = str(TMP / "telemetry.jsonl")
os.environ["ML_CHAMPION_PATH"] = str(TMP / "champion.json")
os.environ["ML_CHALLENGER_PATH"] = str(TMP / "challenger.json")

import update_eta as A                                          # noqa: E402
from dashboard import assistant                                 # noqa: E402
from dashboard.bridge import ControlTowerState, INTEL_EVENTS    # noqa: E402
from intelligence import events, failures as F, learning        # noqa: E402

PASS, FAIL = [], []
FALLBACK = "I don't have that information"


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(str(detail)[:600]) if detail and not condition
                                 else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


# ─────────────────────────────────────────────────────────────────────────
# A LOCAL CARRIER SITE: MSC searched from its page; and a page that is not
# ready on first load (or never)
# ─────────────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("FAILURE_STUB_PORT", "9733"))
BASE = "http://127.0.0.1:{0}".format(PORT)
MSC_REF = "MEDUHP69377"
HITS = {}

SEARCH = ("<!doctype html><html><body><h1>Track a shipment</h1>"
          "<form onsubmit=\"event.preventDefault();location.href='/result?ref='"
          "+encodeURIComponent(document.getElementById('r').value)\">"
          "<input id='r' type='text' placeholder='Container / Bill of Lading number'>"
          "<button type='submit'>Search</button></form>"
          "<p>" + "x" * 200 + "</p></body></html>")


def msc_result(reference):
    return ("<!doctype html><html><body><h1>Tracking</h1>"
            "<p>Bill of Lading " + reference + "</p>"
            "<table><tr><td>Port of Load VALENCIA</td><td>Departed 12/10/2026</td></tr>"
            "<tr><td>Port of Discharge ALEXANDRIA</td><td>ETA 10/11/2026</td></tr></table>"
            "<p>" + "x" * 200 + "</p></body></html>")


READY = ("<!doctype html><html><body><h1>Shipment panel</h1>"
         "<p>" + "Panel content. " * 30 + "</p></body></html>")
NOT_READY = "<!doctype html><html><body><p>Loading…</p></body></html>"


class Site(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        HITS[parsed.path] = HITS.get(parsed.path, 0) + 1
        if parsed.path.startswith("/result"):
            body = msc_result(parse_qs(parsed.query).get("ref", [""])[0])
        elif parsed.path.startswith("/flaky/"):
            # Not ready on the first load; ready from the second load on —
            # the condition a reload genuinely fixes.
            body = NOT_READY if HITS[parsed.path] == 1 else READY
        elif parsed.path.startswith("/broken/"):
            body = NOT_READY                      # never becomes ready
        else:
            body = SEARCH
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


server = ThreadingHTTPServer(("127.0.0.1", PORT), Site)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()


def launch(playwright):
    last = None
    options = [{"headless": True, "channel": "msedge"},
               {"headless": True, "channel": "chrome"}, {"headless": True}]
    if Path("/opt/pw-browsers").is_dir():
        options += [{"headless": True, "executable_path": str(binary)}
                    for binary in sorted(Path("/opt/pw-browsers").glob(
                        "chromium-*/chrome-linux/chrome"))]
    for option in options:
        try:
            return playwright.chromium.launch(**option), None
        except Exception as error:
            last = str(error).split("\n")[0][:100]
    return None, last


LOG = []
A.write_log = lambda message, *a, **k: LOG.append(str(message))
A.save_page_text = lambda *args, **kwargs: None
A.take_screenshot = lambda *args, **kwargs: None
A.PAGE_SETTLE_MAX_SECONDS = 1           # the stub pages render at once
A.PORTALS["MSC"] = dict(A.PORTALS["MSC"], urls=[BASE + "/search"], wait=6)
A.OCEAN_WRITE = False                   # the operator's stop switch (OCEAN_WRITE=0)


def main_mapping(tower, shipment, error):
    """main()'s per-shipment exception mapping, line for line (see below)."""
    if isinstance(error, A.SkipShipment):
        tower.shipment_finished(shipment["bol_awb"], "SKIPPED", str(error),
                                outcome=A.classify_failure(error),
                                failure=getattr(error, "failure", None))
    else:
        tower.shipment_finished(shipment["bol_awb"], "FAILED", str(error),
                                outcome=A.classify_failure(error),
                                failure=getattr(error, "failure", None))


def ask(question, state, context=None):
    return assistant.answer(question, state, context or {})


try:
    from playwright.sync_api import sync_playwright
except Exception as error:                              # pragma: no cover
    sync_playwright = None
    print("Playwright is not available: {0}".format(error))
    FAIL.append("playwright available")

playwright = sync_playwright().start() if sync_playwright else None
browser, why = launch(playwright) if playwright else (None, "no playwright")
if browser is None:
    FAIL.append("a real browser launched")
    print("  FAIL  a real browser launched  ({0})".format(why))
    print()
    print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
    sys.exit(1)
context = browser.new_context()


# ═════════════════════════════════════════════════════════════════════════
rule("1. MSC MEDUHP69377 — READ, NOT WRITTEN; 'WHY THE ERROR?' FROM THE RECORD")
# ═════════════════════════════════════════════════════════════════════════
tower = A.tower
tower.attach_intelligence(events)
tower.set_log_hook(A.write_log)
tower.run_started(run_id="20261005-160000-msc001", dry_run=False,
                  target_status="Under Clearance", max_records=200, max_pages=10)
ship = {"bol_awb": MSC_REF, "carrier": "MSC", "provider": "MSC",
        "current_eta": "05/11/2026", "table_page": 1}
pages = {"DHL": context.new_page()}
tower.shipment_started(ship)                                   # 1. shipment opened
raised = None
try:
    A.get_provider_result(pages, ship)                         # 2-6. carrier, page, ETA, write check
except Exception as error:
    raised = error
check("The real ocean path stopped with SkipShipment", isinstance(raised, A.SkipShipment),
      repr(raised))
check("...whose message says what was read and that it was not written",
      raised is not None and "eta 10/11/2026" in str(raised) and "Not written" in str(raised),
      str(raised))
declared = getattr(raised, "failure", None) or {}
check("...and which DECLARES the failure: category, stage, the deciding setting",
      declared.get("category") == "CARRIER_POLICY_BLOCK" and declared.get("stage") == "hub_write"
      and (declared.get("cause") or {}).get("name") == "OCEAN_WRITE", str(declared))
check("classify_failure() names it a policy block, not NO RESULT",
      A.classify_failure(raised) == A.WRITE_BLOCKED, A.classify_failure(raised))
main_mapping(tower, ship, raised)                              # 7. failure state
SRC = (HERE / "update_eta.py").read_text(encoding="utf-8")
FIX = (HERE / "fixtures" / "remote_runner.py").read_text(encoding="utf-8")
check("main() passes the declared failure to the bridge on both failure paths",
      SRC.count('failure=getattr(error, "failure", None)') >= 2)
check("...and so does the remote runner fixture that mirrors main()",
      FIX.count('failure=getattr(error, "failure", None)') >= 2)

state = tower.snapshot()
record = [r for r in state["shipments"] if r["reference"] == MSC_REF][0]
check("The bridge record keeps what was read (ETA 10/11/2026)",
      record.get("provider_eta") == "10/11/2026", str({k: record.get(k) for k in
                                                       ("provider_eta", "provider_status")}))
check("...the outcome class and the declared failure",
      record.get("outcome") == A.WRITE_BLOCKED and (record.get("failure") or {}).get("category")
      == "CARRIER_POLICY_BLOCK", str(record.get("failure")))
check("...and the steps that happened, in order",
      len(record.get("steps") or []) >= 2, str(record.get("steps")))

r = ask("why the error?", state)
text = r["answer"]
print("\n--- ATLAS: why the error? ---\n" + text + "\n---")
check("Not the generic fallback", FALLBACK not in text)
check("Shipment identity", MSC_REF in text)
check("Carrier", "MSC" in text)
check("In a sentence: the ETA was read, and why it was not written (OCEAN_WRITE)",
      "10/11/2026" in text and "OCEAN_WRITE" in text and "\n" not in text, text)
r = ask("Explain why the error happened", state)
text = r["answer"]
check("Asked to explain: the full diagnosis, for the same shipment", MSC_REF in text and
      "**Fact**" in text, text[:200])
check("Successful ETA extraction, as read", "10/11/2026" in text and
      re.search(r"Data extraction succeeded|eta 10/11/2026 was read", text, re.I) is not None)
check("The read-only write condition, from the run's own declaration",
      "OCEAN_WRITE" in text and "read-only" in text and "not performed" in text.lower()
      or "Nothing was written to the Hub" in text, text)
check("Facts and inferences are labelled apart",
      "**Fact**" in text and ("**Not established**" in text or "**Inference**" in text))
check("Every fact line is backed by the record (no invented values)",
      all(any(token in line for token in ("MEDUHP69377", "MSC", "10/11/2026", "OCEAN_WRITE",
                                          "Hub", "Stopped at", "Last successful", "write",
                                          "Searching", "Opening", "read"))
          for line in text.splitlines() if line.startswith("**Fact**")), text)
check("The root cause is the one the run declared, called confirmed — no other cause invented",
      "confirmed: declared by the run's own code" in text
      and not re.search(r"\b(network|timeout|captcha|login|password|outage)\b", text, re.I), text)
check("Why the policy is set is NOT presented as established",
      "Not established" in text and "is not something this run can establish" in text, text)
check("A recommendation, decided by a person — no production change by ATLAS",
      "**Recommendation**" in text and "OCEAN_WRITE" in text, text)
check("Grounded, with its sources", r.get("grounded") is True and
      "failure intelligence" in (r.get("sources") or []), str(r.get("sources")))
mutated = dict(state, shipments=[dict(record, provider_eta="11/11/2026",
                                      error=record["error"].replace("10/11/2026", "11/11/2026"),
                                      steps=[dict(s, text=s["text"].replace("10/11/2026", "11/11/2026"))
                                             for s in record.get("steps") or []],
                                      failure=dict(record["failure"], observed={"eta": "11/11/2026"},
                                                   last_success="eta 11/11/2026 read from MSC"))])
check("...and a different record gives a different answer (nothing is cached or canned)",
      "11/11/2026" in ask("why the error?", mutated)["answer"]
      and "10/11/2026" not in ask("why the error?", mutated)["answer"])
ASSISTANT_SRC = (HERE / "dashboard" / "assistant.py").read_text(encoding="utf-8")
FAILURES_SRC = (HERE / "intelligence" / "failures.py").read_text(encoding="utf-8")
check("Neither ATLAS nor the failure module hard-codes MEDUHP69377 or 'ocean carriers'",
      MSC_REF not in ASSISTANT_SRC + FAILURES_SRC
      and "ocean carriers" not in ASSISTANT_SRC + FAILURES_SRC)


# ═════════════════════════════════════════════════════════════════════════
rule("2. THE OPERATOR'S QUESTIONS, AGAINST THE SAME RUN")
# ═════════════════════════════════════════════════════════════════════════
EXPECT = [
    ("Why did the error happen?", [MSC_REF, "10/11/2026", "OCEAN_WRITE"]),
    ("What failed?", [MSC_REF, "MSC"]),
    ("Which shipment failed?", [MSC_REF]),
    ("Which carrier failed?", ["MSC", MSC_REF]),
    ("What stage failed?", ["Hub write", "Stage"]),
    ("What was the last successful step?", ["Last successful step", "10/11/2026"]),
    ("What do you know for sure?", ["**Fact**", "10/11/2026", "OCEAN_WRITE"]),
    ("Is the root cause confirmed?", ["confirmed", "OCEAN_WRITE"]),
    ("Did you try recovery?", ["No recovery was attempted for " + MSC_REF]),
    ("What recovery options do we have?", ["No verified recovery strategy exists for this failure class"]),
    ("What should I do next?", ["**Recommendation**", "OCEAN_WRITE"]),
    ("Show me the evidence.", ["No screenshot was captured", "the run's message"]),
    ("What changed in this run?", [MSC_REF]),
    ("Was this a known failure?", ["learning store", "CARRIER_POLICY_BLOCK"]),
    ("What has ATLAS learned from similar failures?", ["CARRIER_POLICY_BLOCK"]),
    ("What did you notice?", [MSC_REF]),
]
for question, needles in EXPECT:
    r = ask(question, state)
    missing = [n for n in needles if n not in r["answer"]]
    check("'{0}' — answered from this run".format(question),
          not missing and FALLBACK not in r["answer"],
          "missing {0}: {1}".format(missing, r["answer"][:500]))
r = ask("What recovery options do we have?", state)
check("A policy block gets no invented recovery: no built-in action is offered for it",
      "reload_page" not in r["answer"] and "retry_navigation" not in r["answer"], r["answer"])
r = ask("Show me the evidence.", state)
check("No screenshot is invented when none was captured",
      r.get("evidence_id") is None and "won't make one up" in r["answer"], str(r)[:300])


# ═════════════════════════════════════════════════════════════════════════
rule("3. NO GENERIC FALLBACK WHEN A FAILURE EXISTS")
# ═════════════════════════════════════════════════════════════════════════
for question in ("why?", "what's wrong?", "explain the failure", "what is the problem?",
                 "why was it skipped?", "why didn't it write?", "what went wrong with MSC?"):
    r = ask(question, state)
    check("'{0}' is not answered with the missing-information reply".format(question),
          FALLBACK not in r["answer"] and MSC_REF in r["answer"], r["answer"][:300])
empty = ControlTowerState()
empty.run_started(run_id="20261005-170000-empty0")
empty.shipment_started({"bol_awb": "N1", "carrier": "DHL Express", "provider": "DHL"})
empty.shipment_finished("N1", "SUCCESS", "", {})
r = ask("why the error?", empty.snapshot())
check("With no failure in the run, ATLAS says there is none — it does not invent one",
      "N1" not in r["answer"] or "fail" in r["answer"].lower(), r["answer"][:300])
check("...and the failure intelligence is empty", F.build(empty.snapshot()) == [])
r = ask("this is broken", state)
check("A statement (not a question) is not forced into a diagnosis",
      r.get("intent") != "failure_why", str(r.get("intent")))


# ═════════════════════════════════════════════════════════════════════════
rule("4. ONE SOURCE: NOTICED, CHAT, PANEL CARD")
# ═════════════════════════════════════════════════════════════════════════
noticed = [n for n in assistant.notices(assistant.RunData(state)) if n["level"] != "ok"]
check("ATLAS noticed the blocked write", any(MSC_REF in n["text"] for n in noticed),
      str(noticed))
r = ask("What did you notice?", state)
check("'What did you notice?' lists exactly what the panel noticed",
      all(n["text"] in r["answer"] for n in noticed) and r["answer"].startswith("I noticed"),
      r["answer"])
brief = assistant.atlas_brief(state)
card = [c for c in brief.get("failures", []) if c["reference"] == MSC_REF]
check("The panel's failure card is the same record", card and card[0]["classification"]
      == "CARRIER_POLICY_BLOCK" and card[0]["root_cause_status"] == "VERIFIED", str(card)[:400])
check("...in the panel's order: what happened, why it matters, known, inferred, plan, "
      "verification, learning",
      card and all(k in card[0] for k in ("headline", "impact", "known", "inferred", "plan",
                                          "verification", "learning")), str(card)[:300])
same = F.build(state, **F.context())[0]
check("Chat, card and noticed share one failure id",
      card and card[0]["failure_id"] == same["failure_id"]
      == ask("why the error?", state).get("failure_id"))
UI = (HERE / "dashboard" / "static" / "index.html").read_text(encoding="utf-8")
CARD_JS = UI[UI.find("function fiCard("):UI.find("function paintFailures(")]
ORDER = ["What happened", "Why it matters", "Known", "Inferred", "Recovery plan",
         "Verification", "Learning"]
positions = [CARD_JS.find("<dt>{0}</dt>".format(label)) for label in ORDER]
check("The ATLAS page's failure card reads in the brief's order: " + " → ".join(ORDER),
      "fiCard" in UI and all(p >= 0 for p in positions) and positions == sorted(positions),
      str(dict(zip(ORDER, positions))))
check("The chat brief counts only what needs attention (not calm notes)",
      "n.level !== 'ok'" in UI)


# ═════════════════════════════════════════════════════════════════════════
rule("5. OBSERVABILITY — STRUCTURED, IN THE RUN LOG, NO SECRETS")
# ═════════════════════════════════════════════════════════════════════════
log = state.get("intel_log") or []
names = [e["event"] for e in log]
for event in ("failure_detected", "failure_classified", "diagnosis_created",
              "recovery_plan_created", "learning_recorded"):
    check("{0} recorded for {1}".format(event, MSC_REF),
          any(e["event"] == event and e.get("reference") == MSC_REF for e in log), str(names))
check("failure_classified carries the category and its basis",
      any(e["event"] == "failure_classified" and e.get("classification") == "CARRIER_POLICY_BLOCK"
          and e.get("basis") == "declared" for e in log),
      str([e for e in log if e["event"] == "failure_classified"]))
check("Every event name is one of the eleven defined", set(names) <= set(INTEL_EVENTS),
      str(set(names) - set(INTEL_EVENTS)))
atlas_lines = [line for line in LOG if line.startswith("[ATLAS] ")]
check("The same events reach the run log as structured lines",
      any("failure_detected" in line and MSC_REF in line for line in atlas_lines), str(atlas_lines[:4]))
SECRET = re.compile(r"password|passwd|secret|token|captcha (value|answer|code)|security code\s*[:=]",
                    re.I)
check("No credential or security-code value in any intelligence line",
      not any(SECRET.search(line) for line in atlas_lines)
      and not any(SECRET.search(str(e)) for e in log))
learning_row = [e for e in events.all_events() if e.get("reference") == MSC_REF]
check("The learning store holds the outcome as SKIPPED and unverified — no credit",
      learning_row and learning_row[-1]["result"] == "SKIPPED" and learning_row[-1]["verified"] is False,
      str(learning_row)[:300])
check("...and the failure record says so", "no positive learning credit" in same["learning_status"],
      same["learning_status"])


# ═════════════════════════════════════════════════════════════════════════
rule("6. RECOVERY THAT VERIFIES — REAL PAGE, REAL EXECUTOR, REAL CHECK")
# ═════════════════════════════════════════════════════════════════════════
# The run's own sequence at a page step: the readiness check fails, the
# executor diagnoses PAGE_NOT_READY, plans from the fixed action table, runs
# each action against the real page and verifies with the caller's check.
# Learning credits a strategy only when the SHIPMENT then verifies.
check("Recovery is enabled and the learning layer is present",
      A.ATLAS_RECOVERY_ENABLED and A.ML_AVAILABLE)
PROVIDER = "AFKL"


def page_step(tower, run_id, reference, path, hub_verified):
    """One shipment: open the page, recover if it is not ready, finish."""
    tower.run_started(run_id=run_id, dry_run=False)
    shipment = {"bol_awb": reference, "carrier": "Air France KLM Cargo", "provider": PROVIDER,
                "table_page": 1}
    tower.shipment_started(shipment)
    page = context.new_page()
    page.goto(BASE + path, wait_until="domcontentloaded")
    tower.step("Opening the shipment panel", system=PROVIDER)
    outcome = None
    if not A.page_has_content(page):                      # DETECT
        error = Exception("the panel is not ready")
        plan = {"field_name": "ETA", "value": "02/11/2026",
                "context": {"provider": PROVIDER, "page": "shipment", "field": "ETA"},
                "in_write": False, "candidates_locators": [], "shipment": shipment}
        outcome = A.atlas_recover(page, error, plan,     # PLAN → ACT → VERIFY
                                  verify=lambda: A.page_has_content(page),
                                  category="PAGE_NOT_READY")
        if not (outcome["recovered"] and outcome["verified"] is True):
            main_mapping(tower, shipment, error)
            page.close()
            return outcome, tower.snapshot()
    # The page is ready: the run reads and writes; the Hub's read-back is
    # reported through the bridge's own call (no Hub in a test).
    tower.provider_result({"provider": PROVIDER, "tracking_status": "Arrived",
                           "eta": "02/11/2026"})
    tower.view_updated("BU", "ETA", "02/11/2026", verified=hub_verified)
    tower.shipment_finished(reference, "SUCCESS", "", {"bu": "ETA updated with 02/11/2026"})
    page.close()
    return outcome, tower.snapshot()


rec_tower = ControlTowerState()
rec_tower.attach_intelligence(events)
rec_tower.set_log_hook(A.write_log)
saved_tower, A.tower = A.tower, rec_tower
try:
    out, snap = page_step(rec_tower, "20261005-180000-rec001", "074-00000001", "/flaky/1", True)
    tried = [a["action"] for a in (snap.get("recovery_history") or [{}])[0].get("attempts", [])] \
        if snap.get("recovery_history") else []
    print("    executor tried, in order:", tried)
    check("DETECT → PLAN: the executor diagnosed PAGE_NOT_READY and planned from the table",
          out is not None and out["error_class"] == "PAGE_NOT_READY" and out["considered"],
          str(out and {k: out[k] for k in ("error_class", "considered", "reason")}))
    check("ACT → VERIFY: a real action made the page ready and the caller's check confirmed it",
          out["recovered"] is True and out["verified"] is True,
          str(out["reason"]))
    check("...and the action that verified is the one that genuinely fixes this page: reload_page",
          "reload_page succeeded and verified" in out["reason"] and HITS.get("/flaky/1") == 2,
          "{0} / hits {1}".format(out["reason"], HITS.get("/flaky/1")))
    check("Actions that ran but did not verify are recorded as failed, not as successes",
          all(a["result"] in ("FAILED", "SUCCESS") for a in
              snap["recovery_history"][0]["attempts"]) and
          [a["action"] for a in snap["recovery_history"][0]["attempts"]
           if a["result"] == "SUCCESS"] == ["reload_page"], str(snap["recovery_history"][0]["attempts"]))
    log = snap["intel_log"]
    seq = [e["event"] for e in reversed(log) if e.get("reference") == "074-00000001"]
    check("Observed in order: plan → started → verification → passed → completed → learning",
          all(e in seq for e in ("recovery_plan_created", "recovery_started", "verification_started",
                                 "verification_passed", "recovery_completed", "learning_recorded"))
          and seq.index("recovery_plan_created") < seq.index("recovery_started")
          < seq.index("verification_passed") < seq.index("recovery_completed")
          < seq.index("learning_recorded"), str(seq))
    issue = (learning.find_issue(learning.build(), provider=PROVIDER, name="PAGE_NOT_READY") or [None])[0]
    strat = (issue or {}).get("strategies", {}).get("reload_page", {})
    check("LEARN: the verified recovery on a verified shipment is credited once",
          strat.get("successes") == 1, str(strat))

    # The same recovery, but the Hub read-back did not confirm: no credit.
    out, snap = page_step(rec_tower, "20261005-180500-rec002", "074-00000002", "/flaky/2", None)
    issue = learning.find_issue(learning.build(), provider=PROVIDER, name="PAGE_NOT_READY")[0]
    strat = issue["strategies"]["reload_page"]
    check("A recovery that verified on a shipment whose write was NOT read back earns no credit",
          out["verified"] is True and strat["successes"] == 1 and strat.get("unverified", 0) >= 1,
          str(strat))

    # Before any strategy has enough verified record, the plan says so.
    out, snap = page_step(rec_tower, "20261005-181000-rec003", "074-00000003", "/broken/3", True)
    fails = F.build(snap, **F.context())
    f = [x for x in fails if x["shipment_id"] == "074-00000003"][0]
    check("A failure before the record is sufficient: no verified strategy is claimed",
          f["recovery_plan"]["status"] != "VERIFIED_STRATEGY"
          and not any(s["source"] == "verified history" for s in f["recovery_plan"]["steps"]),
          str(f["recovery_plan"]["status"]))

    # Four more real runs where reload genuinely fixes the page and the
    # shipment verifies: the record becomes sufficient by the store's own
    # unchanged thresholds.
    for n in range(4, 8):
        page_step(rec_tower, "20261005-18{0:02d}00-rec{1:03d}".format(n * 5, n),
                  "074-0000000{0}".format(n), "/flaky/{0}".format(n), True)
    issue = learning.find_issue(learning.build(), provider=PROVIDER, name="PAGE_NOT_READY")[0]
    strat = issue["strategies"]["reload_page"]
    check("Five verified recoveries on verified shipments: reload_page is the best strategy",
          strat["successes"] == 5 and issue["best"] == "reload_page"
          and strat["confidence"] != "Insufficient data", str({k: strat.get(k) for k in
                                                               ("successes", "failures", "confidence")}))
    check("The thresholds are the store's own, unchanged",
          learning.MIN_RANKED == 3 and learning.confidence(4) == "Insufficient data")
finally:
    A.tower = saved_tower


# ═════════════════════════════════════════════════════════════════════════
rule("7. A FAILURE WITH A VERIFIED STRATEGY — AND THE STRATEGY FAILS")
# ═════════════════════════════════════════════════════════════════════════
fail_tower = ControlTowerState()
fail_tower.attach_intelligence(events)
saved_tower, A.tower = A.tower, fail_tower
try:
    before = learning.find_issue(learning.build(), provider=PROVIDER,
                                 name="PAGE_NOT_READY")[0]["strategies"]["reload_page"]
    out, snap = page_step(fail_tower, "20261005-190000-rec009", "074-00000009", "/broken/9", True)
    check("The executor tried and nothing verified: recovery reports failure",
          out["recovered"] is False and out["verified"] is not True, str(out["reason"]))
    f = [x for x in F.build(snap, **F.context()) if x["shipment_id"] == "074-00000009"][0]
    check("DETECT: the shipment is a failure record classified PAGE_NOT_READY",
          f["classification"] == "PAGE_NOT_READY", f["classification"])
    check("PLAN: the verified strategy is named, from verified history, with its evidence",
          f["recovery_plan"]["status"] == "VERIFIED_STRATEGY"
          and f["recovery_plan"]["steps"][0]["strategy"] == "reload_page"
          and f["recovery_plan"]["steps"][0]["source"] == "verified history"
          and "5 verified successes" in f["recovery_plan"]["steps"][0]["evidence"],
          str(f["recovery_plan"]["steps"][:1]))
    check("...and it executes only through the run, never by ATLAS",
          "never by ATLAS" in f["recovery_plan"]["steps"][0]["executes"])
    check("Each attempt is recorded once, as the executor ran it",
          [a["action"] for a in f["recovery_attempts"]] == [a["action"] for a in
                                                            snap["recovery_history"][0]["attempts"]],
          str(f["recovery_attempts"]))
    check("The attempts are recorded as tried and failed — none as verified",
          f["recovery_attempts"] and not any(a["verified"] is True for a in f["recovery_attempts"])
          and f["recovery_result"] == "EXHAUSTED", str(f["recovery_attempts"]))
    check("The root cause stays INFERRED, not confirmed", f["root_cause_status"] == "INFERRED")
    r = ask("Did you try recovery?", snap)
    text = r["answer"]
    print("\n--- ATLAS: did you try recovery? ---\n" + text + "\n---")
    check("No success claim", not re.search(r"\bworked\b|succeeded|recovered the|"
                                            r"verified, so processing continued", text)
          and "0 recovered" in text, text)
    check("...it says every strategy was tried and none verified",
          "None produced a verified result" in text, text)
    check("...and names the verified strategy as the plan, without claiming it worked here",
          "reload_page" in text and "verified history" in text, text)
    after = learning.find_issue(learning.build(), provider=PROVIDER,
                                name="PAGE_NOT_READY")[0]["strategies"]["reload_page"]
    check("No positive learning credit: successes unchanged, the failure counted",
          after["successes"] == before["successes"] and after["failures"] == before["failures"] + 1,
          "before {0}/{1} after {2}/{3}".format(before["successes"], before["failures"],
                                               after["successes"], after["failures"]))
    seq = [e["event"] for e in reversed(snap["intel_log"]) if e.get("reference") == "074-00000009"]
    check("Observed: verification_failed and recovery_failed, then the failure was diagnosed",
          "verification_failed" in seq and "recovery_failed" in seq and "failure_detected" in seq
          and "diagnosis_created" in seq and "recovery_completed" not in seq, str(seq))
    check("The learning status says the outcome earned no credit",
          "no positive learning credit" in f["learning_status"], f["learning_status"])
finally:
    A.tower = saved_tower


# ═════════════════════════════════════════════════════════════════════════
rule("8. CLASSIFICATION AND ROOT CAUSE, ON THEIR OWN")
# ═════════════════════════════════════════════════════════════════════════
def rec(**kw):
    base = {"reference": "R1", "carrier": "X", "provider": "X", "state": "failed", "steps": []}
    base.update(kw)
    return base


check("A declared category is used as declared",
      F.classify(rec(failure={"category": "HUB_READBACK_FAILURE"})) == ("HUB_READBACK_FAILURE", "declared"))
check("An outcome class maps by the run's own table",
      F.classify(rec(outcome="AFKL NAVIGATION ERROR")) == ("NAVIGATION_FAILURE", "outcome"))
check("A message is read by a stated rule, as an inference",
      F.classify(rec(error="Timeout 30000ms exceeded")) == ("TIMEOUT", "message"))
check("Nothing to go on: UNKNOWN_FAILURE, never a guess",
      F.classify(rec(error="something odd")) == ("UNKNOWN_FAILURE", "none"))
u = F.from_record(rec(error="something odd"), run_id="r")
check("...whose root cause is UNKNOWN, with nothing invented",
      u["root_cause_status"] == "UNKNOWN" and u["root_cause"] is None
      and "does not contain enough evidence" in u["unverified"][0]["text"])
check("...and whose plan says no verified recovery strategy exists",
      u["recovery_plan"]["statement"].startswith("No verified recovery strategy exists for this failure class"))
cap = F.from_record(rec(outcome="HUMAN VERIFICATION REQUIRED"), run_id="r")
check("Human verification: the plan is a person's step, never automation",
      cap["recovery_plan"]["status"] == "HUMAN_REQUIRED"
      and cap["recovery_plan"]["steps"][0]["safety"] == "HUMAN_ONLY"
      and not any(s["source"] == "built-in safe recovery rule" for s in cap["recovery_plan"]["steps"]))
check("An expected 'no result' skip is not a failure; a success is not either",
      F.from_record(rec(state="skipped", outcome="NO RESULT")) is None
      and F.from_record(rec(state="updated")) is None)
check("Every category the brief names exists", set(F.CATEGORIES) >= {
    "NAVIGATION_FAILURE", "PAGE_NOT_READY", "AUTHENTICATION_FAILURE", "TIMEOUT", "NETWORK_FAILURE",
    "DATA_EXTRACTION_FAILURE", "VALIDATION_FAILURE", "HUB_WRITE_FAILURE", "HUB_READBACK_FAILURE",
    "CARRIER_POLICY_BLOCK", "HUMAN_ACTION_REQUIRED", "SECURITY_VERIFICATION_REQUIRED",
    "UNKNOWN_FAILURE"})
check("...and the carrier-access categories added for the 6 Oct case",
      {"CARRIER_ACCESS_RESTRICTED", "CARRIER_ACCESS_NOT_CONFIRMED"} <= set(F.CATEGORIES)
      and len(F.CATEGORIES) == 15)
full = F.build(state)[0]
check("The failure record carries every field of the contract", all(k in full for k in (
    "failure_id", "run_id", "shipment_id", "carrier", "operation", "stage", "timestamp",
    "error_type", "error_message", "observed_state", "evidence_refs", "previous_events",
    "recovery_attempts", "recovery_result", "verification_result", "classification",
    "root_cause_status", "recovery_plan", "learning_status")), sorted(full))
check("Every statement carries one of the five knowledge types",
      {i["type"] for i in full["facts"] + full["inferences"] + full["unverified"] +
       full["recommendations"] + full["learned"]} <= {"FACT", "LEARNED", "INFERENCE",
                                                       "RECOMMENDATION", "UNVERIFIED"})

context.close()
browser.close()
playwright.stop()
server.shutdown()

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
