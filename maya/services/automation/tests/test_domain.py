from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.errors import AutomationError, ErrorCode
from app.core.hashing import data_hash
from app.domain.freshness import FreshnessPolicy
from app.domain.normalize import normalize_equipment_data, parse_build_date, split_value_unit
from app.domain.validate import validate_equipment_data, validate_serial
from app.models.schemas import Freshness, RecordStatus


@pytest.mark.parametrize("raw,expected", [
    ("sn123456", "SN123456"), (" cat-0336 lkbw ", "CAT0336LKBW"),
    ("SN١٢٣٤٥٦", "SN123456"),  # Arabic-Indic digits normalize
])
def test_serial_normalization(raw: str, expected: str) -> None:
    assert validate_serial(raw) == expected


@pytest.mark.parametrize("bad", ["", "ab", "SN#123!", "X" * 20])
def test_invalid_serials_rejected(bad: str) -> None:
    with pytest.raises(AutomationError) as exc:
        validate_serial(bad)
    assert exc.value.code is ErrorCode.INVALID_SERIAL


@pytest.mark.parametrize("raw,value,unit", [
    ("36200 kg", 36200.0, "kg"), ("225 kW", 225.0, "kW"),
    ("Tier 4 Final", "Tier 4 Final", None), ("", None, None),
])
def test_split_value_unit(raw, value, unit) -> None:
    assert split_value_unit(raw) == (value, unit)


@pytest.mark.parametrize("raw,expected", [
    ("07/2019", "2019-07"), ("Jul 2019", "2019-07"), ("2019", "2019"),
    ("2019-07-15", "2019-07-15"), ("sometime in 2019", None),
])
def test_build_date_parsing(raw, expected) -> None:
    assert parse_build_date(raw) == expected


def _record(**fields):
    return normalize_equipment_data(
        {"fields": {"equipment_model": "336", "equipment_type": "Hydraulic Excavator",
                    "build_date": "07/2019", "engine_family": "C9.3B", **fields},
         "specifications": [{"name": "Operating weight", "value": "36200 kg"}]},
        serial_number="SN123456", source_system="cat_sis",
        source_url="https://sis2.cat.com/#/x", retrieved_at=datetime.now(timezone.utc),
        run_id="run_TEST")


def test_normalizer_maps_vocabulary_and_units() -> None:
    record = _record()
    assert record.equipment_type == "HYDRAULIC_EXCAVATOR"
    assert record.build_date == "2019-07"
    assert record.specifications[0].value == 36200.0
    assert record.specifications[0].unit == "kg"
    assert record.specifications[0].value_raw == "36200 kg"  # nothing lost


def test_absent_field_is_null_with_provenance_reason() -> None:
    record = _record()
    assert record.operation_manual_url is None
    assert record.field_provenance["operation_manual_url"].reason == "NOT_PUBLISHED"


def test_validation_quarantines_record_missing_required_fields() -> None:
    record = _record()
    record.equipment_model = None
    with pytest.raises(AutomationError) as exc:
        validate_equipment_data(record)
    assert exc.value.code is ErrorCode.INVALID_DATA
    assert record.status is RecordStatus.QUARANTINED


def test_validation_rejects_impossible_build_date() -> None:
    record = _record()
    record.build_date = "2099"
    with pytest.raises(AutomationError):
        validate_equipment_data(record)


def test_hash_ignores_volatile_fields() -> None:
    a = _record().model_dump(mode="json")
    b = dict(a, retrieved_at="2030-01-01T00:00:00Z", automation_run_id="run_OTHER")
    assert data_hash(a) == data_hash(b)
    c = dict(a, equipment_model="349")
    assert data_hash(a) != data_hash(c)


def _row(age_days: float, status: str = "ACTIVE") -> dict:
    return {"retrieved_at": (datetime.now(timezone.utc)
                             - timedelta(days=age_days)).isoformat(), "status": status}


def test_freshness_matrix() -> None:
    policy = FreshnessPolicy({"sources": {"cat_sis": {"ttl_days": 90, "hard_stale_days": 365}}})
    assert policy.evaluate(_row(3), source="cat_sis").freshness is Freshness.FRESH
    assert policy.evaluate(_row(3), source="cat_sis").action == "use_cache"
    assert policy.evaluate(_row(210), source="cat_sis").freshness is Freshness.STALE
    assert policy.evaluate(_row(800), source="cat_sis").freshness is Freshness.HARD_STALE
    assert policy.evaluate(None, source="cat_sis").freshness is Freshness.MISSING


def test_open_circuit_serves_cache_instead_of_refreshing() -> None:
    policy = FreshnessPolicy({"sources": {"cat_sis": {"ttl_days": 30}}})
    decision = policy.evaluate(_row(200), source="cat_sis", circuit_state="OPEN")
    assert decision.action == "use_cache" and decision.reason == "circuit_open"


def test_negative_cache_expires() -> None:
    policy = FreshnessPolicy({})
    assert policy.evaluate(_row(2, "NOT_FOUND"), source="cat_sis").freshness is Freshness.NEGATIVE_CACHED
    assert policy.evaluate(_row(30, "NOT_FOUND"), source="cat_sis").action == "refresh"


def test_force_refresh_ignores_fresh_cache() -> None:
    policy = FreshnessPolicy({"sources": {"cat_sis": {"ttl_days": 90}}})
    assert policy.evaluate(_row(1), source="cat_sis", mode="force_refresh").action == "refresh"
