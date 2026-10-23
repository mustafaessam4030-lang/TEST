"""
Carrier access: a completed human verification is not access; a restriction
page stops the lookup; the worker diagnostic states only what it can show.

Every carrier page here is a SIMULATED stand-in on 127.0.0.1. Its wording
paraphrases what the operator reported from the real CMA CGM restriction
page (6 Oct 2026) — it is not a copy of that page. Nothing here contacts a
carrier, and nothing tries to get past a restriction.

    1  carrier_restriction(): two signals, no shipment reference -> restricted
    2  the run, in a real browser: restriction at once; after a completed
       verification; and a verification followed by the shipment page —
       HUMAN_VERIFICATION_COMPLETED vs CARRIER_ACCESS_CONFIRMED/RESTRICTED,
       evidence kept, nothing extracted or written
    3  classification, failure intelligence, ATLAS's answer, the work list
    4  the worker diagnostic: determine(), the manual record, the automation
       check against the stand-in, the control plane's level
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
WORK = Path(tempfile.mkdtemp(prefix="ct_carrier_access_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "intel")
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")

import update_eta as A                                       # noqa: E402
from intelligence import failures as F                       # noqa: E402
from intelligence import verification as V                   # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(str(detail)[:400]) if detail and not condition
                                 else ""))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


class Page(object):
    url = "https://carrier.example/tracking"

    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        return self

    def inner_text(self, timeout=None):
        return self.text

    def title(self):
        return "Tracking"

    @property
    def frames(self):
        return []

    @property
    def main_frame(self):
        return None


# SIMULATED — a paraphrase of the operator's report, not CMA CGM's text.
RESTRICTED = ("Access restricted. Your browser's behaviour has attracted our attention. The "
              "current blockage can have different root causes. Please check whether you use a "
              "mobile hotspot, a proxy server or a VPN, and use a normal supported browser such "
              "as Edge, Firefox or Chrome. ")
REF = "CMAU0000001"
LOG = []
A.write_log = lambda message, *a, **k: LOG.append(str(message))

rule("1. carrier_restriction(): WHAT COUNTS AS THE RESTRICTION PAGE")
found = A.carrier_restriction(Page(RESTRICTED + "x" * 100), REF)
check("The restriction wording is recognised, with its signals",
      found and {"attention", "blockage", "mobile hotspot", "proxy server", "VPN",
                 "supported browser"} <= set(found["signals"]), found)
check("...and the causes the page itself names are kept apart",
      found and found["page_names"] == ["mobile hotspot", "proxy server", "VPN",
                                        "supported browser"], found)
check("One signal alone (a footer mentioning a VPN) is not a restriction",
      A.carrier_restriction(Page("Track your cargo. Using a VPN? Contact us. " + "x" * 300), REF)
      is None)
check("A page carrying the shipment's own reference is not a restriction",
      A.carrier_restriction(Page("B/L {0} ETA 12/10/2026. ".format(REF) + RESTRICTED), REF)
      is None)
check("A carrier URL keeps the shipment's reference but never a typed security code",
      A.safe_url("https://g.test/gresult?ship=S330348776&code=7Q4K#x", "S330348776")
      == "https://g.test/gresult?ship=S330348776&code=redacted")
check("A long result page is not mistaken for one",
      A.carrier_restriction(Page(RESTRICTED + "y" * 9000), REF) is None)


rule("2. THE RUN, IN A REAL BROWSER, AGAINST SIMULATED CARRIER PAGES")
PORT = int(os.environ.get("CARRIER_ACCESS_STUB_PORT", "9733"))
BASE = "http://127.0.0.1:{0}".format(PORT)
SEARCH = ("<!doctype html><html><body><h1>Track a shipment</h1>"
          "<form onsubmit=\"event.preventDefault();location.href='/result?ref='"
          "+encodeURIComponent(document.getElementById('r').value)\">"
          "<input id='r' type='text' placeholder='Container / Bill of Lading number'>"
          "<button type='submit'>Search</button></form><p>" + "x" * 200 + "</p></body></html>")


def page_for(ref):
    shipment = ("<h1>Tracking</h1><p>Bill of Lading " + ref + "</p><p>Port of Discharge TEMA</p>"
                "<p>ETA 12/10/2026</p><p>" + "x" * 200 + "</p>")
    restricted = "<h1>Access restricted</h1><p>" + RESTRICTED + "</p><p>" + "z" * 150 + "</p>"
    challenge = "<h1>Security check</h1><p>Please verify you are human.</p>"
    if ref.startswith("RESTRICT"):
        return restricted
    # The "person" completes the check after a moment (SIMULATED): then the
    # carrier shows either its restriction page or the shipment.
    after = restricted if ref.startswith("CHALLENGE_R") else shipment
    return challenge + ("<script>setTimeout(function(){document.body.innerHTML=" +
                        json.dumps(after) + "}, 2500)</script>")


class Carrier(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/ip":
            body, kind = json.dumps({"ip": "203.0.113.7", "org": "AS64500 Example"}), \
                "application/json"
        elif parsed.path == "/result":
            body = "<!doctype html><html><head><title>Tracking</title></head><body>" + page_for(
                parse_qs(parsed.query).get("ref", [""])[0]) + "</body></html>"
            kind = "text/html; charset=utf-8"
        else:
            body, kind = SEARCH, "text/html; charset=utf-8"
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


server = ThreadingHTTPServer(("127.0.0.1", PORT), Carrier)
server.daemon_threads = True
threading.Thread(target=server.serve_forever, daemon=True).start()

A.save_page_text = lambda page, ref, suffix: WORK / "{0}_{1}.txt".format(ref, suffix)
SHOTS = []
A.take_screenshot = lambda page, ref, suffix, full_page=True: SHOTS.append(suffix) or \
    WORK / "{0}_{1}.png".format(ref, suffix)
A.PAGE_SETTLE_MAX_SECONDS = 1
A.CAPTCHA_WAIT_MS = 20000
A.CAPTCHA_POLL_MS = 300
A.HUMAN_CONFIRM_MS = 300
A.HUMAN_QUEUE_ON = False
A.HUMAN_AUTO_RESUME = True
A.PORTALS["CMA_CGM"] = dict(A.PORTALS["CMA_CGM"], urls=[BASE + "/search"], wait=10)
EVENTS = []
_human_event = A.human_event


def recorded_event(event, action, detail="", **fields):
    EVENTS.append(event)
    return _human_event(event, action, detail, **fields)


A.human_event = recorded_event
tower = A.tower
tower.run_started(run_id="20261006-120000-cma001", dry_run=False,
                  target_status="Under Clearance", max_records=10, max_pages=1)


def lookup(pages, ref):
    ship = {"bol_awb": ref, "carrier": "CMA CGM", "provider": "CMA_CGM",
            "current_eta": "05/11/2026", "table_page": 1}
    tower.shipment_started(ship)
    try:
        return A.get_provider_result(pages, ship), None
    except Exception as error:
        return None, error


def record_of(ref):
    return next(r for r in tower.snapshot()["shipments"] if r["reference"] == ref)


try:
    from playwright.sync_api import sync_playwright
except Exception as error:                                    # pragma: no cover
    sync_playwright = None
chromium = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome")) \
    if Path("/opt/pw-browsers").is_dir() else []
LAUNCH = {"headless": True, "executable_path": str(chromium[0])} if chromium else \
    {"headless": True, "channel": "msedge"}
restricted_error = None
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(**LAUNCH)
    pages = {"DHL": browser.new_context().new_page()}

    result, error = lookup(pages, "RESTRICT1")
    restricted_error = error
    check("Restriction page on opening: CarrierAccessRestricted, nothing extracted",
          isinstance(error, A.CarrierAccessRestricted) and result is None, repr(error))
    check("...declared CARRIER_ACCESS_RESTRICTED at stage carrier_access",
          error is not None and error.failure["category"] == "CARRIER_ACCESS_RESTRICTED"
          and error.failure["stage"] == "carrier_access", getattr(error, "failure", None))
    observed = (getattr(error, "failure", None) or {}).get("observed") or {}
    check("...with the exact URL, title, signals and the evidence files",
          observed.get("url", "").startswith(BASE + "/result?ref=RESTRICT1")
          and observed.get("title") and "VPN" in observed.get("signals", "")
          and observed.get("page_text", "").endswith("_access_restricted.txt")
          and observed.get("screenshot", "").endswith("_access_restricted.png"), observed)
    check("...the bridge records carrier access RESTRICTED on the shipment",
          (record_of("RESTRICT1").get("carrier_access") or {}).get("state") == "RESTRICTED")
    check("classify_failure() names it CARRIER ACCESS RESTRICTED",
          A.classify_failure(error) == A.CARRIER_ACCESS_RESTRICTED)
    check("...which is not retried in the run", A.CARRIER_ACCESS_RESTRICTED not in A.RETRYABLE)

    EVENTS[:] = []
    result, error = lookup(pages, "CHALLENGE_RESTRICT")
    check("Verification completed, then the restriction page: RESTRICTED, nothing extracted",
          isinstance(error, A.CarrierAccessRestricted) and result is None, repr(error))
    check("...HUMAN_VERIFICATION_COMPLETED was recorded — and is not access",
          "HUMAN_VERIFICATION_COMPLETED" in EVENTS and "CARRIER_ACCESS_CONFIRMED" not in EVENTS,
          EVENTS)
    check("...CARRIER_ACCESS_RESTRICTED follows it", "CARRIER_ACCESS_RESTRICTED" in EVENTS
          and EVENTS.index("CARRIER_ACCESS_RESTRICTED") > EVENTS.index(
              "HUMAN_VERIFICATION_COMPLETED"), EVENTS)
    check("...and the failure says the verification had been completed",
          error is not None and error.failure["observed"]["after_human_verification"] == "yes"
          and "after the human verification" in str(error))
    hv = tower.snapshot().get("human_verification") or {}
    check("The run state: carrier access RESTRICTED, not just 'verification cleared'",
          hv.get("carrier_access") == "RESTRICTED"
          and hv.get("state") == "CARRIER_ACCESS_RESTRICTED", hv)

    EVENTS[:] = []
    result, error = lookup(pages, "CHALLENGE_OK1")
    check("Verification completed, then the shipment page: the result is read",
          error is None and (result or {}).get("eta") == "12/10/2026", repr(error) or result)
    check("...and only now CARRIER_ACCESS_CONFIRMED, after HUMAN_VERIFICATION_COMPLETED",
          EVENTS.index("CARRIER_ACCESS_CONFIRMED") > EVENTS.index("HUMAN_VERIFICATION_COMPLETED")
          if "CARRIER_ACCESS_CONFIRMED" in EVENTS and "HUMAN_VERIFICATION_COMPLETED" in EVENTS
          else False, EVENTS)
    hv = tower.snapshot().get("human_verification") or {}
    check("The run state: carrier access CONFIRMED", hv.get("carrier_access") == "CONFIRMED"
          and (record_of("CHALLENGE_OK1").get("carrier_access") or {}).get("state")
          == "CONFIRMED", hv)
    check("Between the two, the bridge said NOT_CONFIRMED (cleared is not access)",
          "carrier access not yet confirmed" in json.dumps(tower.snapshot()["timeline"]))

    browser.close()

# The automation check of the worker diagnostic, against the same stand-in
# (its own Playwright session, as on the worker).
if True:
    from worker import carrier_access as C
    auto = C.automation_check("CMA_CGM", "RESTRICT2", BASE + "/ip", launch=LAUNCH)
    check("Diagnostic, automation side: RESTRICTED, with the restriction URL",
          auto["result"] == "RESTRICTED"
          and auto["restriction"]["url"].startswith(BASE + "/result?ref=RESTRICT2"), auto)
    check("...its browser's public IP, and what the page sees (navigator.webdriver)",
          auto["public_ip"].get("ip") == "203.0.113.7"
          and auto["page_sees"].get("webdriver") is True, auto.get("page_sees"))
    check("...a fresh profile, recorded as such",
          "fresh, empty profile" in auto["browser"]["profile"])
    auto_ok = C.automation_check("CMA_CGM", "CHALLENGE_OK2", BASE + "/ip", launch=LAUNCH)
    check("Diagnostic, automation side, page reachable: ACCESS with the dates",
          auto_ok["result"] == "ACCESS" and auto_ok["carrier_result"]["eta"] == "12/10/2026",
          auto_ok)
server.shutdown()


rule("3. RUN STATE, FAILURE INTELLIGENCE AND ATLAS — FROM THE RUN'S OWN EVIDENCE")
tower.shipment_finished("RESTRICT1", "SKIPPED", str(restricted_error),
                        outcome=A.classify_failure(restricted_error),
                        failure=restricted_error.failure)
rec = record_of("RESTRICT1")
item = F.from_record(rec, run_id="20261006-120000-cma001")
check("Failure intelligence: CARRIER_ACCESS_RESTRICTED, declared by the run",
      item["classification"] == "CARRIER_ACCESS_RESTRICTED"
      and item["classification_basis"] == "declared", item["classification"])
facts = " ".join(f["text"] for f in item["facts"])
check("Facts: the restriction URL and the evidence kept; nothing written",
      BASE + "/result?ref=RESTRICT1" in facts and "_access_restricted.png" in facts
      and "Nothing was written to the Hub" in facts, facts)
unv = " ".join(u["text"] for u in item["unverified"])
check("WHY it was restricted is stated as NOT established, with the worker diagnostic",
      "not established" in unv and "worker.verify carrier --carrier CMA_CGM" in unv, unv)
check("No cause is invented: the run does not claim VPN, proxy, hotspot or IP as the cause",
      not re.search(r"(caused by|because of|the cause is)[^.]*(VPN|proxy|hotspot|IP)",
                    json.dumps(item), re.I))
check("Recovery: RECOVERY_REQUIRED — nothing automatic, not retried, not worked around",
      item["recovery_plan"]["status"] == "RECOVERY_REQUIRED"
      and "No automatic recovery is permitted" in item["recovery_plan"]["statement"]
      and not [s for s in item["recovery_plan"]["steps"] if "automatic" in s["executes"]],
      item["recovery_plan"])
check("...its steps diagnose first and end with access verified before extraction",
      [s["strategy"] for s in item["recovery_plan"]["steps"]][0].startswith("Diagnose")
      and "verify access before extraction" in item["recovery_plan"]["steps"][-1]["strategy"])
check("The recommendation is the worker diagnostic, and says not to get past it",
      "worker.verify carrier" in item["recommendations"][0]["text"]
      and "Do not try to get past the restriction" in item["recommendations"][0]["text"])
check("Work list: NEEDS_DECISION, never retried in the run", item["work"]["mode"] == "NEEDS_DECISION")
check("Headline", "carrier access restricted" in item["headline"])

from dashboard import assistant                              # noqa: E402
state = tower.snapshot()
answer = assistant.answer("why the error?", state, {"reference": "RESTRICT1"})["answer"]
print("\n--- ATLAS: why the error? ---\n" + answer[:1500] + "\n---")
check("ATLAS explains from the run: the shipment, the restriction and its URL",
      "RESTRICT1" in answer and "restrict" in answer.lower()
      and BASE + "/result?ref=RESTRICT1" in answer, answer[:600])
check("...and says the reason for the restriction is not established",
      "not established" in answer, answer[:600])


rule("4. THE WORKER DIAGNOSTIC: WHAT THE RESTRICTION FOLLOWS")
from worker import carrier_access as C                       # noqa: E402

clean = {"vpn": {"detected": False}, "proxy": {"configured": False},
         "network": {"hotspot_indicators": []}, "public_ip_python": {"ip": "198.51.100.4"}}
auto_r = {"result": "RESTRICTED", "public_ip": {"ip": "198.51.100.4"},
          "page_sees": {"webdriver": True}}
d = C.determine({"environment": clean, "manual": {"result": "ACCESS"}, "automation": auto_r})
check("Manual Edge reaches it, automation restricted, same IP -> follows the automation's "
      "browser/session, ESTABLISHED; the IP alone ruled out",
      d["confidence"] == "ESTABLISHED" and "automation" in d["follows"]
      and any("public IP alone" in r for r in d["ruled_out"])
      and any("webdriver" in n for n in d["not_established"]), d)
d = C.determine({"environment": dict(clean, vpn={"detected": True}),
                 "manual": {"result": "RESTRICTED"}, "automation": auto_r})
check("Both restricted, VPN present -> network/IP or carrier-side, CONSISTENT only; "
      "VPN listed as present and named by the page, not as the cause",
      d["confidence"] == "CONSISTENT" and "network" in d["follows"]
      and any("VPN" in b and "possible causes" in b for b in d["because"])
      and any("different network" in n for n in d["not_established"]), d)
d = C.determine({"environment": clean, "manual": {"result": "RESTRICTED"},
                 "automation": dict(auto_r, result="ACCESS")})
check("Manual restricted, automation reaches it -> the manual profile/session",
      d["confidence"] == "ESTABLISHED" and "manual browser" in d["follows"], d)
d = C.determine({"environment": clean, "manual": {"result": "ACCESS"},
                 "automation": dict(auto_r, result="ACCESS")})
check("Neither restricted now -> NOT_ESTABLISHED, not reproduced",
      d["confidence"] == "NOT_ESTABLISHED" and "not reproduced" in " ".join(d["not_established"]),
      d)
d = C.determine({"environment": clean, "manual": {"result": "NOT_RUN"}, "automation": auto_r})
check("No manual check -> NOT_ESTABLISHED (network vs browser cannot be separated)",
      d["confidence"] == "NOT_ESTABLISHED", d)
check("The automation uses no carrier account: ruled out as the automation's cause",
      any("carrier account" in r for r in d["ruled_out"]))

answers = iter(["3", "", "2", "https://carrier.example/blocked?x=1", "n", "said VPN"])
m = C.manual_check("https://carrier.example/tracking", "CMAU1", "/bin/true",
                   ask=lambda prompt: next(answers), say=lambda *a: None)
check("Manual check: a challenge is left to the operator, then the answer is recorded",
      m["result"] == "RESTRICTED" and m["url_shown"] == "https://carrier.example/blocked?x=redacted"
      and m["signed_in_to_carrier"] is False and m["operator_note"] == "said VPN", m)
check("Proxy URLs are recorded without their user:password",
      C._redact_proxy("http://user:S3cret@proxy.corp:8080") == "http://***@proxy.corp:8080")
p = C.proxy()
check("The proxy reading runs and says what each browser follows", "configured" in p
      and "WinINET" in p["browser_uses"])
n = C.network()
check("The network reading never asserts a hotspot without an indicator",
      ("no mobile-hotspot indicator" in n["reading"]) == (not n["hotspot_indicators"]))

obs = {"kind": "carrier-access", "run_id": "verify-x", "result": "COMPLETED",
       "automation": dict(auto_r, browser={"real": True, "version": "129.0"},
                          final_url="https://carrier.example/blocked"),
       "manual": {"result": "ACCESS"},
       "determination": C.determine({"environment": clean, "manual": {"result": "ACCESS"},
                                     "automation": auto_r})}
check("Control-plane level: REAL OBSERVED from the worker, with the determination",
      V.classify(obs, "worker")[0] == "REAL OBSERVED"
      and "follows" in V.classify(obs, "worker")[1][0])
check("...TEST from the suite, and never REAL from the cloud",
      V.classify(obs, "test")[0] == "TEST" and V.classify(obs, "cloud")[0] == "BLOCKED")
check("...BLOCKED when the automation's browser could not open the page",
      V.classify(dict(obs, automation={"result": "ERROR", "browser": {"real": False},
                                       "error": "Edge not found"}), "worker")[0] == "BLOCKED")
SRC = (HERE / "worker" / "carrier_access.py").read_text(encoding="utf-8") + \
    (HERE / "update_eta.py").read_text(encoding="utf-8")
check("Nothing that changes identity to get past a carrier: no stealth, no UA override, "
      "no webdriver masking, no proxy rotation",
      not re.search(r"stealth|user_agent\s*=|defineProperty\([^)]*webdriver|add_init_script|"
                    r"AutomationControlled|rotate_?prox", SRC, re.I))

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
