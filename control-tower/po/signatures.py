"""
Who must sign the Duty Payment Request, and who has.

The roles are the template's own signature boxes. The official signing order
is NOT assumed: it is configured (PO_SIGNING_ORDER, roles separated by "|")
or shown as not configured. A role is SIGNED only with a recorded signature
carrying its evidence (who, when, the attachment's SHA-256); nothing here
creates one.
"""

import os

# The signature boxes printed on DUTY_REQUEST_V1 (B43, E43, B51, E51, F61, F68).
ROLES = ["Prepared By", "Authorised By", "Logistics Manager", "Account Sign",
         "1st Authorizer", "2nd Authorizer"]


def order():
    raw = os.environ.get("PO_SIGNING_ORDER") or ""
    names = [r.strip() for r in raw.split("|") if r.strip()]
    return [r for r in names if r in ROLES]


def status(record):
    configured = order()
    signed = {s.get("role"): s for s in (record.get("signatures") or [])
              if s.get("role") in ROLES and s.get("by") and s.get("at") and
              s.get("attachment_sha256") == (record.get("output") or {}).get("sha256")}
    roles = configured or ROLES
    rows = [{"role": r, "status": "SIGNED" if r in signed else "PENDING",
             "by": (signed.get(r) or {}).get("by"), "at": (signed.get(r) or {}).get("at")}
            for r in roles]
    return {"roles": rows, "signed": sum(1 for x in rows if x["status"] == "SIGNED"),
            "required": len(rows), "order_configured": bool(configured)}


def line(record):
    if not record.get("output"):
        return None
    s = status(record)
    return "✍ {0} of {1} signed · {2}".format(
        s["signed"], s["required"], "signing order configured" if s["order_configured"]
        else "official signing order NOT configured")
