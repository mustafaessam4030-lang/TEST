"""
To Do: one list of what needs a person, across the platform.

Every item is counted from a record the platform already keeps: the run
(shipments waiting on Human Action, failed shipments), PO Automation (jobs
needing review, failed jobs) and the Clearance export (open escalations,
shipments long past due with nobody escalating, shipments eHub marks for
correction, agents with past-due shipments and no contact on file, an old
export). Nothing is estimated, and each person sees only the items their
access level lets them open. An item links to where it is dealt with.

    build(can, state) -> {"items": [...], "count": n}

`can(permission)` says whether the person may see something; `state` is the
run state the dashboard shows. Levels: "red" (act now), "amber" (soon),
"grey" (housekeeping).
"""

import datetime as _dt
from urllib.parse import quote

LEVELS = {"red": 0, "amber": 1, "grey": 2}
LONG_PAST_DUE = 30                      # days past a step's due date
EXPORT_OLD = 3                          # days since the export's data date


def _plural(n, one, many=None):
    return "{0:,} {1}".format(n, one if n == 1 else (many or one + "s"))


def _names(values, limit=3):
    values = [v for v in values if v]
    shown = ", ".join(values[:limit])
    return shown + (" and {0} more".format(len(values) - limit) if len(values) > limit else "")


def _run_items(state):
    out = []
    shipments = (state or {}).get("shipments") or []
    waiting = [s for s in shipments if s.get("state") == "waiting_for_human"]
    if waiting:
        out.append({"source": "Human Action", "level": "red", "count": len(waiting),
                    "title": "{0} waiting for a person".format(_plural(len(waiting), "shipment")),
                    "detail": "Open Human Action to continue: " + _names([s.get("reference") for s in waiting]),
                    "href": "/#human", "permission": "human.act"})
    failed = [s for s in shipments if s.get("state") == "failed"]
    if failed:
        out.append({"source": "Run", "level": "amber", "count": len(failed),
                    "title": "{0} failed in this run".format(_plural(len(failed), "shipment")),
                    "detail": "Ask ATLAS why, or look at them in Shipments: " +
                              _names([s.get("reference") for s in failed]),
                    "href": "/#shipments", "permission": "runs.view"})
    return out


def _po_items():
    try:
        from po import service as po_service
        jobs = po_service.local().summary().get("jobs") or []
    except Exception:
        return []
    latest = {}
    for job in jobs:                     # newest first: a document's latest job counts (as the PO page)
        latest.setdefault((job.get("doctype"), job.get("reference") or job.get("po_id")), job)
    review = [j for j in latest.values() if j.get("kpi") == "validation_required"]
    failed = [j for j in latest.values() if j.get("kpi") == "failed"]
    out = []
    if review:
        out.append({"source": "PO Automation", "level": "red", "count": len(review),
                    "title": "{0} need a person's review".format(_plural(len(review), "PO job")),
                    "detail": _names([j.get("reference") for j in review]),
                    "href": "/#po", "permission": "po.view"})
    if failed:
        out.append({"source": "PO Automation", "level": "amber", "count": len(failed),
                    "title": "{0} failed".format(_plural(len(failed), "PO job")),
                    "detail": _names(["{0} ({1})".format(j.get("reference"), j.get("label") or j.get("state"))
                                      for j in failed]),
                    "href": "/#po", "permission": "po.view"})
    return out


def _clearance_items(today=None):
    try:
        from dashboard import clearance
        data = clearance.load()
        if not data:
            return []
        rows = clearance.records(data)
        escalations = [e for e in clearance.escalations() if e.get("status") == "open"]
        contacts = clearance.contacts()
    except Exception:
        return []
    out = []
    for e in sorted(escalations, key=lambda e: e.get("at") or "", reverse=True)[:5]:
        out.append({"source": "Clearance", "level": "red", "count": 1,
                    "title": "Escalated: {0} ({1})".format(e.get("awb"), e.get("bu")),
                    "detail": "{0}{1} · by {2} on {3}".format(
                        e.get("step") or "", ", {0:,} days past due".format(e["overdue"])
                        if isinstance(e.get("overdue"), int) and e["overdue"] > 0 else "",
                        e.get("by"), str(e.get("at") or "")[:10]),
                    "href": "/clearance#q={0}".format(quote(str(e.get("awb") or ""))),
                    "permission": "clearance.view"})
    if len(escalations) > 5:
        out.append({"source": "Clearance", "level": "red", "count": len(escalations) - 5,
                    "title": "{0} more open".format(_plural(len(escalations) - 5, "escalation")),
                    "detail": "All of them are under Shipment Alerts.", "href": "/clearance#flag=escalated",
                    "permission": "clearance.view"})
    escalated = {(e.get("awb"), e.get("bu")) for e in escalations}
    long_late = [r for r in rows if clearance.past_due(r) and r["overdue"] >= LONG_PAST_DUE
                 and (r["awb"], r["bu"]) not in escalated]
    if long_late:
        worst = max(long_late, key=lambda r: r["overdue"])
        out.append({"source": "Clearance", "level": "amber", "count": len(long_late),
                    "title": "{0} over {1} days past due, not escalated".format(
                        _plural(len(long_late), "shipment"), LONG_PAST_DUE),
                    "detail": "The longest: {0} ({1}) at {2}, {3:,} days.".format(
                        worst["awb"], worst["bu"], worst["step"], worst["overdue"]),
                    "href": "/clearance#flag=pastdue", "permission": "clearance.view"})
    correction = [r for r in rows if r.get("status") == "RequiredCorrection"]
    if correction:
        out.append({"source": "Clearance", "level": "amber", "count": len(correction),
                    "title": "{0} marked Correction required in eHub".format(_plural(len(correction), "shipment")),
                    "detail": "Fix them in eHub; the next export shows the change.",
                    "href": "/clearance#status=RequiredCorrection", "permission": "clearance.view"})
    # The same rule as Mail (clearance.draft_mail): the exact name, with an email.
    on_file = {name for name, c in ((contacts or {}).get("agents") or {}).items()
               if str((c or {}).get("email") or "").strip()}
    no_contact = sorted({r["agent"] for r in rows if r.get("agent") and clearance.past_due(r)
                         and r["agent"] not in on_file})
    if no_contact:
        out.append({"source": "Clearance", "level": "grey", "count": len(no_contact),
                    "title": "Add contacts for {0} with past-due shipments".format(
                        _plural(len(no_contact), "clearing agent")),
                    "detail": "Mail and Call need an address on file: " + _names(no_contact),
                    "href": "/clearance#contacts=1", "permission": "clearance.contacts"})
    asof = data.get("asof")
    try:
        age = ((today or _dt.date.today()) - _dt.date.fromisoformat(str(asof)[:10])).days
    except (TypeError, ValueError):
        age = None
    if age is not None and age > EXPORT_OLD:
        out.append({"source": "Clearance", "level": "grey", "count": 1,
                    "title": "Load a newer eHub export",
                    "detail": "The data on Clearance is from {0}, {1} ago.".format(asof, _plural(age, "day")),
                    "href": "/clearance", "permission": "clearance.upload"})
    return out


def build(can, state=None, today=None):
    items = []
    if can("dashboard.view"):
        items += _run_items(state)
    if can("po.view"):
        items += _po_items()
    if can("clearance.view"):
        items += _clearance_items(today)
    items = [dict((k, v) for k, v in i.items() if k != "permission") for i in items if can(i["permission"])]
    items.sort(key=lambda i: (LEVELS[i["level"]], -i["count"]))
    return {"items": items, "count": sum(1 for i in items if i["level"] != "grey")}
