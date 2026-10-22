"""
ATLAS answers setup questions from what happened when the tower went live
(DEPLOYMENT_LESSONS.md), and run questions still from the run.

    python test_deployment_lessons.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard import assistant                              # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


SETUP = {
    "python is not recognized after I installed it": "python is not recognized",
    "PowerShell stuck after installing Ollama": "installing Ollama",
    "the set password link says can't reach this page": "set-password link",
    "How do I add the DNS record?": "DNS",
    "From a PC I can't reach the azure server": "from a PC",
    "the run stopped with browser session lost after we restarted the server": "browser session lost",
    "server check says PyMuPDF missing": "PyMuPDF",
    "You do not have adequate user rights when I install IIS": "adequate user rights",
    "We moved to another server, what do I copy?": "Moving to another server",
}
for q, title in SETUP.items():
    r = assistant._answer_rules(q, {}, {"_raw": True})
    a = r.get("answer") or ""
    check("setup: " + q, r.get("intent") == "handbook_guide" and "DEPLOYMENT_LESSONS.md" in a
          and title.lower() in a.lower(), (r.get("intent"), a[:120]))

RUN = {"Which shipments are stuck?": "stuck", "How many shipments are left?": "counts",
       "what is the ETA of MEDUAHP69377": "eta"}
for q, intent in RUN.items():
    r = assistant._answer_rules(q, {}, {"_raw": True})
    check("run question stays a run question: " + q, r.get("intent") == intent, r.get("intent"))

guide = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "DEPLOYMENT_LESSONS.md"), encoding="utf-8").read()
check("the guide holds no password, key or token", not any(w in guide for w in
      ("1R98GCSbwa1fnpNTPt", "token=KQ9", "Hunter-", "PASSWORD=the")) and "#token=..." in guide)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
