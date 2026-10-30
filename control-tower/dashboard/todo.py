"""
To Do: what needs a person, across the platform.

TASKS (build) — derived on each request from the records the platform
already keeps; nothing is stored, so nothing can be duplicated or drift:

    Human Action queue (human_queue.py, via the run state): a task waiting
    for a person (overdue once its own timeout has passed, urgent before), a
    task a person is working on (urgent); and a shipment waiting for a person
    with no task in the queue (the queue off, or the wait before a task).
    PO Automation (po/service.py): a job needing a person's review (pending
    approval, for those who may review), a prepared email waiting for a
    person to send it when auto-send is off (pending approval), a job whose
    email needs checking, a failed job.
    The run: shipments that failed (one task).
    Clearance (dashboard/clearance.py): every open escalation (overdue when
    the current export says its step is past due, or, if it has left the
    export, when its step's due date has passed), shipments 30+ days past due nobody escalated
    (overdue, one task), shipments eHub marks for correction, clearing agents
    with past-due shipments and no email on file, an export over 3 days old.
    Completed: escalations resolved and Human Action tasks finished in the
    last 7 days.

Each task: id, source, kind, state (overdue | urgent | pending_approval |
open | completed), priority (higher first), level (red | amber | grey, for
older callers), count, title, detail, href (where it is dealt with), due
(the record's own date, or None), at (when it started or was completed).
Each person sees only what their access level lets them open.
"""

import datetime as _dt
import time
from urllib.parse import quote

LEVELS = {"red": 0, "amber": 1, "grey": 2}
STATES = ("overdue", "urgent", "pending_approval", "open", "completed")
LONG_PAST_DUE = 30                      # days past a step's due date
EXPORT_OLD = 3                          # days since the export's data date
DONE_DAYS = 7                           # how long a completed task stays listed
HUMAN_TERMINAL = {"SUCCESS", "TIMEOUT", "HUMAN_SESSION_LOST", "VERIFICATION_NOT_CONFIRMED", "FAILED"}
PO_REVIEW_STATES = {"NEEDS_REVIEW", "VALIDATION_FAILED", "DOCUMENT_AMBIGUOUS"}


def _plural(n, one, many=None):
    return "{0:,} {1}".format(n, one if n == 1 else (many or one + "s"))


def _names(values, limit=3):
    values = [v for v in values if v]
    shown = ", ".join(values[:limit])
    return shown + (" and {0} more".format(len(values) - limit) if len(values) > limit else "")


def _task(id, source, kind, state, priority, level, title, detail, href, permission,
          count=1, due=None, at=None):
    return {"id": id, "source": source, "kind": kind, "state": state, "priority": priority,
            "level": level, "count": count, "title": title, "detail": detail, "href": href,
            "due": due, "at": at, "permission": permission}


def _recent(stamp, now):
    """True when an ISO timestamp is within DONE_DAYS of now."""
    try:
        when = _dt.datetime.fromisoformat(str(stamp)[:19])
    except (TypeError, ValueError):
        return False
    return (now - when).days < DONE_DAYS


# ── the run and its Human Action queue ────────────────────────────────────

# Human Action statuses (human_queue.py): who has the next move.
HUMAN_WAITING = "WAITING_FOR_HUMAN"                     # a person must pick it up (has a timeout)
HUMAN_WITH_PERSON = {"OPERATOR_OPENED", "VERIFICATION_PENDING"}       # a person is on it
# HUMAN_COMPLETED, POST_VERIFICATION_CHECK, RESUMING: the run continues by itself, no task.


def _human_act(t):
    """What the To Do list's Open / Resume buttons send to /api/human: the same
    run and action ids the run page sends; the server and the run check them."""
    return {"run_id": t.get("run_id"), "action_id": t.get("action_id"), "status": t.get("status"),
            "reference": t.get("reference"), "carrier": t.get("carrier")}


