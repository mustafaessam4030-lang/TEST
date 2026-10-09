"""
The control plane's database: users, sessions, the audit trail, workers,
runs, the latest state of each run, worker commands and Human Action claims.

One schema, two engines:

    sqlite:///C:/ata/ata.db               local development, a single VM
    postgresql://user:pass@host/db        Azure Database for PostgreSQL

The SQL is written once, in the subset both engines share — TEXT, INTEGER,
REAL, ON CONFLICT ... DO UPDATE — with `?` placeholders that are rewritten for
psycopg. Every multi-step change that must not interleave with another
request (claiming a Human Action, starting a run) goes through `tx()`.
"""

import json
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY, value TEXT)""",
    """CREATE TABLE IF NOT EXISTS users (
        user_id TEXT PRIMARY KEY,
        work_email TEXT NOT NULL UNIQUE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        password_hash TEXT,
        auth_source TEXT NOT NULL DEFAULT 'local',
        entra_oid TEXT,
        must_set_password INTEGER NOT NULL DEFAULT 0,
        failed_logins INTEGER NOT NULL DEFAULT 0,
        locked_until DOUBLE PRECISION,
        prefs TEXT NOT NULL DEFAULT '{}',
        created_at DOUBLE PRECISION NOT NULL,
        created_by TEXT,
        updated_at DOUBLE PRECISION,
        updated_by TEXT,
        last_login DOUBLE PRECISION)""",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        csrf TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        last_seen DOUBLE PRECISION NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL,
        ip TEXT, user_agent TEXT,
        auth_method TEXT,
        revoked INTEGER NOT NULL DEFAULT 0)""",
    "CREATE INDEX IF NOT EXISTS sessions_user ON sessions (user_id)",
    """CREATE TABLE IF NOT EXISTS tokens (
        token_hash TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        purpose TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        created_by TEXT,
        expires_at DOUBLE PRECISION NOT NULL,
        used_at DOUBLE PRECISION)""",
    """CREATE TABLE IF NOT EXISTS sso_states (
        state_hash TEXT PRIMARY KEY,
        nonce TEXT NOT NULL,
        verifier TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        next_path TEXT)""",
    """CREATE TABLE IF NOT EXISTS audit (
        audit_id TEXT PRIMARY KEY,
        ts DOUBLE PRECISION NOT NULL,
        at TEXT NOT NULL,
        user_id TEXT, user_email TEXT,
        action TEXT NOT NULL,
        target_type TEXT, target_id TEXT,
        run_id TEXT,
        result TEXT NOT NULL,
        ip TEXT,
        metadata TEXT NOT NULL DEFAULT '{}')""",
    "CREATE INDEX IF NOT EXISTS audit_ts ON audit (ts)",
    """CREATE TABLE IF NOT EXISTS workers (
        worker_id TEXT PRIMARY KEY,
        name TEXT,
        token_hash TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at DOUBLE PRECISION NOT NULL,
        created_by TEXT,
        version TEXT,
        state TEXT NOT NULL DEFAULT 'OFFLINE',
        last_heartbeat DOUBLE PRECISION,
        current_run_id TEXT,
        current_step TEXT,
        browser_state TEXT,
        info TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY,
        worker_id TEXT,
        status TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        created_by TEXT, created_by_email TEXT,
        started_at DOUBLE PRECISION, ended_at DOUBLE PRECISION,
        options TEXT NOT NULL DEFAULT '{}',
        summary TEXT NOT NULL DEFAULT '{}',
        detail TEXT,
        last_state_at DOUBLE PRECISION)""",
    "CREATE INDEX IF NOT EXISTS runs_created ON runs (created_at)",
    """CREATE TABLE IF NOT EXISTS run_state (
        run_id TEXT PRIMARY KEY,
        updated_at DOUBLE PRECISION NOT NULL,
        state TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS commands (
        command_id TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL,
        run_id TEXT,
        kind TEXT NOT NULL,
        payload TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL,
        created_by TEXT,
        delivered_at DOUBLE PRECISION,
        finished_at DOUBLE PRECISION,
        attempts INTEGER NOT NULL DEFAULT 0,
        result TEXT)""",
    "CREATE INDEX IF NOT EXISTS commands_worker ON commands (worker_id, status)",
    # What a worker observed in the real eHub (controlplane/observations.py).
    """CREATE TABLE IF NOT EXISTS observations (
        observation_id TEXT PRIMARY KEY,
        worker_id TEXT NOT NULL,
        run_id TEXT,
        kind TEXT NOT NULL,
        level TEXT NOT NULL,
        claimed_level TEXT,
        received_at DOUBLE PRECISION NOT NULL,
        observed_at TEXT,
        reference TEXT,
        status TEXT,
        ehub_host TEXT,
        data TEXT NOT NULL DEFAULT '{}')""",
    "CREATE INDEX IF NOT EXISTS observations_received ON observations (received_at)",
    """CREATE TABLE IF NOT EXISTS claims (
        action_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        user_email TEXT,
        claimed_at DOUBLE PRECISION NOT NULL,
        lease_until DOUBLE PRECISION NOT NULL,
        released_at DOUBLE PRECISION)""",
]


