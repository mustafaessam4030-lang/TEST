"""
ATLAS as a colleague — shipment intelligence, error investigation, general
questions, and web research — end to end through assistant.answer() and the
real dashboard server.

SIMULATED: the research service. A local stand-in plays the Claude Messages
API: it streams the same event types the real API streams (message_start,
content_block_start/delta/stop for server_tool_use, web_search_tool_result,
web_fetch_tool_result and cited text, message_delta, message_stop). It
proves the plumbing — what is sent, what is shown, what is refused — never
the quality of a real search. Everything else (the run, the bridge, ATLAS's
routing, the knowledge, the server, the progress endpoint) is the real code.

    python test_atlas_research.py
"""

import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_INTEL_DIR", tempfile.mkdtemp(prefix="ct_research_intel_"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")
os.environ.setdefault("PO_DATA_DIR", tempfile.mkdtemp(prefix="ct_research_po_"))

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


# ── the stand-in Messages API (SIMULATED) ───────────────────────────────
CALLS = []                 # every request body received
SCENARIO = {"name": "where", "status": 200, "delay": 0.0}


def sse(blocks, stop="end_turn"):
    out = [("message_start", {"type": "message_start", "message": {
        "id": "msg_stub", "type": "message", "role": "assistant", "content": [],
        "usage": {"input_tokens": 10, "output_tokens": 0}}})]
    for i, b in enumerate(blocks):
        if b["type"] == "server_tool_use":
            out.append(("content_block_start", {"type": "content_block_start", "index": i,
                        "content_block": {"type": "server_tool_use", "id": b["id"],
                                          "name": b["name"], "input": {}}}))
            out.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                        "delta": {"type": "input_json_delta",
                                  "partial_json": json.dumps(b["input"])}}))
            out.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        elif b["type"] == "text":
            out.append(("content_block_start", {"type": "content_block_start", "index": i,
                        "content_block": {"type": "text", "text": ""}}))
            for chunk in re.findall(r".{1,40}", b["text"], re.S):
                out.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                            "delta": {"type": "text_delta", "text": chunk}}))
            for c in b.get("citations") or []:
                out.append(("content_block_delta", {"type": "content_block_delta", "index": i,
                            "delta": {"type": "citations_delta", "citation": c}}))
            out.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
        else:
            out.append(("content_block_start", {"type": "content_block_start", "index": i,
                        "content_block": b}))
            out.append(("content_block_stop", {"type": "content_block_stop", "index": i}))
    out.append(("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop},
                                  "usage": {"output_tokens": 50}}))
    out.append(("message_stop", {"type": "message_stop"}))
    return out


def search(sid, query):
    return {"type": "server_tool_use", "id": sid, "name": "web_search", "input": {"query": query}}


def results(sid, items):
    return {"type": "web_search_tool_result", "tool_use_id": sid,
            "content": [dict({"type": "web_search_result", "encrypted_content": "ENC"}, **x)
                        for x in items]}


def cite(url, title, text="..."):
    return {"type": "web_search_result_location", "url": url, "title": title,
            "encrypted_index": "IDX", "cited_text": text}


MSC_URL = "https://www.msc.com/en/track-a-shipment?agencyPath=msc"
MSC_NOTICE = "https://www.msc.com/en/newsroom/customer-advisories/2026/october/schedule-update"
PORT_URL = "https://www.ghanaports.gov.gh/page/operational-notices"
FAKE_URL = "https://made-up-tracker.example/MEDUAHP69377"
CMA_URL = "https://www.cma-cgm.com/news/customer-advisory-tracking"


