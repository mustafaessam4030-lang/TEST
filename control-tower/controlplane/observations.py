"""
What the Windows worker observed in the real eHub — received, classified,
kept.

The control plane has no route to eHub and makes no request to it. What it
knows about the real eHub is only what a registered worker reports on the
authenticated worker channel (POST /worker/v1/observations), and the level
of each report — TEST · SIMULATED · REAL OBSERVED · REAL VERIFIED · BLOCKED
— is computed here from the evidence (intelligence/verification.classify),
never taken from the level the report claims. The worker id is the one the
token authenticated, never the one in the payload.
"""

import secrets

from intelligence import verification as V

from .db import dumps, loads, now

MAX_TEXT = 400
_SECRET = ("pass", "secret", "token", "credential", "authorization", "cookie", "captcha")


def _clean(value, depth=0):
    """Bounded, and without anything named like a secret."""
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {str(k)[:60]: _clean(v, depth + 1) for k, v in list(value.items())[:80]
                if not any(s in str(k).lower() for s in _SECRET)}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth + 1) for v in list(value)[:60]]
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:MAX_TEXT]


class Observations(object):
    def __init__(self, db, audit):
        self.db, self.audit = db, audit

    def accept(self, worker, payload):
        """Store one worker report; returns the public record."""
        data = _clean(payload if isinstance(payload, dict) else {})
        kind = data.get("kind") if data.get("kind") in V.KINDS else "unknown"
        data["kind"] = kind
        # Decided here, from the channel and the evidence.
        level, reasons = V.classify(data, "worker")
        claimed = data.pop("level_here", None)
        data.pop("level_reasons", None)
        shipment = data.get("shipment") or {}
        record = {
            "observation_id": "obs_" + secrets.token_hex(8),
            "worker_id": worker["worker_id"], "worker_name": worker.get("name"),
            "run_id": str(data.get("run_id") or "")[:64] or None,
            "kind": kind, "level": level, "reasons": reasons, "claimed_level": claimed,
            "received_at": now(), "observed_at": data.get("observed_at"),
            "environment": dict(data.get("environment") or {}, channel="worker",
                                authenticated_worker=worker["worker_id"]),
            "ehub_host": data.get("ehub_host"),
            "page": data.get("page") or {},
            "navigation": [{k: s.get(k) for k in ("stage", "ok", "category", "error")}
                           for s in data.get("stages") or [] if isinstance(s, dict)],
            "shipment_reference": shipment.get("reference"),
            "shipment_status": shipment.get("status"),
            "real_browser": (data.get("browser") or {}).get("real") is True,
            "browser": data.get("browser") or {},
            "blocked_reason": data.get("blocked_reason"),
        }
        if kind == "eta-write":
            record.update(carrier_result=data.get("carrier_result"),
                          writes=data.get("writes") or [], read_backs=data.get("read_backs") or [],
                          shipment_outcome=data.get("shipment_outcome"))
        self.db.execute(
            "INSERT INTO observations (observation_id, worker_id, run_id, kind, level, "
            "claimed_level, received_at, observed_at, reference, status, ehub_host, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record["observation_id"], record["worker_id"], record["run_id"], kind, level,
             claimed, record["received_at"], record["observed_at"],
             record["shipment_reference"], record["shipment_status"], record["ehub_host"],
             dumps(dict(record, evidence=data))))
        self.audit.record("WORKER_OBSERVATION", result="SUCCESS" if level != V.BLOCKED
                          else "FAILURE", target_type="observation",
                          target_id=record["observation_id"], run_id=record["run_id"],
                          metadata={"worker": worker["worker_id"], "kind": kind, "level": level,
                                    "claimed": claimed,
                                    "shipment": record["shipment_reference"],
                                    "status": record["shipment_status"]})
        return record

    def recent(self, limit=30):
        rows = self.db.all("SELECT data FROM observations ORDER BY received_at DESC LIMIT ?",
                           (max(1, min(200, int(limit))),))
        out = []
        for row in rows:
            record = loads(row["data"], {})
            record.pop("evidence", None)
            out.append(record)
        return out

    def get(self, observation_id):
        row = self.db.one("SELECT data FROM observations WHERE observation_id = ?",
                          (observation_id,))
        return loads(row["data"], {}) if row else None

    def latest(self):
        """The most recent worker report, for the health bar; None before any."""
        rows = self.recent(1)
        if not rows:
            return None
        r = rows[0]
        return {"level": r["level"], "kind": r["kind"], "at": r["received_at"],
                "worker_id": r["worker_id"], "reference": r.get("shipment_reference"),
                "status": r.get("shipment_status"), "reason": "; ".join(r.get("reasons") or [])}
