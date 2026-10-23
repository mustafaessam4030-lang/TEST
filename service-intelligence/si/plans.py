"""Recommendation plan persistence and audit trail.

Only these fixed statements write anything, and only to the SI_APP plan tables,
through the writer connection (its role has INSERT/UPDATE on those two tables
and nothing else). Company service records are never modified. Every change is
recorded in RECOMMENDATION_EVENTS with the actor, time, old and new values.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone

from .backends.base import Backend, BackendError

STATUSES = ("proposed", "reviewed", "approved", "in_progress", "completed", "rejected", "blocked")
TRANSITIONS = {
    "proposed": {"reviewed", "rejected", "blocked"},
    "reviewed": {"approved", "rejected", "blocked", "proposed"},
    "approved": {"in_progress", "blocked", "rejected"},
    "in_progress": {"completed", "blocked"},
    "blocked": {"reviewed", "in_progress", "rejected"},
    "completed": set(),
    "rejected": set(),
}
# Which application permission each target status needs.
STATUS_PERMISSION = {"reviewed": "plan.review", "proposed": "plan.review", "approved": "plan.approve",
                     "rejected": "plan.approve", "in_progress": "plan.approve", "completed": "plan.approve",
                     "blocked": "plan.approve"}
REC_ID = re.compile(r"^R-\d{1,9}$")
MAX_NOTE = 2000


class PlanError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


_clock_lock = threading.Lock()
_last = datetime.min


def _now() -> datetime:
    """UTC now, strictly increasing within the process so audit events keep their order."""
    global _last
    with _clock_lock:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        _last = now if now > _last else _last + timedelta(microseconds=1)
        return _last


class PlanStore:
    def __init__(self, backend: Backend, schema: str, *, data_source: str, create: bool = False):
        self.b = backend
        self.schema = schema
        self.data_source = data_source
        self.recs = f"{schema}.RECOMMENDATIONS"
        self.events = f"{schema}.RECOMMENDATION_EVENTS"
        self._lock = threading.Lock()
        if create:
            self._create_local()

    def _create_local(self) -> None:
        """Development/test only. Production tables come from sql/snowflake/02_app_schema.sql."""
        for ddl in (
            f"CREATE SEQUENCE IF NOT EXISTS {self.schema}.RECOMMENDATION_SEQ START 101",
            f"""CREATE TABLE IF NOT EXISTS {self.recs} (rec_id VARCHAR PRIMARY KEY, candidate_key VARCHAR, title VARCHAR,
                status VARCHAR, priority_score DOUBLE, priority_label VARCHAR, owner VARCHAR, created_at TIMESTAMP,
                created_by VARCHAR, updated_at TIMESTAMP, updated_by VARCHAR, snapshot_id VARCHAR,
                registry_version VARCHAR, data_source VARCHAR, payload VARCHAR, version INTEGER)""",
            f"""CREATE TABLE IF NOT EXISTS {self.events} (event_id VARCHAR PRIMARY KEY, rec_id VARCHAR, event_at TIMESTAMP,
                actor VARCHAR, action VARCHAR, from_status VARCHAR, to_status VARCHAR, note VARCHAR, detail VARCHAR)""",
        ):
            self.b.write(ddl, (), tag="plan_setup", native=True)

    def _next_id(self) -> str:
        if self.b.kind == "snowflake":
            res = self.b.write(f"SELECT {self.schema}.RECOMMENDATION_SEQ.NEXTVAL AS v", (), tag="plan_seq")
        else:
            res = self.b.write(f"SELECT nextval('{self.schema}.RECOMMENDATION_SEQ') AS v", (), tag="plan_seq", native=True)
        return f"R-{int(res.rows[0][0])}"

    def _event(self, rec_id, actor, action, from_status, to_status, note, detail=None) -> None:
        self.b.write(f"INSERT INTO {self.events} (event_id, rec_id, event_at, actor, action, from_status, to_status, note, detail) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, rec_id, _now(), actor, action, from_status, to_status, note,
                      json.dumps(detail or {}, default=str)), tag="plan_event")

    @staticmethod
    def _row(r: dict) -> dict:
        out = {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items() if k != "payload"}
        out["payload"] = json.loads(r["payload"]) if r.get("payload") else {}
        return out

    # -- reads -------------------------------------------------------------------------------------
    def list(self, status: str | None = None) -> list[dict]:
        sql = (f"SELECT rec_id, candidate_key, title, status, priority_score, priority_label, owner, created_at, "
               f"created_by, updated_at, updated_by, snapshot_id, registry_version, data_source, payload, version "
               f"FROM {self.recs}")
        params: tuple = ()
        if status:
            if status not in STATUSES:
                raise PlanError("Unknown status")
            sql += " WHERE status = ?"
            params = (status,)
        sql += " ORDER BY priority_score DESC, rec_id LIMIT 1000"
        return [self._row(r) for r in self.b.write(sql, params, tag="plan_read").records()]

    def get(self, rec_id: str) -> dict:
        if not REC_ID.match(rec_id or ""):
            raise PlanError("Recommendation ids look like R-101", 400)
        res = self.b.write(f"SELECT rec_id, candidate_key, title, status, priority_score, priority_label, owner, created_at, "
                           f"created_by, updated_at, updated_by, snapshot_id, registry_version, data_source, payload, version "
                           f"FROM {self.recs} WHERE rec_id = ?", (rec_id,), tag="plan_read")
        if not res.rows:
            raise PlanError(f"{rec_id} is not in the plan", 404)
        rec = self._row(res.records()[0])
        ev = self.b.write(f"SELECT event_id, event_at, actor, action, from_status, to_status, note, detail FROM {self.events} "
                          f"WHERE rec_id = ? ORDER BY event_at, event_id", (rec_id,), tag="plan_read")
        rec["history"] = [{**{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in e.items() if k != "detail"},
                           "detail": json.loads(e["detail"] or "{}")} for e in ev.records()]
        return rec

    def find_open_by_key(self, key: str) -> dict | None:
        res = self.b.write(f"SELECT rec_id, status FROM {self.recs} WHERE candidate_key = ? "
                           f"AND status NOT IN ('completed', 'rejected') ORDER BY rec_id LIMIT 1", (key,), tag="plan_read")
        return res.records()[0] if res.rows else None

    # -- writes ------------------------------------------------------------------------------------
    def add(self, candidate: dict, actor: str, note: str | None = None) -> dict:
        if not candidate.get("key") or not candidate.get("evidence"):
            raise PlanError("Only generated candidates with evidence can be added to the plan")
        note = (note or "")[:MAX_NOTE]
        with self._lock:
            existing = self.find_open_by_key(candidate["key"])
            if existing:
                raise PlanError(f"Already in the plan as {existing['rec_id']} ({existing['status']})", 409)
            rec_id = self._next_id()
            now = _now()
            payload = {**candidate, "rec_id": rec_id}
            self.b.write(f"INSERT INTO {self.recs} (rec_id, candidate_key, title, status, priority_score, priority_label, "
                         "owner, created_at, created_by, updated_at, updated_by, snapshot_id, registry_version, "
                         "data_source, payload, version) VALUES (?, ?, ?, 'proposed', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                         (rec_id, candidate["key"], candidate["title"], candidate["priority"]["score"],
                          candidate["priority"]["label"], now, actor, now, actor, candidate["snapshot"]["snapshot_id"],
                          candidate["snapshot"]["registry_version"], self.data_source, json.dumps(payload, default=str)),
                         tag="plan_add")
            self._event(rec_id, actor, "created", None, "proposed", note,
                        {"snapshot_id": candidate["snapshot"]["snapshot_id"], "priority": candidate["priority"]})
        return self.get(rec_id)

    def _update(self, rec_id: str, expected_version: int, sets: str, params: tuple) -> None:
        res = self.b.write(f"UPDATE {self.recs} SET {sets}, version = version + 1 WHERE rec_id = ? AND version = ?",
                           params + (rec_id, expected_version), tag="plan_update")
        changed = int(res.rows[0][0]) if res.rows and res.rows[0] and res.rows[0][0] is not None else 0
        if changed != 1:
            raise PlanError("This recommendation was changed by someone else; reload and try again", 409)

    def transition(self, rec_id: str, to_status: str, actor: str, permissions: set[str], expected_version: int,
                   note: str | None = None) -> dict:
        if to_status not in STATUSES:
            raise PlanError("Unknown status")
        if STATUS_PERMISSION[to_status] not in permissions:
            raise PlanError(f"You are not allowed to set status '{to_status}'", 403)
        note = (note or "")[:MAX_NOTE]
        if to_status in ("rejected", "blocked") and not note.strip():
            raise PlanError("A note is required when rejecting or blocking")
        with self._lock:
            rec = self.get(rec_id)
            if to_status not in TRANSITIONS[rec["status"]]:
                raise PlanError(f"Cannot move from '{rec['status']}' to '{to_status}'", 409)
            self._update(rec_id, expected_version, "status = ?, updated_at = ?, updated_by = ?", (to_status, _now(), actor))
            self._event(rec_id, actor, "status_changed", rec["status"], to_status, note)
        return self.get(rec_id)

    def assign(self, rec_id: str, owner: str, actor: str, permissions: set[str], expected_version: int,
               note: str | None = None) -> dict:
        if "plan.approve" not in permissions:
            raise PlanError("You are not allowed to assign owners", 403)
        owner = (owner or "").strip()
        if not owner or len(owner) > 200:
            raise PlanError("Owner must be 1–200 characters")
        with self._lock:
            rec = self.get(rec_id)
            if rec["status"] in ("completed", "rejected"):
                raise PlanError("Closed recommendations cannot be reassigned", 409)
            self._update(rec_id, expected_version, "owner = ?, updated_at = ?, updated_by = ?", (owner, _now(), actor))
            self._event(rec_id, actor, "assigned", rec["status"], rec["status"], (note or "")[:MAX_NOTE],
                        {"from_owner": rec.get("owner"), "to_owner": owner})
        return self.get(rec_id)

    def comment(self, rec_id: str, actor: str, permissions: set[str], note: str) -> dict:
        if "plan.review" not in permissions:
            raise PlanError("You are not allowed to comment", 403)
        note = (note or "").strip()[:MAX_NOTE]
        if not note:
            raise PlanError("Comment is empty")
        rec = self.get(rec_id)
        self._event(rec_id, actor, "commented", rec["status"], rec["status"], note)
        return self.get(rec_id)


def visible(payload: dict, scope: dict) -> bool:
    """A scoped user sees a plan item only if its evidence lies inside their scope."""
    if not scope:
        return True
    snap = payload.get("snapshot") or {}
    affected = payload.get("affected") or {}
    for dim, allowed in scope.items():
        allowed = set(allowed)
        bounded = [snap.get("scope", {}).get(dim), snap.get("filters", {}).get(dim)]
        if any(b and set(b) <= allowed for b in bounded):
            continue
        if affected.get("dimension") == dim and affected.get("segment") in allowed and any(bounded):
            continue
        return False
    return True


def safe_backend_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BackendError as exc:
        raise PlanError(f"Plan storage unavailable ({exc.kind})", 503) from None
