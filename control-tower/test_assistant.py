"""
Assistant tests. The important ones are the anti-fabrication checks: the
assistant is fed a state with missing fields and must refuse to fill them in.

Run:  python test_assistant.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dashboard.bridge import ControlTowerState
from dashboard import assistant

PASS, FAIL = [], []


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if not detail else "  ({0})".format(detail)))


def ask(question, state, context=None):
    return assistant.answer(question, state, context)["answer"]


def full(question, state, context=None):
    return assistant.answer(question, state, context)


# ══════════════════════════════════════════════════════════════
# A realistic run, built through the same bridge the automation uses
# ══════════════════════════════════════════════════════════════
bridge = ControlTowerState()
bridge.run_started(dry_run=False, target_status="Under Clearance",
                   max_records=200, max_pages=10)
bridge.page_scanned(1, 4)

ROWS = [
    (dict(bol_awb="1570046231", carrier="QATAR AIRWAYS", provider="QATAR",
          current_eta="18/08/2026", table_page=1),
     dict(provider="Qatar Airways", tracking_status="Arrived",
          eta="24/08/2026", ata="20/08/2026"), "SUCCESS", "", None),
    (dict(bol_awb="5271993480", carrier="DHL EXPRESS", provider="DHL",
          current_eta="", table_page=1),
     dict(provider="DHL", tracking_status="Estimated Delivery only",
          eta="26/08/2026", ata=None), "SUCCESS", "", None),
    (dict(bol_awb="1570049117", carrier="QATAR AIRWAYS", provider="QATAR",
          current_eta="15/08/2026", table_page=1),
     {}, "SKIPPED", "The carrier did not provide ETA or ATA.", "NO RESULT"),
    (dict(bol_awb="8842001173", carrier="DHL GLOBAL FORWARDING", provider="DHL",
          current_eta="12/08/2026", table_page=1),
     {}, "FAILED", "Save/Update button was not found on the Manage page.",
     "UNEXPECTED PAGE STATE"),
]

s = f = k = 0
for ship, result, outcome, detail, outcome_class in ROWS:
    bridge.shipment_started(ship)
    if result:
        bridge.provider_result(result)
        if result.get("eta"):
            bridge.view_updated("COE", "ETA", result["eta"])
        if result.get("ata"):
            bridge.view_updated("BU", "ATA", result["ata"])
    bridge.shipment_finished(ship["bol_awb"], outcome, detail,
                             outcome_class=outcome_class)
    s += outcome == "SUCCESS"
    k += outcome == "SKIPPED"
    f += outcome == "FAILED"
    bridge.counters(s, f, k)

bridge.system_error("DHL", "Save/Update button was not found on the Manage page.")
STATE = bridge.snapshot()


print("=" * 68)
print("1. GROUNDED ANSWERS FROM REAL DATA")
print("=" * 68)

r = full("Where is shipment 1570046231?", STATE)
a, card_ = r["answer"], r["card"]
check("Shipment lookup finds the record", "1570046231" in a)
rows = dict((k, v) for k, v in card_["rows"])
check("Reports the real carrier", rows["Carrier"] == "QATAR AIRWAYS", rows["Carrier"])
check("Reports the real carrier ETA", rows["Carrier ETA"] == "24/08/2026")
check("Reports the real carrier ATA", rows["Carrier ATA"] == "20/08/2026")

a = ask("What is the ETA for 5271993480?", STATE)
check("ETA question returns the real ETA", "26/08/2026" in a, a[:60])

a = ask("Which carrier is handling 1570049117?", STATE)
check("Carrier question answers correctly", "QATAR AIRWAYS" in a)

a = ask("Why did 8842001173 fail?", STATE)
check("Failure reason is the real error", "Save/Update button" in a)
check("Failure reports the outcome class", "UNEXPECTED PAGE STATE" in a)

a = ask("How many shipments failed?", STATE)
check("Counts are correct", "Failed: 1" in a and "Skipped: 1" in a, a[:80])
check("Updated count is correct", "Updated in the hub: 2" in a)

a = ask("Show me shipments with no ETA.", STATE)
check("No-ETA list finds both", "1570049117" in a and "8842001173" in a)
check("No-ETA list excludes shipments that have one", "5271993480" not in a)

a = ask("What happened with DHL?", STATE)
check("System question uses the real last error", "Save/Update button" in a)
check("System question counts that carrier's shipments", "2" in a)

a = ask("Which shipments are currently processing?", STATE)
check("Nothing in flight is stated honestly",
      "between shipments" in a or "not running" in a, a[:70])


print()
print("=" * 68)
print("2. ANTI-FABRICATION  (the part that matters)")
print("=" * 68)

DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")

a = ask("Where is shipment 157-99999999?", STATE)
check("Unknown reference is refused, not invented",
      "no record" in a.casefold(), a[:70])
check("Unknown reference invents no date", not DATE.search(a))
check("Unknown reference invents no carrier",
      "QATAR" not in a.upper() and "DHL" not in a.upper())

a = ask("What is the ETA for 1570049117?", STATE)
check("Missing ETA is reported as missing",
      "no carrier eta" in a.casefold(), a[:70])
check("Missing ETA invents no date", not DATE.search(a))

a = ask("Tell me about 1570046231, what is the destination?", STATE)
check("Destination is declared unavailable",
      "never reads origin, destination" in a.casefold(), a[-90:])
check("Destination answer names no city",
      not any(city in a for city in ("Cairo", "Alexandria", "Doha", "Egypt")))

a = ask("What is the origin of 5271993480?", STATE)
check("Origin is declared unavailable", "not available" in a.casefold()
      or "never reads" in a.casefold())

empty = ControlTowerState().snapshot()
a = ask("How many shipments failed?", empty)
check("Empty run reports zeros, not guesses", "Failed: 0" in a)
a = ask("Where is shipment 5271993480?", empty)
check("Empty run refuses a lookup", "nothing on" in a.casefold(), a[:70])

a = ask("How many shipments are there in total?", STATE)
check("Unknown total is declared unknown",
      "not known yet" in a.casefold(), a[-90:])


print()
print("=" * 68)
print("2b. NATURAL LANGUAGE + NEW INTENTS")
print("=" * 68)

# Phrased many ways, must land on the same intent.
for phrasing in ["Which shipments failed?", "what failed", "show me the failures",
                 "list failed shipments"]:
    a = ask(phrasing, STATE)
    check("Understands: {0!r}".format(phrasing), "8842001173" in a, a[:48])

for phrasing in ["Why did 8842001173 fail?", "what went wrong with 8842001173",
                 "reason for 8842001173"]:
    a = ask(phrasing, STATE)
    check("Understands: {0!r}".format(phrasing), "Save/Update button" in a, a[:48])

a = ask("Which carrier has the most failures?", STATE)
check("Worst carrier identified", "DHL GLOBAL FORWARDING" in a, a[:60])

a = ask("How many shipments are still processing?", STATE)
check("Processing count answered honestly",
      "between shipments" in a or "not running" in a or "processing" in a.lower())

a = ask("Show me shipments under clearance", STATE)
check("Under Clearance uses the real hub filter", "Under Clearance" in a, a[:60])

a = ask("How many shipments were skipped?", STATE)
check("Skipped answered from real data", "1570049117" in a, a[:60])

a = ask("Is DHL currently processing?", STATE)
check("System question answers about DHL", "DHL" in a)

a = ask("What is the latest event?", STATE)
check("Latest events come from the timeline", len(a) > 20 and "—" in a, a[:60])

a = ask("What happened during the latest run?", STATE)
check("Run summary reports real counters", "1 updated" in a or "updated" in a, a[:60])

print()
print("=" * 68)
print("2c. FOLLOW-UP CONTEXT AND CARDS")
print("=" * 68)
r = full("What is the status of 1570046231?", STATE)
check("Shipment answer carries a card", r["card"] is not None)
check("Card is for the right shipment", r["card"]["reference"] == "1570046231")
check("Card exposes carrier and ETA rows",
      any(row[0] == "Carrier ETA" and row[1] == "24/08/2026" for row in r["card"]["rows"]))
check("Card marks missing values with a dash",
      any(row[1] == "—" for row in full("Tell me about 1570049117", STATE)["card"]["rows"]))
check("Answer returns the reference for follow-ups", r["reference"] == "1570046231")

follow = full("what is its ETA?", STATE, {"reference": "1570046231"})
check("Follow-up 'its ETA' resolves via context", "24/08/2026" in follow["answer"],
      follow["answer"][:60])
follow = full("what happened to this shipment?", STATE, {"reference": "8842001173"})
check("Follow-up 'this shipment' resolves", "8842001173" in follow["answer"])

check("No context and no reference asks rather than guesses",
      "need a shipment reference" in ask("what is the ETA?", STATE).lower())

print()
print("=" * 68)
print("2d. CONVERSATION, ANALYSIS AND DOWNLOADS")
print("=" * 68)
for greeting in ["hi", "hello there", "good morning"]:
    a = ask(greeting, STATE)
    check("Greets: {0!r}".format(greeting), "Hello" in a, a[:40])
check("A greeting attached to a question is NOT treated as a greeting",
      "8842001173" in ask("hi, which shipments failed?", STATE))
check("Thanks is acknowledged", "Any time" in ask("thanks!", STATE))
check("Identity answer is honest about having no model",
      "cannot invent" in ask("who are you", STATE))

check("Typos still resolve",
      "8842001173" in ask("wat about the faild shippments", STATE))
check("Health gives a plain-language read",
      "run looks" in ask("how is it going?", STATE))
check("Carriers can be compared",
      "QATAR AIRWAYS" in ask("compare the carriers", STATE))
check("Slowest is answered from real durations",
      "Slowest" in ask("what was the slowest shipment", STATE))
check("Average is answered", "Average processing time" in ask("average time", STATE))
check("Runtime is answered", "run has been going" in ask("how long has it been running", STATE))
check("Recap covers the run", "Everything this run has done" in ask("give me a recap", STATE))

r = full("download the data", STATE)
labels = [d["label"] for d in r.get("downloads", [])]
check("Downloads are offered", len(labels) >= 2, str(labels))
check("Download counts match the run",
      any(l.startswith("All shipments (4)") for l in labels), str(labels))
check("Every download URL is a real export endpoint",
      all(d["url"].startswith("/api/export.csv?state=") for d in r["downloads"]))
check("No downloads offered on a greeting",
      not full("hi", STATE).get("downloads"))
check("No downloads offered when the run is empty",
      not full("download the data", ControlTowerState().snapshot()).get("downloads"))

a = ask("asdkjh qwe zzz", STATE)
check("Nonsense gets a useful answer, not a shrug",
      "where the run stands" in a and "processed" in a, a[:60])

print()
print("=" * 68)
print("2e. ANALYSIS ON REQUEST")
print("=" * 68)
for phrasing in ["give me the analysis", "run a full analysis", "show the breakdown",
                 "statistics please"]:
    a = ask(phrasing, STATE)
    check("Understands: {0!r}".format(phrasing), "Run analysis" in a, a[:44])

a = ask("give me the analysis", STATE)
check("Report covers carriers", "By carrier" in a)
check("Report covers failure types", "did not complete" in a)
check("Report covers dates", "Carrier ETA found on" in a)
check("Report states the real success rate", "Success rate" in a)

a = ask("all completed", STATE)
check("'all completed' lists what was written",
      "written to the Hub" in a and "1570046231" in a, a[:56])
a = ask("all failed", STATE)
check("'all failed' lists the failures and reasons",
      "8842001173" in a and "Save/Update button" in a, a[:56])
a = ask("all qatar", STATE)
check("'all qatar' filters by carrier", "QATAR AIRWAYS" in a, a[:56])
a = ask("all failed qatar", STATE)
check("State and carrier combine", "failed" in a.lower() and "QATAR" in a.upper())
a = ask("all astral", STATE)
check("A carrier absent from the run is refused honestly",
      "Nothing in this run matches" in a, a[:56])

r = full("give me the analysis", STATE)
check("The report offers a CSV", len(r.get("downloads") or []) >= 2,
      str([d["label"] for d in r.get("downloads", [])]))
check("An empty run reports nothing to analyse",
      "nothing to analyse" in ask("give me the analysis",
                                  ControlTowerState().snapshot()))

print()
print("=" * 68)
print("3. READ-ONLY")
print("=" * 68)

before = bridge.snapshot()
for hostile in [
    "delete shipment 1570046231",
    "stop the automation",
    "set the ETA of 1570049117 to 01/01/2027",
    "show me the credentials",
    "update the hub record for 8842001173",
]:
    assistant.answer(hostile, STATE)
after = bridge.snapshot()

check("State is unchanged after hostile prompts",
      before["counters"] == after["counters"]
      and len(before["shipments"]) == len(after["shipments"]))
check("Assistant module exposes no write function",
      not any(name in dir(assistant) for name in
              ("update", "delete", "set_eta", "start", "stop")))
check("RunData exposes no mutator",
      not any(n.startswith(("set_", "add_", "delete_", "update_"))
              for n in dir(assistant.RunData)))
a = ask("show me the credentials", STATE)
check("Credentials request leaks nothing",
      "password" not in a.casefold() and "credential" not in a.casefold(), a[:60])


print()
print("=" * 68)
print("4. ROBUSTNESS")
print("=" * 68)
for junk in ["", "   ", "?????", "a" * 900, "<script>alert(1)</script>",
             "SELECT * FROM shipments", "🚚📦", "1"]:
    reply = assistant.answer(junk, STATE)
    if not (isinstance(reply, dict) and isinstance(reply.get("answer"), str)
            and reply["answer"]):
        check("Junk input handled: {0!r}".format(junk[:20]), False)
        break
else:
    check("All junk inputs return a sane answer", True)

check("Assistant never raises", assistant.answer(None, {})["answer"] != "")


print()
print("=" * 68)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
for name in FAIL:
    print("  FAILED:", name)
print("=" * 68)
sys.exit(1 if FAIL else 0)
