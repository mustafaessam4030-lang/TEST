"""
The audit trail: who did what, to what, in which run, and how it ended.

Append-only from the application's side — there is no update or delete path
in this module or anywhere in the app. Metadata is filtered before it is
written: anything that looks like a credential, a token, a password or a
verification code is dropped by name, and every value is cut short. The
remote browser session's keystrokes never reach this module at all.
"""

import re
import uuid
from datetime import datetime, timezone

from .db import dumps, loads, now

ACTIONS = (
    "LOGIN", "LOGOUT", "LOGIN_FAILED", "SESSION_EXPIRED",
    "START_RUN", "STOP_RUN", "PAUSE_RUN", "RESUME_RUN", "REPROCESS",
    "OPEN_HUMAN_ACTION", "CONTINUE_HUMAN_ACTION", "RELEASE_HUMAN_ACTION",
    "HUMAN_SESSION_VIEW",
    "APPROVE_PROPOSAL", "REJECT_PROPOSAL",
    "CREATE_USER", "CHANGE_ROLE", "DISABLE_USER", "ENABLE_USER",
    "RESET_ACCESS", "REVOKE_SESSIONS", "SET_PASSWORD",
    "VIEW_EVIDENCE", "UPLOAD_EVIDENCE",
    "SYSTEM_SETTING_CHANGED", "WORKER_REGISTERED", "WORKER_DISCONNECTED",
    "WORKER_RECONNECTED", "RUN_RECONCILED", "ACCESS_DENIED",
    # A worker's report of what it observed in the real eHub, with the level
    # the control plane gave it.
    "WORKER_OBSERVATION",
    # PO Automation: who started it, what the document and validation said,
    # the template, the recipient and what Microsoft 365 answered.
    "PO_PROCESS_STARTED", "PO_RUN_STARTED", "PO_PROCESSED", "PO_EMAIL_SENT", "PO_EMAIL_CONFIRMED",
    "PO_EMAIL_FAILED", "PO_EMAIL_BLOCKED", "PO_RESEND_AUTHORIZED", "PO_OUTPUT_DOWNLOADED",
    "PO_REVIEW_SUPPLIED", "PO_EMAIL_UNKNOWN",
)

# Never stored, whatever a caller passes.
_SECRET_KEY = re.compile(r"pass|secret|token|code|captcha|otp|cookie|csrf|"
                         r"credential|authorization|key", re.I)
_MAX_VALUE = 240


def _clean(value, depth=0):
    if depth > 3:
        return None
    if isinstance(value, dict):
        return {str(k)[:40]: _clean(v, depth + 1) for k, v in list(value.items())[:30]
                if not _SECRET_KEY.search(str(k))}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:20]]
    if isinstance(value, str):
        return value[:_MAX_VALUE]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:_MAX_VALUE]


class Audit(object):
    def __init__(self, db):
        self.db = db

    def record(self, action, result="SUCCESS", user=None, target_type=None,
               target_id=None, run_id=None, ip=None, metadata=None, conn=None):
        if action not in ACTIONS:
            raise ValueError("unknown audit action {0}".format(action))
        ts = now()
        row = {
            "audit_id": uuid.uuid4().hex, "ts": ts,
            "at": datetime.fromtimestamp(ts, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"),
            "user_id": (user or {}).get("user_id"),
            "user_email": (user or {}).get("work_email"),
            "action": action, "target_type": target_type,
            "target_id": None if target_id is None else str(target_id)[:120],
            "run_id": None if run_id is None else str(run_id)[:64],
            "result": str(result)[:40], "ip": (ip or None) and str(ip)[:64],
            "metadata": dumps(_clean(metadata or {})),
        }
        self.db.execute(
            "INSERT INTO audit (audit_id, ts, at, user_id, user_email, action, "
            "target_type, target_id, run_id, result, ip, metadata) VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [row[k] for k in ("audit_id", "ts", "at", "user_id", "user_email",
                              "action", "target_type", "target_id", "run_id",
                              "result", "ip", "metadata")], conn)
        return row

    def list(self, limit=200, before=None, action=None, user_email=None, run_id=None):
        clauses, params = [], []
        if before:
            clauses.append("ts < ?")
            params.append(float(before))
        if action:
            clauses.append("action = ?")
            params.append(action)
        if user_email:
            clauses.append("user_email = ?")
            params.append(user_email.lower())
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.db.all("SELECT * FROM audit{0} ORDER BY ts DESC LIMIT {1}".format(
            where, max(1, min(int(limit), 1000))), params)
        for row in rows:
            row["metadata"] = loads(row.get("metadata"))
        return rows
