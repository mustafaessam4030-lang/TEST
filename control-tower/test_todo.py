"""
To Do (dashboard/todo.py): what needs a person, derived
from the platform's records, with a state (overdue, urgent, pending
approval, to do, completed), a priority, a due date where the record has
one, a link to where it is dealt with, and only what the person's access
level lets them open. Plus the pages: the original Overview layout and the
To Do slide-out. No browser, no network.
"""
import datetime as dt
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="todo_")
os.environ.update({"ATA_RUNTIME_DIR": TMP, "ATA_CLEARANCE_INBOX": os.path.join(TMP, "inbox"),
                   "ATLAS_INTEL_DIR": os.path.join(TMP, "intel"), "PO_DATA_DIR": os.path.join(TMP, "po")})
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
        row(awb="LATE-31", overdue=31, agent="AGENT TWO", due="2026-09-27"),
        row(awb="LATE-5", overdue=5),
        row(awb="ESC-60", overdue=60, bu="GH", due="2026-08-29"),
        row(awb="ESC-DONE", overdue=12, bu="NG"),
        row(awb="FIX-1", status="RequiredCorrection", overdue=2),
        row(awb="DONE-1", step="Completed", ontime=1)]
clearance.data_path().write_text(json.dumps({"asof": "2026-10-20", "rows": ROWS, "dq": {},
                                              "fields": clearance.FIELDS, "count": len(ROWS)}))
ok, _msg, ESC = clearance.escalate(dict(zip(clearance.FIELDS, ROWS[3])), "Agent not answering", "ops@mantrac.example")
check("fixture: one open escalation", ok, _msg)
ok, _msg, E2 = clearance.escalate(dict(zip(clearance.FIELDS, ROWS[4])), "Duty", "ops@mantrac.example")
clearance.resolve(E2["id"], "Agent paid the duty", "lead@mantrac.example")
clearance.save_contacts({"agents": {"AGENT ONE": {"email": "one@agent.example", "person": "One"},
                                    "AGENT TWO": {"email": "", "person": "No mail"}},
                         "bus": {}, "forwarders": {}, "coe": {}}, "admin@mantrac.example")

NOW = dt.datetime.now().replace(microsecond=0)
EPOCH = time.mktime(NOW.timetuple())
fmt = lambda d: d.strftime("%Y-%m-%d %H:%M:%S")             # noqa: E731
STATE = {"human_queue": [
    {"action_id": "late", "reference": "157-11111111", "carrier": "Qatar Airways Cargo", "status": "WAITING_FOR_HUMAN",
     "created_at": fmt(NOW - dt.timedelta(hours=2)), "timeout_at": fmt(NOW - dt.timedelta(minutes=5)),
     "timeout_epoch": EPOCH - 300, "label": "Waiting for a person"},
    {"action_id": "fresh", "reference": "176-22222222", "carrier": "Emirates", "status": "WAITING_FOR_HUMAN",
     "created_at": fmt(NOW - dt.timedelta(minutes=10)), "timeout_at": fmt(NOW + dt.timedelta(minutes=50)),
     "timeout_epoch": EPOCH + 3000},
    {"action_id": "done", "reference": "057-33333333", "status": "SUCCESS", "label": "Written and read back",
     "created_at": fmt(NOW - dt.timedelta(hours=3)), "closed_at": fmt(NOW - dt.timedelta(hours=1))},
    {"action_id": "old", "reference": "057-44444444", "status": "SUCCESS",
     "created_at": fmt(NOW - dt.timedelta(days=20)), "closed_at": fmt(NOW - dt.timedelta(days=19))}],
    "shipments": [{"reference": "157-22222222", "state": "failed"}, {"reference": "157-33333333", "state": "updated"}],
    "run": {"run_id": "R7", "status": "completed", "finished_at": (NOW - dt.timedelta(minutes=30)).isoformat()}}


class _Svc:
    """A stand-in PO service: summary() as po/service.py returns it."""
    def summary(self):
        return {"config": {"auto_send": False}, "jobs": [
            {"po_id": "p-review", "reference": "MEDU0001", "doctype": "boe", "state": "NEEDS_REVIEW",
             "kpi": "validation_required", "label": "Needs review", "updated": "2026-10-28T09:00:00"},
            {"po_id": "p-send", "reference": "MEDU0002", "doctype": "boe", "state": "EMAIL_PREPARED",
             "kpi": "generated", "label": "Ready (not sent)", "updated": "2026-10-28T09:30:00"},
            {"po_id": "p-old", "reference": "MEDU0002", "doctype": "boe", "state": "VALIDATION_FAILED",
             "kpi": "validation_required", "label": "older job, superseded"},
            {"po_id": "p-fail", "reference": "MEDU0003", "doctype": "boe", "state": "PDF_NOT_FOUND",
             "kpi": "failed", "label": "PDF not found"},
            {"po_id": "p-sent", "reference": "MEDU0004", "doctype": "boe", "state": "EMAIL_SENT", "kpi": "sent"}]}


from po import service as _po_service                        # noqa: E402
_po_service.local = lambda: _Svc()
everyone = lambda _p: True                                     # noqa: E731