def _human_items(state, now_epoch, now):
    queue = (state or {}).get("human_queue") or []
    out, queued = [], set()
    for t in queue:
        status = str(t.get("status") or "")
        ref = t.get("reference") or "a shipment"
        queued.add(str(t.get("reference") or ""))
        if status in HUMAN_TERMINAL:
            if status == "SUCCESS" and _recent(t.get("closed_at") or t.get("updated_at"), now):
                out.append(_task("human:" + str(t.get("action_id")), "Human Action", "human_action", "completed", 0,
                                 "grey", "Human Action done: {0}".format(ref),
                                 t.get("label") or "Completed", "/#human", "human.act",
                                 at=t.get("closed_at") or t.get("updated_at")))
            continue
        if status == HUMAN_WAITING:
            # Only a waiting task has a timeout (human_queue.expire): past it, overdue.
            timeout = t.get("timeout_epoch")
            late = isinstance(timeout, (int, float)) and now_epoch > timeout
            out.append(_task("human:" + str(t.get("action_id")), "Human Action", "human_action",
                             "overdue" if late else "urgent", 100 if late else 95, "red",
                             "{0} needs a person: {1}".format(t.get("carrier") or "The carrier page", ref),
                             t.get("label") or t.get("reason") or "Open Human Action to continue.",
                             "/#human", "human.act", due=t.get("timeout_at"), at=t.get("created_at")))
            out[-1]["act"] = _human_act(t)
        elif status in HUMAN_WITH_PERSON:
            out.append(_task("human:" + str(t.get("action_id")), "Human Action", "human_action", "urgent", 90, "red",
                             "In progress with a person: {0}".format(ref),
                             t.get("label") or "Finish it in Human Action.", "/#human", "human.act",
                             at=t.get("created_at")))
            out[-1]["act"] = _human_act(t)
    # A shipment waiting for a person with no task in the queue (the queue off,
    # or the in-place wait before a task is made) still needs a person.
    waiting = [s for s in (state or {}).get("shipments") or []
               if s.get("state") == "waiting_for_human" and str(s.get("reference") or "") not in queued]
    if waiting:
        out.append(_task("human:waiting", "Human Action", "human_action", "urgent", 95, "red",
                         "{0} waiting for a person".format(_plural(len(waiting), "shipment")),
                         "Open Human Action to continue: " + _names([s.get("reference") for s in waiting]),
                         "/#human", "human.act", count=len(waiting)))
    return out


def _run_items(state):
    failed = [s for s in (state or {}).get("shipments") or [] if s.get("state") == "failed"]
    if not failed:
        return []
    return [_task("run:failed", "Run", "run_failed", "open", 70, "amber",
                  "{0} failed in this run".format(_plural(len(failed), "shipment")),
                  "Ask ATLAS why, or look at them in Shipments: " + _names([s.get("reference") for s in failed]),
                  "/#shipments", "runs.view", count=len(failed))]


# ── PO Automation ─────────────────────────────────────────────────────────

def _po_items():
    try:
        from po import service as po_service
        summary = po_service.local().summary()
    except Exception:
        return []
    jobs = summary.get("jobs") or []
    auto_send = bool((summary.get("config") or {}).get("auto_send"))
    latest = {}
    for job in jobs:                     # newest first: a document's latest job counts (as the PO page)
        latest.setdefault((job.get("doctype"), job.get("reference") or job.get("po_id")), job)
    out = []
    for j in latest.values():
        pid, ref = str(j.get("po_id") or ""), j.get("reference") or "a PO job"
        href = "/#po?po=" + quote(pid, safe="") if pid else "/#po"
        what = j.get("label") or j.get("state")
        if j.get("state") in PO_REVIEW_STATES:
            # Approving needs po.process (the PO page's Review): only those who can.
            out.append(_task("po:review:" + pid, "PO Automation", "po_review", "pending_approval", 80, "red",
                             "Review PO {0}".format(ref), "{0} — a person decides; nothing is sent until then.".format(what),
                             href, "po.process", at=j.get("updated") or j.get("created")))
        elif j.get("kpi") == "validation_required":
            # Not a review: e.g. the email's fate is unknown or does not match. Said as it is,
            # never "nothing is sent" (it may have been).
            out.append(_task("po:check:" + pid, "PO Automation", "po_check", "urgent", 76, "red",
                             "Check PO {0}".format(ref), "{0} — open it before doing anything else.".format(what),
                             href, "po.view", at=j.get("updated") or j.get("created")))
        elif j.get("state") == "EMAIL_PREPARED" and not auto_send:
            out.append(_task("po:send:" + pid, "PO Automation", "po_send", "pending_approval", 78, "amber",
                             "Approve and send the PO email for {0}".format(ref),
                             "Prepared and checked; waiting for a person to send it.",
                             href, "po.send", at=j.get("updated") or j.get("created")))
        elif j.get("kpi") == "failed":
            out.append(_task("po:failed:" + pid, "PO Automation", "po_failed", "open", 65, "amber",
                             "PO {0} failed".format(ref), str(what or "Failed"),
                             href, "po.view", at=j.get("updated") or j.get("created")))
    return out


# ── Clearance ─────────────────────────────────────────────────────────────