def scenario(name):
    if name == "where":
        return sse([
            search("s1", "MEDUAHP69377 MSC tracking"),
            results("s1", [{"url": MSC_URL, "title": "Track a shipment | MSC", "page_age": "Oct 6, 2026"}]),
            search("s2", "MSC vessel voyage MEDUAHP69377 sailing"),
            results("s2", [{"url": MSC_NOTICE, "title": "Schedule update | MSC"}]),
            {"type": "text", "text": "I checked the run first: MEDUAHP69377 is with MSC and the "
             "run read an ETA of 10 Nov 2026. MSC's public schedule notice says the service is "
             "running about two days late this week. I couldn't verify the vessel's live "
             "position from a reliable source. See " + FAKE_URL + " for details.",
             "citations": [cite(MSC_NOTICE, "Schedule update | MSC"),
                           cite(FAKE_URL, "Made-up tracker")]},
        ])
    if name == "port":
        return sse([
            search("p1", "Tema port congestion operational notice October 2026"),
            results("p1", [{"url": PORT_URL, "title": "Operational notices | GPHA"}]),
            {"type": "text", "text": "I checked the port authority's notices: no congestion "
             "notice for Tema this week.", "citations": [cite(PORT_URL, "Operational notices")]},
        ])
    if name == "carrier":
        return sse([
            search("c1", "CMA CGM customer advisory tracking access restricted"),
            results("c1", [{"url": CMA_URL, "title": "Customer advisory | CMA CGM"}]),
            {"type": "server_tool_use", "id": "f1", "name": "web_fetch", "input": {"url": CMA_URL}},
            {"type": "web_fetch_tool_result", "tool_use_id": "f1", "content": {
                "type": "web_fetch_tool_result_error", "error_code": "url_not_accessible"}},
            {"type": "text", "text": "From our run: CMA CGM showed its restriction page after "
             "the verification, so nothing was extracted or written. From public information: "
             "CMA CGM lists a tracking advisory, but I couldn't open the page itself. I can't "
             "connect the two: nothing shows the advisory caused our restriction.",
             "citations": [cite(CMA_URL, "Customer advisory | CMA CGM")]},
        ])
    if name == "nothing":
        return sse([
            search("n1", "MEDUAHP69377 vessel position"),
            {"type": "web_search_tool_result", "tool_use_id": "n1", "content": []},
            {"type": "text", "text": "I couldn't verify the vessel's position from a reliable "
             "source. What I can confirm is what the run holds: MSC, ETA 10 Nov 2026."},
        ])
    if name == "general":
        return sse([{"type": "text", "text": "A blank sailing is a cancelled voyage; this week "
                     "I found no MSC blank sailings announced."}])
    if name == "pause":
        return None
    return sse([{"type": "text", "text": "ok"}])