print("1. TASKS FROM THE RECORDS, WITH A STATE")
out = todo.build(everyone, STATE, now=NOW)
items = out["items"]
by_id = {i["id"]: i for i in items}
check("every task has the fields the pages use",
      all({"id", "source", "kind", "state", "priority", "title", "detail", "href", "due", "at", "count"} <= set(i) for i in items))
check("ids are unique: no task listed twice", len(by_id) == len(items), [i["id"] for i in items])
check("a Human Action past its own timeout is overdue, due at its timeout",
      by_id["human:late"]["state"] == "overdue" and by_id["human:late"]["due"] == STATE["human_queue"][0]["timeout_at"])
check("…one still inside it is urgent", by_id["human:fresh"]["state"] == "urgent")
check("a Human Action finished today is completed; one 19 days ago is not listed",
      by_id["human:done"]["state"] == "completed" and "human:old" not in by_id)
esc = by_id["esc:" + ESC["id"]]
check("an open escalation whose step is past due is overdue, due at the step's due date",
      esc["state"] == "overdue" and esc["due"] == "2026-08-29" and esc["href"] == "/clearance#q=ESC-60")
check("a resolved escalation is completed, with who resolved it",
      by_id["esc:" + E2["id"]]["state"] == "completed" and "lead@mantrac.example" in by_id["esc:" + E2["id"]]["detail"])
check("a PO job needing review is pending approval, opening that job",
      by_id["po:review:p-review"]["state"] == "pending_approval" and by_id["po:review:p-review"]["href"] == "/#po?po=p-review")
check("a prepared PO email (auto-send off) waits for a person to approve sending",
      by_id["po:send:p-send"]["state"] == "pending_approval" and "Approve and send" in by_id["po:send:p-send"]["title"])
check("only a document's latest PO job counts (the superseded one is not a task)", "po:review:p-old" not in by_id)
check("a failed PO job is a task; a sent one is not", "po:failed:p-fail" in by_id and not any("p-sent" in k for k in by_id))
long_late = by_id["clr:long-late"]
check("shipments 30+ days past due and not escalated: one overdue task, counted, the escalated one left out",
      long_late["state"] == "overdue" and long_late["count"] == 2 and "LATE-45" in long_late["detail"])
check("agents without an email on file (the same rule as Mail): one task", by_id["clr:contacts"]["count"] == 1
      and "AGENT TWO" in by_id["clr:contacts"]["detail"])
check("an export 8 days old asks for a newer one", "clr:export" in by_id)

print("\n2. PRIORITY")
order = [i["id"] for i in items if i["state"] != "completed"]
check("the overdue Human Action comes first, then the urgent one, then the escalation",
      order[:3] == ["human:late", "human:fresh", "esc:" + ESC["id"]], order[:4])
check("…then PO approvals before the run's failures and Clearance housekeeping",
      order.index("po:review:p-review") < order.index("run:failed") < order.index("clr:correction") < order.index("clr:export"), order)
check("completed tasks come last, newest first",
      [i["id"] for i in items[-2:]] == ["esc:" + E2["id"], "human:done"], [i["id"] for i in items[-3:]])
check("outstanding and the counts by state are counted from the list, not set",
      out["outstanding"] == sum(1 for i in items if i["state"] != "completed")
      and sum(out["by_state"].values()) == len(items) and out["by_state"]["pending_approval"] == 2)

print("\n3. ONLY WHAT THE PERSON MAY OPEN")
viewer = {"dashboard.view", "runs.view", "clearance.view", "po.view", "atlas.chat"}
tv = {i["id"] for i in todo.build(lambda p: p in viewer, STATE, now=NOW)["items"]}
check("a Viewer sees no Human Action task (human.act), no send approval (po.send), no contacts or export (Admin)",
      not any(t.startswith("human:") for t in tv) and "po:send:p-send" not in tv and "clr:contacts" not in tv
      and "clr:export" not in tv, sorted(tv))
check("…nor a PO review it cannot approve (po.process)", "po:review:p-review" not in tv, sorted(tv))
check("…but sees the escalation and the run's failures", {"esc:" + ESC["id"], "run:failed"} <= tv, sorted(tv))
check("without clearance.view: no Clearance task",
      not any(i["source"] == "Clearance" for i in todo.build(lambda p: p == "dashboard.view", STATE, now=NOW)["items"]))

print("\n4. FROM THE REVIEW")
q = todo.build(everyone, {"human_queue": [], "shipments": [{"reference": "X1", "state": "waiting_for_human"}]}, now=NOW)
check("queue off or empty: a shipment waiting for a person is still a task",
      [i["id"] for i in q["items"] if i["source"] == "Human Action"] == ["human:waiting"], q["items"])
q = todo.build(everyone, {"human_queue": [
    {"action_id": "busy", "reference": "R1", "status": "OPERATOR_OPENED", "timeout_epoch": EPOCH - 999},
    {"action_id": "auto", "reference": "R2", "status": "RESUMING", "timeout_epoch": EPOCH - 999}],
    "shipments": [{"reference": "R2", "state": "waiting_for_human"}]}, now=NOW)