def _clearance_items(today, now):
    try:
        from dashboard import clearance
        data = clearance.load()
        all_esc = clearance.escalations()
        contacts = clearance.contacts()
    except Exception:
        return []
    out = []
    escalations = [e for e in all_esc if e.get("status") == "open"]
    # The current export's own day count, the same figure the Clearance page shows.
    current = {(r["awb"], r["bu"]): r for r in clearance.records(data)} if data else {}
    for e in escalations:
        row = current.get((e.get("awb"), e.get("bu")))
        if row is not None and isinstance(row.get("overdue"), (int, float)) and not isinstance(row.get("overdue"), bool):
            days = int(round(row["overdue"]))
        else:
            # Not in the current export: from the step's due date as of today
            # (the count stored when it was escalated would be stale).
            try:
                days = (today - _dt.date.fromisoformat(str(e.get("due"))[:10])).days
            except (TypeError, ValueError):
                days = None
        late = days is not None and days > 0
        out.append(_task("esc:" + str(e.get("id")), "Clearance", "escalation", "overdue" if late else "urgent",
                         90 if late else 85, "red", "Escalated: {0} ({1})".format(e.get("awb"), e.get("bu")),
                         "{0}{1} · by {2} on {3}".format(
                             e.get("step") or "", ", {0:,} days past due".format(days) if late else "",
                             e.get("by"), str(e.get("at") or "")[:10]),
                         "/clearance#q={0}".format(quote(str(e.get("awb") or ""), safe="")), "clearance.view",
                         due=e.get("due"), at=e.get("at")))
    for e in all_esc:
        if e.get("status") == "resolved" and _recent(e.get("resolved_at"), now):
            out.append(_task("esc:" + str(e.get("id")), "Clearance", "escalation", "completed", 0, "grey",
                             "Resolved: {0} ({1})".format(e.get("awb"), e.get("bu")),
                             "by {0}{1}".format(e.get("resolved_by"), ": " + e["resolved_note"] if e.get("resolved_note") else ""),
                             "/clearance#q={0}".format(quote(str(e.get("awb") or ""), safe="")), "clearance.view",
                             at=e.get("resolved_at")))
    if not data:
        return out
    rows = clearance.records(data)
    escalated = {(e.get("awb"), e.get("bu")) for e in escalations}
    long_late = [r for r in rows if clearance.past_due(r) and r["overdue"] >= LONG_PAST_DUE
                 and (r["awb"], r["bu"]) not in escalated]
    if long_late:
        worst = max(long_late, key=lambda r: r["overdue"])
        out.append(_task("clr:long-late", "Clearance", "long_past_due", "overdue", 60, "amber",
                         "{0} over {1} days past due, not escalated".format(_plural(len(long_late), "shipment"), LONG_PAST_DUE),
                         "The longest: {0} ({1}) at {2}, {3:,} days.".format(worst["awb"], worst["bu"], worst["step"], worst["overdue"]),
                         "/clearance#flag=pastdue", "clearance.view", count=len(long_late), due=worst.get("due")))
    correction = [r for r in rows if r.get("status") == "RequiredCorrection"]
    if correction:
        out.append(_task("clr:correction", "Clearance", "correction", "open", 50, "amber",
                         "{0} marked Correction required in eHub".format(_plural(len(correction), "shipment")),
                         "Fix them in eHub; the next export shows the change.",
                         "/clearance#status=RequiredCorrection", "clearance.view", count=len(correction)))
    # The same rule as Mail (clearance.draft_mail): the exact name, with an email.
    on_file = {name for name, c in ((contacts or {}).get("agents") or {}).items()
               if str((c or {}).get("email") or "").strip()}
    no_contact = sorted({r["agent"] for r in rows if r.get("agent") and clearance.past_due(r)
                         and r["agent"] not in on_file})
    if no_contact:
        out.append(_task("clr:contacts", "Clearance", "contacts", "open", 20, "grey",
                         "Add contacts for {0} with past-due shipments".format(_plural(len(no_contact), "clearing agent")),
                         "Mail and Call need an address on file: " + _names(no_contact),
                         "/clearance#contacts=1", "clearance.contacts", count=len(no_contact)))
    asof = data.get("asof")
    try:
        age = (today - _dt.date.fromisoformat(str(asof)[:10])).days
    except (TypeError, ValueError):
        age = None
    if age is not None and age > EXPORT_OLD:
        out.append(_task("clr:export", "Clearance", "export_old", "open", 10, "grey", "Load a newer eHub export",
                         "The data on Clearance is from {0}, {1} ago.".format(asof, _plural(age, "day")),
                         "/clearance", "clearance.upload"))
    return out


def build(can, state=None, today=None, now=None):
    now = now or _dt.datetime.now()
    today = today or now.date()
    now_epoch = time.mktime(now.timetuple())
    items = []
    if can("dashboard.view"):
        items += _human_items(state, now_epoch, now) + _run_items(state)
    if can("po.view"):
        items += _po_items()
    if can("clearance.view"):
        items += _clearance_items(today, now)
    items = [dict((k, v) for k, v in i.items() if k != "permission") for i in items if can(i["permission"])]
    # Outstanding: highest priority first, then the earliest due date. Completed last, newest first.
    todo = sorted((i for i in items if i["state"] != "completed"),
                  key=lambda i: (-i["priority"], str(i["due"] or "9999"), i["title"]))
    done = sorted((i for i in items if i["state"] == "completed"),
                  key=lambda i: str(i["at"] or "").replace(" ", "T")[:19], reverse=True)
    items = todo + done
    by_state = {s: sum(1 for i in items if i["state"] == s) for s in STATES}
    outstanding = len(items) - by_state["completed"]
    return {"items": items, "outstanding": outstanding, "by_state": by_state,
            # The number that needs action (red and amber), as before.
            "count": sum(1 for i in items if i["state"] != "completed" and i["level"] != "grey")}
