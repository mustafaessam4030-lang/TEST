from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from app.adapters.base import RawPayload, SearchOutcome, SourceCapabilities
from app.adapters.registry import SourceRegistry
from app.config import Settings
from app.domain.freshness import FreshnessPolicy
from app.repositories.memory_repo import MemoryEquipmentRepository
from app.services.equipment_service import EquipmentService


class FakeAdapter:
    """Stands in for CatSisAdapter: same four methods, no browser."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.cfg = config
        self.behaviour = config.get("_behaviour", "ok")

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(source_id="cat_sis", label="Caterpillar SIS",
                                  needs_login=True, selector_version="test-v1")

    async def ensure_session(self, ctx: Any, *, force_relogin: bool = False) -> None:
        from app.core.errors import AutomationError, ErrorCode

        if self.behaviour == "login_failed":
            raise AutomationError(ErrorCode.LOGIN_FAILED, "rejected")

    async def search(self, ctx: Any, serial_number: str) -> SearchOutcome:
        from app.core.errors import AutomationError, ErrorCode

        if self.behaviour == "not_found":
            return SearchOutcome(found=False, evidence="empty_state:.no-results")
        if self.behaviour == "timeout":
            raise AutomationError(ErrorCode.TIMEOUT, "slow")
        return SearchOutcome(found=True, detail_url="https://sis2.cat.com/#/detail/1")

    async def extract(self, ctx: Any) -> RawPayload:
        return RawPayload(
            fields={"equipment_model": "336", "equipment_type": "Hydraulic Excavator",
                    "build_date": "07/2019", "engine_family": "C9.3B",
                    "parts_manual_url": "https://sis2.cat.com/#/media/SEBP7015"},
            specifications=[{"group": "Weights", "name": "Operating weight", "value": "36200 kg"},
                            {"group": "Engine", "name": "Net power", "value": "225 kW"}],
            payload_kind="xhr", source_url="https://sis2.cat.com/#/detail/1",
            retrieved_at=datetime.now(timezone.utc))


class FakePool:
    """A browser pool that hands out nothing, because nothing needs a browser here."""

    class _Vault:
        def invalidate(self, *_a: Any, **_k: Any) -> None: ...

    def __init__(self) -> None:
        self.vault = self._Vault()

    async def acquire(self, **_kwargs: Any) -> Any:
        class _Ctx:
            page = type("P", (), {"url": "https://sis2.cat.com/#/"})()
        return _Ctx()

    async def release(self, *_a: Any, **_k: Any) -> None: ...

    async def capture_artifacts(self, *_a: Any, **_k: Any) -> dict[str, str]:
        return {}

    async def stop(self) -> None: ...


@pytest.fixture
def repo() -> MemoryEquipmentRepository:
    return MemoryEquipmentRepository()


@pytest.fixture
def make_service(repo: MemoryEquipmentRepository):
    def _make(behaviour: str = "ok", **overrides: Any) -> EquipmentService:
        registry = SourceRegistry()
        registry.register("cat_sis", "Caterpillar SIS",
                          {"_behaviour": behaviour, "selector_version": "test-v1"},
                          FakeAdapter, precedence=10)
        defaults: dict[str, Any] = {"allow_live_automation": True, "repository": "memory",
                                    "run_deadline_ms": 5000, "step_timeout_ms": 2000}
        settings = Settings(**{**defaults, **overrides})
        return EquipmentService(repo=repo, registry=registry, pool=FakePool(),
                                freshness=FreshnessPolicy({"sources": {"cat_sis": {"ttl_days": 90}}}),
                                settings=settings)
    return _make