def _rewrite(sql, engine):
    return sql.replace("?", "%s") if engine == "postgres" else sql


class Database(object):
    """A small connection pool and the handful of operations the app needs."""

    def __init__(self, url, pool_size=8):
        self.url = url
        if url.startswith("sqlite:///"):
            self.engine = "sqlite"
            self.path = url[len("sqlite:///"):]
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        elif url.startswith(("postgresql://", "postgres://")):
            self.engine = "postgres"
            try:
                import psycopg   # noqa: F401 — only needed for PostgreSQL
            except ImportError:
                raise RuntimeError("DATABASE_URL is PostgreSQL but psycopg is not "
                                   "installed: pip install psycopg[binary]")
        else:
            raise ValueError("DATABASE_URL must be sqlite:/// or postgresql://")
        self._pool = queue.LifoQueue()
        self._size = pool_size
        self._made = 0
        self._lock = threading.Lock()
        self.migrate()

    # -- connections -----------------------------------------------------

    def _connect(self):
        if self.engine == "sqlite":
            conn = sqlite3.connect(self.path, timeout=10, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.execute("PRAGMA foreign_keys=ON")
            return conn
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)

    @contextmanager
    def _conn(self):
        try:
            conn = self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                self._made += 1
            conn = self._connect()
        broken = False
        try:
            yield conn
        except Exception:
            broken = self._broken(conn)
            raise
        finally:
            if broken or self._pool.qsize() >= self._size:
                try:
                    conn.close()
                except Exception:
                    pass
            else:
                self._pool.put(conn)

    def _broken(self, conn):
        if self.engine == "postgres":
            return bool(getattr(conn, "closed", False)) or \
                getattr(getattr(conn, "info", None), "transaction_status", 0) == 4
        return False

    # -- statements ------------------------------------------------------

    def execute(self, sql, params=(), conn=None):
        """Run one statement; returns the number of rows it changed."""
        if conn is not None:
            cur = conn.execute(_rewrite(sql, self.engine), tuple(params))
            return cur.rowcount
        with self._conn() as c:
            cur = c.execute(_rewrite(sql, self.engine), tuple(params))
            return cur.rowcount

    def all(self, sql, params=(), conn=None):
        def run(c):
            cur = c.execute(_rewrite(sql, self.engine), tuple(params))
            return [dict(row) for row in cur.fetchall()]
        if conn is not None:
            return run(conn)
        with self._conn() as c:
            return run(c)

    def one(self, sql, params=(), conn=None):
        rows = self.all(sql, params, conn)
        return rows[0] if rows else None

    @contextmanager
    def tx(self):
        """
        A transaction. SQLite takes the write lock at BEGIN IMMEDIATE, so two
        requests racing for the same Human Action serialise here; PostgreSQL
        callers lock the rows they read with FOR UPDATE (see lock_clause).
        """
        with self._conn() as c:
            if self.engine == "sqlite":
                c.execute("BEGIN IMMEDIATE")
                try:
                    yield c
                    c.execute("COMMIT")
                except Exception:
                    c.execute("ROLLBACK")
                    raise
            else:
                with c.transaction():
                    yield c

    @property
    def lock_clause(self):
        return " FOR UPDATE" if self.engine == "postgres" else ""

    # -- schema ----------------------------------------------------------

    def migrate(self):
        with self._conn() as c:
            for statement in SCHEMA:
                c.execute(statement)
            # Columns added after a database was first created.
            for table, column, kind in (("commands", "attempts", "INTEGER NOT NULL DEFAULT 0"),):
                try:
                    c.execute("SELECT {0} FROM {1} LIMIT 1".format(column, table))
                except Exception:
                    c.execute("ALTER TABLE {0} ADD COLUMN {1} {2}".format(table, column, kind))
            row = self.one("SELECT value FROM meta WHERE key = ?", ("schema",), c)
            if row is None:
                self.execute("INSERT INTO meta (key, value) VALUES (?, ?)",
                             ("schema", str(SCHEMA_VERSION)), c)

    def ping(self):
        """True when the database answers; for the health panel."""
        try:
            self.one("SELECT 1 AS ok")
            return True
        except Exception:
            return False


def dumps(value):
    return json.dumps(value if value is not None else {}, separators=(",", ":"),
                      default=str)


def loads(text, default=None):
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {} if default is None else default


def now():
    return time.time()
