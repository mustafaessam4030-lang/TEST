"""Wires settings, mapping, registry, backend, executor and the three experiences together."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from . import contract, guard
from .analysis import Analytics
from .backends.base import Backend
from .chat.assistant import Assistant
from .chat.cortex import CortexPlanner
from .config import Settings
from .executor import Executor
from .fixtures import SYNTHETIC_LABEL
from .mapping import SourceMapping, load_mapping
from .plans import PlanStore
from .query import QueryBuilder
from .recommend import Recommender
from .registry import Registry, load_registry

log = logging.getLogger(__name__)
CANDIDATE_TTL_S = 3600


@dataclass
class DataSource:
    kind: str
    synthetic: bool
    label: str

    def public(self) -> dict:
        return {"kind": self.kind, "synthetic": self.synthetic, "label": self.label}


class ServiceIntel:
    def __init__(self, settings: Settings, backend: Backend | None = None, mapping: SourceMapping | None = None,
                 registry: Registry | None = None):
        self.settings = settings
        self.mapping = mapping or load_mapping(settings.mapping_path)
        self.registry = registry or load_registry(settings.metrics_path)
        if settings.backend == "snowflake" and self.mapping.synthetic:
            raise ValueError("A synthetic mapping cannot be used with the Snowflake backend")
        if backend is None:
            if settings.backend == "fixture":
                from .backends.fixture import FixtureBackend
                backend = FixtureBackend(self.mapping, settings.contract_view,
                                         plans_path=settings.data_dir / "plans-dev.duckdb" if settings.env == "dev" else None)
            else:
                from .backends.snowflake import SnowflakeBackend
                backend = SnowflakeBackend(settings)
        self.backend = backend
        self.source = DataSource(backend.kind, backend.synthetic,
                                 SYNTHETIC_LABEL if backend.synthetic else f"Snowflake: {settings.contract_view}")
        self.available = self.mapping.available
        policy = guard.Policy(relations=frozenset({settings.contract_view.upper()}),
                              columns=frozenset(contract.NAMES) | {"metric_value", "n", "bucket", "segment", "value"},
                              max_rows=settings.max_rows,
                              denied_columns=frozenset(c.lower() for c in self.mapping.sensitive_columns))
        self.executor = Executor(backend, policy, timeout_s=settings.query_timeout_s, max_rows=settings.max_rows,
                                 cache_ttl_s=settings.cache_ttl_s, log_path=settings.data_dir / "query_log.jsonl")
        self.builder = QueryBuilder(self.registry, settings.contract_view, self.available, settings.tz, settings.max_rows)
        self.analytics = Analytics(self.registry, self.builder, self.executor, self.available)
        self.recommender = Recommender(self.analytics)
        if backend.kind == "fixture":
            from .backends.fixture import APP_SCHEMA
            self.plans = PlanStore(backend, APP_SCHEMA, data_source="synthetic", create=True)
        elif settings.snowflake.app_schema and settings.snowflake.writer_role:
            self.plans = PlanStore(backend, settings.snowflake.app_schema, data_source="snowflake")
        else:
            self.plans = None
        cortex = None
        if settings.cortex_enabled and backend.kind == "snowflake":
            cortex = CortexPlanner(backend, self.registry, settings.cortex_model, log_fn=self.executor._log)
        self.assistant = Assistant(self.registry, self.analytics, self.recommender, self.plans, cortex)
        self._candidates: dict[tuple[str, str], tuple[float, dict]] = {}
        self._cand_lock = threading.Lock()

    def remember_candidates(self, candidates: list[dict]) -> None:
        now = time.time()
        with self._cand_lock:
            for k in [k for k, (t, _) in self._candidates.items() if now - t > CANDIDATE_TTL_S]:
                del self._candidates[k]
            for c in candidates:
                self._candidates[(c["snapshot"]["snapshot_id"], c["key"])] = (now, c)

    def candidate(self, snapshot_id: str, key: str) -> dict | None:
        with self._cand_lock:
            item = self._candidates.get((snapshot_id, key))
        if item and time.time() - item[0] <= CANDIDATE_TTL_S:
            return item[1]
        return None
