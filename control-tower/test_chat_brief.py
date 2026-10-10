"""
ATLAS chat: a normal answer to a normal question (dashboard/brief.py).

    python test_chat_brief.py

Everyday questions get a sentence or two from the run's records — no
headings, lists, cards or reports — and the full answer is still there when
asked for. Nothing is invented, missing data is said to be missing, and the
change is presentation only: asking never changes the run. TEST DATA only.
"""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="ct_brief_intel_")
os.environ.pop("ATLAS_LLM_PROVIDER", None)
os.environ.pop("ATLAS_DEMO_TEST_DATA", None)

import atlas_demo                                     # noqa: E402
from dashboard import assistant                       # noqa: E402
from dashboard.bridge import ControlTowerState        # noqa: E402

PASS, FAIL = [], []
DATE = re.compile(r"\b\d{2}/\d{2}/\d{4}\b")


def check(name, condition, detail=""):
    (PASS if condition else FAIL).append(name)
    print("  {0}  {1}{2}".format("PASS" if condition else "FAIL", name,
                                 "" if condition else "  ({0})".format(detail)))


def rule(title):
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)


bridge = ControlTowerState()
atlas_demo.seed_test_run(bridge)
STATE = bridge.snapshot()
RECORD_DATES = set(DATE.findall(json.dumps(STATE)))


def ask(question, state=STATE, context=None):
    return assistant.answer(question, state, dict(context or {}))


def plain(reply):
    """A normal answer: no heading, bold label, bullet, list or second paragraph, no card."""
    a = reply["answer"]
    return ("\n" not in a.strip() and "**" not in a and "•" not in a and
            not re.search(r"^\s*[-*]\s", a, re.M) and reply.get("card") is None and
            not reply.get("investigation") and len(a) < 360)


EXPECT = [
    ("Where is shipment 1570046231?",
     "1570046231 (QATAR AIRWAYS) arrived on 20/08/2026, carrier ETA 24/08/2026. I wrote it to the Hub."),
    ("What is the ETA for 5271993480?", "5271993480's carrier ETA is 26/08/2026."),
    ("What's the ATA of 1570046231?", "1570046231's carrier ATA is 20/08/2026."),
    ("Why did 8842001173 fail?",
     "8842001173 (DHL GLOBAL FORWARDING) failed (UNEXPECTED PAGE STATE): Save/Update button was "
     "not found on the Manage page. Nothing was written to the Hub. It's queued for one retry "
     "later in this run."),
    ("How many shipments failed?", "1 shipment failed: 8842001173."),
    ("How many shipments were skipped?", "1 shipment was skipped: 1570049117."),
    ("How many shipments were written to the Hub?", "2 shipments were written to the Hub."),
    ("How many shipments?", "4 shipments processed: 2 written to the Hub, 1 skipped, 1 failed."),
    ("Give me a summary of the last run",
     "The last run (TEST-DATA-DEMO) finished: 4 processed, 2 written to the Hub, 1 skipped, "
     "1 failed. The failure was 8842001173, queued for a retry."),
    ("What needs my attention?",
     "8842001173 (DHL GLOBAL FORWARDING) failed, and it's queued for a retry."),
]

# ═════════════════════════════════════════════════════════════════════════
rule("1. A NORMAL QUESTION GETS A NORMAL ANSWER — exact, from the records")
for question, want in EXPECT:
    r = ask(question)
    check("{0!r} → one plain answer".format(question), r["answer"] == want and plain(r),
          r["answer"])
r = ask("Where is shipment 1570046231?")
check("The answer to a shipment question comes first: the reference opens it",
      r["answer"].startswith("1570046231"))
check("...and the full details are one tap away", r["suggestions"][0] ==
      "Tell me everything about 1570046231", str(r["suggestions"]))
r = ask("Why did 8842001173 fail?")
check("A failure answer offers the explanation, not a report",
      r["suggestions"][0] == "Explain why 8842001173 failed", str(r["suggestions"]))
r = ask("How many shipments failed?")
check("A count question carries no download buttons", r.get("downloads") == [])

# ═════════════════════════════════════════════════════════════════════════
rule("2. THE FULL ANSWER — only when asked for")
r = ask("Tell me everything about 1570046231")
check("'Tell me everything about …' brings the full answer and its card",
      r.get("card") is not None and "\n" in r["answer"], r["answer"][:120])
r = ask("Explain why 8842001173 failed")
check("'Explain why … failed' brings the diagnosis (Fact / Not established)",
      "**Fact**" in r["answer"] and "Not established" in r["answer"])
r = ask("How many shipments failed, in detail?")
check("'… in detail' brings every counter", "Failed: 1" in r["answer"] and
      "Skipped: 1" in r["answer"])
