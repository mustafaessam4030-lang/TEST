"""
ATLAS as a colleague — shipment intelligence, error investigation, general
questions — and its two OPTIONAL, LOCAL, FREE additions: a self-hosted
SearXNG search and a local Ollama model that only re-phrases.

SIMULATED: SearXNG, an "official" web page server and Ollama are played by
local HTTP stand-ins with the same endpoints (/search?format=json,
/robots.txt + a page, /api/tags + /api/chat). They prove the plumbing — what
is sent, what is shown, what is refused, what happens when they fail — never
the quality of a real search or a real model. Everything else (the run, the
bridge, ATLAS's rules and routing, the knowledge, the fact guard, the server,
the progress endpoint) is the real code.

There is no paid AI API anywhere: §8 checks the code for one.

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
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("ATLAS_INTEL_DIR", tempfile.mkdtemp(prefix="ct_research_intel_"))
os.environ.setdefault("ATLAS_DATA_ORIGIN", "test")
os.environ.setdefault("PO_DATA_DIR", tempfile.mkdtemp(prefix="ct_research_po_"))
# These checks cover the phrasing path ATLAS uses with the conversation layer
# off; the conversation layer (intelligence/converse.py) has test_converse.py.
os.environ["ATLAS_CONVERSE"] = "0"
for k in ("ATLAS_SEARCH_URL", "ATLAS_LLM_PROVIDER", "ATLAS_LLM_MODEL", "ATLAS_LLM_URL"):
    os.environ.pop(k, None)

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


# ── stand-ins (SIMULATED) ────────────────────────────────────────────────
SEARCHES, FETCHES, CHATS = [], [], []
STAND = {"results": "msc", "chat": "good", "search_down": False, "delay": 0.0}
PAGE_PORT = None


def page_url(path):
    return "http://127.0.0.1:{0}{1}".format(PAGE_PORT, path)


class Searx(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        SEARCHES.append({"q": q.get("q", [""])[0], "format": q.get("format", [""])[0]})
        if STAND["delay"]:
            time.sleep(STAND["delay"])
        if STAND["search_down"]:
            self.send_response(503)
            self.end_headers()
            return
        results = []
        if STAND["results"] == "msc":
            results = [
                {"url": page_url("/en/newsroom/customer-advisories/schedule-update"),
                 "title": "Customer advisory: schedule update",
                 "content": "Service AE7 vessels are running about two days late this week."},
                {"url": page_url("/en/track-a-shipment"), "title": "Track a shipment",
                 "content": "Track your shipment."},
                {"url": "https://blog.example.net/msc-news", "title": "MSC news roundup",
                 "content": "Industry commentary."}]
        body = json.dumps({"results": results}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Pages(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        FETCHES.append(self.path)
        if self.path == "/robots.txt":
            body = b"User-agent: *\nDisallow: /private/\n"
            ctype = "text/plain"
        else:
            body = ("<html><head><script>var x=1;</script></head><body><h1>Schedule update"
                    "</h1><p>Service AE7 vessels are running about two days late this week."
                    "</p></body></html>").encode()
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class Ollama(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json({"models": [{"name": "qwen2.5:7b-instruct", "model": "qwen2.5:7b-instruct"}]})
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        CHATS.append(body)
        if STAND["chat"] == "slow":
            time.sleep(3)
        mode = STAND["chat"]
        if mode == "good":
            text = ("I checked the run first. MEDUAHP69377 is with MSC; the carrier's ETA was "
                    "10/11/2026 and the run wrote it to the Hub and read it back.")
        elif mode == "invent":
            text = ("MEDUAHP69377 is on the vessel MSC AURORA, IMO 9812345, arriving 14/11/2026 "
                    "at Tema. Delivery was confirmed.")
        else:
            text = "x"
        self._json({"message": {"role": "assistant", "content": text}, "done": True})


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:{0}".format(srv.server_address[1])


searx, SEARX = serve(Searx)
pages, PAGES = serve(Pages)
PAGE_PORT = pages.server_address[1]
ollama, OLLAMA = serve(Ollama)

from dashboard import assistant                         # noqa: E402
from dashboard import server as tower_server            # noqa: E402
from dashboard.bridge import ControlTowerState          # noqa: E402
from intelligence import factguard as FG, knowledge as K, llm as L, research as R  # noqa: E402

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


def search_on():
    os.environ.update({"ATLAS_SEARCH_URL": SEARX, "ATLAS_FETCH_ALLOW": "127.0.0.1"})
    os.environ.pop("ATLAS_SEARCH", None)
    R.clear_cache()


def search_off():
    os.environ["ATLAS_SEARCH"] = "0"


def llm_on(mode="good"):
    STAND["chat"] = mode
    os.environ.update({"ATLAS_LLM_PROVIDER": "ollama", "ATLAS_LLM_URL": OLLAMA,
                       "ATLAS_LLM_MODEL": "qwen2.5:7b-instruct", "ATLAS_LLM_TIMEOUT_S": "2",
                       "ATLAS_LLM_RETRIES": "0"})


def llm_off():
    os.environ["ATLAS_LLM_PROVIDER"] = "none"


ROBOTIC = re.compile(r"based on the available information|i am unable to provide|"
                     r"i don't have that information", re.I)

# ═════════════════════════════════════════════════════════════════════════
rule("1. NOTHING SET UP — THE DETERMINISTIC ATLAS, SAID LIKE A PERSON, HONESTLY BOUNDED")
# ═════════════════════════════════════════════════════════════════════════
search_off()
llm_off()
before = (len(SEARCHES), len(CHATS))
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
check("No search and no model call while neither is set up",
      (len(SEARCHES), len(CHATS)) == before)
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
os.environ.pop("ATLAS_SEARCH", None)
check("Nothing configured: research off, and ATLAS says how to set it up (self-hosted SearXNG)",
      not R.enabled() and "SearXNG" in R.why_off() and "ATLAS_SEARCH_URL" in R.why_off())
check("No model configured: the provider is 'none' and says so",
      L.provider().name == "none" and L.provider().health()["ok"] is False)

# ═════════════════════════════════════════════════════════════════════════
rule("2. SELF-HOSTED SEARCH (SearXNG) — targeted queries, official pages only, labelled WEB")
# ═════════════════════════════════════════════════════════════════════════
search_on()
llm_off()
STAND["results"] = "msc"
s0, f0 = len(SEARCHES), len(FETCHES)
r = ask("Where is MEDUAHP69377?", {"progress_id": "test-progress-001"})
qs = [x["q"] for x in SEARCHES[s0:]]
check("Targeted searches, built from the run (carrier + reference, carrier advisories)",
      qs[:2] == ["MSC MEDUAHP69377", "MSC customer advisory schedule update"]
      and all(x["format"] == "json" for x in SEARCHES[s0:]), qs)
check("The run's answer comes first, unchanged; outside information is a labelled section",
      r["answer"].startswith("I checked what we have in the run first.")
      and "**From public sources (not run data)**" in r["answer"]
      and "two days late" in r["answer"], r["answer"][-300:])
check("Sources listed with who publishes them",
      any(s["url"].endswith("/schedule-update") for s in r.get("web_sources") or []),
      r.get("web_sources"))
paths = FETCHES[f0:]
check("Only an allowed official page was read — robots.txt first; never a tracking page",
      "/robots.txt" in paths and "/en/newsroom/customer-advisories/schedule-update" in paths
      and "/en/track-a-shipment" not in paths, paths)
check("A blog result is listed but never fetched",
      not R.fetch_allowed("https://blog.example.net/msc-news"))
check("Fetch rules: carrier tracking and sign-in pages are never fetched",
      not R.fetch_allowed("https://www.msc.com/en/track-a-shipment")
      and not R.fetch_allowed("https://www.cma-cgm.com/login")
      and R.fetch_allowed("https://www.msc.com/en/newsroom/customer-advisories"))
p = R.progress("test-progress-001")
check("Real progress stages were recorded (the searches and the page read)", p["steps"] >= 3, p)
s1 = len(SEARCHES)
ask("Where is MEDUAHP69377?")
check("The same question again within 30 min: from the cache, no new search",
      len(SEARCHES) == s1)
ask("Where is MEDUAHP69377 right now? latest please")
check("'latest' searches again", len(SEARCHES) > s1)
R.clear_cache()
r = ask("Why did CMAU7700001 fail?")
q_err = [x["q"] for x in SEARCHES][-2:]
check("Error question: the exact error text searched (no URL, no session values sent)",
      any("CMA CGM CMA CGM restricted access" in q or "restricted access after the human" in q
          for q in q_err) and not any("session=abc123" in q or "4417" in q for q in q_err),
      q_err)
check("...the ranked causes stay; WEB is added after them",
      r["answer"].index("What I think is going on") < r["answer"].index("From public sources"))
STAND["results"] = "none"
R.clear_cache()
r = ask("Is MSC having problems today with MEDUAHP69377?")
check("Nothing reliable found: said so, nothing invented, no sources",
      "found nothing reliable" in r["answer"] and not r.get("web_sources"), r["answer"][-160:])
STAND["results"], STAND["search_down"] = "msc", True
R.clear_cache()
r = ask("Where is MEDUAHP69377?")
check("Search service down: ATLAS still answers from the run and says it couldn't check",
      r["answer"].startswith("I checked what we have in the run first.")
      and "couldn't" in r["answer"] and "search service could not be used" in r["answer"],
      r["answer"][-200:])
STAND["search_down"] = False
os.environ["ATLAS_SEARCH_URL"] = "https://searx.example.com"
check("A non-local search host is refused unless explicitly allowed",
      not R.enabled() and "not on this machine" in R.why_off())
os.environ["ATLAS_SEARCH_URL"] = SEARX

# ═════════════════════════════════════════════════════════════════════════
rule("3. LOCAL MODEL (Ollama) — phrasing only, behind the fact guard, always a fallback")
# ═════════════════════════════════════════════════════════════════════════
search_off()
llm_on("good")
c0 = len(CHATS)
r = ask("Where is MEDUAHP69377?")
check("A healthy local model re-phrases ATLAS's answer (the original kept as details)",
      (r.get("llm") or {}).get("used") is True and r["answer"].startswith("I checked the run first")
      and r.get("details", "").startswith("I checked what we have in the run first."),
      (r.get("llm"), r["answer"][:80]))
body = CHATS[-1]
check("The request is Ollama's /api/chat, local, with ATLAS's answer and the run evidence",
      body["model"] == "qwen2.5:7b-instruct" and body["stream"] is False
      and "ANSWER (authoritative)" in body["messages"][1]["content"]
      and "10/11/2026" in body["messages"][1]["content"])
check("...and its rules: only the given facts, no invented success, no security bypass",
      "Use ONLY facts present" in body["messages"][0]["content"]
      and "Never suggest bypassing CAPTCHA" in body["messages"][0]["content"])
check("No credential or session value is sent to the model",
      "session=abc123" not in json.dumps(CHATS) and "4417" not in json.dumps(CHATS))
llm_on("invent")
r = ask("Where is MEDUAHP69377?")
check("A phrasing that INVENTS a vessel, IMO, date and 'confirmed': rejected, ATLAS's own answer "
      "shown", (r.get("llm") or {}).get("used") is False
      and "added things ATLAS does not hold" in r["llm"]["reason"]
      and r["answer"].startswith("I checked what we have in the run first."),
      (r.get("llm"), r["answer"][:80]))
llm_on("slow")
t0 = time.monotonic()
r = ask("Where is MEDUAHP69377?")
check("A model that does not answer within the timeout: ATLAS's own answer, bounded wait",
      (r.get("llm") or {}).get("used") is False and time.monotonic() - t0 < 6
      and r["answer"].startswith("I checked what we have in the run first."),
      (r.get("llm"), round(time.monotonic() - t0, 1)))
os.environ["ATLAS_LLM_MODEL"] = "not-pulled:1b"
r = ask("Where is MEDUAHP69377?")
check("A model that is not pulled: health says so, ATLAS answers without it",
      (r.get("llm") or {}).get("used") is False and "not pulled" in r["llm"]["reason"])
os.environ.update({"ATLAS_LLM_MODEL": "qwen2.5:7b-instruct", "ATLAS_LLM_URL":
                   "https://llm.example.com"})
check("A non-local model host is refused unless explicitly allowed (no hosted endpoint by "
      "accident)", L.provider().health()["ok"] is False and "not on this machine" in
      L.provider().health()["detail"])
os.environ["ATLAS_LLM_URL"] = OLLAMA
llm_off()
check("PO answers never go through the model (they are records, not prose to improve)",
      ask("What happened with this PO?", {"domain": "po"}).get("llm") is None)

# ═════════════════════════════════════════════════════════════════════════
rule("4. THE FACT GUARD — deterministic")
# ═════════════════════════════════════════════════════════════════════════
src = ["MEDUAHP69377 is with MSC. ETA 10/11/2026. 653,492.35 GHS.", {"carrier": "MSC"}]
check("Same facts re-worded: accepted",
      FG.check("MSC has MEDUAHP69377; its ETA is 10/11/2026 and duty 653492.35 GHS.", src)[0])
ok, v = FG.check("MEDUAHP69377 arrives 14/11/2026 on IMO 9812345.", src)
check("A new date and a new number: rejected, each named", not ok and len(v) >= 2, v)
ok, v = FG.check("The shipment MSKU1234567 is also affected.", src)
check("A reference not in the inputs: rejected", not ok, v)
ok, v = FG.check("The ETA was confirmed and written to the Hub.", src)
check("A success claim the inputs do not make: rejected", not ok and any("claim" in x for x in v),
      v)
ok, v = FG.check("See https://made-up.example/x for details.", src)
check("A link not in the inputs: rejected", not ok, v)
neg = ["CMAU7700001: CMA CGM restricted access. Nothing was extracted or written. "
       "Status RESTRICTED. The ETA was not updated."]
ok, v = FG.check("For CMAU7700001 the ETA was written to the Hub.", neg)
check("A negated fact turned positive ('nothing was written' → 'was written'): rejected",
      not ok and any("did not happen" in x for x in v), v)
ok, v = FG.check("CMAU7700001's ETA was updated successfully.", neg)
check("'not updated' → 'updated successfully': rejected", not ok, v)
check("The negation kept: accepted",
      FG.check("CMA CGM restricted access for CMAU7700001; nothing was extracted or written, and "
               "the ETA wasn't updated.", neg)[0])
ok, v = FG.check("CMAU7700001 is now SUCCESS.", neg)
check("A status label ATLAS did not use (SUCCESS for a RESTRICTED shipment): rejected",
      not ok and any("status SUCCESS" in x for x in v), v)
ok, v = FG.check("The email was sent and the PO is EMAIL_CONFIRMED.",
                 ["The PO is EMAIL_PREPARED; nothing has been sent yet."])
check("A PO email claim the record does not make: rejected", not ok, v)

# ═════════════════════════════════════════════════════════════════════════
rule("5. THE SERVER — progress and the local health endpoint")
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
search_on()
llm_off()
STAND["delay"] = 0.3
got = {}


def post():
    req = urllib.request.Request("http://127.0.0.1:%d/api/ask" % PORT, data=json.dumps({
        "question": "Where is MEDUAHP69377?", "context": {"progress_id": "srv-progress-0001"}})
        .encode(), headers={"Content-Type": "application/json"})
    got["reply"] = json.load(urllib.request.urlopen(req, timeout=60))


worker = threading.Thread(target=post)
worker.start()
seen = []
for _ in range(120):
    pr = json.load(urllib.request.urlopen(
        "http://127.0.0.1:%d/api/ask/progress?id=srv-progress-0001" % PORT, timeout=5))
    if pr.get("stage") and (not seen or seen[-1] != (pr["stage"], pr.get("detail"))):
        seen.append((pr["stage"], pr.get("detail")))
    if pr.get("done"):
        break
    time.sleep(0.05)
worker.join(30)
check("While ATLAS searched, the page could read what it was doing — the real queries",
      any(d == "MSC MEDUAHP69377" for _s, d in seen), seen)
check("...and the reply came back with its sources",
      bool((got.get("reply") or {}).get("web_sources")))
health = json.load(urllib.request.urlopen("http://127.0.0.1:%d/api/atlas/llm" % PORT, timeout=5))
check("/api/atlas/llm: status only (provider, model, configured) — no prompt, no data",
      health["llm"]["provider"] == "none" and health["search"]["configured"] is True
      and set(health) == {"llm", "search"}, health)
check("A malformed progress id is ignored", R.clean_progress_id("../../etc") == "")
STAND["delay"] = 0.0

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
rule("7. NOTHING RESEARCHED IS LEARNED; NOTHING LEAVES WHEN IT IS OFF")
# ═════════════════════════════════════════════════════════════════════════
src = (HERE / "intelligence" / "research.py").read_text(encoding="utf-8")
check("Research never writes to the learning store",
      "learning" not in re.sub(r'""".*?"""', "", src, flags=re.S).replace("learned", ""))
search_off()
llm_off()
before = (len(SEARCHES), len(FETCHES), len(CHATS))
ask("Where is MEDUAHP69377?")
ask("Search the web for MSC news")
check("ATLAS_SEARCH=0 and no model: no request leaves the building",
      (len(SEARCHES), len(FETCHES), len(CHATS)) == before)

# ═════════════════════════════════════════════════════════════════════════
rule("8. NO PAID AI API ANYWHERE")
# ═════════════════════════════════════════════════════════════════════════
code = ""
for path in HERE.rglob("*"):
    rel = path.relative_to(HERE).parts
    # searxng/: the search service SETUP_WEB_SEARCH.bat installs on the PC —
    # third-party, ignored by Git, never packaged (make_release.py).
    if not path.is_file() or rel[0].startswith((".", "C:")) or rel[0] == "searxng" \
            or path.name.startswith("test_") \
            or path.name == "make_release.py" \
            or path.suffix not in (".py", ".js", ".html", ".txt", ".json", ".yml", ".yaml",
                                   ".bicep", ".ps1", ".bat", ".cfg", ".toml", ".env"):
        continue
    code += path.read_text(encoding="utf-8", errors="replace")
paid = re.findall(r"api\.anthropic\.com|anthropic-version|x-api-key|ANTHROPIC_API_KEY|"
                  r"api\.openai\.com|OPENAI_API_KEY|generativelanguage\.googleapis|"
                  r"import anthropic|import openai|ATLAS_RESEARCH_API_KEY", code)
check("No Anthropic, OpenAI or Gemini endpoint, key, header or SDK in the product code, "
      "static pages, requirements or deployment files",
      not paid, sorted(set(paid)))

for srv in (searx, pages, ollama):
    srv.shutdown()
print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
