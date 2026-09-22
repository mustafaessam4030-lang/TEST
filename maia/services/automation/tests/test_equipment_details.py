"""The equipment-details block: scroll, read, cross-check, and never invent.

These cover the requirement that the detail page is read in a fixed order —
Machine Serial Number, Machine Build Date, Engine Serial Number, Engine Build
Date, then the "Product - …" parts groups — from a section that only exists
after the SIS content pane has been scrolled.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from app.core.errors import AutomationError, ErrorCode
from app.domain.normalize import normalize_equipment_data, parse_build_date
from app.domain.validate import validate_equipment_data

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = yaml.safe_load((ROOT / "config" / "sources" / "cat_sis.yaml").read_text(encoding="utf-8"))
SIS_DOM = (ROOT / "services" / "automation" / "app" / "adapters" / "sis_dom.js").read_text(encoding="utf-8")
ADAPTER = (ROOT / "services" / "automation" / "app" / "adapters" / "cat_sis.py").read_text(encoding="utf-8")

#: The values from the operator's screenshot. They are ground truth for a manual
#: check and must never appear in shipped code — a test, not a fixture value.
GROUND_TRUTH = ("JAZ01865", "PRH04588", "08/02/2014", "06/30/2014")


# ── date order ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("raw,order,expected", [
    ("08/02/2014", "month_first", "2014-08-02"),   # SIS publishes MM/DD/YYYY
    ("08/02/2014", "day_first", "2014-02-08"),     # the same text, other order
    ("06/30/2014", "month_first", "2014-06-30"),
    ("06/30/2014", "day_first", "2014-06-30"),     # 30 is not a month: unambiguous
    ("30/06/2014", "month_first", "2014-06-30"),   # ditto, the other way round
    ("13/13/2014", "month_first", None),           # impossible: null, never guessed
])
def test_date_order_only_settles_what_the_value_cannot(raw, order, expected) -> None:
    assert parse_build_date(raw, date_order=order) == expected


def test_contract_declares_the_source_date_order() -> None:
    """The order is a property of the source, declared once, not inferred per value."""
    assert CONTRACT["extraction"]["date_order"] == "month_first"


# ── normalization ───────────────────────────────────────────────────────────
def _raw(**overrides):
    fields = {
        "machine_serial_number": "SN123456", "machine_build_date": "08/02/2014",
        "engine_serial_number": "PRH04588", "engine_build_date": "06/30/2014",
        "equipment_model": "C32", "equipment_type": "Generator Set",
    }
    fields.update(overrides.pop("fields", {}))
    return {"fields": fields, "payload_kind": "dom", **overrides}


def _normalize(raw, serial="SN123456"):
    return normalize_equipment_data(
        raw, serial_number=serial, source_system="cat_sis",
        source_url="https://sis2.cat.com/#/detail/1",
        retrieved_at=datetime.now(timezone.utc), run_id="run_test",
        field_map=CONTRACT["field_map"], date_order=CONTRACT["extraction"]["date_order"])


def test_the_four_fields_normalize_with_the_source_date_order() -> None:
    record = _normalize(_raw())
    assert record.machine_serial_number == "SN123456"
    assert record.machine_build_date == "2014-08-02"
    assert record.engine_serial_number == "PRH04588"
    assert record.engine_build_date == "2014-06-30"
    for field in ("machine_serial_number", "machine_build_date",
                  "engine_serial_number", "engine_build_date"):
        assert record.field_provenance[field].reason == "OK"


def test_a_field_the_page_did_not_publish_is_null_with_a_reason() -> None:
    record = _normalize(_raw(fields={"engine_build_date": None}))
    assert record.engine_build_date is None
    assert record.field_provenance["engine_build_date"].reason == "NOT_PUBLISHED"


def test_an_unparseable_date_is_null_and_a_violation_not_a_guess() -> None:
    record = _normalize(_raw(fields={"machine_build_date": "soon"}))
    assert record.machine_build_date is None
    assert any(v.startswith("machine_build_date_unparseable") for v in record.quality.violations)


# ── the cross-check ─────────────────────────────────────────────────────────
def test_a_page_showing_another_machine_is_quarantined_not_served() -> None:
    """The worst possible failure is a confident answer about the wrong machine."""
    record = _normalize(_raw(fields={"machine_serial_number": "ZZZ99999"}))
    assert "serial_mismatch:machine_serial_number" in record.quality.violations
    with pytest.raises(AutomationError) as exc:
        validate_equipment_data(record)
    assert exc.value.code is ErrorCode.INVALID_DATA
    assert any("serial_mismatch" in v for v in exc.value.details["fatal"])


def test_the_adapter_refuses_a_mismatch_before_normalization_ever_sees_it() -> None:
    from app.adapters.base import RawPayload
    from app.adapters.cat_sis import CatSisAdapter

    adapter = CatSisAdapter(CONTRACT)
    payload = RawPayload(fields={"machine_serial_number": "ZZZ99999"})
    with pytest.raises(AutomationError) as exc:
        adapter._cross_check_serial(payload, "SN123456")
    assert exc.value.code is ErrorCode.EXTRACTION_ERROR
    # Punctuation and case are not a mismatch; a different machine is.
    ok = RawPayload(fields={"machine_serial_number": "sn-123456"})
    adapter._cross_check_serial(ok, "SN123456")


def test_a_record_without_the_machine_serial_cannot_be_served() -> None:
    record = _normalize(_raw(fields={"machine_serial_number": None}))
    with pytest.raises(AutomationError) as exc:
        validate_equipment_data(record)
    assert "missing_required:machine_serial_number" in exc.value.details["violations"]


def test_reading_the_label_instead_of_the_value_is_caught() -> None:
    """If a selector drifts onto the label, the value stops being serial-shaped."""
    record = _normalize(_raw(fields={"engine_serial_number": "Engine Serial Number -"}))
    try:
        validate_equipment_data(record)
    except AutomationError as exc:
        violations = exc.details["violations"]
    else:
        violations = record.quality.violations
    assert "unreadable_value:engine_serial_number" in violations


# ── the contract ────────────────────────────────────────────────────────────
def test_every_new_target_is_declared_and_unproven_until_captured() -> None:
    detail = CONTRACT["selectors"]["detail"]
    for key in ("scroll_container", "details_anchor", "machine_serial_number",
                "machine_build_date", "engine_serial_number", "engine_build_date",
                "parts_group", "parts_table", "parts_header_cells", "parts_rows"):
        assert detail[key] == "TODO_CAPTURE", f"{key} must start unproven"


def test_the_serial_is_the_one_field_extraction_cannot_do_without() -> None:
    """It is the proof we are on the right record. The other three are collected
    when the page publishes them and stay null when it does not — a thin answer
    is still a true answer."""
    assert CONTRACT["extraction"]["required_fields"] == ["machine_serial_number"]


def test_a_field_the_page_did_not_yield_marks_the_answer_partial() -> None:
    adapter_src = ADAPTER
    assert 'payload.artifacts["extraction_status"] = "PARTIAL" if absent else "SUCCESS"' \
        in adapter_src
    # …and it is still never filled in.
    assert "fields_not_read" in adapter_src


def test_the_contract_cannot_be_called_usable_without_them() -> None:
    from app.adapters import selector_store

    for name in ("detail.machine_serial_number", "detail.machine_build_date",
                 "detail.engine_serial_number", "detail.engine_build_date",
                 "detail.parts_group", "detail.parts_rows"):
        assert name in selector_store.REQUIRED_SELECTORS


def test_labels_are_patterns_the_page_prints_not_values() -> None:
    labels = CONTRACT["extraction"]["detail_labels"]
    assert set(labels) == {"machine_serial_number", "machine_build_date",
                           "engine_serial_number", "engine_build_date"}
    for pattern in labels.values():
        assert not any(truth in pattern for truth in GROUND_TRUTH)


# ── the reader ──────────────────────────────────────────────────────────────
def test_the_reader_scrolls_the_pane_and_not_only_the_window() -> None:
    assert "scrollableContainers" in SIS_DOM and "scrollParent" in SIS_DOM
    # It must look at what actually scrolls, not at the document alone.
    assert "el.scrollHeight - el.clientHeight" in SIS_DOM
    assert "overflowY" in SIS_DOM


def test_the_reader_waits_for_the_spa_after_scrolling() -> None:
    assert "async function settle" in SIS_DOM
    # Reaching the bottom is not the end: late-rendered content must still count.
    assert "quietLimit" in SIS_DOM


def test_extraction_scrolls_before_it_reads() -> None:
    reveal = ADAPTER.index("reveal = await self._reveal_details(ctx)")
    read = ADAPTER.index("labelled = await self._extract_labelled(ctx)")
    assert reveal < read, "the page must be scrolled before any value is read"


def test_an_unreachable_details_section_is_website_changed_not_an_empty_answer() -> None:
    assert "The equipment-details section never appeared after scrolling" in ADAPTER
    assert "ErrorCode.WEBSITE_CHANGED" in ADAPTER


def test_all_product_groups_are_collected_not_only_the_first() -> None:
    assert CONTRACT["extraction"]["parts"]["all_groups"] is True
    assert "readProductGroupsFrom" in SIS_DOM
    assert "productHeadings" in SIS_DOM


def test_no_example_value_is_baked_into_shipped_code() -> None:
    """The operator's screenshot is ground truth for a test, never a default."""
    shipped = [
        ROOT / "services" / "automation" / "app" / "adapters" / "sis_dom.js",
        ROOT / "services" / "automation" / "app" / "adapters" / "cat_sis.py",
        ROOT / "services" / "automation" / "app" / "domain" / "normalize.py",
        ROOT / "config" / "sources" / "cat_sis.yaml",
        ROOT / "scripts" / "capture" / "discover.js",
        ROOT / "scripts" / "capture" / "autodiscover.py",
        ROOT / "scripts" / "capture" / "capture_selectors.py",
    ]
    for path in shipped:
        text = path.read_text(encoding="utf-8")
        for truth in GROUND_TRUTH:
            assert truth not in text, f"{path.name} hard-codes the example value {truth}"