hq = {i["id"]: i for i in q["items"] if i["source"] == "Human Action"}
check("a person already on it: urgent, never overdue (only a waiting task has a timeout)",
      hq.get("human:busy", {}).get("state") == "urgent" and "In progress" in hq["human:busy"]["title"], hq)
check("the run resuming by itself is not a task, and its shipment is not counted twice", list(hq) == ["human:busy"], list(hq))
_esc = {"id": "e9", "awb": "A9", "bu": "EG", "step": "Customs Release", "due": "2099-01-01", "overdue": 40,
        "by": "x@y", "at": "2026-10-01T10:00:00", "status": "open"}
_orig = clearance.escalations
clearance.escalations = lambda: [_esc]
q = {i["id"]: i for i in todo.build(everyone, {}, now=NOW)["items"]}
check("an escalation no longer in the export: state from its step's due date today, not the count stored when made",
      q["esc:e9"]["state"] == "urgent" and "days past due" not in q["esc:e9"]["detail"], q["esc:e9"])
clearance.escalations = _orig


class _Unknown:
    def summary(self):
        return {"config": {"auto_send": True}, "jobs": [
            {"po_id": "p-unk", "reference": "MEDU0009", "doctype": "boe", "state": "EMAIL_UNKNOWN",
             "kpi": "validation_required", "label": "UNKNOWN — reconcile, do not resend"}]}


_po_service.local = lambda: _Unknown()
q = {i["id"]: i for i in todo.build(everyone, {}, now=NOW)["items"]}
check("a PO email whose fate is unknown is 'Check', never 'nothing is sent'",
      "po:check:p-unk" in q and "nothing is sent" not in q["po:check:p-unk"]["detail"]
      and "do not resend" in q["po:check:p-unk"]["detail"], q.get("po:check:p-unk"))
_po_service.local = lambda: _Svc()

_e = {i["id"]: i for i in todo.build(everyone, STATE, now=NOW)["items"]}["esc:" + ESC["id"]]
check("an escalation in the export shows the export's own day count (as the Clearance page)",
      "60 days past due" in _e["detail"] and _e["state"] == "overdue", _e["detail"])

print("\n5. THE PAGES: the original layout, the To Do slide-out")
S = os.path.join(HERE, "dashboard", "static")
idx = open(os.path.join(S, "index.html"), encoding="utf-8").read()
cl = open(os.path.join(S, "clearance.html"), encoding="utf-8").read()
js = open(os.path.join(S, "todo.js"), encoding="utf-8").read()
check("the Overview: no Tasks page, no bell, no action-center card",
      "data-action-card" not in idx and "data-task-center" not in idx and "{id:'tasks'" not in idx
      and "data-notify-slot" not in idx + cl and not os.path.exists(os.path.join(S, "tasks.js"))
      and not os.path.exists(os.path.join(S, "notify.js")))
check("the Overview still has its Tracking map, On the way and ATLAS card",
      'class="card route"' in idx and 'id="routeMap"' in idx and "On the way" in idx and "atlas-fig" in idx)
_ovr = idx.split('<div class="ov-vis-r">')[1]
check("the To Do list is a card on the Overview's right side, first in the right column, before On the way",
      'data-todo-card' in _ovr and _ovr.index("data-todo-card") < _ovr.index('id="fleet"')
      and idx.count("data-todo-card") == 1)
check("…the same list as the panel, hidden until it loads (and on a server without To Do)",
      'data-todo-card hidden' in idx and "card.hidden = false" in js and "if (card) card.hidden = true" in js
      and "if (cardList && d) {" in js
      and "cardList.innerHTML = html" in js and "panel.querySelector(\".todo-l\").innerHTML = html" in js)
check("…its links open where the item is dealt with, even the page already open",
      'cardList.addEventListener("click"' in js and "follow(e, a)" in js)
check("the To Do button in the top bar of both pages", idx.count("<span data-todo-slot></span>") == 1
      and cl.count("<span data-todo-slot></span>") == 1 and '<script src="/static/todo.js" defer></script>' in idx + cl)
check("the slide-out opens only on a click, never on load", "open(true)" not in js and 'btn.addEventListener("click"' in js)
check("…compact, under its button, inside the screen (not the full height of the window)",
      "max-height:min(480px" in js and "bottom:0" not in js and "function place()" in js and "innerWidth - w - 8" in js)
check("…dismissible: x, Escape (focus back), a click elsewhere, picking an item",
      ".todo-x" in js and '"Escape"' in js and "!panel.contains(e.target)" in js and "btn.focus()" in js
      and 'closest("a")' in js)
check("…lists what is still to do, with its state and due date; every value escaped; same-site links only",
      'i.state !== "completed"' in js and "STATE[i.state]" in js and "esc(day(i.due))" in js
      and "${esc(i.title)}" in js and "/^\\/(?![\\/\\\\])/.test" in js)

print("\n{0} passed, {1} failed".format(len(PASS), len(FAIL)))
sys.exit(1 if FAIL else 0)
