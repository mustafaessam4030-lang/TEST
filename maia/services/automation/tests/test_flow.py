"""End-to-end decision-flow tests against the fake source adapter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import AutomationError, ErrorCode
from app.models.schemas import (
    EquipmentSearchRequest, EquipmentSearchResponse, Freshness, SearchMode,
)


async def test_cache_miss_runs_automation_and_persists(make_service, repo) -> None:
    service = make_service("ok")
    result = await service.lookup(EquipmentSearchRequest(serial_number="sn123456"))

    assert isinstance(result, EquipmentSearchResponse)
    assert result.data.equipment_model == "336"
    assert result.cache.hit is False
    assert result.attribution.source_label == "Caterpillar SIS"
    assert result.attribution.automation_run_id.startswith("run_")
    assert await repo.get_current("SN123456", "cat_sis") is not None

    run = await repo.get_run(result.attribution.automation_run_id)
    assert run["status"] == "SUCCESS"
    assert [s["step"] for s in run["steps_executed"]][:3] == [
        "ACQUIRE_CONTEXT", "ENSURE_SESSION", "SEARCH_SERIAL"]


async def test_second_lookup_is_served_from_cache_without_automation(make_service) -> None:
    service = make_service("ok")
    await service.lookup(EquipmentSearchRequest(serial_number="SN123456"))
    # Break the source: a cache hit must not need it.
    service.registry.entry("cat_sis").config["_behaviour"] = "timeout"
    second = await service.lookup(EquipmentSearchRequest(serial_number="SN123456"))
    assert second.cache.hit is True
    assert second.attribution.freshness is Freshness.FRESH
    assert second.source == "internal_store"


async def test_not_found_is_negative_cached_and_carries_no_data(make_service, repo) -> None:
    service = make_service("not_found")
    with pytest.raises(AutomationError) as exc:
        await service.lookup(EquipmentSearchRequest(serial_number="SN999999"))
    assert exc.value.code is ErrorCode.SERIAL_NOT_FOUND
    assert "data" not in exc.value.to_payload()

    row = await repo.get_current("SN999999", "cat_sis")
    assert row["status"] == "NOT_FOUND"

    with pytest.raises(AutomationError) as second:   # served from the negative cache
        await service.lookup(EquipmentSearchRequest(serial_number="SN999999"))
    assert second.value.details.get("cached") is True


async def test_stale_cache_plus_source_failure_offers_labelled_fallback(make_service, repo) -> None:
    service = make_service("ok")
    await service.lookup(EquipmentSearchRequest(serial_number="SN123456"))

    row = await repo.get_current("SN123456", "cat_sis")
    row["retrieved_at"] = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
    service.registry.entry("cat_sis").config["_behaviour"] = "timeout"

    with pytest.raises(AutomationError) as exc:
        await service.lookup(EquipmentSearchRequest(serial_number="SN123456", timeout_ms=3000))
    fallback = exc.value.details["fallback"]
    assert fallback["available"] is True
    assert fallback["age_days"] > 365
    assert fallback["freshness"] == "HARD_STALE"       # age is always labelled


async def test_login_failure_opens_the_circuit(make_service) -> None:
    service = make_service("login_failed")
    with pytest.raises(AutomationError) as exc:
        await service.lookup(EquipmentSearchRequest(serial_number="SN123456"))
    assert exc.value.code is ErrorCode.LOGIN_FAILED
    assert service.registry.breaker("cat_sis").state == "OPEN"

    with pytest.raises(AutomationError) as second:
        await service.lookup(EquipmentSearchRequest(serial_number="SN654321"))
    assert second.value.code is ErrorCode.CIRCUIT_OPEN


async def test_cache_only_mode_never_touches_the_source(make_service) -> None:
    service = make_service("timeout")
    with pytest.raises(AutomationError) as exc:
        await service.lookup(EquipmentSearchRequest(serial_number="SN123456",
                                                    mode=SearchMode.CACHE_ONLY))
    assert exc.value.code is ErrorCode.SERIAL_NOT_FOUND


async def test_live_automation_disabled_by_default(make_service) -> None:
    service = make_service("ok", allow_live_automation=False)
    with pytest.raises(AutomationError) as exc:
        await service.lookup(EquipmentSearchRequest(serial_number="SN123456"))
    assert exc.value.code is ErrorCode.CIRCUIT_OPEN


async def test_history_records_changes_not_polls(make_service, repo) -> None:
    service = make_service("ok")
    await service.lookup(EquipmentSearchRequest(serial_number="SN123456"))
    await service.lookup(EquipmentSearchRequest(serial_number="SN123456",
                                                mode=SearchMode.FORCE_REFRESH))
    assert len(await repo.history("SN123456")) == 1   # identical data ⇒ one version
