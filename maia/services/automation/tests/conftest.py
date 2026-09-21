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

    async def extract(self, ctx: Any, serial_number: str | None = None) -> RawPayload:
        # The machine serial follows the serial that was asked for, exactly as a
        # real detail page does — the cross-check is part of the flow under test.
        serial = serial_number or "CAT0336LKBW00123"
        return RawPayload(
            fields={"equipment_model": "336", "equipment_type": "Hydraulic Excavator",
                    "build_date": "07/2019", "engine_family": "C9.3B",
                    "machine_serial_number": serial, "machine_build_date": "08/02/2014",
                    "engine_serial_number": "FIX00588", "engine_build_date": "06/30/2014",
                    "parts_manual_url": "https://sis2.cat.com/#/media/SEBP7015"},
            specifications=[{"group": "Weights", "name": "Operating weight", "value": "36200 kg"},
                            {"group": "Engine", "name": "Net power", "value": "225 kW"}],
            parts_data={
                "group_titles": [f"Product - Entire Group ({serial})"],
                "group_count": 1,
                "entire_group_title": f"Product - Entire Group ({serial})",
                "columns": ["Part Number", "Serial Number", "Part Name"],
                "total_rows": 1,
                "serial_mismatched_groups": [],
                "selector_id": "detail.parts_group",
                "groups": [{
                    "title": f"Product - Entire Group ({serial})",
                    "group_serial": serial, "is_entire_group": True,
                    "discovered_by": "selector:detail.parts_group",
                    "columns": ["Part Number", "Serial Number", "Part Name"],
                    "row_count": 1, "column_count": 3,
                    "rows": [{"cells": ["1000", "FIX00588", "Engine"],
                              "values": {"Part Number": "1000", "Serial Number": "FIX00588",
                                         "Part Name": "Engine"}}],
                }],
            },
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
