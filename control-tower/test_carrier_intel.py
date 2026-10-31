"""
Phase 2: carrier-page change detection, carrier health and the morning
briefing — from recorded evidence only.

    python test_carrier_intel.py

Offline. Section 3 drives the real open_portal() in a real browser against a
local test server (a page with no tracking box, a 404, a blank page) when
Playwright is installed.
"""

import http.server
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(tempfile.mkdtemp(prefix="ct_carrier_intel_"))
os.environ["ATLAS_INTEL_DIR"] = str(WORK / "store")
os.environ["ATA_BASE_FOLDER"] = str(WORK / "base")
os.environ["CAPTCHA_WAIT_MS"] = "0"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "dashboard"))

from intelligence import briefing, carrier_health, events, pagecheck, store  # noqa: E402
from dashboard.bridge import ControlTowerState                               # noqa: E402
from dashboard import assistant                                              # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "  ({0})".format(detail) if detail and not condition else ""))


def rule(title):
    print("\n" + "=" * 72 + "\n" + title + "\n" + "=" * 72)


LOADED = {"navigated": True, "http_status": 200, "ready_state": "complete",
          "text_len": 4200, "title": "Track your cargo", "carrier_probe":
          "HTTP/1.1 200 OK | connected in 40ms, replied in 90ms", "control_probe":
          "HTTP/1.1 200 OK | connected in 30ms, replied in 60ms"}

rule("1. A MISSING BOX IS CLASSIFIED BEFORE IT IS CALLED A SITE CHANGE")
cases = [
    ("navigation failed", dict(LOADED, navigated=False, http_status=None,
                               nav_error="net::ERR_CONNECTION_RESET"), "NETWORK"),
    ("server error 503", dict(LOADED, http_status=503), "NETWORK"),
    ("carrier unreachable", dict(LOADED, carrier_probe="NO REPLY after 6000ms — timeout"),
     "NETWORK"),
    ("a human check showing", dict(LOADED, captcha=True), "CHALLENGE"),
    ("access restricted", dict(LOADED, restricted=True), "RESTRICTED"),
    ("address answers 404", dict(LOADED, http_status=404), "PAGE_MOVED"),
    ("'page not found' title", dict(LOADED, title="Page not found | Carrier"), "PAGE_MOVED"),
    ("still loading", dict(LOADED, ready_state="interactive"), "LOADING"),
    ("rendered almost nothing", dict(LOADED, text_len=35), "LOADING"),
    ("loaded, reachable, no check — no box", dict(LOADED), "LAYOUT_CANDIDATE"),
]
for name, evidence, expected in cases:
    got = pagecheck.classify(evidence)
    check("{0} -> {1}".format(name, expected), got["cause"] == expected,
          "{0} {1}".format(got["cause"], got["reasons"]))
check("A challenge on a slow page is a CHALLENGE, not a redesign",
      pagecheck.classify(dict(LOADED, captcha=True, ready_state="loading"))["cause"]
      == "CHALLENGE")
check("Every classification says why",
      all(pagecheck.classify(e)["reasons"] for _n, e, _x in cases))
check("A 'success' probe is not mistaken for a failure",
      pagecheck.classify(dict(LOADED, carrier_probe="HTTP/1.1 403 Forbidden | connected "
                              "in 40ms | IT IS REFUSING US: 'access denied'"))["cause"]
      == "LAYOUT_CANDIDATE")


def obs(carrier, run, cause, sig="s1", ts=None):
    return {"kind": "missing_box", "carrier": carrier, "label": carrier, "run_id": run,
            "cause": cause, "reasons": ["r"], "ts": ts,
            "evidence": {"input_signature": sig, "page_text_file": "p.txt",
                         "screenshot_file": "p.png"}}


def ok(carrier, run, ts):
    return {"kind": "box_found", "carrier": carrier, "run_id": run, "ts": ts}


def finding(rows, carrier="X"):
    return next((f for f in pagecheck.assess(rows) if f["carrier"] == carrier), None)


