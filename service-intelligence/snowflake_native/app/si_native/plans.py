"""Recommendation plan persistence in SERVICE_INTELLIGENCE.SI_APP, with an append-only audit trail.

Writes happen only through these functions, only for app users whose role allows the action, and
always with bind parameters. Nothing here changes operational systems.
"""
from __future__ import annotations

import json

SCHEMA = "SERVICE_INTELLIGENCE.SI_APP"
STATUSES = ("proposed", "reviewed", "approved", "in_progress", "completed", "rejected", "blocked")
ROLE_RANK = {"viewer": 0, "analyst": 1, "approver": 2, "admin": 3}
# (from, to) -> minimum app role
TRANSITIONS = {
    ("proposed", "reviewed"): "analyst",
    ("proposed", "rejected"): "approver",
    ("reviewed", "approved"): "approver",
    ("reviewed", "rejected"): "approver",
    ("approved", "in_progress"): "analyst",
    ("in_progress", "completed"): "analyst",
    ("blocked", "in_progress"): "approver",
}
for _s in ("proposed", "reviewed", "approved", "in_progress"):
    TRANSITIONS[(_s, "blocked")] = "approver"


class PlanError(ValueError):
    pass


def can(role: str, needed: str) -> bool:
    return ROLE_RANK.get(role, -1) >= ROLE_RANK[needed]


def allowed_moves(status: str, role: str) -> list[str]:
    return [to for (frm, to), need in TRANSITIONS.items() if frm == status and can(role, need)]


class PlanStore:
    def __init__(self, session):
        self.s = session

    def _q(self, sql: str, params: list) -> list[dict]:
        return [{k.lower(): v for k, v in r.as_dict().items()} for r in self.s.sql(sql, params=params).collect()]

    def app_role(self, email: str | None) -> str:
        if not email:
            return "viewer"
        rows = self._q(f"SELECT app_role FROM {SCHEMA}.APP_USERS WHERE email = ?", [email.lower()])
        role = rows[0]["app_role"] if rows else "viewer"
        return role if role in ROLE_RANK else "viewer"

    def list(self) -> list[dict]:
        return self._q(f"SELECT rec_id, title, priority, score, status, metric_id, owner, created_by, created_at, "
                       f"updated_at, version, data_as_of FROM {SCHEMA}.RECOMMENDATIONS ORDER BY created_at DESC LIMIT 200", [])

    def get(self, rec_id: str) -> dict | None:
        rows = self._q(f"SELECT * FROM {SCHEMA}.RECOMMENDATIONS WHERE rec_id = ?", [rec_id.upper()])
        if not rows:
            return None
        rec = rows[0]
        for k in ("segment", "evidence", "score_breakdown", "actions"):
            if isinstance(rec.get(k), str):
                rec[k] = json.loads(rec[k])
        rec["history"] = self._q(f"SELECT event_at, actor, action, from_status, to_status, note FROM "
                                 f"{SCHEMA}.RECOMMENDATION_EVENTS WHERE rec_id = ? ORDER BY event_at", [rec_id.upper()])
        return rec

    def save(self, cand: dict, user: str, role: str, registry_version: str) -> str:
        if not can(role, "analyst"):
            raise PlanError("Saving to the plan needs the analyst role or higher.")
        if cand["detector"].startswith("top_machine"):
            # the same machine re-appears on later days' lists: one open plan item per machine
            dup = self._q(f"SELECT rec_id FROM {SCHEMA}.RECOMMENDATIONS WHERE candidate_key = ? "
                          f"AND status NOT IN ('rejected', 'completed')", [cand["candidate_key"]])
        else:
            dup = self._q(f"SELECT rec_id FROM {SCHEMA}.RECOMMENDATIONS WHERE candidate_key = ? AND data_as_of = ? "
                          f"AND status NOT IN ('rejected')", [cand["candidate_key"], cand["data_as_of"]])
        if dup:
            raise PlanError(f"Already in the plan as {dup[0]['rec_id']}.")
        rec_id = "R-" + str(self._q(f"SELECT {SCHEMA}.RECOMMENDATION_SEQ.NEXTVAL AS n", [])[0]["n"])
        evidence = {"measured": cand["measured"], "queries": cand["evidence"]}
        self.s.sql(
            f"INSERT INTO {SCHEMA}.RECOMMENDATIONS (rec_id, detector, candidate_key, title, explanation, problem, metric_id, "
            "segment, evidence, priority, score, score_breakdown, expected_impact, actions, risks, confidence, owner, "
            "status, data_as_of, analysis_version, created_by) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, PARSE_JSON(?), PARSE_JSON(?), ?, ?, PARSE_JSON(?), ?, PARSE_JSON(?), ?, ?, ?, "
            "'proposed', ?::TIMESTAMP_NTZ, ?, ?",
            params=[rec_id, cand["detector"], cand["candidate_key"], cand["title"], cand["explanation"], cand["problem"],
                    cand["metric_id"], json.dumps(cand["segment"]), json.dumps(evidence, default=str), cand["priority"],
                    cand["score"], json.dumps(cand["score_breakdown"]), cand["expected_impact"], json.dumps(cand["actions"]),
                    cand["risks"], cand["confidence"], cand.get("owner"), cand["data_as_of"],
                    f"registry {registry_version}; {cand['detector']}", user]).collect()
        self._event(rec_id, user, "created", None, "proposed", None)
        return rec_id

    def move(self, rec_id: str, to: str, user: str, role: str, expected_version: int, note: str | None = None) -> None:
        rec = self.get(rec_id)
        if rec is None:
            raise PlanError(f"{rec_id} does not exist.")
        need = TRANSITIONS.get((rec["status"], to))
        if need is None:
            raise PlanError(f"{rec['status']} → {to} is not an allowed change.")
        if not can(role, need):
            raise PlanError(f"{rec['status']} → {to} needs the {need} role.")
        res = self.s.sql(
            f"UPDATE {SCHEMA}.RECOMMENDATIONS SET status = ?, version = version + 1, "
            "updated_at = CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ WHERE rec_id = ? AND version = ?",
            params=[to, rec_id, int(expected_version)]).collect()
        if not res or int(list(res[0].as_dict().values())[0]) != 1:
            raise PlanError("Someone else changed this recommendation; reload and try again.")
        self._event(rec_id, user, "status", rec["status"], to, note)

    def assign(self, rec_id: str, owner: str, user: str, role: str, expected_version: int) -> None:
        if not can(role, "approver"):
            raise PlanError("Assigning needs the approver role.")
        res = self.s.sql(f"UPDATE {SCHEMA}.RECOMMENDATIONS SET owner = ?, version = version + 1, "
                         "updated_at = CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ WHERE rec_id = ? AND version = ?",
                         params=[owner[:200], rec_id, int(expected_version)]).collect()
        if not res or int(list(res[0].as_dict().values())[0]) != 1:
            raise PlanError("Someone else changed this recommendation; reload and try again.")
        self._event(rec_id, user, "assign", None, None, owner[:200])

    def comment(self, rec_id: str, user: str, role: str, note: str) -> None:
        if not can(role, "analyst"):
            raise PlanError("Commenting needs the analyst role or higher.")
        self._event(rec_id, user, "comment", None, None, note[:2000])

    def _event(self, rec_id, user, action, frm, to, note) -> None:
        self.s.sql(f"INSERT INTO {SCHEMA}.RECOMMENDATION_EVENTS (rec_id, actor, action, from_status, to_status, note) "
                   "VALUES (?, ?, ?, ?, ?, ?)", params=[rec_id, user, action, frm, to, note]).collect()
