"""
To Do (dashboard/todo.py): what needs a person, counted from the records,
each item linking to where it is dealt with, and only what the person's
access level lets them open. No browser, no network.
"""
import datetime as dt
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="todo_")
os.environ.update({"ATA_RUNTIME_DIR": TMP, "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "ATLAS_INTEL_DIR": os.path.join(TMP, "intel")})
from dashboard import clearance, todo                         # noqa: E402

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print("  [{0}] {1}{2}".format("PASS" if ok else "FAIL", name, "" if ok else "  ({0})".format(detail)))


def row(**kw):
    base = dict(bu="EG", div="Spare Parts", mode="Air", ffw="FFW A", agent="AGENT ONE", status="Under Clearance",
                step="Submit to BU", eta="2026-09-01", ata=None, ontime=0, overdue=None, awb="X", ach="---------",
                cycle=None, target=None, due=None, next=None, submitted=None, release=None, completed=None)
    base.update(kw)
    return [base[k] for k in clearance.FIELDS]


ROWS = [row(awb="LATE-45", overdue=45, due="2026-09-13"),
        row(awb="LATE-31", overdue=31, agent="AGENT TWO"),
        row(awb="LATE-5", overdue=5),
        row(awb="ESC-60", overdue=60, bu="GH"),
        row(awb="FIX-1", status="RequiredCorrection", overdue=2),
        row(awb="DONE-1", step="Completed", ontime=1)]
clearance.data_path().write_text(json.dumps({"asof": "2026-10-20", "rows": ROWS, "dq": {},
                                              "fields": clearance.FIELDS, "count": len(ROWS)}))
ok, _msg, _e = clearance.escalate(dict(zip(clearance.FIELDS, ROWS[3])), "Agent not answering", "ops@mantrac.example")
check("fixture: one open escalation", ok, _msg)
clearance.save_contacts({"agents": {"AGENT ONE": {"email": "one@agent.example", "person": "One"},
                                    "AGENT TWO": {"email": "", "person": "No mail"}},
                         "bus": {}, "forwarders": {}, "coe": {}}, "admin@mantrac.example")
STATE = {"shipments": [{"reference": "157-11111111", "state": "waiting_for_human"},
                       {"reference": "157-22222222", "state": "failed"},
                       {"reference": "157-33333333", "state": "updated"}]}
TODAY = dt.date(2026, 10, 28)
everyone = lambda _p: True                                     # noqa: E731

print("1. WHAT NEEDS A PERSON, FROM THE RECORDS")
out = todo.build(everyone, STATE, today=TODAY)
items = out["items"]
by = {i["title"]: i for i in items}
titles = [i["title"] for i in items]
check("a shipment waiting on Human Action comes first, red, linking to Human Action",
      items[0]["title"] == "1 shipment waiting for a person" and items[0]["level"] == "red"
      and items[0]["href"] == "/#human" and "157-11111111" in items[0]["detail"], items[0])
check("an open escalation is listed with who and when, linking to the shipment",
      "Escalated: ESC-60 (GH)" in by and by["Escalated: ESC-60 (GH)"]["href"] == "/clearance#q=ESC-60"
      and "ops@mantrac.example on " in by["Escalated: ESC-60 (GH)"]["detail"], titles)
check("a failed run shipment is listed (amber)", by.get("1 shipment failed in this run", {}).get("level") == "amber", titles)
long_late = [i for i in items if "past due, not escalated" in i["title"]]
check("shipments 30+ days past due and not escalated: counted, the escalated one left out",
      long_late and long_late[0]["count"] == 2 and "LATE-45" in long_late[0]["detail"], long_late)
check("shipments eHub marks for correction", any(i["title"] == "1 shipment marked Correction required in eHub"
                                                for i in items), titles)
miss = [i for i in items if i["title"].startswith("Add contacts")]
check("an agent with past-due shipments and no email on file (the same rule as Mail)",
      miss and miss[0]["count"] == 1 and "AGENT TWO" in miss[0]["detail"] and "AGENT ONE" not in miss[0]["detail"]
      and miss[0]["level"] == "grey", miss)
check("an export 8 days old asks for a newer one (grey)",
      any(i["title"] == "Load a newer eHub export" and "2026-10-20" in i["detail"] for i in items), titles)
check("the count is what needs action: red and amber, not housekeeping",
      out["count"] == sum(1 for i in items if i["level"] in ("red", "amber")), out["count"])
check("every link stays on this site", all(i["href"].startswith("/") and not i["href"].startswith("//") for i in items))
check("nothing is invented: every number is in the records",
      all(isinstance(i["count"], int) and i["count"] > 0 for i in items))

print("\n2. ONLY WHAT THE PERSON MAY OPEN")
viewer = {"dashboard.view", "runs.view", "clearance.view", "atlas.chat"}
out_v = todo.build(lambda p: p in viewer, STATE, today=TODAY)
tv = [i["title"] for i in out_v["items"]]
check("a Viewer does not see Human Action (needs human.act)", not any("waiting for a person" in t for t in tv), tv)
check("…nor contacts (clearance.contacts) or loading an export (clearance.upload)",
      not any(t.startswith("Add contacts") or t == "Load a newer eHub export" for t in tv), tv)
check("…but sees the escalation and the run's failure", "Escalated: ESC-60 (GH)" in tv and
      "1 shipment failed in this run" in tv, tv)
out_n = todo.build(lambda p: p == "dashboard.view", STATE, today=TODAY)
check("without clearance.view: no Clearance items at all",
      not any(i["source"] == "Clearance" for i in out_n["items"]), out_n["items"])

print("\n3. QUIET WHEN THERE IS NOTHING")
os.remove(str(clearance.data_path()))
clearance._cache.update(mtime=None, data=None)
out_e = todo.build(everyone, {"shipments": []}, today=TODAY)
check("no run, no export, no PO jobs: an empty list and a zero count",
      out_e["count"] == 0 and not [i for i in out_e["items"] if i["source"] != "PO Automation"], out_e)

print("\n4. THE BUTTON")
for page in ("index.html", "clearance.html"):
    html = open(os.path.join(HERE, "dashboard", "static", page), encoding="utf-8").read()
    check("{0}: the To Do button in the top bar".format(page),
          "<span data-todo-slot></span>" in html and '<script src="/static/todo.js" defer></script>' in html)
js = open(os.path.join(HERE, "dashboard", "static", "todo.js"), encoding="utf-8").read()
check("todo.js escapes every value and follows only same-site links",
      "const esc = " in js and "${esc(i.title)}" in js and "/^\\/(?!\\/)/.test" in js)
check("…and reads only (GET /api/todo)", js.count("fetch(") == 1 and "method" not in js)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