r = ask("Give me the full report")
check("'Full report' is still the report, with its charts", r.get("intent") == "report"
      and len(r.get("charts") or []) >= 1)

# ═════════════════════════════════════════════════════════════════════════
rule("3. FACTUAL — nothing invented, missing data said to be missing")
dates = set()
for question, _ in EXPECT:
    dates |= set(DATE.findall(ask(question)["answer"]))
check("Every date in the short answers is in the run's records", dates <= RECORD_DATES,
      str(dates - RECORD_DATES))
r = ask("What is the ETA for 1570049117?")
check("A missing ETA is said to be missing, with the recorded reason, and no date",
      r["answer"] == "The carrier didn't give an ETA for 1570049117: The carrier did not provide "
                     "ETA or ATA." and not DATE.search(r["answer"]), r["answer"])
r = ask("Where is shipment 9999999999?")
check("An unknown reference: no record, no guess", "no record of 9999999999" in r["answer"]
      and not DATE.search(r["answer"]), r["answer"][:100])
r = ask("Where is 1570046231 coming from?")
check("A question the short answer can't cover keeps the full one (origin not recorded)",
      "never reads origin" in r["answer"], r["answer"][-160:])
empty = ControlTowerState().snapshot()
check("Nothing processed: said so, no zeros dressed up as results",
      ask("How many shipments failed?", empty)["answer"] ==
      "No shipments have been processed yet.")
r = ask("How many shipments are there in total?")
check("'In total' keeps the full answer, which says whether the total is known",
      not r.get("brief") and "Processed: 4" in r["answer"], r["answer"][-100:])

# ═════════════════════════════════════════════════════════════════════════
rule("4. POTATO MODE — a sentence, not a report")
pb = ControlTowerState()
atlas_demo.seed_test_run(pb)
r = ask("How many potatoes do you have?", pb.snapshot())
check("'How many potatoes?' with none harvested yet",
      r["answer"] == "No potatoes harvested yet 🥔 One is growing for 8842001173." and plain(r),
      r["answer"])
r = ask("Why did potato mode activate?", pb.snapshot())
check("'Why did Potato Mode activate?' in one sentence, no joke or garden report appended",
      r["answer"] == "Shipment 8842001173 failed 3 times in a row (UNEXPECTED PAGE STATE), so I "
                     "stopped repeating the same attempts and moved on 🥔" and plain(r),
      r["answer"])
pb.deferred_retry_started("8842001173")
pb.shipment_started({"bol_awb": "8842001173", "carrier": "DHL GLOBAL FORWARDING", "provider": "DHL"})
pb.view_updated("COE", "ETA", "12/09/2026", verified=True)
pb.shipment_finished("8842001173", "SUCCESS", "")
pb.potato.harvests += 3                    # as if three earlier incidents had been harvested
r = ask("How many potatoes do you have?", pb.snapshot())
check("With harvests: 'I've harvested 4 potatoes so far 🥔'",
      r["answer"] == "I've harvested 4 potatoes so far 🥔", r["answer"])
pb.potato.harvests = 1
check("...and the singular reads right", ask("How many potatoes?", pb.snapshot())["answer"]
      == "I've harvested 1 potato so far 🥔")

# ═════════════════════════════════════════════════════════════════════════
rule("5. PRESENTATION ONLY — the run, Human Action and safety untouched")
before = json.dumps(STATE, sort_keys=True, default=str)
for question, _ in EXPECT:
    ask(question)
check("Asking every question changes nothing in the run state",
      json.dumps(STATE, sort_keys=True, default=str) == before)
h = ControlTowerState()
atlas_demo.seed_test_run(h)
h.human_action_opened({"action_id": "act1", "reference": "HX1", "carrier": "AFKL",
                       "step": "verify", "reason": "captcha"})
r = ask("What needs my attention?", h.snapshot())
check("A Human Action waiting: the full human-first answer is kept, not shortened",
      "HX1" in r["answer"] and not r.get("brief"), r["answer"][:120])
r = ask("Can you type the captcha code for me?")
check("A request to solve a verification is still refused, word for word",
      r["answer"].startswith("I won't do that. Security verification is completed by a person"),
      r["answer"][:80])
os.environ["ATLAS_DEMO_TEST_DATA"] = "1"
r = ask("Where is shipment 1570046231?")
check("TEST DATA is still labelled, in front of the answer, without a paragraph of its own",
      r["answer"].startswith("**TEST DATA** · 1570046231"), r["answer"][:60])
os.environ.pop("ATLAS_DEMO_TEST_DATA", None)

print()
print("=" * 74)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 74)
sys.exit(1 if FAIL else 0)
