"""
Every stage of a PO job, with its status, its evidence and its source —
derived from the job record alone, so the drawer, ATLAS and the probe
report cannot disagree.

    stages(record) -> [{"stage", "label", "status", "evidence", "source"}]

    status   OK · FAILED · STOPPED (a rule ended the job here, by design) ·
             BLOCKED · WAITING · NOT_RUN
    source   REAL or TEST — the job's provenance, the same for every stage

A stage is OK only with its evidence present: a found row with its status, a
Manage URL, a file name, an identifier, a SHA-256, read-back cells, a Graph
message id, a Sent Items confirmation. Nothing is inferred to fill a gap.
"""

ORDER = (
    ("ehub_record", "eHub record"), ("clearance", "Clearance status"),
    ("manage", "Manage opened"), ("documents", "Documents section"),
    ("bill_entry", "Bill Entry document"), ("identifier", "PO identifier"),
    ("pdf", "PDF retrieved"), ("fields", "Fields extracted"),
    ("validation", "Validated against eHub"), ("template", "Approved template"),
    ("output", "Output saved"), ("email", "Email sent (Graph)"),
    ("verified", "Email verified (Sent Items)"),
)


def _trail_step(trail, name):
    return next((s for s in (trail or {}).get("steps") or [] if s.get("step") == name), None)


def stages(record):
    trail = record.get("discovery") or {}
    prov = record.get("provenance") or {}
    source = prov.get("source") or "UNKNOWN"
    state = record.get("state")
    out = {}

    def put(key, status, evidence=None):
        out[key] = {"status": status, "evidence": evidence}

    row = _trail_step(trail, "ehub_record")
    if row:
        put("ehub_record", "OK" if row["ok"] else "STOPPED",
            {"bol_awb": row.get("bol_awb"), "view": row.get("view"), "page": row.get("table_page"),
             "list_url": trail.get("list_url")} if row["ok"] else
            {"reason": (record.get("failure") or {}).get("detail")})
    clearance = trail.get("clearance")
    if clearance:
        put("clearance", "OK" if clearance.get("ok") else "STOPPED",
            {"found": clearance.get("found"), "required": clearance.get("required")})
    manage = _trail_step(trail, "manage")
    if manage:
        put("manage", "OK" if manage["ok"] else "FAILED",
            {"url": manage.get("url")} if manage["ok"] else {"error": manage.get("error")})
    docs = trail.get("documents")
    if docs:
        put("documents", "OK" if docs.get("found") else "FAILED",
            {"scope": docs.get("scope"), "files": docs.get("entries")})
    bill = trail.get("bill_entry")
    if bill:
        if bill.get("selected"):
            put("bill_entry", "OK", {"filename": bill["selected"], "rule": bill.get("rule"),
                                     "candidates": [c["name"] for c in bill.get("candidates") or []]})
        else:
            put("bill_entry", "STOPPED" if bill.get("candidates") else "FAILED",
                {"reason": bill.get("rule") or "no document whose name starts with 'Bill Entry'",
                 "candidates": [c["name"] for c in bill.get("candidates") or []]})
    if record.get("identifier"):
        put("identifier", "OK", {"identifier": record["identifier"],
                                 "from": (bill or {}).get("selected")})
    doc = record.get("document") or {}
    if doc.get("sha256"):
        put("pdf", "OK", {"filename": doc.get("filename"), "bytes": doc.get("bytes"),
                          "sha256": doc.get("sha256"), "url": doc.get("url"),
                          "method": doc.get("method"), "retrieved_at": doc.get("retrieved_at")})
    elif _trail_step(trail, "download") and not _trail_step(trail, "download")["ok"]:
        put("pdf", "FAILED", {"error": _trail_step(trail, "download").get("error")})
    fields = record.get("fields")
    if fields:
        found = [n for n, f in fields.items() if f.get("status") == "FOUND"]
        put("fields", "OK" if state != "EXTRACTION_FAILED" else "FAILED",
            {"found": found, "missing": [n for n, f in fields.items() if f.get("status") == "MISSING"],
             "ambiguous": [n for n, f in fields.items() if f.get("status") == "AMBIGUOUS"]})
    elif state in ("PDF_UNREADABLE", "EXTRACTION_FAILED"):
        put("fields", "FAILED", {"error": (record.get("failure") or {}).get("detail")})
    validation = record.get("validation")
    if validation:
        put("validation", "OK" if validation.get("passed") else "FAILED",
            {"checks": [{"check": c["label"], "pdf": c.get("pdf"), "ehub": c.get("hub"),
                         "status": c["status"]} for c in validation.get("checks") or []
                        if c["name"].startswith("hub:") or c.get("blocking")],
             "reasons": validation.get("reasons")})
    output = record.get("output") or {}
    if output.get("verified"):
        put("template", "OK", {"template": output.get("template_version"),
                               "template_sha256": output.get("template_sha256"),
                               "cells_read_back": len(output.get("cells") or {})})
        put("output", "OK", {"filename": output.get("filename"), "sha256": output.get("sha256"),
                             "bytes": output.get("bytes")})
    elif state == "TEMPLATE_FAILED":
        put("template", "FAILED", {"error": (record.get("failure") or {}).get("detail")})
    email = record.get("email") or {}
    if state in ("EMAIL_SENT", "EMAIL_CONFIRMED"):
        put("email", "OK", {"recipient": email.get("recipient"), "graph_status": email.get("graph_status"),
                            "internet_message_id": email.get("internet_message_id"),
                            "sent_at": email.get("sent_at")})
    elif state == "EMAIL_FAILED":
        put("email", "FAILED", {"error": email.get("error")})
    elif email.get("status") == "BLOCKED":
        put("email", "BLOCKED", {"reasons": email.get("reasons")})
    elif state == "EMAIL_PREPARED":
        put("email", "WAITING", {"recipient": email.get("recipient"), "subject": email.get("subject")})
    if state == "EMAIL_CONFIRMED":
        put("verified", "OK", {"confirmed_at": email.get("confirmed_at"),
                               "confirmation": email.get("confirmation")})
    elif state == "EMAIL_SENT":
        put("verified", "WAITING", {"confirmation": email.get("confirmation")})
    return [{"stage": key, "label": label, "status": (out.get(key) or {}).get("status", "NOT_RUN"),
             "evidence": (out.get(key) or {}).get("evidence"), "source": source}
            for key, label in ORDER]
