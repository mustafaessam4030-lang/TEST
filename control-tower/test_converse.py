"""
ATLAS conversation layer (intelligence/converse.py), offline: a scripted
stand-in for the model and for the search. The live model and search are
exercised by atlas_demo_acceptance.py.

Run:  python test_converse.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
for name in ("ATLAS_LLM_PROVIDER", "ATLAS_SEARCH_URL", "ATLAS_DEMO_TEST_DATA", "ATLAS_CONVERSE"):
    os.environ.pop(name, None)

from intelligence import converse, llm
from intelligence import research as R
from dashboard import assistant
from dashboard.bridge import ControlTowerState
import atlas_demo

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


class Scripted:
    """Returns the next scripted reply per call; records the prompts."""
    name = "ollama"
    model = "stand-in"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate(self, system, prompt, timeout=None, json_mode=False, max_tokens=700):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)


def use(provider):
    llm.provider = lambda: provider


def plan(q, web=False, query=""):
    return {"question_en": q, "needs_web": web, "web_query": query}


bridge = ControlTowerState()
atlas_demo.seed_test_run(bridge)
STATE = bridge.snapshot()
real_provider, real_search, real_enabled = llm.provider, R.search, R.enabled

print("=" * 68)
print("1. OFF BY DEFAULT")
print("=" * 68)
check("No model configured: conversation is off", not converse.enabled())
r = assistant.answer("Which shipments failed?", STATE)
check("Rules answer as before", "8842001173" in r["answer"] and "llm" not in r)

print("=" * 68)
print("2. GROUNDED ANSWERS, ENGLISH AND ARABIC")
print("=" * 68)
use(Scripted(plan("Which shipments failed?"), {
    "answer": "Shipment 8842001173 failed: the Save/Update button was not found.",
    "verified": ["8842001173 failed with UNEXPECTED PAGE STATE"],
    "likely": ["The page was not in the expected state"], "missing": [], "external": []}))
r = converse.answer("which ones broke?", STATE, {}, assistant._answer_rules)
check("Model answer used", r["llm"]["used"], r["llm"].get("reason"))
check("Records label added by ATLAS", "**Verified (ELAP records)**" in r["answer"])
check("Likely label added by ATLAS", "**Likely, not proven**" in r["answer"])
check("The records are kept as details", "Save/Update" in r["details"])

use(Scripted(plan("Which shipments failed?"), {
    "answer": "الشحنة 8842001173 فشلت.", "verified": ["الشحنة 8842001173 فشلت"],
    "likely": [], "missing": [], "external": []}))
r = converse.answer("ما هي الشحنات التي فشلت؟", STATE, {}, assistant._answer_rules)
check("Arabic question detected", r["llm"].get("language") == "ar")
check("Arabic labels", "مؤكد (من سجلات ELAP)" in r["answer"])

print("=" * 68)
print("3. FAIL CLOSED")
print("=" * 68)
use(Scripted(plan("Which shipments failed?"), {
    "answer": "Shipment 9990001112 failed.", "verified": [], "likely": [],
    "missing": [], "external": []}))
r = converse.answer("which failed?", STATE, {}, assistant._answer_rules)
check("Invented reference rejected -> rules answer",
      not r["llm"]["used"] and "9990001112" not in r["answer"] and "8842001173" in r["answer"],
      r["llm"].get("reason"))

use(Scripted(plan("Which shipments failed?"), {
    "answer": "تم تحديث الشحنة 8842001173 بنجاح", "verified": [], "likely": [],
    "missing": [], "external": []}))
r = converse.answer("ما الذي فشل؟", STATE, {}, assistant._answer_rules)
check("Arabic success claim the records do not make is rejected", not r["llm"]["used"],
      r["llm"].get("reason"))

use(Scripted(plan("Which shipments failed?"), {
    "answer": "الشحنة 8842001173 فشلت ولم تتم كتابة أي بيانات في Hub.", "verified": [],
    "likely": [], "missing": [], "external": []}))
r = converse.answer("ما الذي فشل؟", STATE, {}, assistant._answer_rules)
check("A negated Arabic claim ('was not written') is not a claim", r["llm"]["used"],
      r["llm"].get("reason"))

use(Scripted(llm.LLMError("the local model is not reachable")))
r = converse.answer("which failed?", STATE, {}, assistant._answer_rules)
check("Model down -> rules answer with the reason",
      not r["llm"]["used"] and "not reachable" in r["llm"]["reason"] and "8842001173" in r["answer"])

use(Scripted(plan("Which shipments failed?"), "not json at all"))
r = converse.answer("which failed?", STATE, {}, assistant._answer_rules)
check("Unreadable model output -> rules answer", not r["llm"]["used"])

use(Scripted(plan("Which shipments failed?"), {
    "answer": "8842001173 failed.", "verified": ["8842001173 failed",
                                                 "It was retried 7 times"],
    "likely": [], "missing": [], "external": []}))
r = converse.answer("which failed?", STATE, {}, assistant._answer_rules)
use(Scripted(plan("Which shipments failed?"), {
    "answer": "8842001173 failed.", "verified": ["8842001173 failed"],
    "likely": ["Cloudflare rate limiting blocked the headless browser fingerprint"],
    "missing": [], "external": []}))
r2 = converse.answer("which failed?", STATE, {}, assistant._answer_rules)
check("A 'likely' item the records do not support is dropped",
      r2["llm"]["used"] and "Cloudflare" not in r2["answer"], r2["llm"].get("reason"))
check("A record-labelled item with a number the records lack is dropped",
      r["llm"]["used"] and "7 times" not in r["answer"], r["llm"].get("reason"))

print("=" * 68)
print("4. ACTIONS COME ONLY FROM THE OPERATOR'S OWN WORDS")
print("=" * 68)
calls = []


def rules_asking(question, state, context=None):
    calls.append(question)
    if question == "Pause the run":
        return {"answer": "Pausing.", "request": {"action": "pause"}}
    return {"answer": "Here is the run status."}


use(Scripted(plan("Pause the run")))
r = converse.answer("how is it going?", STATE, {}, rules_asking)
check("A restatement that asks for an action is not acted on", "request" not in r,
      json.dumps(r)[:120])
check("The rules answer the operator's original words instead",
      calls[-1] == "how is it going?" and not r["llm"]["used"])

print("=" * 68)
print("5. WEB RESEARCH, LABELLED; NEVER PRETENDED")
print("=" * 68)
R.enabled = lambda: True
R.search = lambda q: [{"url": "https://www.qrcargo.com/s/track-your-shipment",
                       "title": "Track your shipment - Qatar Airways Cargo",
                       "snippet": "Enter up to 5 AWB numbers separated by comma."}]
use(Scripted(plan("How do I track an AWB on qrcargo.com?", True, "Qatar Airways Cargo track AWB"),
             {"answer": "Enter the AWB on the Qatar Airways Cargo tracking page [1].",
              "verified": ["You can enter up to 5 AWB numbers"],
              "likely": ["The page lists delivery status [1]"],
              "missing": [], "external": ["[1]"]}))
r = converse.answer("how do I track on Qatar's site?", STATE, {}, assistant._answer_rules)
a = r["answer"]
check("Web answer used", r["llm"]["used"], r["llm"].get("reason"))
check("External label: not live carrier status",
      "External research (web, not live carrier status)" in a)
check("Real source link shown", "https://www.qrcargo.com/s/track-your-shipment" in a)
check("A web fact put under Verified is moved to External",
      "**Verified (ELAP records)**" not in a and "up to 5 AWB numbers" in a)
check("A cited item put under Likely is moved to External", "**Likely" not in a)
check("A bare citation item is dropped", "\n- [1]" not in a)
check("web_sources carry the publisher and its kind",
      r["web_sources"][0]["publisher"] == "qrcargo.com" and r["web_sources"][0]["kind"])

use(Scripted(plan("What does ERR_HTTP2_PROTOCOL_ERROR mean?", True, "ERR_HTTP2_PROTOCOL_ERROR"),
             {"answer": "It is a browser network error [1].", "verified": [], "likely": [],
              "missing": [], "external": ["The server broke the HTTP/2 connection [1]"]}))
r = converse.answer("what does ERR_HTTP2_PROTOCOL_ERROR mean?", STATE,
                    {"reference": "8842001173"}, assistant._answer_rules)
check("A web question carries no badge for the shipment last discussed",
      r["llm"]["used"] and not r.get("reference") and not r.get("card"), r.get("reference"))



def search_down(query):
    raise R.ResearchError("not reachable")


R.search = search_down
use(Scripted(plan("What does ERR_HTTP2_PROTOCOL_ERROR mean?", True, "ERR_HTTP2_PROTOCOL_ERROR"),
             {"answer": "I could not research this.", "verified": [], "likely": [],
              "missing": ["What the error means"], "external": []}))
r = converse.answer("what does ERR_HTTP2_PROTOCOL_ERROR mean?", STATE, {},
                    assistant._answer_rules)
check("Search down -> says it did not search, no sources",
      "I did not search the web" in r["answer"] and not r.get("web_sources"), r["answer"][-120:])

R.enabled = lambda: False
use(Scripted(plan("What does ERR_HTTP2_PROTOCOL_ERROR mean?", True, "ERR_HTTP2_PROTOCOL_ERROR"),
             {"answer": "No research.", "verified": [], "likely": [], "missing": [],
              "external": []}))
r = converse.answer("what does it mean?", STATE, {}, assistant._answer_rules)
check("No search configured -> says so", "I did not search the web" in r["answer"])

print("=" * 68)
print("6. TEST DATA LABEL")
print("=" * 68)
llm.provider, R.search, R.enabled = real_provider, real_search, real_enabled
os.environ["ATLAS_DEMO_TEST_DATA"] = "1"
r = assistant.answer("How is the run going?", STATE)
check("Every answer says TEST DATA in demo test-data mode", r["answer"].startswith("**TEST DATA**"))
os.environ.pop("ATLAS_DEMO_TEST_DATA")
r = assistant.answer("How is the run going?", STATE)
check("No label otherwise", "TEST DATA" not in r["answer"])

print("=" * 68)
print("6a. PERSONAL MESSAGES: A WARM REPLY, NOT A ROBOTIC ONE")
print("=" * 68)
llm.provider = real_provider
sad = Scripted({"chat": True, "question_en": "I am so sad", "needs_web": False, "web_query": ""},
               {"answer": "I'm really sorry you're feeling down. I'm here if you want to talk."})
use(sad)
r = converse.answer("im so sad", STATE, {}, assistant._answer_rules)
check("A feeling gets a warm reply from the model", r["llm"]["used"] and r["intent"] == "chat"
      and "sorry" in r["answer"], r["answer"])
check("No second, robotic answer underneath (no details box)", not r.get("details"))
check("Shipments are kept out of a personal message", "not needed" in sad.prompts[-1]
      and "8842001173" not in sad.prompts[-1])

use(Scripted({"chat": True, "question_en": "", "needs_web": False, "web_query": ""},
             {"answer": "Sending you a hug. I'm here if you need me."}))
r = converse.answer("im so sad", STATE, {}, assistant._answer_rules)
check("A personal message with nothing to restate still gets the warm reply",
      r["llm"]["used"] and r["intent"] == "chat", r["llm"].get("reason"))

worried = Scripted({"chat": True, "question_en": "I'm stressed, the run is slow",
                    "needs_web": False, "web_query": ""},
                   {"answer": "That sounds stressful. 8842001173 is queued for a retry."})
use(worried)
r = converse.answer("I'm stressed, the run is slow", STATE, {}, assistant._answer_rules)
check("Work worries may use one real fact from the run", r["llm"]["used"]
      and "8842001173" in worried.prompts[-1], r["answer"])

use(Scripted({"chat": True, "question_en": "I'm stressed about work", "needs_web": False,
              "web_query": ""},
             {"answer": "Don't worry, 9990001112 was saved."}))
r = converse.answer("I'm stressed about work", STATE, {}, assistant._answer_rules)
check("An invented fact in a warm reply is refused: a kind fallback instead",
      not r["llm"]["used"] and "9990001112" not in r["answer"] and "💛" in r["answer"],
      r["answer"])

use(Scripted(plan("What is the meaning of life?"),
             {"answer": "That one is beyond the run records.", "verified": [], "likely": [],
              "missing": [], "external": []}))
r = converse.answer("what is the meaning of life?", STATE, {}, assistant._answer_rules)
check("The rules' 'I didn't understand' text is not shown as a second answer",
      not r.get("details"), r.get("details", "")[:80])
use(Scripted(plan("Which shipments failed?"), {
    "answer": "8842001173 failed.", "verified": ["8842001173 failed"], "likely": [],
    "missing": [], "external": []}))
r = converse.answer("which failed?", STATE, {}, assistant._answer_rules)
check("A real records answer stays one click away (details)", bool(r.get("details")))
llm.provider = real_provider

print("=" * 68)
print("6b. FROM THE PO PAGE, A WAITING PERSON IS NEVER HIDDEN")
print("=" * 68)
waiting_bridge = ControlTowerState()
waiting_bridge.run_started(dry_run=False, target_status="Under Clearance")
waiting_bridge.shipment_started(dict(bol_awb="S330348776", carrier="Grimaldi", provider="GRIMALDI"))
waiting_bridge.human_action_opened({
    "run_id": "r1", "action_id": "9f2c11ab33d0", "reference": "S330348776",
    "carrier": "Grimaldi Lines", "reason": "human_verification_required",
    "opened_at": time.strftime("%Y-%m-%d %H:%M:%S"), "timeout_s": 180,
    "deadline": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 180)),
    "instructions": "Type the security code shown on the page."})
WAITING = waiting_bridge.snapshot()
po_reply = {"answer": "No PO job has been processed yet.", "intent": "po_status"}
out = assistant._also_waiting(dict(po_reply), WAITING)
check("A PO answer while a person is needed ends with what is waiting",
      "Also waiting for you in the ETA run" in out["answer"] and "S330348776" in out["answer"],
      out["answer"][-160:])
out = assistant._also_waiting(dict(po_reply), STATE)
check("Nothing waiting: the PO answer is unchanged", out["answer"] == po_reply["answer"])
out = assistant._also_waiting({"answer": "Run status", "intent": "run"}, WAITING)
check("A shipment-run answer is left alone (it covers waiting itself)",
      out["answer"] == "Run status")

print("=" * 68)
print("7. THE REAL TOWER SWITCHES THE LOCAL AI ON BY ITSELF")
print("=" * 68)
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from intelligence import autoconfig

LOADS = []


class StandIn(BaseHTTPRequestHandler):
    """Ollama (/api/tags, /api/generate) and SearXNG (/search) stand-ins."""
    models = ["qwen3.5:4b"]

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/api/tags"):
            body = {"models": [{"name": m} for m in self.models]}
        elif self.path.startswith("/search"):
            body = {"results": [{"url": "https://example.org", "title": "t", "content": "c"}]}
        else:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        LOADS.append(json.loads(self.rfile.read(length) or b"{}"))
        data = b"{}"
        self.send_response(200); self.send_header("Content-Length", "2")
        self.end_headers(); self.wfile.write(data)


stand_in = ThreadingHTTPServer(("127.0.0.1", 0), StandIn)
threading.Thread(target=stand_in.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:{0}".format(stand_in.server_address[1])
AI_VARS = ("ATLAS_AI", "ATLAS_LLM_PROVIDER", "ATLAS_LLM_URL", "ATLAS_LLM_MODEL",
           "ATLAS_LLM_TIMEOUT_S", "ATLAS_LLM_RETRIES", "ATLAS_LLM_THREADS",
           "ATLAS_LLM_KEEP_ALIVE", "ATLAS_SEARCH_URL")


def fresh(**env):
    for name in AI_VARS:
        os.environ.pop(name, None)
    os.environ.update(env)
    autoconfig._started.clear()
    autoconfig._started["done"] = False
    del LOADS[:]


lines = []
fresh(ATLAS_LLM_URL=BASE, ATLAS_SEARCH_URL=BASE)
autoconfig.apply(lines.append)
for _ in range(50):
    if LOADS:
        break
    time.sleep(0.1)
check("Model installed and pulled: conversation switched on",
      os.environ.get("ATLAS_LLM_PROVIDER") == "ollama" and
      os.environ.get("ATLAS_LLM_MODEL") == "qwen3.5:4b", lines)
check("Search answering: web research switched on", os.environ.get("ATLAS_SEARCH_URL") == BASE)
check("A core is left for Edge and the automation",
      int(os.environ["ATLAS_LLM_THREADS"]) == max(1, (os.cpu_count() or 2) - 1))
check("The model is loaded once in the background, kept for the day",
      LOADS and LOADS[0].get("model") == "qwen3.5:4b" and LOADS[0].get("keep_alive") == "8h")
check("The startup lines say what is on", any("conversation: ON" in x for x in lines))

StandIn.models = []
fresh(ATLAS_LLM_URL=BASE, ATLAS_SEARCH_URL=BASE)
lines = []
autoconfig.apply(lines.append)
check("Model not pulled: conversation stays off, and says how to fix it",
      "ATLAS_LLM_PROVIDER" not in os.environ and any("ollama pull" in x for x in lines), lines)
StandIn.models = ["qwen3.5:4b"]

fresh(ATLAS_AI="0", ATLAS_LLM_URL=BASE, ATLAS_SEARCH_URL=BASE)
autoconfig.apply(lambda line: None)
check("ATLAS_AI=0: nothing is switched on", "ATLAS_LLM_PROVIDER" not in os.environ)

fresh(ATLAS_LLM_URL=BASE, ATLAS_SEARCH_URL=BASE, ATLAS_LLM_TIMEOUT_S="300")
autoconfig.apply(lambda line: None)
check("A setting the operator made wins", os.environ.get("ATLAS_LLM_TIMEOUT_S") == "300")

# A company PC's proxy setting must not swallow requests to this machine.
saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy")}
for k in ("NO_PROXY", "no_proxy"):
    os.environ.pop(k, None)
os.environ["HTTP_PROXY"] = os.environ["http_proxy"] = "http://127.0.0.1:9"
fresh(ATLAS_LLM_URL=BASE, ATLAS_SEARCH_URL=BASE)
ok_model, _d = autoconfig.check_model(BASE, "qwen3.5:4b")
ok_search, _d2 = autoconfig.check_search(BASE)
check("Behind a (broken) company proxy, Ollama and SearXNG here are still reached",
      ok_model and ok_search, (_d, _d2))
for k, v in saved.items():
    if v is None:
        os.environ.pop(k, None)
    else:
        os.environ[k] = v

fresh(ATLAS_LLM_URL="http://127.0.0.1:9", ATLAS_SEARCH_URL="http://127.0.0.1:9")
lines = []
autoconfig.apply(lines.append)
check("Nothing installed: ATLAS stays rule-based, as before",
      "ATLAS_LLM_PROVIDER" not in os.environ and "conversation: off" in lines[0], lines)
fresh()
stand_in.shutdown()

print("=" * 68)
print("8. WEB SEARCH ON THE PC: THE LOCAL SEARXNG'S SETTINGS")
print("=" * 68)
from pathlib import Path
from intelligence import searxng_local as SX
real_paths = (SX.HOME, SX.SRC, SX.VENV)
SX.HOME = Path(tempfile.mkdtemp(prefix="sx_"))
SX.SRC, SX.VENV = SX.HOME / "src", SX.HOME / "venv"
saved = {k: os.environ.get(k) for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")}
os.environ["HTTPS_PROXY"] = os.environ["https_proxy"] = "http://proxy.company.example:8080"
try:
    import yaml
    data = yaml.safe_load(SX._settings().read_text(encoding="utf-8"))
    check("Listens on this PC only (127.0.0.1:8888)",
          data["server"]["bind_address"] == "127.0.0.1" and data["server"]["port"] == 8888)
    check("JSON answers on, rate limiter off (ATLAS is its only user)",
          "json" in data["search"]["formats"] and data["server"]["limiter"] is False)
    check("The PC's proxy is passed on",
          data["outgoing"]["proxies"] == {"all://": ["http://proxy.company.example:8080"]})
    bundle = Path(data["outgoing"]["verify"])
    check("Trusted certificates go in one file SearXNG checks against",
          bundle.exists() and "BEGIN CERTIFICATE" in bundle.read_text(encoding="ascii"))
    check("No secret is written to the settings file", "secret_key" not in data["server"])
    check("Not installed: nothing is started", not SX.installed() and SX.start() is False)
except ImportError:
    check("PyYAML present for the settings test", False, "pip install pyyaml")
finally:
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    SX.HOME, SX.SRC, SX.VENV = real_paths
check("The tower only starts the local search for the default address",
      (os.environ.update({"ATLAS_SEARCH_URL": "http://127.0.0.1:9"}) or True) and
      autoconfig._start_local_search() is False)
os.environ.pop("ATLAS_SEARCH_URL", None)

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
