"""Guarded, cached, de-duplicated query execution with a query log.

Every read goes through `Executor.run`:
  1. guard.validate  — one bounded read-only SELECT over approved objects
  2. cache           — identical (sql, params) within the TTL reuse the result
  3. single-flight   — concurrent identical requests share one warehouse query
  4. backend         — timeout and row cap enforced by the backend
  5. query log       — query id, purpose, duration, status; never credentials or row data
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict, deque
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from . import guard
from .backends.base import Backend, BackendError, Result
from .query import QuerySpec

log = logging.getLogger(__name__)


class Executor:
    def __init__(self, backend: Backend, policy: guard.Policy, *, timeout_s: int, max_rows: int,
                 cache_ttl_s: int, log_path: Path | None = None, cache_size: int = 512):
        self.backend = backend
        self.policy = policy
        self.timeout_s = timeout_s
        self.max_rows = max_rows
        self.cache_ttl_s = cache_ttl_s
        self.log_path = log_path
        self._cache: OrderedDict = OrderedDict()
        self._cache_size = cache_size
        self._inflight: dict = {}
        self._lock = threading.Lock()
        self.recent: deque = deque(maxlen=500)
        self.warehouse_queries = 0          # executions that reached the backend (for tests and the admin view)

    def _log(self, entry: dict) -> None:
        self.recent.append(entry)
        if self.log_path:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, default=str) + "\n")
            except OSError:
                log.warning("query log write failed")

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def run(self, spec: QuerySpec, *, user: str = "system", use_cache: bool = True) -> Result:
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user": user,
                 "purpose": spec.purpose, "metric": spec.metric_id, "backend": self.backend.kind}
        try:
            guard.validate(spec.sql, self.policy)
        except guard.GuardError as exc:
            self._log({**entry, "status": "refused", "reason": str(exc)})
            raise BackendError("refused", f"Query refused by the safety check: {exc}") from None

        key = (spec.sql, spec.params)
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key) if use_cache and self.cache_ttl_s else None
            if hit and now - hit[0] < self.cache_ttl_s:
                self._cache.move_to_end(key)
                self._log({**entry, "status": "cache_hit", "query_id": hit[1].query_id})
                return replace(hit[1], cached=True)
            waiter = self._inflight.get(key)
            if waiter is None:
                waiter = {"event": threading.Event(), "result": None, "error": None}
                self._inflight[key] = waiter
                owner = True
            else:
                owner = False
        if not owner:
            waiter["event"].wait(self.timeout_s + 5)
            if waiter["error"]:
                raise waiter["error"]
            if waiter["result"] is None:
                raise BackendError("timeout", "The query exceeded its time limit.")
            self._log({**entry, "status": "shared", "query_id": waiter["result"].query_id})
            return replace(waiter["result"], cached=True)

        try:
            self.warehouse_queries += 1
            result = self.backend.query(spec.sql, spec.params, timeout_s=self.timeout_s,
                                        max_rows=self.max_rows, tag=spec.purpose)
            waiter["result"] = result
            with self._lock:
                if self.cache_ttl_s:
                    self._cache[key] = (time.monotonic(), result)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
            self._log({**entry, "status": "ok", "query_id": result.query_id, "elapsed_ms": result.elapsed_ms,
                       "rows": len(result.rows), "truncated": result.truncated})
            return result
        except BackendError as exc:
            waiter["error"] = exc
            self._log({**entry, "status": "error", "error_kind": exc.kind, "query_id": exc.query_id})
            raise
        except Exception as exc:          # unexpected driver error: classify as a failure, never leak details
            err = BackendError("query_failed", "The query failed unexpectedly.")
            waiter["error"] = err
            self._log({**entry, "status": "error", "error_kind": "query_failed", "detail": type(exc).__name__})
            raise err from None
        finally:
            with self._lock:
                self._inflight.pop(key, None)
            waiter["event"].set()
