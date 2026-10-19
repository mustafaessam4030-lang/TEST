"""
ATLAS's skills and handbook (dashboard/atlas_skills.py, intelligence/handbook.py,
intelligence/pdf_report.py): validating a shipment, the run or a typed date;
PDF reports; the run's screenshots; web search (with a stand-in search service
on this machine, and without one); and questions about how the system works.

Answered by the rules from a seeded run, no model involved.

    python test_atlas_skills.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="atlas_skills_")
os.environ["ATLAS_DATA_ORIGIN"] = "test"
os.environ.pop("ATLAS_SEARCH_URL", None)

import atlas_demo                                            # noqa: E402
from dashboard import assistant as A                         # noqa: E402
from dashboard import atlas_skills as SK                     # noqa: E402
from dashboard.bridge import ControlTowerState               # noqa: E402
from intelligence import converse as C                       # noqa: E402
from intelligence import handbook as H                       # noqa: E402
from intelligence import pdf_report as P                     # noqa: E402
from intelligence import research as R                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name,
                                  "" if ok else "  ({0})".format(str(detail)[:400])))


def rule(title):
    print("\n" + "=" * 74 + "\n" + title + "\n" + "=" * 74)


bridge = ControlTowerState()
atlas_demo.seed_test_run(bridge)
future = (date.today() + timedelta(days=9)).strftime("%d/%m/%Y")
# An estimate read twice (ETA = ATA) and an arrival in the future: both FAIL.
bridge.shipment_started(dict(bol_awb="MEDUAHP69377", carrier="MSC", provider="MSC"))
bridge.provider_result({"provider": "MSC", "eta": "10/10/2026", "ata": "10/10/2026",
                        "eta_source": "ETA", "ata_source": "ETA"})
bridge.shipment_finished("MEDUAHP69377", "SKIPPED", "NEEDS REVIEW: ETA and ATA are the same "
                         "date (10/10/2026) from one read", outcome="NEEDS REVIEW")
bridge.shipment_started(dict(bol_awb="K358403", carrier="DHL Global Forwarding", provider="DHL"))
bridge.provider_result({"provider": "DHL", "eta": None, "ata": future, "ata_source": "Arrived"})
bridge.shipment_finished("K358403", "SKIPPED", "NEEDS REVIEW: ATA {0} is in the future".format(future),
                         outcome="NEEDS REVIEW")
STATE = bridge.snapshot()


def ask(q, context=None):
    return A._answer_rules(q, STATE, dict(context or {}, _raw=True))


# ── validation ─────────────────────────────────────────────────────────
rule("Validate: a shipment, the run, a typed date")
r = ask("validate 1570046231")
check("validate <ref> is the validation skill", r.get("intent") == "skill_validate", r.get("intent"))
check("a clean shipment passes every check", "every check passes" in r["answer"], r["answer"])
check("its dates are checked as real dd/mm/yyyy dates",
      "ETA 24/08/2026 is a real" in r["answer"] and "ATA 20/08/2026 is not in the future" in r["answer"],
      r["answer"])
check("the Hub write is listed", "Written to the Hub" in r["answer"], r["answer"])
check("it says nothing was changed", "Nothing was changed" in r["answer"], r["answer"])
check("the shipment card comes with it", (r.get("card") or {}).get("reference") == "1570046231")

r = ask("is the ETA for MEDUAHP69377 correct?")
check("'is the ETA for X correct' validates X", r.get("intent") == "skill_validate", r.get("intent"))
check("ETA = ATA fails", "❌ FAIL" in r["answer"] and "same date (10/10/2026)" in r["answer"], r["answer"])
check("the review reason is shown", "Sent to a person" in r["answer"], r["answer"])

r = ask("double check K358403")
check("a future ATA fails", "ATA {0} is in the future".format(future) in r["answer"], r["answer"])

r = ask("validate it", {"reference": "1570046231"})
check("'validate it' uses the shipment being discussed", r.get("reference") == "1570046231",
      r.get("reference"))

r = ask("check all the dates")
check("'check all the dates' validates the run", r.get("intent") == "skill_validate" and
      "Validated" in r["answer"], r)
check("the run validation names what needs attention",
      "MEDUAHP69377" in r["answer"] and "K358403" in r["answer"], r["answer"])

r = ask("is 31/02/2026 a valid ATA?")
check("31/02 is not a real date", "not a real calendar date" in r["answer"], r["answer"])
r = ask("is {0} a valid ATA?".format(future))
check("a future ATA date fails", "in the future" in r["answer"] and "❌" in r["answer"], r["answer"])
r = ask("is 05/03/2026 ok as an ETA?")
check("day/month order is spelled out", "05 March" in r["answer"], r["answer"])
r = ask("validate 12/10/26")
check("a two-digit year fails", "two-digit year" in r["answer"], r["answer"])

check("'did ATLAS check the DHL dates' still answers from the run",
      ask("did ATLAS check the DHL dates")["intent"] == "atlas_checks")
check("'was the ata verified' still answers from telemetry",
      ask("was the ata verified")["intent"] == "verification")

# ── PDF ────────────────────────────────────────────────────────────────
rule("PDF reports")
r = ask("give me a pdf report of the run")
check("asking for a PDF offers a download", r.get("intent") == "skill_pdf" and
      any(d["url"] == "/api/report.pdf?kind=run" for d in r.get("downloads") or []), r)
r = ask("make a pdf for 1570046231")
check("a PDF for one shipment", any("kind=shipment&ref=1570046231" in d["url"]
                                    for d in r.get("downloads") or []), r.get("downloads"))
r = ask("pdf of the failed shipments")
check("a PDF of the failures", any("kind=failed" in d["url"] for d in r.get("downloads") or []),
      r.get("downloads"))
r = ask("send me the review list as a pdf")
check("a PDF of the review list", any("kind=review" in d["url"] for d in r.get("downloads") or []),
      r.get("downloads"))
check("'where is the PDF for PO 9117835' is not a report request",
      SK.detect("where is the PDF for PO 9117835") is None)

for kind, ref in (("run", None), ("failed", None), ("review", None), ("shipment", "MEDUAHP69377")):
    body, name = P.build(kind, STATE, ref)
    ok = bool(body) and body[:5] == b"%PDF-"
    check("the {0} PDF is a real PDF ({1})".format(kind, name), ok, name)
    if ok:
        import pymupdf
        doc = pymupdf.open("pdf", body)
        text = "".join(p.get_text() for p in doc)
        if kind == "run":
            check("the run PDF lists every shipment", all(x in text for x in (
                "1570046231", "5271993480", "8842001173", "MEDUAHP69377")), text[:300])
            check("the run PDF has page numbers", "Page 1 of" in text)
        if kind == "failed":
            check("the failed PDF holds the failure", "8842001173" in text and
                  "1570046231" not in text, text[:300])
        if kind == "review":
            check("the review PDF holds the review list", "MEDUAHP69377" in text and "K358403" in text)
        if kind == "shipment":
            check("the shipment PDF holds its checks", "FAIL" in text and "same date" in text, text[:400])
            check("the PDF says nothing was inferred", "Nothing" in text and "inferred" in text)
body, why = P.build("shipment", STATE, "NOPE123")
check("an unknown shipment gives no PDF", body is None and "NOPE123" in why, why)

# ── screenshots ────────────────────────────────────────────────────────
rule("Screenshots the run captured")
import pymupdf                                                # noqa: E402
from intelligence import evidence as EV                       # noqa: E402
png = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), False).tobytes("png")
EV.register_upload(png, "qatar.png", reference="1570046231")
r = ask("show me the screenshot for 1570046231")
check("'screenshot for X' shows the run's capture", r.get("intent") == "evidence" and
      len(r.get("evidence") or []) == 1 and r["evidence"][0]["url"].startswith("/api/evidence/file"),
      r)
r = ask("show me the screenshot for 5271993480")
check("no capture: it says so", "didn't capture a screenshot" in r["answer"], r["answer"])
body, _ = P.build("shipment", STATE, "1570046231")
doc = pymupdf.open("pdf", body)
check("the shipment PDF carries the screenshot", sum(len(p.get_images()) for p in doc) >= 1)
body, _ = P.build("shipment", STATE, "1570046231", images=False)
doc = pymupdf.open("pdf", body)
check("without permission the PDF leaves screenshots out",
      "Screenshots captured" not in "".join(p.get_text() for p in doc))
check("a photo just attached is not a screenshot request",
      SK.detect("what does this photo show", None, {"evidence_id": "abc"}) is None)

# ── web ────────────────────────────────────────────────────────────────
rule("Web search: off, then a stand-in search service")
r = ask("search MSC website for advisories")
check("without a search service it says why", r.get("intent") == "skill_web" and
      "couldn't search the web" in r["answer"], r["answer"])
check("...and gives MSC's official link", any("msc.com" in s["url"] for s in r.get("web_sources") or []),
      r.get("web_sources"))
check("...and how to turn search on", "SETUP_WEB_SEARCH.bat" in r["answer"])
r = ask("search the web for HS code of excavator parts")
check("a customs question gets the WCO link", any("wcoomd.org" in s["url"]
                                                 for s in r.get("web_sources") or []),
      r.get("web_sources"))

SEEN = []


class FakeSearx(BaseHTTPRequestHandler):
    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        SEEN.append(parse_qs(urlparse(self.path).query).get("q", [""])[0])
        body = json.dumps({"results": [
            {"url": "https://www.msc.com/en/newsroom/customer-advisories", "title": "Customer advisories",
             "content": "Port congestion surcharge update for West Africa."},
            {"url": "https://www.example-blog.com/msc", "title": "A blog", "content": "Someone's view."},
        ]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), FakeSearx)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["ATLAS_SEARCH_URL"] = "http://127.0.0.1:{0}".format(server.server_address[1])
r = ask("search MSC website for advisories")
check("the carrier's site is searched with site:", SEEN and SEEN[0].startswith("site:msc.com"), SEEN)
check("results are labelled web, not run facts", "not run facts" in r["answer"], r["answer"])
check("the official carrier source comes first",
      (r.get("web_sources") or [{}])[0].get("kind") == "Carrier (official)", r.get("web_sources"))
r = ask("any news on Tema port congestion?")
check("'any news on port congestion' searches the web", r.get("intent") == "skill_web", r.get("intent"))
os.environ.pop("ATLAS_SEARCH_URL", None)
server.shutdown()
check("a standards body is fetchable", R.fetch_allowed("https://www.wcoomd.org/en/topics/nomenclature.aspx"))
check("a carrier tracking page is never fetched",
      not R.fetch_allowed("https://www.msc.com/en/track-a-shipment"))

# ── handbook ───────────────────────────────────────────────────────────
rule("The handbook: how the system works")
CASES = [
    ("what is the control tower?", "overview", "four parts"),
    ("how does the ETA update work?", "eta_flow", "five things"),
    ("what checks does it do before writing?", "checks", "No future arrival"),
    ("which carriers do you support?", "carriers", "MSC"),
    ("why is CMA CGM on hold?", "hold", "CMA CGM"),
    ("how does the PO process work?", "po_flow", "Under Clearance"),
    ("how is a PO validated?", "po_checks", "Invoice No."),
    ("where does the invoice number come from?", "invoice", "never"),
    ("what can an operator do?", "roles", "Operator"),
    ("how do I handle a captcha?", "human_action", "never"),
    ("I can't open the dashboard", "not_reachable", "8787"),
    ("what are the run limits?", "limits", "MAX_HUB_WRITES"),
    ("how do I install the new version?", "update_tower", "Ctrl+F5"),
    ("can you search the web?", "web", "SETUP_WEB_SEARCH.bat"),
    ("can you read screenshots?", "photos", "paperclip"),
    ("where are the logs kept?", "data_where", "C:\\Automation"),
]
for q, topic, word in CASES:
    r = ask(q)
    check("'{0}' -> {1}".format(q, topic), r.get("intent") == "handbook" and r.get("topic") == topic
          and word in r["answer"], "{0} / {1}".format(r.get("intent"), r.get("topic")))
held = ask("which carriers do you support?")["answer"]
check("the carrier list is read from the code (held carriers named)", "On hold" in held, held)

r = ask("how does the worker send its heartbeat?")
check("a question the topics don't cover is answered from the guides",
      r.get("intent") == "handbook_guide" and "project guide" in r["answer"], r.get("answer", "")[:200])

# Everyday run questions keep their own answers.
for q, intent in (("how is the run going", None), ("which shipments failed", None),
                  ("what should I do next?", None), ("hello", "greeting"),
                  ("why did 8842001173 fail", None)):
    got = ask(q).get("intent")
    check("'{0}' is not taken by the handbook or skills ({1})".format(q, got),
          got not in ("handbook", "skill_pdf", "skill_validate", "skill_web") and
          (intent is None or got == intent), got)

# ── the conversation layer keeps them exact ───────────────────────────
rule("With a model: skills and handbook are returned as built")
for q in ("validate 1570046231", "pdf report of the run", "how does the PO process work?",
          "search MSC website for advisories"):
    r = ask(q)
    check("'{0}' is never reworded ({1})".format(q, r.get("intent")),
          r.get("intent") in C.SKILL_INTENTS, r.get("intent"))

# ── the dashboard serves the PDF ──────────────────────────────────────
rule("GET /api/report.pdf")
import socket                                                 # noqa: E402
from dashboard import server as tower_server                  # noqa: E402
from dashboard import bridge as bridge_module                 # noqa: E402
bridge_module.bridge = bridge
tower_server.bridge = bridge
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
tower_server.start(port=port, open_browser=False, host="127.0.0.1")
base = "http://127.0.0.1:{0}".format(port)
import time                                                   # noqa: E402
for _ in range(50):
    try:
        urllib.request.urlopen(base + "/api/atlas", timeout=1)
        break
    except Exception:
        time.sleep(0.1)
with urllib.request.urlopen(base + "/api/report.pdf?kind=shipment&ref=MEDUAHP69377", timeout=30) as resp:
    data = resp.read()
    check("the server sends a PDF", resp.headers.get("Content-Type") == "application/pdf" and
          data[:5] == b"%PDF-", resp.headers.get("Content-Type"))
    check("as a download with a clear name", "ATLAS_shipment_MEDUAHP69377" in
          (resp.headers.get("Content-Disposition") or ""), resp.headers.get("Content-Disposition"))
try:
    urllib.request.urlopen(base + "/api/report.pdf?kind=shipment&ref=NOPE", timeout=30)
    check("an unknown shipment is a 404", False)
except urllib.error.HTTPError as error:
    check("an unknown shipment is a 404", error.code == 404, error.code)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
