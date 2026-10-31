"""
One search across the platform: an AWB, BOL or PO reference, wherever it is.

Looks in the run's shipments, the Clearance export and the PO Automation
jobs, and returns each match with a link that opens it where it lives. A
query is compared without spaces, dashes or case, so "157-4956 8713" finds
157-49568713. Each source needs the permission to open it. Read-only.

    search(query, can, state) -> {"query": q, "groups": [{"source", "items": [...]}]}
"""

import re
from urllib.parse import quote

MIN = 3                          # characters (after normalising) before searching
LIMIT = 8                        # matches per source

STATE_WORDS = {"updated": "updated", "failed": "failed", "skipped": "skipped", "processing": "processing",
               "partial": "partly updated", "waiting_for_human": "waiting for a person",
               "human_timeout": "human timeout"}


def norm(value):
    return re.sub(r"[^0-9A-Z]", "", str(value or "").upper())


def _hit(nq, *values):
    return any(nq in norm(v) for v in values if v)


def _run(nq, state):
    out = []
    for s in (state or {}).get("shipments") or []:
        ref = s.get("reference")
        if not ref or not _hit(nq, ref):
            continue
        bits = [s.get("carrier"), STATE_WORDS.get(s.get("state"), s.get("state")),
                "ETA " + str(s["eta"]) if s.get("eta") else None, "ATA " + str(s["ata"]) if s.get("ata") else None]
        out.append({"title": str(ref), "detail": " · ".join(str(b) for b in bits if b),
                    "href": "/#shipments?ref=" + quote(str(ref), safe="")})
        if len(out) >= LIMIT:
            break
    return out


def _clearance(nq):
    try:
        from dashboard import clearance
        rows = clearance.records()
    except Exception:
        return []
    out = []
    for r in rows:
        if not r.get("awb") or not _hit(nq, r["awb"]):
            continue
        where = "completed" if not clearance.is_open(r) else (
            "{0:,} days past due".format(r["overdue"]) if clearance.past_due(r) else "open")
        bits = [r.get("bu"), r.get("mode"), r.get("step"), where]
        out.append({"title": r["awb"], "detail": " · ".join(str(b) for b in bits if b),
                    "href": "/clearance#q=" + quote(str(r["awb"]), safe="")})
        if len(out) >= LIMIT:
            break
    return out


def _po(nq):
    try:
        from po import service as po_service
        jobs = po_service.local().summary().get("jobs") or []
    except Exception:
        return []
    out, seen = [], set()
    for j in jobs:
        if not _hit(nq, j.get("reference"), j.get("number"), j.get("declaration"), j.get("identifier")):
            continue
        key = j.get("po_id")
        if key in seen or not re.match(r"^[0-9a-z-]{1,40}$", str(key or "")):
            continue
        seen.add(key)
        bits = [j.get("label") or j.get("state"), "No. " + str(j["number"]) if j.get("number") else None]
        out.append({"title": str(j.get("reference")), "detail": " · ".join(str(b) for b in bits if b),
                    "href": "/#po?po=" + key})
        if len(out) >= LIMIT:
            break
    return out


def search(query, can, state=None):
    q = str(query or "")[:80]
    nq = norm(q)
    groups = []
    if len(nq) >= MIN:
        if can("dashboard.view"):
            groups.append({"source": "Shipments (this run)", "items": _run(nq, state)})
        if can("clearance.view"):
            groups.append({"source": "Clearance (eHub export)", "items": _clearance(nq)})
        if can("po.view"):
            groups.append({"source": "PO Automation", "items": _po(nq)})
    return {"query": q, "groups": [g for g in groups if g["items"]], "searched": len(nq) >= MIN}
