"""
ATLAS as the Control Tower's operational copilot.

The run below is built through the real bridge, the same calls the automation
makes. Every check is about one of two things: ATLAS answers from that run
and nothing else, and the only things it can DO are dashboard navigation —
filter, open, go to a page. It cannot touch a shipment, the run, or a human
verification, because there is no action of that kind for it to take.

    python test_atlas_copilot.py
"""

import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from dashboard.bridge import ControlTowerState, transport_mode    # noqa: E402
from dashboard import assistant                                    # noqa: E402

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


RUN = "20261004-091512-a41c9e"
b = ControlTowerState()
b.run_started(run_id=RUN, dry_run=False, target_status="Under Clearance")
b.page_scanned(1, 6)


def started(ref, carrier, provider):
    b.shipment_started({"bol_awb": ref, "carrier": carrier, "provider": provider,
                        "current_eta": "", "table_page": 1})


# DHL — road, written and read back
started("K179801", "DHL Express", "DHL")
b.step("Tracking K179801 on the carrier site", system="DHL")
b.provider_result({"provider": "DHL", "tracking_status": "Delivered", "eta": "05/10/2026",
                   "ata": "03/10/2026", "ata_source": "Delivered"})
b.view_updated("COE", "ETA", "05/10/2026", verified=True)
b.view_updated("BU", "ATA", "03/10/2026", verified=True)
b.shipment_finished("K179801", "SUCCESS", "", {"bu": "BU ATA updated with 03/10/2026 and saved"})
# AFKL — air, failed, with its real steps
started("057-05765454", "Air France Cargo", "AFKL")
b.step("Opening AFKL tracking", system="AFKL")
b.step("Searching 057-05765454 in the header search", system="AFKL")
b.shipment_finished("057-05765454", "FAILED", "AFKL NAVIGATION ERROR: the shipment page did not open",
                    outcome="AFKL NAVIGATION ERROR")
# Maersk — ocean, read only (skipped) with an ETA
started("274599284", "Maersk", "MAERSK")
b.provider_result({"provider": "MAERSK", "tracking_status": "Estimated arrival",
                   "eta": "14/10/2026", "ata": None, "eta_source": "ETA"})
b.shipment_finished("274599284", "SKIPPED", "Maersk read eta 14/10/2026. Not written: read-only.",
                    outcome="NO RESULT")
# an unknown provider — no mode is invented for it
started("XZ-55120", "Unlisted Forwarder", "XYZ")
b.shipment_finished("XZ-55120", "SKIPPED", "No tracking address", outcome="NO RESULT")
# Grimaldi — ocean, waiting for a person
started("S330348776", "Grimaldi", "GRIMALDI")
b.step("Filled S330348776 into Shipment #", system="GRIMALDI")
b.human_action_opened({"run_id": RUN, "action_id": "9f2c11ab33d0", "reference": "S330348776",
                       "carrier": "Grimaldi Lines", "reason": "human_verification_required",
                       "opened_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 134)),
                       "deadline": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 46)),
                       "timeout_s": 180,
                       "instructions": "Type the security code shown on the Grimaldi Lines page and press Search."})
b.counters(1, 1, 2, 0)
STATE = b.snapshot()
STATE_TEXT = repr(STATE)


def ask(q, context=None, state=None):
    return assistant.answer(q, state or STATE, context)


def dates(text):
    return set(re.findall(r"\b\d{2}/\d{2}/\d{4}\b", text or ""))


rule("TRANSPORT MODE COMES FROM THE PROVIDER, NEVER A GUESS")
check("KLM / Air France → air", transport_mode("AFKL") == "air")
check("Grimaldi and MSC → ocean", transport_mode("GRIMALDI") == "ocean" and transport_mode("MSC") == "ocean")
check("DHL → road", transport_mode("DHL") == "road")
check("A rail carrier name → rail", transport_mode("XYZ", "Egyptian National Railways") == "rail")
check("Anything else → unknown, never a vehicle", transport_mode("XYZ", "Unlisted Forwarder") == "unknown")
rows = {r["reference"]: r for r in STATE["shipments"]}
check("The record carries the mode for the dashboard and ATLAS alike",
      rows["S330348776"]["transport_mode"] == "ocean" and rows["XZ-55120"]["transport_mode"] == "unknown")

rule("1. HOW IS THE RUN GOING?")
r = ask("How is the run going?")
check("Answered from the run's counters", "1" in r["answer"] and r["grounded"], r["answer"][:80])
check("...inventing no date", not (dates(r["answer"]) - dates(STATE_TEXT)))

