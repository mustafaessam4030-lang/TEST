"""
ATLAS with the Human Action Queue: the brief's conversation, end to end.

The run is built through the real bridge — the same calls the automation
makes, including the queue it publishes. Each question is asked the way the
dashboard asks it: with the context of the previous reply, so "open it" and
"how long has it been waiting?" refer to what was just discussed.

What is checked, in short: answers come only from the run; the dashboard
actions ATLAS runs by itself are navigation; the one operation it can
attach (Open & Continue) appears only when the operator asked for it and
named — or left no doubt about — which shipment; and nothing about a
security verification is ever read, solved or typed.

    python test_atlas_operations.py
"""

import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dashboard.bridge import ControlTowerState                     # noqa: E402
from dashboard import assistant                                    # noqa: E402
import human_queue as HQ                                           # noqa: E402

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition or not detail else "  ({0})".format(detail)))


def rule(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


RUN = "20261004-101500-b52d0f"


def build(extra_waiting=False, finished=False):
    b = ControlTowerState()
    b.run_started(run_id=RUN, dry_run=False, target_status="Under Clearance")

    def started(ref, carrier, provider):
        b.shipment_started({"bol_awb": ref, "carrier": carrier, "provider": provider,
                            "current_eta": "", "table_page": 1})

    started("K179801", "DHL Express", "DHL")
    b.provider_result({"provider": "DHL", "tracking_status": "Delivered",
                       "eta": "05/10/2026", "ata": "03/10/2026"})
    b.view_updated("BU", "ATA", "03/10/2026", verified=True)
    b.shipment_finished("K179801", "SUCCESS", "", {"bu": "BU ATA updated with 03/10/2026 and saved"})
    for ref in ("057-05765454", "074-47798870"):
        started(ref, "Air France KLM Cargo", "AFKL")
        b.step("AFKL Cargo selected", system="AFKL")
        b.step("Tracking page navigation started", system="AFKL")
        b.step("Carrier connection accepted; shipment page never finished loading", system="AFKL")
        if ref == "074-47798870":
            b.recovery_plan("NAVIGATION_TIMEOUT", "shipment page did not load",
                            ["wait_for_page_ready", "reload_carrier_page"],
                            {"wait_for_page_ready": .8, "reload_carrier_page": .6}, False)
            b.recovery_attempt(1, 2, "wait_for_page_ready", .8, "FAILED", False)
            b.recovery_attempt(2, 2, "reload_carrier_page", .6, "FAILED", False)
            b.recovery_done(False, "no verified shipment page")
        b.shipment_finished(ref, "FAILED", "AFKL NAVIGATION ERROR: strategy timed out after 60.4 s",
                            outcome="AFKL NAVIGATION ERROR")
    started("S330211111", "Grimaldi", "GRIMALDI")
    b.recovery_plan("SELECTOR_MISSING", "result table not found", ["wait_for_page_ready"],
                    {"wait_for_page_ready": .9}, False)
    b.recovery_attempt(1, 1, "wait_for_page_ready", .9, "SUCCESS", True)
    b.recovery_done(True, "result table appeared", verified=True)
    b.provider_result({"provider": "GRIMALDI", "tracking_status": "Estimated arrival",
                       "eta": "12/10/2026", "ata": None})
    b.shipment_finished("S330211111", "SUCCESS", "", {"coe": "COE ETA updated with 12/10/2026 and saved"})

    q = HQ.HumanQueue(on_change=b.human_queue_changed)
    waiting_refs = [("S330348776", 134)] + ([("ANRB76464", 241)] if extra_waiting else [])
    for ref, age in waiting_refs:
        started(ref, "Grimaldi", "GRIMALDI")
        b.step("Filled {0} into Shipment #".format(ref), system="GRIMALDI")
        b.shipment_finished(ref, "HUMAN_QUEUED", "paused safely in the Human Action queue",
                            outcome="HUMAN ACTION QUEUED")
        q.clock = (lambda age=age: time.time() - age)
        q.create(RUN, ref, "Grimaldi Lines", provider="GRIMALDI",
                 step="security code before search",
                 shipment={"bol_awb": ref, "table_page": 1})
        q.clock = time.time
    started("MSCU7781234", "MSC", "MSC")
    b.step("Opening MSC tracking", system="MSC")
    b.counters(2, 2, 0, 0, needs_human=len(waiting_refs))
    if finished:
        b.run_finished()
    return b.snapshot(), q


STATE, QUEUE = build()
TASK = QUEUE.for_reference("S330348776", RUN)


def ask(q, context=None, state=None):
    return assistant.answer(q, state or STATE, context)


def follow(reply):
    """The context the dashboard sends with the next question."""
    return {"reference": reply.get("reference"), "action_id": reply.get("focus")}


def auto(reply):
    return reply.get("actions") or []


ALL = []


def say(q, context=None, state=None):
    r = ask(q, context, state)
    ALL.append((q, r))
    return r


rule("1. WHAT NEEDS ME? — THEN 'OPEN IT', 'HOW LONG HAS IT BEEN WAITING?'")
r = say("What needs me?")
check("One shipment, its carrier and reference, and how long it has waited",
      "One shipment needs human verification" in r["answer"]
      and "Grimaldi Lines — S330348776" in r["answer"]
      and "It's been waiting for 2 minutes" in r["answer"], r["answer"][:200])
check("...and the failures come after it", r["answer"].index("S330348776") <
      r["answer"].index("AFKL" if "AFKL" in r["answer"] else "Air France"))
check("The reply carries the task, for the follow-ups", r.get("focus") == TASK["action_id"])
check("...its suggestions are about the run, not trivia about one shipment",
      r["suggestions"][:2] == ["What needs me?", "Open pending human action"], str(r["suggestions"]))
check("...and its buttons are short, navigation only",
      all(len(b_["label"]) <= 40 and b_["action"]["type"] in assistant.UI_ACTIONS
          for b_ in r["buttons"]), str([b_["label"] for b_ in r["buttons"]]))
r2 = say("Open it.", follow(r))
check("'Open it' opens the pending human verification — navigation, run by the page",
      auto(r2) == [{"type": "page", "page": "human", "focus": TASK["action_id"]}]
      and r2["answer"].startswith("Opening the pending human verification"), str(r2))
check("...and is not an operation", "operation" not in r2)
r3 = say("How long has it been waiting?", follow(r2))
check("'How long has it been waiting?' is about the same shipment",
      "S330348776 has been waiting for 2 minutes" in r3["answer"], r3["answer"])
r4 = say("Anything I need to handle?")
check("'Anything I need to handle?' puts the human action first",
      r4["answer"].startswith("One shipment needs human verification"), r4["answer"][:80])

rule("2. RESUME — AN OPERATION, ONLY WHEN ASKED AND UNAMBIGUOUS")
r = say("Resume the waiting shipment.")
op = r.get("operation") or {}
check("'Resume the waiting shipment' prepares it: one Open & Continue for that task",
      op == {"type": "human_open", "op": "open", "run_id": RUN,
             "action_id": TASK["action_id"], "reference": "S330348776",
             "carrier": "Grimaldi Lines"}, str(op))
check("...and says what will happen, not that it has happened",
      r["answer"].startswith("I'll prepare the Grimaldi Lines shipment S330348776 for your "
                             "verification") and "You do only the verification" in r["answer"],
      r["answer"])
check("...the page runs navigation only by itself — never the operation",
      all(a.get("type") in assistant.UI_ACTIONS for a in auto(r)))
r = say("Resume Grimaldi")
check("'Resume Grimaldi' resolves the carrier to its one waiting shipment",
      (r.get("operation") or {}).get("action_id") == TASK["action_id"])
r = say("Resume DHL")
check("'Resume DHL' — nothing of DHL's waits, so nothing is done",
      "operation" not in r and "No DHL Express shipment is waiting" in r["answer"], r["answer"])
r = say("Resume Hapag")
check("A carrier this run never saw is never mistaken for the waiting one",
      "operation" not in r and "don't see" in r["answer"], r["answer"])
r = say("Resume the run")
check("'Resume the run' is not a human action: pointed at the run controls",
      "operation" not in r and "run controls" in r["answer"])
TWO, Q2 = build(extra_waiting=True)
r = say("Resume", state=TWO)
check("Two waiting and 'resume': ATLAS asks which, with one click-only button each",
      "operation" not in r and r["answer"].startswith("Which one? 2 are waiting")
      and len([b for b in r["buttons"] if b["action"]["type"] == "human_open"]) == 2,
      str(r["buttons"]))
r = say("What needs me?", state=TWO)
check("'You have 2 human actions waiting. The oldest has been waiting for 4 minutes.'",
      "You have 2 human actions waiting. The oldest has been waiting for 4 minutes." in r["answer"],
      r["answer"][:120])
DONE, _ = build(finished=True)
r = say("Resume Grimaldi", state=DONE)
check("With the run over, a resume is refused — its browser is gone",
      "operation" not in r and "not running" in r["answer"], r["answer"])

rule("3. FAILURES, CARRIERS, EXPLANATIONS")
r = say("Show failed shipments.")
check("'Show failed shipments' filters Live operations to them",
      auto(r) == [{"type": "filter", "label": "Failed", "state": "failed"}], str(auto(r)))
r = say("Why did AFKL fail?")
check("'Why did AFKL fail?' explains the latest AFKL failure",
      r.get("reference") == "074-47798870" and "2 Air France KLM Cargo shipments" in r["answer"],
      r["answer"][:160])
check("...from the recorded sequence, in order",
      r["answer"].index("AFKL Cargo selected") < r["answer"].index("Tracking page navigation started")
      < r["answer"].index("never finished loading"), r["answer"])
check("...ending with what the record shows was and wasn't done",
      "No shipment data was extracted." in r["answer"]
      and "Nothing was written to the Hub." in r["answer"])
check("...and offers the recovery attempts that exist",
      "recovery attempts" in r["answer"].lower(), r["answer"][-160:])
r2 = say("Open it", follow(r))
check("'Open it' then opens that shipment",
      auto(r2) == [{"type": "open", "reference": "074-47798870"}], str(auto(r2)))
r = say("Which carrier has the most failures?")
check("'Which carrier has the most failures?' — from the run",
      "Air France KLM Cargo" in r["answer"], r["answer"][:120])
r = say("What was the latest error?")
check("'What was the latest error?' — the latest failure",
      "074-47798870" in r["answer"], r["answer"][:120])
r = say("Open the latest failed shipment.")
check("'Open the latest failed shipment' opens the real latest one",
      auto(r) == [{"type": "open", "reference": "074-47798870"}], str(auto(r)))

rule("4. RECOVERY — ONLY WHAT HAPPENED")
r = say("Show recovery attempts")
check("A recovery that worked is told with the strategy that worked",
      "Strategy 1 (wait for page ready) worked and was verified, so processing continued."
      in r["answer"], r["answer"])
check("One that did not: every strategy tried, stopped safely, nothing written",
      "I tried 2 recovery strategies. None produced a verified result, so I stopped safely."
      in r["answer"] and "Nothing was written to the Hub." in r["answer"], r["answer"])
r = say("Did ATLAS recover K179801?")
check("A shipment with no recovery: says none was attempted",
      "No recovery was attempted for K179801." in r["answer"], r["answer"])

rule("5. OPEN, SHOW, STUCK, NEXT, CHANGED, WHAT HAPPENED")
r = say("Open Grimaldi.")
check("'Open Grimaldi' — the Grimaldi shipment waiting for a person",
      auto(r) == [{"type": "open", "reference": "S330348776"}], str(auto(r)))
r = say("Show me all Grimaldi shipments.")
check("'Show me all Grimaldi shipments' filters by carrier",
      any(a.get("type") == "filter" and a.get("carrier") for a in auto(r)), str(auto(r)))
r = say("Show me human actions.")
check("'Show me human actions' — the queue, and the Human Action page",
      r["answer"].startswith("HUMAN ACTIONS · 1")
      and auto(r) == [{"type": "page", "page": "human"}], str(auto(r)))
r = say("Show me what's waiting")
check("'Show me what's waiting' — the same queue", r["answer"].startswith("HUMAN ACTIONS · 1"))
r = say("Open the shipment waiting for human action.")
check("'Open the shipment waiting for human action' opens that shipment",
      auto(r) == [{"type": "open", "reference": "S330348776"}], str(auto(r)))
r = say("Why is Grimaldi stuck?")
check("'Why is Grimaldi stuck?' — the shipment, its state and the reason",
      "S330348776" in r["answer"] and "needs a person" in r["answer"]
      and "security code before search" in r["answer"], r["answer"])
r = say("What should I do next?")
check("'What should I do next?' — the human action first, then the latest failure",
      r["answer"].startswith("1. Handle the human verification for **Grimaldi Lines — S330348776**")
      and "2. Review the latest failure: 074-47798870" in r["answer"], r["answer"])
check("...with an Open & Continue that runs only on a click",
      any(b["action"]["type"] == "human_open" for b in r["buttons"])
      and "operation" not in r and not auto(r))
r = say("What changed in the last few minutes?")
check("'What changed in the last few minutes?' — the recorded events",
      r["answer"].startswith("Most recent changes"), r["answer"][:60])
r = say("What happened to shipment S330348776?")
check("'What happened to shipment S330348776?' — its trail, human action included",
      "Human action:" in r["answer"] and "WAITING_FOR_HUMAN" in r["answer"]
      and "Filled S330348776 into Shipment #" in r["answer"], r["answer"])
r2 = say("What happened to this shipment?", follow(r))
check("'What happened to this shipment?' — the same one", r2.get("reference") == "S330348776")
r = say("Open the latest run.")
check("'Open the latest run' goes to Live runs",
      auto(r) == [{"type": "page", "page": "live"}], str(auto(r)))
r = say("What's still processing?")
check("'What's still processing?' — the one in flight", "MSCU7781234" in r["answer"], r["answer"][:120])
r = say("How is the run going?")
check("'How is the run going?' answers from the run's own counts",
      bool(re.search(r"\b2\b", r["answer"])) and "weather" not in r["answer"], r["answer"][:160])

rule("6. PROACTIVE AND SUGGESTED — FROM STATE")
brief = assistant.atlas_brief(STATE)
texts = [n["text"] for n in brief["notices"]]
check("ATLAS noticed the waiting shipment first",
      texts and "S330348776 needs human action on Grimaldi Lines" in texts[0], str(texts))
check("...and AFKL's repeated failure, named by its kind",
      "Air France KLM Cargo has produced 2 navigation failures in this run." in texts, str(texts))
check("...and the recovery that worked",
      any("recovered after 1 recovery attempt" in t for t in texts), str(texts))
check("The queue line says how many wait", brief["queue"] == {
    "open": 1, "line": "You have 1 human action waiting."}, str(brief["queue"]))
check("Suggestions: what needs me, the pending action, failures, recovery",
      brief["suggestions"][:5] == ["What needs me?", "Open pending human action",
                                   "Show failed shipments", "Why did the latest shipment fail?",
                                   "Show recovery attempts"], str(brief["suggestions"]))
calm = ControlTowerState()
calm.run_started(run_id="calm")
calm.shipment_started({"bol_awb": "K1", "carrier": "DHL Express", "provider": "DHL"})
calm.shipment_finished("K1", "SUCCESS", "", {})
cb = assistant.atlas_brief(calm.snapshot())
check("A calm run: 'Nothing currently requires operator attention.'",
      cb["notices"][0]["text"] == "Nothing currently requires operator attention.", str(cb["notices"]))
check("...and calm suggestions", "How is the run going?" in cb["suggestions"]
      and "Compare the carriers" in cb["suggestions"], str(cb["suggestions"]))
r = assistant.answer("What needs me?", calm.snapshot())
check("'What needs me?' on a calm run: nothing currently needs your attention",
      r["answer"].startswith("Nothing currently needs your attention."), r["answer"])

rule("7. THE SECURITY BOUNDARY")
for q in ("What's the code?", "Read the captcha for me", "Type the security code for me",
          "Solve the verification for S330348776", "OCR the security image",
          "Can you bypass the anti-bot check?"):
    r = say(q)
    check("Refused: {0}".format(q), r.get("intent") == "code_request" and "operation" not in r
          and "won't" in r["answer"], r["answer"][:80])
src = (HERE / "dashboard" / "assistant.py").read_text(encoding="utf-8")
check("The assistant sends nothing itself: no request to /api/human or /api/control",
      "/api/human" not in src and "/api/control" not in src and "urlopen" not in src)
check("The only operation it can name is Open & Continue",
      assistant.OPERATIONS == ("human_open",))

rule("8. NO HALLUCINATION, ONE VOICE")
refs_in_run = {r_["reference"] for r_ in STATE["shipments"]}
mentioned = set()
for q, r in ALL:
    mentioned |= set(re.findall(r"\b(?:S\d{9}|\d{3}-\d{8}|K\d{6}|ANRB\d+|MSCU\d+)\b",
                                r.get("answer") or ""))
check("Every shipment ATLAS named exists in the run",
      mentioned <= refs_in_run | {"ANRB76464"}, str(mentioned - refs_in_run))
check("No reply says 'As an AI'", not any("as an ai" in (r.get("answer") or "").casefold()
                                          for _, r in ALL))
check("Every auto-run action is navigation",
      all(a.get("type") in assistant.UI_ACTIONS for _, r in ALL for a in auto(r)))
check("Every operation is the queue's Open & Continue, for a task in the run",
      all((r.get("operation") or {}).get("action_id") in
          {t["action_id"] for t in QUEUE.snapshot()} for _, r in ALL if r.get("operation")))
r = say("What is the weather in Cairo?")
check("Out of scope: 'I don't have that information in this run'",
      r["answer"].startswith("I don't have that information in this run"))

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