rule("2. CONFIDENCE COMES FROM REPETITION, AND A GOOD LOOKUP CLOSES IT")
f = finding([obs("X", "r1", "LAYOUT_CANDIDATE", ts="1")])
check("Seen once: POSSIBLE", f["status"] == "SITE_CHANGE" and f["confidence"] == "POSSIBLE", str(f))
f = finding([obs("X", "r1", "LAYOUT_CANDIDATE", ts="1"), obs("X", "r1", "LAYOUT_CANDIDATE", ts="2")])
check("Twice in one run, same page: LIKELY", f["confidence"] == "LIKELY", str(f))
f = finding([obs("X", "r1", "LAYOUT_CANDIDATE", ts="1"), obs("X", "r2", "LAYOUT_CANDIDATE", ts="2")])
check("In two runs, same page: CONFIRMED", f["confidence"] == "CONFIRMED", str(f))
f = finding([obs("X", "r1", "LAYOUT_CANDIDATE", "s1", "1"),
             obs("X", "r2", "LAYOUT_CANDIDATE", "s2", "2")])
check("Two runs but the page looked different each time: only POSSIBLE",
      f["confidence"] == "POSSIBLE" and "looked different" in f["why"], str(f))
f = finding([obs("X", "r1", "PAGE_MOVED", ts="1")])
check("A 404 once is already LIKELY", f["cause"] == "PAGE_MOVED" and f["confidence"] == "LIKELY")
f = finding([obs("X", "r1", "PAGE_MOVED", ts="1"), obs("X", "r2", "PAGE_MOVED", ts="2")])
check("A 404 in two runs is CONFIRMED", f["confidence"] == "CONFIRMED")
f = finding([obs("X", "r1", "LOADING", ts="1"), obs("X", "r2", "NETWORK", ts="2"),
             obs("X", "r3", "CHALLENGE", ts="3")])
check("Only ordinary causes: NOT a site change, and it says which",
      f["status"] == "NOT_A_SITE_CHANGE" and "LOADING x1" in f["why"], str(f))
f = finding([obs("X", "r1", "LAYOUT_CANDIDATE", ts="1"), obs("X", "r2", "LAYOUT_CANDIDATE", ts="2"),
             ok("X", "r3", "3")])
check("A later successful lookup closes it: RESOLVED", f["status"] == "RESOLVED", str(f))
f = finding([ok("X", "r0", "0"), obs("X", "r1", "LAYOUT_CANDIDATE", ts="1")])
check("Only observations AFTER the last good lookup count", f["confidence"] == "POSSIBLE")
check("The finding points at the saved page and screenshot",
      finding([obs("X", "r1", "LAYOUT_CANDIDATE", ts="1")])["evidence"] == ["p.txt", "p.png"])
check("Plain words for a person",
      "site change CONFIRMED" in pagecheck.describe(finding(
          [obs("X", "r1", "PAGE_MOVED", ts="1"), obs("X", "r2", "PAGE_MOVED", ts="2")]))
      and "not found" in pagecheck.describe(finding(
          [obs("X", "r1", "PAGE_MOVED", ts="1"), obs("X", "r2", "PAGE_MOVED", ts="2")])))