rule("2. WHICH SHIPMENTS FAILED?")
r = ask("Which shipments failed?")
check("Names the failed shipment", "057-05765454" in r["answer"], r["answer"][:80])
check("...and only it", "K179801" not in r["answer"] and "274599284" not in r["answer"])
check("Offers to show them in Live operations",
      any(b_["action"] == {"type": "filter", "state": "failed", "label": "Failed"} or
          (b_["action"].get("type") == "filter" and b_["action"].get("state") == "failed")
          for b_ in r["buttons"]), str(r["buttons"]))

rule("3. WHAT NEEDS MY ATTENTION?")
r = ask("What needs my attention?")
check("The waiting human action comes first", r["answer"].index("S330348776") < r["answer"].index("failed"),
      r["answer"][:120])
check("...with how long it has waited", re.search(r"waiting \d+m \d{2}s", r["answer"]) is not None)
check("...and a way to it", any(x["action"] == {"type": "page", "page": "human"} for x in r["buttons"]))
check("The failure is in the list", "Air France Cargo" in r["answer"])

rule("4. COMPARE THE CARRIERS")
r = ask("Compare the carriers.")
check("Every carrier in the run is compared",
      all(c in r["answer"] for c in ("DHL Express", "Air France Cargo", "Maersk", "Grimaldi")), r["answer"][:120])

rule("5. A SHIPMENT IN THE RUN")
r = ask("What happened to 274599284?")
card = dict(r["card"]["rows"])
check("The card is for that shipment", r["card"]["reference"] == "274599284")
check("Its mode is the real one", card["Mode"] == "Ocean")
check("Its ETA is the carrier's", card["Carrier ETA"] == "14/10/2026")
check("It can be opened from the answer", any(x["action"] == {"type": "open", "reference": "274599284"}
                                                for x in r["buttons"]))
r = ask("Why is S330348776 waiting?")
check("A waiting shipment explains its wait from the human action",
      "waiting for a person" in r["answer"] and "Grimaldi Lines" in r["answer"], r["answer"][:100])

rule("6. A SHIPMENT NOT IN THE RUN")
r = ask("What is the status of 9988776655?")
check("Refused, not invented", "no record" in r["answer"].lower(), r["answer"][:80])
check("...with no card and no action", r["card"] is None and not r["actions"])

rule("7–8. SOMETHING THE RUN DOES NOT CONTAIN")
for q in ("What is the weather in Cairo?", "Who won the match last night?"):
    r = ask(q)
    check("Says it has no such information: {0!r}".format(q),
          "don't have that information in this run" in r["answer"], r["answer"][:80])
r = ask("What is the origin of 274599284?")
check("Origin is declared unavailable, no city named",
      "not available" in r["answer"].lower() or "never reads origin" in r["answer"].lower(), r["answer"][:90])
for q in ("How is the run going?", "Summarize this run", "What needs my attention?",
          "Which shipments failed?", "What changed recently?", "Show DHL shipments"):
    a = ask(q)["answer"]
    check("No date outside the run in: {0!r}".format(q), not (dates(a) - dates(STATE_TEXT)), str(dates(a)))

rule("9. ATLAS DRIVES THE DASHBOARD — NAVIGATION ONLY")
r = ask("Show DHL shipments")
check("'Show DHL' filters Live operations to DHL, now",
      r["actions"] and r["actions"][0]["type"] == "filter" and r["actions"][0].get("carrier") == "dhl",
      str(r["actions"]))
check("...and says what it found", "DHL Express" in r["answer"] and "K179801" in r["answer"])
r = ask("Show ocean shipments")
check("'Show ocean shipments' filters by mode",
      r["actions"] and r["actions"][0].get("mode") == "ocean", str(r["actions"]))
check("...listing only ocean ones", "274599284" in r["answer"] and "K179801" not in r["answer"])
r = ask("Show air shipments")
check("'Show air shipments' finds the air one", "057-05765454" in r["answer"])
r = ask("Show failed shipments")
check("'Show failed shipments' filters to failed, now",
      r["actions"] and r["actions"][0].get("state") == "failed", str(r["actions"]))
r = ask("Open shipment 274599284")
check("'Open shipment …' opens it", r["actions"] == [{"type": "open", "reference": "274599284"}], str(r["actions"]))
r = ask("Show the latest failure")
check("'Show the latest failure' opens it",
      r["actions"] == [{"type": "open", "reference": "057-05765454"}], str(r["actions"]))
r = ask("Show waiting shipments")
check("'Show waiting shipments' goes to Human Action",
      r["actions"] == [{"type": "page", "page": "human"}], str(r["actions"]))
r = ask("Which shipments failed?")
check("A question is answered, not acted on", r["actions"] == [], str(r["actions"]))
everything = [ask(q) for q in ("Show DHL", "Show failed", "Open 274599284", "Show human actions",
                               "What needs my attention?", "Delete 274599284", "Resume the run",
                               "Re-run 274599284", "Stop the automation", "Submit the code 7Q4K")]