class Api(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        CALLS.append({"path": self.path, "body": body,
                      "headers": {k.lower(): v for k, v in self.headers.items()}})
        if SCENARIO.get("status", 200) != 200:
            data = json.dumps({"type": "error", "error": {
                "type": "authentication_error", "message": "invalid x-api-key"}}).encode()
            self.send_response(SCENARIO["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        name = SCENARIO["name"]
        if name == "pause":
            # First call pauses after a search; the continuation finishes it.
            first = len([c for c in CALLS if c["body"].get("messages") and
                         len(c["body"]["messages"]) == 1]) and len(
                             CALLS[-1]["body"]["messages"]) == 1
            events = sse([search("q1", "MSC schedule"),
                          results("q1", [{"url": MSC_NOTICE, "title": "Schedule update"}])],
                         stop="pause_turn") if first else sse([
                              {"type": "text", "text": "Continued after the pause.",
                               "citations": [cite(MSC_NOTICE, "Schedule update")]}])
        else:
            events = scenario(name)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for event, payload in events:
            self.wfile.write("event: {0}\ndata: {1}\n\n".format(event, json.dumps(payload))
                             .encode("utf-8"))
            self.wfile.flush()
            if SCENARIO.get("delay"):
                time.sleep(SCENARIO["delay"])
        self.close_connection = True


api = ThreadingHTTPServer(("127.0.0.1", 0), Api)
api.daemon_threads = True
threading.Thread(target=api.serve_forever, daemon=True).start()
API = "http://127.0.0.1:{0}".format(api.server_address[1])

from dashboard import assistant                       # noqa: E402
from dashboard import server as tower_server          # noqa: E402
from dashboard.bridge import ControlTowerState        # noqa: E402
from intelligence import knowledge as K, research as R  # noqa: E402

# ── a run, as the bridge records it ─────────────────────────────────────
T = ControlTowerState()
T.run_started(run_id="20261006-090000-r1", dry_run=False, target_status="Under Clearance",
              max_records=200, max_pages=10)
T.shipment_started({"bol_awb": "MEDUAHP69377", "carrier": "MSC", "provider": "MSC",
                    "table_page": 1, "current_eta": "05/11/2026"})
T.provider_result({"provider": "MSC", "tracking_status": "Estimated arrival", "eta": "10/11/2026",
                   "eta_source": "ETA"})
T.view_updated("COE", "ETA", "10/11/2026", verified=True)
T.shipment_finished("MEDUAHP69377", "SUCCESS", "", {"coe": "COE ETA updated with 10/11/2026"})
REF = "CMAU7700001"
URL = "https://www.cma-cgm.com/ebusiness/tracking/search"
T.shipment_started({"bol_awb": REF, "carrier": "CMA CGM", "provider": "CMA_CGM", "table_page": 1})
T.human_verification_required(REF, "CMA CGM")
T.human_verification_cleared(REF, 40)
T.carrier_access(REF, "RESTRICTED", URL + "?session=abc123&code=4417",
                 "CMA CGM restriction page after the human verification",
                 facts={"carrier": "CMA CGM", "verification_completed": True,
                        "carrier_access": "restricted"})
T.shipment_finished(REF, "FAILED", "CMA CGM restricted access after the human verification "
                    "was completed. Nothing was extracted or written.",
                    outcome="CARRIER ACCESS RESTRICTED",
                    failure={"category": "CARRIER_ACCESS_RESTRICTED", "stage": "carrier_access",
                             "operation": "Carrier access",
                             "detail": "CMA CGM showed a restriction page after the human "
                                       "verification; the lookup was stopped.",
                             "observed": {"url": URL, "title": "Access is temporarily restricted",
                                          "after_human_verification": "yes"}})
T.counters(2, 1, 0, 1)
STATE = T.snapshot()


def ask(question, context=None):
    return assistant.answer(question, STATE, dict(context or {}))


def research_on():
    os.environ.update({"ATLAS_RESEARCH": "1", "ATLAS_RESEARCH_API_KEY": "test-key-not-real",
                       "ATLAS_RESEARCH_BASE_URL": API, "ATLAS_RESEARCH_CACHE_S": "1800"})
    os.environ.pop("ANTHROPIC_API_KEY", None)
    R.clear_cache()


def research_off():
    os.environ["ATLAS_RESEARCH"] = "0"


ROBOTIC = re.compile(r"based on the available information|i am unable to provide|"
                     r"i don't have that information", re.I)

# ═════════════════════════════════════════════════════════════════════════
rule("1. RESEARCH OFF — THE RUN'S OWN PICTURE, SAID LIKE A PERSON, HONESTLY BOUNDED")
# ═════════════════════════════════════════════════════════════════════════
research_off()
before = len(CALLS)
r = ask("Where is MEDUAHP69377?")
a = r["answer"]
check("Shipment question from run data: a colleague's opening",
      a.startswith("I checked what we have in the run first. MEDUAHP69377 is with MSC."), a[:160])
check("...with the run's own facts: the carrier's ETA, written and read back",
      "10/11/2026" in a and "read it back" in a, a[:400])
check("...and says what it would have to look up, and why it can't",
      "I'd have to look up" in a and "switched off" in a, a[-220:])
check("...no vessel, position or port invented", not re.search(
    r"\b(IMO \d|latitude|longitude|berth \d|vessel [A-Z]{3,})\b", a), a)
check("No research call was made while research is off", len(CALLS) == before)
check("No robotic phrasing", not ROBOTIC.search(a), a[:200])

r = ask("Why did CMAU7700001 fail?")
a = r["answer"]
check("Error investigation: the plain-words lead first",
      a.startswith("The run got past the human verification, but CMA CGM still returned its "
                   "access-restricted page for CMAU7700001."), a[:200])
check("...the run's grounded record kept (Fact / Not established)",
      "**Fact**" in a and "Not established" in a)
inv = r.get("investigation") or {}
check("...causes ranked against the evidence: LIKELY access-level first, network POSSIBLE",
      inv.get("causes", [{}])[0].get("status") == "LIKELY"
      and "not a problem with the shipment" in inv["causes"][0]["cause"]
      and any(c["status"] == "POSSIBLE" and "network path" in c["cause"] for c in inv["causes"]),
      [(c["status"], c["cause"][:50]) for c in inv.get("causes", [])])
check("...nothing CONFIRMED as a cause without proof",
      not any(c["status"] == "CONFIRMED" for c in inv.get("causes", [])))
check("...and what it will not do: no retrying against it, no IP rotation, no evasion",
      "rotating IPs" in a and "retrying the lookup against the restriction" in a)
check("...says outside sources were not checked, and why",
      "I haven't checked outside sources for this" in a)

r = ask("What is transshipment?")
check("General question: answered from knowledge, not refused",
      r["answer"].startswith("Transshipment is when cargo is unloaded") and
      r.get("intent") == "general_knowledge", r["answer"][:120])
r = ask("What's the difference between ETA and ATA?")
check("ETA vs ATA explained", "Estimated Time of Arrival" in r["answer"]
      and "Actual Time of Arrival" in r["answer"])
r = ask("What does Under Clearance mean?")
check("'Under Clearance' explained in this Control Tower's terms",
      "customs clearance is in progress" in r["answer"])
r = ask("What is the weather in Cairo?")
check("Something the run doesn't hold: said plainly, never the generic escape",
      r["answer"].startswith("That isn't something this run records") and not
      ROBOTIC.search(r["answer"]) and r.get("fallback") is True, r["answer"][:120])

# ═════════════════════════════════════════════════════════════════════════
rule("2. PROACTIVE SHIPMENT RESEARCH — RUN FIRST, THEN THE WEB, SOURCES THAT WERE CHECKED")
# ═════════════════════════════════════════════════════════════════════════
research_on()
SCENARIO.update(name="where", status=200, delay=0)
before = len(CALLS)
r = ask("Where is MEDUAHP69377?", {"progress_id": "test-progress-001"})
check("Researched without being told to search (proactive)", len(CALLS) == before + 1)
call = CALLS[-1]
body = call["body"]
check("The real API shape: /v1/messages, version header, key header, streamed",
      call["path"] == "/v1/messages" and call["headers"].get("anthropic-version") == "2023-06-01"
      and call["headers"].get("x-api-key") == "test-key-not-real" and body.get("stream") is True)
check("...with the web search and web fetch server tools",
      {t["type"] for t in body["tools"]} == {"web_search_20250305", "web_fetch_20250910"})
prompt = body["messages"][0]["content"]
check("The run's facts are sent as authoritative: MSC, ETA 10/11/2026",
      "CONTROL TOWER RUN" in prompt and "10/11/2026" in prompt and "MSC" in prompt, prompt[:400])
check("The rules travel with it: run facts win, disagreements stated, never invent",
      "Never silently replace them" in body["system"] and "do not pick one silently" in
      body["system"] and "Never invent anything" in body["system"])
check("The answer is the researched, conversational one",
      r["answer"].startswith("I checked the run first: MEDUAHP69377 is with MSC"), r["answer"][:120])
check("A URL that no search or fetch returned is removed from the answer (no fabricated source)",
      FAKE_URL not in r["answer"] and "link removed" in r["answer"], r["answer"][-160:])
srcs = r.get("web_sources") or []
check("Sources: only cited pages that came back from a real search",
      [s["url"] for s in srcs] == [MSC_NOTICE], srcs)
check("...labelled by who publishes them (carrier, official)",
      srcs and srcs[0]["kind"] == "Carrier (official)" and srcs[0]["publisher"] == "msc.com", srcs)
check("The searches that ran are reported", (r.get("research") or {}).get("searches") ==
      ["MEDUAHP69377 MSC tracking", "MSC vessel voyage MEDUAHP69377 sailing"], r.get("research"))
check("The run's own grounded reply is kept as the answer's details",
      "MEDUAHP69377" in (r.get("details") or ""), (r.get("details") or "")[:120])
check("'I couldn't verify' — uncertainty said, not hidden", "I couldn't verify" in r["answer"])

# ═════════════════════════════════════════════════════════════════════════
rule("3. VESSEL, PORT, DELAY, CARRIER, MIXED — TARGETED, AND THE PROGRESS IS REAL")
# ═════════════════════════════════════════════════════════════════════════
R.clear_cache()
SCENARIO.update(name="port")
r = ask("Can you check the port for MEDUAHP69377 — any congestion?", {"progress_id": "test-port-0001"})
p = R.progress("test-port-0001")
check("Port research: the stage shown is the real one, with its query",
      p["stage"] in ("Checking the port…", "Putting it together…") and p["steps"] >= 2, p)
check("...port authority classified as Port / terminal",
      (r.get("web_sources") or [{}])[0].get("kind") == "Port / terminal", r.get("web_sources"))
R.clear_cache()
SCENARIO.update(name="where")
r = ask("Why is MEDUAHP69377 late?")
check("Delayed-shipment question goes to shipment research",
      "MSC vessel voyage" in " ".join((r.get("research") or {}).get("searches") or []))
check("...vessel query recognised as vessel research",
      R._friendly("MSC vessel voyage MEDUAHP69377 sailing") == "Checking the vessel…")
R.clear_cache()
SCENARIO.update(name="carrier")
r = ask("Why is CMAU7700001 restricted and is CMA CGM having issues today?")
body = CALLS[-1]["body"]
check("Mixed question: run first, then outside, then the careful connection",
      "FROM OUR RUN" in body["messages"][0]["content"] and r["answer"].startswith("From our run"))
check("...the ranked causes from the run's evidence go with it",
      "ranked_causes_from_run_evidence" in body["messages"][0]["content"])
check("...a page that could not be opened is reported as such: the fetch error is kept",
      (r.get("research") or {}).get("errors") == ["url_not_accessible"] and
      (r.get("research") or {}).get("fetched") == [CMA_URL], r.get("research"))
check("...and only the search result it cites is offered as a source",
      [x["url"] for x in r.get("web_sources") or []] == [CMA_URL], r.get("web_sources"))
check("...and it does not claim the advisory caused the failure",
      "I can't connect the two" in r["answer"])
check("Carrier advisory search shown as checking public updates",
      R._friendly("CMA CGM customer advisory tracking access restricted") in
      ("Checking the latest public updates…", "Looking up this error…"))
sent = json.dumps(CALLS[-1]["body"])
check("Security: the restriction URL is sent without its query (no session, no code)",
      "session=abc123" not in sent and "code=4417" not in sent)
check("Security: nothing named like a credential is sent",
      not re.search(r'"(password|token|cookie|secret|api_key)"', sent, re.I))
check("Security: the rules forbid bypassing CAPTCHA, restrictions, IP rotation",
      "never suggest or attempt to bypass CAPTCHA" in CALLS[-1]["body"]["system"]
      and "rotating IPs" in CALLS[-1]["body"]["system"])

# ═════════════════════════════════════════════════════════════════════════
rule("4. NO RELIABLE RESULT, EXPLICIT SEARCH, GENERAL QUESTIONS, CACHE, PAUSE, ERRORS")
# ═════════════════════════════════════════════════════════════════════════
R.clear_cache()
SCENARIO.update(name="nothing")
r = ask("Where is the vessel carrying MEDUAHP69377 right now?")
check("No reliable result: says so, keeps what it did confirm, shows no sources",
      r["answer"].startswith("I couldn't verify the vessel's position") and
      not r.get("web_sources"), (r["answer"][:100], r.get("web_sources")))
SCENARIO.update(name="general")
before = len(CALLS)
r = ask("Search the web: any MSC blank sailings announced this week?")
check("Explicit search request: researched", len(CALLS) == before + 1 and
      "blank sailing" in r["answer"].lower())
before = len(CALLS)
r = ask("What is demurrage?")
check("Plain general knowledge: answered without a search", len(CALLS) == before and
      r["answer"].startswith("Demurrage is the charge"))
R.clear_cache()
SCENARIO.update(name="where")
before = len(CALLS)
ask("Tell me everything about MEDUAHP69377")
ask("Tell me everything about MEDUAHP69377")
check("The same question twice: researched once (cache)", len(CALLS) == before + 1)
ask("Tell me everything about MEDUAHP69377 — the latest, please")
check("'latest' asks again (fresh)", len(CALLS) == before + 2)
R.clear_cache()
SCENARIO.update(name="pause")
before = len(CALLS)
r = ask("Where is MEDUAHP69377?")
check("A paused turn (pause_turn) is continued by sending it back unchanged",
      len(CALLS) == before + 2 and CALLS[-1]["body"]["messages"][-1]["role"] == "assistant"
      and r["answer"] == "Continued after the pause.", (len(CALLS) - before, r["answer"][:80]))
check("...the paused search's results travel back with their encrypted content",
      "ENC" in json.dumps(CALLS[-1]["body"]["messages"][-1]))
R.clear_cache()
SCENARIO.update(name="where", status=401)
r = ask("Where is MEDUAHP69377?")
check("Research service refuses (401): said plainly, the run's answer still stands",
      "I tried to look this up but couldn't: the research service answered HTTP 401" in
      r["answer"] and "MEDUAHP69377" in r["answer"], r["answer"][-200:])
SCENARIO.update(status=200)
os.environ["ATLAS_RESEARCH_BASE_URL"] = "http://127.0.0.1:9"
R.clear_cache()
r = ask("Where is MEDUAHP69377?")
check("Research service unreachable: said plainly", "could not be reached" in r["answer"],
      r["answer"][-200:])
os.environ["ATLAS_RESEARCH_BASE_URL"] = API

# ═════════════════════════════════════════════════════════════════════════
rule("5. THE SERVER: progress_id IN, REAL STAGES OUT, WHILE IT RESEARCHES")
# ═════════════════════════════════════════════════════════════════════════
from dashboard.bridge import bridge as B                               # noqa: E402
B.run_started(run_id="20261006-090000-r1", dry_run=False, target_status="Under Clearance",
              max_records=200, max_pages=10)
B.shipment_started({"bol_awb": "MEDUAHP69377", "carrier": "MSC", "provider": "MSC",
                    "table_page": 1})
B.provider_result({"provider": "MSC", "tracking_status": "Estimated arrival", "eta": "10/11/2026"})
B.shipment_finished("MEDUAHP69377", "SUCCESS", "", {"coe": "COE ETA updated"})
s = socket.socket()
s.bind(("127.0.0.1", 0))
PORT = s.getsockname()[1]
s.close()
tower_server.start(port=PORT, open_browser=False, host="127.0.0.1")
time.sleep(0.6)
R.clear_cache()
SCENARIO.update(name="where", delay=0.08)
got = {}


def post():
    req = urllib.request.Request("http://127.0.0.1:%d/api/ask" % PORT, data=json.dumps({
        "question": "Where is MEDUAHP69377?", "context": {"progress_id": "srv-progress-0001"}})
        .encode(), headers={"Content-Type": "application/json"})
    got["reply"] = json.load(urllib.request.urlopen(req, timeout=60))


worker = threading.Thread(target=post)
worker.start()
seen = []
for _ in range(80):
    pr = json.load(urllib.request.urlopen(
        "http://127.0.0.1:%d/api/ask/progress?id=srv-progress-0001" % PORT, timeout=5))
    if pr.get("stage") and (not seen or seen[-1] != (pr["stage"], pr.get("detail"))):
        seen.append((pr["stage"], pr.get("detail")))
    if pr.get("done"):
        break
    time.sleep(0.05)
worker.join(30)
check("While ATLAS researched, the page could read what it was doing — real stages",
      any(st == "Looking at the carrier information…" and d == "MEDUAHP69377 MSC tracking"
          for st, d in seen) and any(st == "Checking the vessel…" for st, _d in seen), seen)
check("...and the reply came back with its sources",
      (got.get("reply") or {}).get("web_sources", [{}])[0].get("url") == MSC_NOTICE)
check("A malformed progress id is ignored", R.clean_progress_id("../../etc") == "")
SCENARIO.update(delay=0)

# ═════════════════════════════════════════════════════════════════════════
rule("6. ERROR KNOWLEDGE — RANKED, SAFE, NEVER A GUESSED ROOT CAUSE")
# ═════════════════════════════════════════════════════════════════════════
graph = K.investigate_failure({"classification": "UNKNOWN_FAILURE", "carrier": "Graph",
                               "error_message": "Microsoft Graph sendMail returned 403 "
                                                "ErrorAccessDenied"})
check("Graph Mail.Send 403: LIKELY permission/consent/access policy, first",
      graph["causes"][0]["status"] == "LIKELY" and "Mail.Send" in graph["causes"][0]["cause"])
proxy = K.investigate_failure({"classification": "NETWORK_FAILURE", "carrier": "MSC",
                               "error_message": "net::ERR_PROXY_CONNECTION_FAILED at "
                                                "https://www.msc.com"})
check("Edge ERR_PROXY_CONNECTION_FAILED: LIKELY the worker's proxy",
      proxy["causes"][0]["status"] == "LIKELY" and "proxy" in proxy["causes"][0]["cause"])
policy = K.investigate_failure({"classification": "CARRIER_POLICY_BLOCK", "carrier": "MSC",
                                "declared_cause": {"name": "OCEAN_WRITE"}})
check("A rule of the automation: CONFIRMED, declared by the run",
      policy["causes"][0]["status"] == "CONFIRMED" and "OCEAN_WRITE" in
      policy["causes"][0]["cause"])
human = K.investigate_failure({"classification": "SECURITY_VERIFICATION_REQUIRED",
                               "carrier": "Grimaldi"})
check("Human verification: never automated, never a code read",
      "reading or typing security codes" in human["never"])
blank = K.investigate_failure({"classification": "UNKNOWN_FAILURE"})
check("No evidence: UNKNOWN, no cause proposed", blank["causes"][0]["status"] == "UNKNOWN")
every = [c for inv in (graph, proxy, policy, human) for c in inv["causes"]]
check("Every cause carries why, a check, a fix and how to verify",
      all(c["why"] and c["check"] and c["fix"] and c["verify"] for c in every))
check("No cause recommends evading security",
      not any(re.search(r"\b(bypass|solve the captcha|rotate (the )?ip|residential prox)",
                        c["fix"], re.I) for c in every))

# ═════════════════════════════════════════════════════════════════════════
rule("7. NOTHING RESEARCHED IS LEARNED, NOTHING LEAVES WHEN IT IS OFF")
# ═════════════════════════════════════════════════════════════════════════
src = (HERE / "intelligence" / "research.py").read_text(encoding="utf-8")
check("Research never writes to the learning store",
      "learning" not in re.sub(r'""".*?"""', "", src, flags=re.S).replace("learned", ""))
research_off()
before = len(CALLS)
ask("Where is MEDUAHP69377?")
ask("Search the web for MSC news")
check("ATLAS_RESEARCH=0: no request leaves the building", len(CALLS) == before)
os.environ.pop("ATLAS_RESEARCH_API_KEY", None)
os.environ.pop("ATLAS_RESEARCH", None)
check("No key configured: research is off, and ATLAS says how to switch it on",
      not R.enabled() and "ATLAS_RESEARCH_API_KEY" in R.why_off())

api.shutdown()
print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
