"""
The missions store: missions, their tasks, evidence, hypotheses, events,
consent and declines, in one SQLite file in the intelligence folder
(ATLAS_INTEL_DIR/missions.db, WAL). It survives a restart: the engine picks
up unfinished work from it (engine.recover()).

Every text is redacted on the way in by intelligence/store.py's rules; a
field name that could carry a secret is refused. Standard library only.
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from .. import store as _store

_lock = threading.RLock()
_conn = {"path": None, "db": None}

SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY, fingerprint TEXT, title TEXT, objective TEXT, question TEXT,
  level TEXT, severity TEXT, template TEXT, scope TEXT, profile TEXT, status TEXT, phase TEXT,
  priority REAL, created_at REAL, started_at REAL, ended_at REAL, created_by TEXT, approved_by TEXT,
  approved_at REAL, plan TEXT, plan_hash TEXT, budget_tokens INTEGER, reasons TEXT, report TEXT,
  grade TEXT, drill INTEGER DEFAULT 0, escalation TEXT, origin TEXT
);
CREATE TABLE IF NOT EXISTS tasks (
  mission_id TEXT, id TEXT, objective TEXT, specialist TEXT, needs TEXT, depends_on TEXT,
  priority REAL, status TEXT, timeout_s REAL, idempotent INTEGER, attempts INTEGER DEFAULT 0,
  model TEXT, started_at REAL, ended_at REAL, error TEXT, summary TEXT, evidence_required TEXT,
  completion TEXT, output TEXT, PRIMARY KEY (mission_id, id)
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, mission_id TEXT, task_id TEXT, claim TEXT, class TEXT, source TEXT,
  record TEXT, captured_at REAL, freshness TEXT, agent TEXT, model TEXT, verification TEXT,
  basis TEXT, identifiers TEXT, data TEXT, status TEXT DEFAULT 'kept'
);
CREATE TABLE IF NOT EXISTS hypotheses (
  mission_id TEXT, id TEXT, label TEXT, status TEXT, why TEXT, support TEXT, against TEXT,
  needed TEXT, origin TEXT, PRIMARY KEY (mission_id, id)
);
CREATE TABLE IF NOT EXISTS events (
  mission_id TEXT, at REAL, kind TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS declines (
  fingerprint TEXT PRIMARY KEY, at REAL, by TEXT
);
CREATE INDEX IF NOT EXISTS ev_m ON evidence (mission_id);
CREATE INDEX IF NOT EXISTS t_m ON tasks (mission_id);
CREATE INDEX IF NOT EXISTS e_m ON events (mission_id);
"""

JSON_COLS = {"scope", "plan", "reasons", "report", "depends_on", "needs", "evidence_required",
             "identifiers", "data", "support", "against", "needed", "escalation", "output"}


def path():
    base = Path(os.environ.get("ATLAS_INTEL_DIR") or
                Path(__file__).resolve().parent.parent.parent / "ml" / "data" / "intelligence")
    base.mkdir(parents=True, exist_ok=True)
    return base / "missions.db"


def db():
    p = str(path())
    with _lock:
        if _conn["path"] != p or _conn["db"] is None:
            c = sqlite3.connect(p, check_same_thread=False, timeout=15, isolation_level=None)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            _conn.update(path=p, db=c)
        return _conn["db"]


def reset_for_tests():
    with _lock:
        if _conn["db"] is not None:
            try:
                _conn["db"].close()
            except Exception:
                pass
        _conn.update(path=None, db=None)


def _clean(value):
    """Redacted text; nested structures redacted leaf by leaf."""
    if isinstance(value, str):
        return _store.redact(value, limit=6000)
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if _store.FORBIDDEN_KEYS.search(str(k)):
                continue
            out[k] = _clean(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _enc(col, value):
    if col in JSON_COLS:
        return json.dumps(_clean(value), default=str)
    return _clean(value)


def _dec(row):
    if row is None:
        return None
    out = dict(row)
    for k in list(out):
        if k in JSON_COLS and isinstance(out[k], str):
            try:
                out[k] = json.loads(out[k])
            except ValueError:
                pass
    return out


def insert(table, **fields):
    cols = list(fields)
    with _lock:
        db().execute("INSERT OR REPLACE INTO {0} ({1}) VALUES ({2})".format(
            table, ",".join(cols), ",".join("?" * len(cols))), [_enc(c, fields[c]) for c in cols])


def update(table, where, **fields):
    if not fields:
        return
    sets = ",".join("{0}=?".format(c) for c in fields)
    keys = list(where)
    with _lock:
        db().execute("UPDATE {0} SET {1} WHERE {2}".format(table, sets, " AND ".join("{0}=?".format(k) for k in keys)),
                     [_enc(c, fields[c]) for c in fields] + [where[k] for k in keys])


def one(sql, *args):
    with _lock:
        return _dec(db().execute(sql, args).fetchone())


def many(sql, *args):
    with _lock:
        return [_dec(r) for r in db().execute(sql, args).fetchall()]


def event(mission_id, kind, text):
    insert("events", mission_id=mission_id, at=time.time(), kind=kind, text=str(text)[:600])
