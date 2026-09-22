"""Contract tests: the shapes Maia depends on cannot drift silently."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.schemas import EquipmentRecord, ErrorResponse

ROOT = Path(__file__).resolve().parents[3]


def test_error_response_cannot_carry_data() -> None:
    with pytest.raises(Exception):
        ErrorResponse(error_code="TIMEOUT", retryable=True, message="x",
                      user_message_hint="x", data={"equipment_model": "336"})


def test_record_rejects_unexpected_fields() -> None:
    with pytest.raises(Exception):
        EquipmentRecord(serial_number="SN1", source_system="cat_sis",
                        retrieved_at=datetime.now(timezone.utc), surprise_field="from a redesign")


def test_json_schema_matches_pydantic_field_names() -> None:
    schema = json.loads((ROOT / "schemas" / "equipment.normalized.schema.json").read_text())
    assert set(schema["properties"]) == set(EquipmentRecord.model_fields)
    assert schema["additionalProperties"] is False


def test_tool_definitions_are_strict_and_closed() -> None:
    tools = json.loads((ROOT / "schemas" / "maia_tools.json").read_text())["tools"]
    names = {t["name"] for t in tools}
    assert names == {"understand_request",
                     "get_equipment_from_database", "get_equipment_from_local_store",
                     "search_equipment_in_sis", "get_equipment_history",
                     "get_automation_run_status", "save_equipment_data"}
    for tool in tools:
        assert tool["strict"] is True
        assert tool["input_schema"]["additionalProperties"] is False
        # No tool may expose a browser primitive or a credential to the model.
        assert not ({"url", "selector", "sql", "username", "password", "script"}
                    & set(tool["input_schema"]["properties"]))


def test_sis_config_requires_confirmed_selectors_not_guesses() -> None:
    import yaml

    cfg = yaml.safe_load((ROOT / "config" / "sources" / "cat_sis.yaml").read_text())
    assert cfg["base_url"] == "https://sis2.cat.com/#/"
    # The empty-state marker is the only permissible proof of SERIAL_NOT_FOUND.
    assert "no_results_marker" in cfg["selectors"]["search"]
    assert cfg["selector_version"].endswith("unconfirmed")  # honest until captured
