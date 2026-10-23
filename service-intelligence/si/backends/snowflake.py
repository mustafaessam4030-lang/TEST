"""Server-side Snowflake access with snowflake-connector-python.

Two connections, each with its own role:
  reader -> SNOWFLAKE_ROLE: SELECT on the contract view only (plus CORTEX_USER if enabled)
  writer -> SI_SNOWFLAKE_WRITER_ROLE: INSERT/UPDATE on the SI_APP plan tables only
Every session carries QUERY_TAG, TIMEZONE=UTC and a statement timeout, and binds
use qmark (server-side binding). Credentials are read from the environment or a
mounted key file and never logged.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from ..config import Settings
from .base import Backend, BackendError, Result

log = logging.getLogger(__name__)
QUERY_TAG = "service-intelligence"
RECONNECT_ERRNOS = {390111, 390112, 390114, 250001, 250002}
WAREHOUSE_ERRNOS = {606, 608}
PERMISSION_ERRNOS = {2003, 3001, 2043, 1063}
TIMEOUT_ERRNOS = {604, 630}


def _private_key_der(path: str, passphrase: str | None) -> bytes:
    from cryptography.hazmat.primitives import serialization
    key = serialization.load_pem_private_key(Path(path).read_bytes(),
                                             password=passphrase.encode() if passphrase else None)
    return key.private_bytes(encoding=serialization.Encoding.DER, format=serialization.PrivateFormat.PKCS8,
                             encryption_algorithm=serialization.NoEncryption())


def classify(exc: Exception) -> BackendError:
    errno = getattr(exc, "errno", None)
    qid = getattr(exc, "sfqid", None)
    msg = str(getattr(exc, "msg", "") or exc).splitlines()[0][:300]
    if errno in TIMEOUT_ERRNOS:
        return BackendError("timeout", "The query exceeded its time limit and was cancelled.", qid)
    low = msg.lower()
    if errno in WAREHOUSE_ERRNOS or ("warehouse" in low and ("suspend" in low or "no active" in low)):
        return BackendError("warehouse_unavailable", "The Snowflake warehouse is unavailable or suspended.", qid)
    if errno in PERMISSION_ERRNOS or "not authorized" in low or "insufficient privileges" in low:
        return BackendError("permission_denied", "The application's Snowflake role is not authorised for this data.", qid)
    if errno in RECONNECT_ERRNOS or type(exc).__name__ in ("OperationalError", "InterfaceError"):
        return BackendError("connection", "Could not connect to Snowflake.", qid)
    return BackendError("query_failed", f"Snowflake rejected the query (error {errno}).", qid)


class SnowflakeBackend(Backend):
    kind = "snowflake"
    synthetic = False
    label = "Snowflake"

    def __init__(self, settings: Settings, pool_size: int = 4):
        self.settings = settings
        self.sf = settings.snowflake
        self._pool: queue.LifoQueue = queue.LifoQueue(maxsize=pool_size)
        self._created = 0
        self._pool_size = pool_size
        self._lock = threading.Lock()
        self._writer = None
        self._writer_lock = threading.Lock()
        self.label = f"Snowflake ({settings.contract_view})"

    def _connect(self, role: str):
        try:
            import snowflake.connector
        except ImportError as exc:     # pragma: no cover - dependency is in requirements.txt
            raise BackendError("not_configured", "snowflake-connector-python is not installed") from exc
        kwargs = dict(account=self.sf.account, user=self.sf.user, role=role, warehouse=self.sf.warehouse,
                      application="service_intelligence", paramstyle="qmark", login_timeout=30,
                      client_session_keep_alive=False,
                      session_parameters={"QUERY_TAG": QUERY_TAG, "TIMEZONE": "UTC",
                                          "STATEMENT_TIMEOUT_IN_SECONDS": self.settings.query_timeout_s,
                                          "ABORT_DETACHED_QUERY": True})
        if self.sf.private_key_path:
            kwargs["private_key"] = _private_key_der(self.sf.private_key_path, self.sf.private_key_passphrase)
            kwargs["authenticator"] = "SNOWFLAKE_JWT"
        elif self.sf.oauth_token_path:
            kwargs["authenticator"] = "oauth"
            kwargs["token"] = Path(self.sf.oauth_token_path).read_text(encoding="utf-8").strip()
        elif self.sf.authenticator:
            kwargs["authenticator"] = self.sf.authenticator
        elif self.sf.password:
            kwargs["password"] = self.sf.password
        else:
            raise BackendError("not_configured", "No Snowflake credential is configured (key pair, OAuth or authenticator)")
        try:
            return snowflake.connector.connect(**kwargs)
        except Exception as exc:
            err = classify(exc)
            log.warning("snowflake connect failed: kind=%s", err.kind)
            raise err from None

    def _acquire(self):
        try:
            return self._pool.get_nowait()
        except queue.Empty:
            with self._lock:
                if self._created < self._pool_size:
                    self._created += 1
                    try:
                        return self._connect(self.sf.role)
                    except Exception:
                        self._created -= 1
                        raise
            return self._pool.get(timeout=self.settings.query_timeout_s)

    def _release(self, con, broken: bool) -> None:
        if broken:
            try:
                con.close()
            except Exception:
                pass
            with self._lock:
                self._created -= 1
        else:
            self._pool.put(con)

    def _run(self, con, sql: str, params: tuple, timeout_s: int, max_rows: int, tag: str) -> Result:
        started = time.monotonic()
        cur = con.cursor()
        try:
            cur.execute(sql, params, timeout=timeout_s)
            cols = [d[0].lower() for d in cur.description or []]
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
            truncated = len(rows) > max_rows
            return Result(cols, [tuple(r) for r in rows[:max_rows]], cur.sfqid or "",
                          int((time.monotonic() - started) * 1000), truncated)
        finally:
            cur.close()

    def query(self, sql, params, *, timeout_s, max_rows, tag) -> Result:
        for attempt in (1, 2):
            con = self._acquire()
            try:
                result = self._run(con, sql, params, timeout_s, max_rows, tag)
                self._release(con, False)
                return result
            except BackendError:
                self._release(con, True)
                raise
            except Exception as exc:
                err = classify(exc)
                self._release(con, err.kind == "connection")
                if err.kind == "connection" and attempt == 1:
                    continue
                raise err from None
        raise BackendError("connection", "Could not connect to Snowflake.")

    def write(self, sql, params, *, tag, native=False) -> Result:
        if not self.sf.writer_role or not self.sf.app_schema:
            raise BackendError("not_configured", "Plan persistence needs SI_SNOWFLAKE_WRITER_ROLE and SI_APP_SCHEMA")
        with self._writer_lock:
            if self._writer is None:
                self._writer = self._connect(self.sf.writer_role)
            try:
                return self._run(self._writer, sql, params, self.settings.query_timeout_s, 1000, tag)
            except Exception as exc:
                err = exc if isinstance(exc, BackendError) else classify(exc)
                if err.kind == "connection":
                    self._writer = None
                raise err from None

    def close(self) -> None:
        while not self._pool.empty():
            try:
                self._pool.get_nowait().close()
            except Exception:
                pass
        if self._writer is not None:
            self._writer.close()
