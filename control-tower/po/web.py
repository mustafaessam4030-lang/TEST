"""
The PO HTTP routes, once — served by the single-machine dashboard and the
remote control plane alike, so the two cannot drift apart.

    GET  /api/po                    queue, KPIs, configuration     po.view
    POST /api/po                    start a job                    po.process
    GET  /api/po/<id>               one job, with its events       po.view
    GET  /api/po/<id>/output        the generated document         po.view
    POST /api/po/<id>/send          send it                        po.send
                                    (authorize_resend: po.resend)
    POST /api/po/<id>/confirm       look for it in Sent Items again po.send
    POST /api/po/<id>/supply        a value review asked for (G4)  po.process
    GET  /api/po/quality            reliability metrics, from the  po.view
                                    recorded jobs only

handle() returns ("json", status, payload), ("file", status, (bytes, type,
name)) or ("forbidden", permission). The caller enforces the permission —
the control plane with its RBAC table and audit, the local dashboard with
its access key — and always on the server.
"""

import re
from pathlib import Path

ID = re.compile(r"^po-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}$")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def permission_for(method, route, body=None):
    if route == "/api/po":
        return "po.view" if method == "GET" else "po.process"
    parts = route.split("/")
    if len(parts) == 4:
        return "po.view"
    if len(parts) == 5:
        if parts[4] == "output":
            return "po.view"
        if parts[4] == "send":
            return "po.resend" if (body or {}).get("authorize_resend") else "po.send"
        if parts[4] == "confirm":
            return "po.send"
        if parts[4] == "supply":
            return "po.process"
    return None


def handle(service, method, route, body, actor, can):
    permission = permission_for(method, route, body)
    if permission is None:
        return "json", 404, {"error": "not_found"}
    if not can(permission):
        return "forbidden", permission, None
    if route == "/api/po":
        if method == "GET":
            return "json", 200, service.summary()
        if method == "POST":
            reference = str((body or {}).get("reference") or "").strip()
            if not reference and not (body or {}).get("discover"):
                return "json", 400, {"accepted": False, "message":
                                     "Give a BOL/AWB, or ask for the next Under Clearance record."}
            try:
                record = service.start(actor, reference,
                                       {k: v for k, v in (body or {}).items()
                                        if k not in ("reference", "discover")})
            except ValueError as error:
                return "json", 400, {"accepted": False, "message": str(error)}
            return "json", 200, {"accepted": True, "po_id": record["po_id"],
                                 "message": "Processing {0} in the background.".format(
                                     record["reference"] or "the next Under Clearance record")}
    if route == "/api/po/quality" and method == "GET":
        from . import quality
        return "json", 200, quality.metrics(service.store)
    parts = route.split("/")
    po_id = parts[3] if len(parts) > 3 else ""
    if not ID.match(po_id):
        return "json", 404, {"error": "not_found"}
    if len(parts) == 4 and method == "GET":
        detail = service.detail(po_id)
        return ("json", 200, detail) if detail else ("json", 404, {"error": "not_found"})
    action = parts[4] if len(parts) > 4 else None
    if action == "output" and method == "GET":
        record = service.store.get(po_id)
        out = (record or {}).get("output") or {}
        path = Path(out.get("path") or "")
        if not out.get("verified") or not path.is_file():
            return "json", 404, {"error": "not_found", "message": "No generated document."}
        return "file", 200, (path.read_bytes(), XLSX, out.get("filename") or path.name)
    if action == "send" and method == "POST":
        try:
            record, outcome, reasons = service.send(
                actor, po_id, authorize_resend=bool((body or {}).get("authorize_resend")),
                reason=str((body or {}).get("reason") or "")[:300] or None)
        except KeyError:
            return "json", 404, {"error": "not_found"}
        if outcome == "BLOCKED":
            return "json", 409, {"accepted": False, "outcome": "BLOCKED", "reasons": reasons,
                                 "message": "Email blocked: " + "; ".join(reasons)}
        return "json", 200, {"accepted": True, "outcome": outcome,
                             "message": "Sending through Microsoft 365…"}
    if action == "supply" and method == "POST":
        try:
            record, problems = service.supply(actor, po_id, {
                "invoice_no": str((body or {}).get("invoice_no") or "")[:60]})
        except KeyError:
            return "json", 404, {"error": "not_found"}
        if problems:
            return "json", 409, {"accepted": False, "message": "; ".join(problems)}
        return "json", 200, {"accepted": True, "state": record["state"],
                             "message": "Validated again: {0}.".format(record.get("label"))}
    if action == "confirm" and method == "POST":
        record = service.reconfirm(po_id)
        return "json", 200, {"ok": bool(record), "state": (record or {}).get("state")}
    return "json", 404, {"error": "not_found"}