types = {a["type"] for r in everything for a in (r.get("actions") or [])} | \
        {x["action"]["type"] for r in everything for x in (r.get("buttons") or [])}
check("Only filter / open / page ever leave ATLAS", types <= set(assistant.UI_ACTIONS), str(types))

rule("10. HUMAN ACTION IS DETECTED")
brief = assistant.atlas_brief(STATE)
check("The panel says it is waiting for your action", brief["status"] == "Waiting for your action")
check("The first thing ATLAS noticed is the waiting shipment",
      brief["notices"][0]["level"] == "warn" and "S330348776" in brief["notices"][0]["text"]
      and brief["notices"][0]["action"] == {"type": "page", "page": "human"}, str(brief["notices"][:1]))
check("Its counts are the run's", brief["summary"] == {"completed": 1, "failed": 1, "waiting": 1,
                                                       "processing": 0, "skipped": 2}, str(brief["summary"]))
check("Suggestions fit the run: attention and failures first",
      brief["suggestions"][:4] == ["What needs my attention?", "Show waiting shipments",
                                   "Show failed shipments", "Why did the latest shipment fail?"],
      str(brief["suggestions"]))
quiet = ControlTowerState()
quiet.run_started(run_id="r2")
check("A quiet run produces no notices — nothing to report is reported as nothing",
      assistant.atlas_brief(quiet.snapshot())["notices"] == [])

rule("11. A FAILURE IS EXPLAINED FROM ITS RECORDED EVENTS ONLY")
r = ask("Why did the latest shipment fail?")
a = r["answer"]
check("The recorded steps, in order", a.index("Opening AFKL tracking") < a.index("Searching 057-05765454"))
check("The recorded outcome and reason", "AFKL NAVIGATION ERROR" in a and "did not open" in a)
check("No step that was not recorded (no retry, timeout or load claimed)",
      not re.search(r"\b(retry|retried|timeout|timed out|page load)\b", a, re.I), a)
check("It says that is everything recorded", "can't confirm anything beyond" in a)
bare = ControlTowerState()
bare.run_started(run_id="r3")
bare.shipment_started({"bol_awb": "111222333", "carrier": "DHL Express", "provider": "DHL"})
bare.shipment_finished("111222333", "FAILED", "Unexpected page", outcome="UNEXPECTED PAGE STATE")
a = assistant.answer("Why did 111222333 fail?", bare.snapshot())["answer"]
check("With no trace recorded, it says so instead of making one up",
      "No step-by-step trace was recorded" in a and "UNEXPECTED PAGE STATE" in a, a)

rule("12. SECURITY VERIFICATION STAYS HUMAN")
for q in ("Solve the security code for me", "Enter the code 7Q4K", "Bypass the captcha",
          "Type the code into Grimaldi"):
    r = ask(q)
    check("Refuses: {0!r}".format(q), "won't" in r["answer"] and "person" in r["answer"], r["answer"][:70])
    check("...and takes no action but pointing to Human Action: {0!r}".format(q),
          all(x["action"]["type"] == "page" for x in r["buttons"]) and not r["actions"])
SRC = (HERE / "dashboard" / "assistant.py").read_text(encoding="utf-8")
check("The assistant has no route to the human-action or control endpoints",
      "/api/human" not in SRC and "/api/control" not in SRC and "set_human_pending" not in SRC)
check("It imports no browser and no automation",
      "playwright" not in SRC.lower() and "update_eta" not in SRC)

rule("13. IT KEEPS UP WHILE THE RUN IS UPDATING")
errors = []
stop = threading.Event()


def writer():
    i = 0
    while not stop.is_set():
        ref = "LIVE{0:04d}".format(i)
        b.shipment_started({"bol_awb": ref, "carrier": "DHL Express", "provider": "DHL"})
        b.step("Tracking " + ref, system="DHL")
        b.shipment_finished(ref, "SUCCESS", "", {})
        i += 1


t = threading.Thread(target=writer, daemon=True)
t.start()
answers = 0
for _ in range(150):
    try:
        for q in ("How is the run going?", "What needs my attention?", "Show DHL"):
            assistant.answer(q, b.snapshot())
            answers += 1
        assistant.atlas_brief(b.snapshot())
    except Exception as error:
        errors.append(repr(error))
stop.set()
t.join(2)
check("{0} answers while shipments were being written, no error".format(answers), not errors, str(errors[:2]))
latest = assistant.atlas_brief(b.snapshot())
check("...and the brief reflects the new shipments", latest["summary"]["completed"] > 1,
      str(latest["summary"]))

print()
print("=" * 72)
print("{0} passed, {1} failed".format(len(PASS), len(FAIL)))
print("=" * 72)
sys.exit(1 if FAIL else 0)