rule("3. THE REAL open_portal() IN A REAL BROWSER")
try:
    from playwright.sync_api import sync_playwright
    CHROME = sorted(Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"))
except ImportError:
    sync_playwright = None

if sync_playwright is None:
    print("  SKIP  Playwright is not installed")
else:
    class Pages(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/gone":
                body, code = b"<html><head><title>Page not found</title></head>" \
                             b"<body>404 - page not found</body></html>", 404
            elif self.path == "/blank":
                body, code = b"<html><body></body></html>", 200
            else:
                body = ("<html><head><title>Carrier</title></head><body><h1>Welcome</h1>"
                        "<input type='search' placeholder='Search the site'>"
                        + "<p>Our services and news.</p>" * 40 + "</body></html>").encode()
                code = 200
            self.send_response(code)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{0}".format(server.server_address[1])

    import update_eta as A
    A.PORTAL_FORM_READY_MS = 3000
    A.probe_host = lambda host: "HTTP/1.1 200 OK | connected in 1ms, replied in 2ms"
    seen = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=str(CHROME[-1]) if CHROME else None)
        for name, path in (("no box", "/nobox"), ("moved", "/gone"), ("blank", "/blank")):
            page = browser.new_page()
            config = {"label": "Stub " + name, "urls": [base + path],
                      "placeholder": r"AWB\s*Number", "button": r"Track"}
            try:
                A.open_portal(page, config, "485-12345675")
                seen[name] = None
            except A.SkipShipment as error:
                seen[name] = error
            page.close()
        browser.close()
    server.shutdown()
    check("A page that loads with no tracking box -> LAYOUT_CANDIDATE",
          seen["no box"] and seen["no box"].failure["category"]
          == "CARRIER_PAGE_LAYOUT_CANDIDATE", seen["no box"] and seen["no box"].failure)
    check("A 404 -> PAGE_MOVED", seen["moved"] and seen["moved"].failure["category"]
          == "CARRIER_PAGE_PAGE_MOVED", seen["moved"] and seen["moved"].failure)
    check("A blank page -> LOADING, not a site change",
          seen["blank"] and seen["blank"].failure["category"] == "CARRIER_PAGE_LOADING",
          seen["blank"] and seen["blank"].failure)
    check("The shipment is still skipped as before (SkipShipment), with the cause named",
          all(isinstance(e, A.SkipShipment) for e in seen.values())
          and "page moved" in str(seen["moved"]))
    check("Without the production store attached, nothing is written",
          not (WORK / "store" / pagecheck.FILE).exists())
    check("The saved page text is kept as evidence (the run's own log folder)",
          any("stub no box_no_input" in p.name for p in Path(A.LOG_FOLDER).glob("*.txt")))

rule("4. CARRIER HEALTH: COUNTED, OR 'INSUFFICIENT DATA'")
NOW = time.time()


def ship(provider, result, verified=False, extracted=True, days_ago=1, human=False,
         category=None, dur=20000, run="r1"):
    return {"kind": "shipment", "provider": provider, "carrier": provider, "result": result,
            "verified": verified, "extracted": extracted, "human_step": human,
            "failure_category": category, "duration_ms": dur, "run_id": run,
            "epoch": NOW - days_ago * 86400, "at": "x"}


check("No records: says there is nothing to measure — no zero rates",
      "Nothing to measure yet" in carrier_health.render(carrier_health.health([], now=NOW)))
rows = [ship("SMALL", "SUCCESS", True)] * 3
h = carrier_health.health(rows, now=NOW)
small = h["carriers"][0]
check("3 shipments: listed with counts, but NO rates",
      small["sufficient"] is False and all(v is None for v in small["rates"].values()), str(small))
check("...and the text says insufficient data",
      "insufficient data" in carrier_health.render(h))
rows = ([ship("BIG", "SUCCESS", True)] * 6 + [ship("BIG", "FAILED", category="CARRIER_PAGE_PAGE_MOVED")] * 4
        + [ship("BIG", "SUCCESS", True, days_ago=9)] * 8 + [ship("BIG", "FAILED", days_ago=9)] * 2)
h = carrier_health.health(rows, now=NOW)
big = h["carriers"][0]
check("10 shipments this week: rates from the counts (6 verified, 4 failed)",
      big["rates"]["verified_updates"] == 0.6 and big["rates"]["failed"] == 0.4, str(big))
check("...the top cause is the recorded category",
      big["top_causes"][0] == ("CARRIER_PAGE_PAGE_MOVED", 4), str(big["top_causes"]))
check("...and a trend against last week (80% -> 60%) because both weeks are big enough",
      big["trend_verified"] == -0.2, str(big["trend_verified"]))
h2 = carrier_health.health([ship("BIG", "SUCCESS", True)] * 6 + [ship("BIG", "SUCCESS", True,
                                                                       days_ago=9)], now=NOW)
check("No trend when last week had too few shipments", h2["carriers"][0]["trend_verified"] is None)
check("A carrier with no records this week is not listed at all",
      [c["carrier"] for c in carrier_health.health(
          [ship("OLD", "SUCCESS", True, days_ago=20)], now=NOW)["carriers"]] == [])
check("Shipments still waiting for a person are not counted as outcomes",
      carrier_health.health([dict(ship("Q", "SUCCESS"), result="HUMAN_QUEUED")],
                            now=NOW)["records"] == 0)

rule("5. THE MORNING BRIEFING")
b = briefing.build(now=NOW, shipment_rows=[], page_rows=[])
check("Nothing recorded: it says so, recommends nothing, estimates nothing",
      "No shipments finished" in b["lines"][0] and "Nothing to act on" in b["action"]
      and "nothing was estimated" in briefing.render(b), briefing.render(b))
recent = ([ship("DHL", "SUCCESS", True, days_ago=0.2)] * 5
          + [ship("ASTRAL", "FAILED", days_ago=0.2, category="CARRIER_PAGE_PAGE_MOVED")] * 6
          + [ship("GRIMALDI", "SKIPPED", days_ago=0.2, human=True)])
pages = [obs("Astral Aviation", "r1", "PAGE_MOVED", ts="1"),
         obs("Astral Aviation", "r2", "PAGE_MOVED", ts="2")]
b = briefing.build(now=NOW, shipment_rows=recent, page_rows=pages)
text = briefing.render(b)
check("The day is counted: 12 shipments, 5 verified, 6 failed, 1 needed a person",
      "12 shipments" in text and "5 updated and verified" in text and "6 failed" in text
      and "1 needed a person" in text, text)
check("A confirmed site change leads, with its evidence",
      "Astral Aviation: site change CONFIRMED" in text and "evidence: p.txt" in text, text)
check("...and is the first thing to do",
      b["action"].startswith("Check Astral Aviation's tracking page"), b["action"])
b = briefing.build(now=NOW, shipment_rows=recent, page_rows=[])
check("Without a site change, the carrier with the most failures is the action",
      b["action"].startswith("Look at ASTRAL: 6 of 6"), b["action"])
check("Carriers with too few shipments are named as such, not judged",
      "Too few shipments to judge this week: DHL (5)" not in briefing.render(b)
      and "GRIMALDI (1)" in briefing.render(b), briefing.render(b))
check("It says what it was built from", "Built from: 12 shipment outcome records" in
      briefing.render(b))
quiet = briefing.build(now=NOW, shipment_rows=[ship("DHL", "SUCCESS", True, days_ago=0.1)] * 6,
                       page_rows=[])
check("A clean day: no action needed", quiet["action"] == "No action needed.")

rule("6. WIRED IN: THE RUN RECORDS IT, ATLAS ANSWERS FROM IT")
b2 = ControlTowerState()
b2.run_started(run_id="run-health")
b2.attach_intelligence(events)
b2.shipment_started({"bol_awb": "485-12345675", "carrier": "Astral Aviation",
                     "provider": "ASTRAL"})
b2.shipment_finished("485-12345675", "SKIPPED", "No box", outcome="UNEXPECTED PAGE STATE",
                     failure={"category": "CARRIER_PAGE_PAGE_MOVED", "stage": "carrier_search_form"})
stored = [e for e in events.all_events(("shipment",)) if e.get("reference") == "485-12345675"]
check("A finished shipment's event carries its failure category",
      stored and stored[-1].get("failure_category") == "CARRIER_PAGE_PAGE_MOVED", str(stored))
for question, intent in (("morning briefing", "briefing"), ("has any carrier website changed",
                                                              "carrier_health")):
    check("'{0}' routes to {1}".format(question, intent),
          assistant.detect_intent(question) == intent)
text, sources = assistant._answer_briefing()
check("ATLAS's briefing answer is the briefing, with its sources",
      text.startswith("ATLAS morning briefing") and isinstance(sources, list), text[:120])
text, sources = assistant._answer_carrier_health()
check("ATLAS's carrier-health answer counts the stored record",
      "Astral Aviation: 1 shipment(s)" in text and sources
      and "shipment outcome records" in sources[0], text)
server = (HERE / "dashboard" / "server.py").read_text(encoding="utf-8")
check("The dashboard serves it at /api/atlas/briefing",
      'route == "/api/atlas/briefing"' in server)
src = (HERE / "update_eta.py").read_text(encoding="utf-8")
check("Only a production run (store attached in main) writes page checks",
      'if INTEL.get("events") is None:\n        return None' in src)
check("A verification screen is never screenshotted as evidence",
      'if evidence.get("captcha"):\n        shot = None' in src)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
