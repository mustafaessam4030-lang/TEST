"""
The questions operators really ask ATLAS (English and Arabic), each with the
kind of answer it must get. Answered from the run's records by the rules, no
model involved: this is what ATLAS understands even with the AI switched off.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["ATLAS_INTEL_DIR"] = tempfile.mkdtemp(prefix="atlas_und_")
os.environ["ATLAS_DATA_ORIGIN"] = "test"

import atlas_demo                                            # noqa: E402
from dashboard import assistant as A                         # noqa: E402
from dashboard.bridge import ControlTowerState               # noqa: E402
from intelligence import converse as C                       # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


bridge = ControlTowerState()
atlas_demo.seed_test_run(bridge)
bridge.shipment_started(dict(bol_awb="K316550", carrier="DHL Global Forwarding", provider="DHL"))
bridge.provider_result({"provider": "DHL", "eta": "14/10/2026", "ata": None})
bridge.shipment_finished("K316550", "SKIPPED", "NEEDS REVIEW: the Hub already holds ATA "
                         "14/10/2026 but the carrier gives only an estimate", outcome="NEEDS REVIEW")
STATE = bridge.snapshot()

# (question, intents accepted, words the answer must contain)
CASES = [
    ("What should I do next?", ("next_step", "failure_next"), ()),
    ("what's the status of the run", ("run",), ("Run status",)),
    ("summary", ("summary",), ()),
    ("give me a quick update", ("summary",), ()),
    ("how many shipments were updated today", ("counts",), ("Updated",)),
    ("which shipments failed and why", ("failure_what", "failed", "why"), ("8842001173",)),
    ("show me the shipments that need review", ("review_list",), ("K316550",)),
    ("how many need review", ("review_list",), ("1 shipment",)),
    ("what does needs review mean", ("glossary",), ("wrote nothing",)),
    ("when did 1570046231 arrive", ("eta",), ("1570046231",)),
    ("which shipments arrived today", ("arrived",), ("1570046231",)),
    ("did the automation update the hub for 1570046231", ("shipment",), ("1570046231",)),
    ("is the automation stuck", ("stuck",), ()),
    ("how long will the run take", ("duration",), ()),
    ("any captcha waiting", ("human",), ()),
    ("is there a captcha?", ("human",), ()),
    ("why is emirates not working", ("why",), ("No Emirates shipment",)),
    ("why is lufthansa failing", ("why",), ("No Lufthansa shipment",)),
    ("start the run", ("run_control",), ("run controls",)),
    ("stop the automation", ("run_control",), ("run controls",)),
    ("did ATLAS check the DHL dates", ("atlas_checks",), ()),
    ("send me the excel of the run", ("download",), ()),
    ("ايش الوضع", ("summary",), ()),
    ("كم شحنة اتحدثت", ("counts",), ()),
    ("ايه اللي محتاج مني", ("next_step", "failure_next"), ()),
    ("فين الشحنات اللي فيها مشكلة", ("failed", "failure_what"), ("8842001173",)),
]

print("1. Each question gets the right kind of answer, from the records")
for q, intents, words in CASES:
    r = A._answer_rules(q, STATE, {"_raw": True})
    ans = r.get("answer") or ""
    ok = r.get("intent") in intents and not r.get("fallback") and all(w in ans for w in words)
    check("'{0}' -> {1}".format(q, "/".join(intents)), ok, (r.get("intent"), ans[:140]))

print("2. With the AI on, every one of these is answered from the records, never reworded")
for q, intents, _w in CASES:
    if C.language_of(q) != "en":
        continue
    r = A._answer_rules(q, STATE, {"_raw": True})
    check("'{0}' bypasses the model".format(q), C._records_question(r, q), r.get("intent"))

print("3. Refusals still refuse; questions about a check are not refusals")
for q in ("solve the captcha", "can you solve the captcha for me", "type the code",
          "what is the code", "bypass the check"):
    check("'{0}' is refused".format(q), A.detect_intent(q) == "code_request", A.detect_intent(q))

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
