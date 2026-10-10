"""
One send, authorized by a named person, for one prepared PO email.

A PO email is sent only with a single-use authorization bound to the job,
its recipient, its subject and the exact attachment (SHA-256). It is used up
by the send it authorizes: the next PO email needs its own. Nothing else
(auto-send, the dashboard button, the API, ATLAS) can create one — only
grant(), called by the person on the machine that holds the Graph settings.

The test suite's stand-in mailbox (PO_ALLOW_TEST_SEND=1) is the one
exception, as for the provenance check.
"""

import json
from pathlib import Path

from . import store as S


def _path(store, po_id):
    safe = "".join(c for c in str(po_id) if c.isalnum() or c in "-_")
    return Path(store.folder) / "authorizations" / (safe + ".json")


def read(store, po_id):
    try:
        return json.loads(_path(store, po_id).read_text(encoding="utf-8"))
    except Exception:
        return None


def _bound(record):
    email, out = record.get("email") or {}, record.get("output") or {}
    return {"po_id": record["po_id"], "recipient": email.get("recipient"),
            "subject": email.get("subject"), "attachment": out.get("filename"),
            "attachment_sha256": out.get("sha256")}


def grant(store, record, recipient, by):
    """The named person authorizes THIS prepared email, once. -> (ok, reasons)."""
    reasons = []
    if not by or not str(by).strip():
        reasons.append("the authorizing person must be named")
    if record.get("state") != S.EMAIL_PREPARED:
        reasons.append("the email is not prepared (the job is {0})".format(
            S.LABELS.get(record.get("state"), record.get("state"))))
    bound = _bound(record)
    if bound["recipient"] != recipient:
        reasons.append("the prepared recipient is {0}, not {1}".format(bound["recipient"],
                                                                      recipient))
    previous = read(store, record["po_id"])
    if previous and previous.get("used_at"):
        reasons.append("this job's authorization was already used on {0} — a new send needs a "
                       "resend authorization".format(previous["used_at"]))
    if reasons:
        return False, reasons
    entry = dict(bound, by=str(by).strip(), at=S.now_iso(), used_at=None)
    path = _path(store, record["po_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    S.write_atomic(path, json.dumps(entry, indent=1))
    store.event(record, "SEND_AUTHORIZED", "email", "OK", by=entry["by"],
                recipient=entry["recipient"], attachment=entry["attachment"],
                attachment_sha256=entry["attachment_sha256"])
    return True, []


def problems(store, record):
    """Why this record has no valid authorization to send now ([] = it has one)."""
    entry = read(store, record["po_id"])
    if not entry:
        return ["no one has authorized this send (python -m po authorize-send)"]
    if entry.get("used_at"):
        return ["the authorization was already used on {0}".format(entry["used_at"])]
    bound = _bound(record)
    changed = [k for k in ("recipient", "subject", "attachment", "attachment_sha256")
               if entry.get(k) != bound[k]]
    if changed:
        return ["the email changed after it was authorized ({0})".format(", ".join(changed))]
    return []


def consume(store, record):
    """Use the authorization up — before the message is created."""
    entry = read(store, record["po_id"])
    if entry and not entry.get("used_at"):
        entry["used_at"] = S.now_iso()
        S.write_atomic(_path(store, record["po_id"]), json.dumps(entry, indent=1))
