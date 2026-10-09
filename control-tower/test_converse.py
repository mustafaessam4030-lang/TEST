"""
ATLAS conversation layer (intelligence/converse.py), offline: a scripted
stand-in for the model and for the search. The live model and search are
exercised by atlas_demo_acceptance.py.

Run:  python test_converse.py
"""

import json
import os
import sys
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

print()
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
