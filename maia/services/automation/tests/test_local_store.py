"""The temporary local JSON store, and the rule that makes it temporary safely.

A store that silently loses a write is worse than no store: the lookup is
reported as done, the browser session is gone, and nothing on disk says what
happened. So the contract here is narrow — save everything, invent nothing,
and fail the lookup if the save fails.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.errors import AutomationError, ErrorCode
from app.models.schemas import EquipmentRecord, RecordStatus, RunRecord, RunStatus
from app.repositories.base import ExtractionArtifact
from app.repositories.local_json_repo import (
    LocalJsonRepository, REDACTED, render_text, scrub,
)

SERIAL = "JAZ01865"
RUN_ID = "run_01TESTLOCALSTORE"


def _record(**overrides) -> EquipmentRecord:
    data = {
        "serial_number": SERIAL, "source_system": "cat_sis",
        "retrieved_at": datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
        "automation_run_id": RUN_ID,
        "equipment_model": "C32", "equipment_type": "GENERATOR_SET",
        "manufacturer": "Caterpillar",
        "machine_serial_number": SERIAL, "machine_build_date": "2014-08-02",
        "engine_serial_number": "PRH04588", "engine_build_date": "2014-06-30",
        "parts_manual_url": "https://sis2.cat.com/#/media/SEBP7015",
        "operation_manual_url": None,
        "source_url": "https://sis2.cat.com/#/detail/JAZ01865",
        "specifications": [{"name": "Operating weight", "value": 36200, "unit": "kg",
                            "value_raw": "36200 kg"}],
        "parts_data": {
            "group_titles": [f"Product - Entire Group ({SERIAL})"],
            "group_count": 1, "entire_group_title": f"Product - Entire Group ({SERIAL})",
            "columns": ["Part Number", "Serial Number", "Part Name", "Install Ind.",
                        "Install Date", "Description"],
            "total_rows": 2, "serial_mismatched_groups": [],
            "selector_id": "detail.parts_group",
            "groups": [{
                "title": f"Product - Entire Group ({SERIAL})", "group_serial": SERIAL,
                "is_entire_group": True, "discovered_by": "selector:detail.parts_group",
                "columns": ["Part Number", "Serial Number", "Part Name", "Install Ind.",
                            "Install Date", "Description"],
                "row_count": 2, "column_count": 6,
                "rows": [
                    {"cells": ["444-5867", "", "General AR", "Factory", "", "—"]},
                    {"cells": ["1000", "PRH04588", "Engine", "Factory", "", "ENGINE"]},
                ],
            }],
        },
    }
    data.update(overrides)
    return EquipmentRecord(**data)


def _artifact(tmp_path: Path | None = None, **overrides) -> ExtractionArtifact:
    shots = {}
    if tmp_path is not None:
        for name in ("page", "details", "parts"):
            shot = tmp_path / f"{name}.png"
            shot.write_bytes(b"\x89PNG\r\n\x1a\n fake")
            shots[name] = str(shot)
    data = {
        # Deliberately includes a key the canonical schema has no column for.
        "fields": {"machine_serial_number": SERIAL, "engine_serial_number": "PRH04588",
                   "warranty_status": "Expired", "dealer_code": "EG01"},
        "specifications": [{"name": "Operating weight", "value": "36200 kg"}],
        "parts_data": None,
        "final_url": "https://sis2.cat.com/#/detail/JAZ01865",
        "page_title": "C32 Generator Set",
        "selector_version": "auto-20260921T140000Z",
        "screenshots": shots,
        "evidence": {"scroll": "pane #content, 3 steps"},
    }
    data.update(overrides)
    return ExtractionArtifact(**data)


@pytest.fixture
def store(tmp_path: Path) -> LocalJsonRepository:
    return LocalJsonRepository(tmp_path / "sis-results")


# ── the files that must exist after a successful lookup ─────────────────────
@pytest.mark.asyncio
async def test_a_successful_lookup_writes_json_txt_and_screenshots(
        store: LocalJsonRepository, tmp_path: Path) -> None:
    shots_src = tmp_path / "run"
    shots_src.mkdir()
    await store.upsert(_record(), extraction=_artifact(shots_src))

    stem = f"{SERIAL}_{RUN_ID}"
    assert (store.results / f"{stem}.json").exists()
    assert (store.results / f"{stem}.txt").exists()
    for name in ("page", "details", "parts"):
        assert (store.results / stem / f"{name}.png").exists(), name


@pytest.mark.asyncio
async def test_every_named_field_is_in_the_json(store: LocalJsonRepository) -> None:
    await store.upsert(_record(), extraction=_artifact())
    doc = json.loads((store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8"))

    assert doc["serial_number"] == SERIAL
    assert doc["run_id"] == RUN_ID
    assert doc["source"] == "Caterpillar SIS"
    assert doc["retrieved_at"].startswith("2026-09-21")
    assert doc["final_url"] == "https://sis2.cat.com/#/detail/JAZ01865"
    assert doc["page_title"] == "C32 Generator Set"
    assert doc["model"] == "C32"
    assert doc["equipment_type"] == "GENERATOR_SET"
    assert doc["machine_serial_number"] == SERIAL
    assert doc["machine_build_date"] == "2014-08-02"
    assert doc["engine_serial_number"] == "PRH04588"
    assert doc["engine_build_date"] == "2014-06-30"
    assert doc["specifications"]
    assert doc["parts_data"]["group_count"] == 1
    assert doc["parts_manual_url"].startswith("https://")
    assert doc["extraction_status"] == "SUCCESS"
    assert doc["selector_version"] == "auto-20260921T140000Z"
    assert "quality" in doc and "field_provenance" in doc
    assert doc["data_hash"] and doc["schema_version"]


@pytest.mark.asyncio
async def test_part_numbers_and_names_are_pulled_out_for_reading(
        store: LocalJsonRepository) -> None:
    await store.upsert(_record(), extraction=_artifact())
    doc = json.loads((store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8"))
    summary = doc["parts_summary"]
    assert summary["part_numbers"] == ["444-5867", "1000"]
    assert summary["part_names"] == ["General AR", "Engine"]
    assert summary["part_serial_numbers"] == ["PRH04588"]
    assert summary["group_titles"] == [f"Product - Entire Group ({SERIAL})"]


@pytest.mark.asyncio
async def test_a_field_with_no_column_is_kept_not_discarded(
        store: LocalJsonRepository) -> None:
    """Re-driving a browser to recover a field we already read is the failure
    this rule exists to prevent."""
    await store.upsert(_record(), extraction=_artifact())
    doc = json.loads((store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8"))
    raw = doc["raw_data"]["extracted_fields"]
    assert raw["warranty_status"] == "Expired"      # no schema column for this
    assert raw["dealer_code"] == "EG01"
    assert doc["raw_data"]["normalized_record"]["serial_number"] == SERIAL


@pytest.mark.asyncio
async def test_a_missing_value_is_null_never_invented(store: LocalJsonRepository) -> None:
    await store.upsert(_record(engine_serial_number=None, engine_build_date=None),
                       extraction=_artifact())
    text = (store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8")
    doc = json.loads(text)
    assert doc["engine_serial_number"] is None
    assert doc["engine_build_date"] is None
    assert doc["operation_manual_url"] is None
    readable = (store.results / f"{SERIAL}_{RUN_ID}.txt").read_text(encoding="utf-8")
    assert "Engine Serial Number" in readable
    engine_line = next(ln for ln in readable.splitlines()
                       if ln.strip().startswith("Engine Serial Number"))
    assert engine_line.strip().endswith(": null")


# ── the human-readable twin ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_the_txt_shows_the_fields_a_person_asked_for(
        store: LocalJsonRepository) -> None:
    await store.upsert(_record(), extraction=_artifact())
    text = (store.results / f"{SERIAL}_{RUN_ID}.txt").read_text(encoding="utf-8")
    for expected in ("CATERPILLAR SIS LOOKUP — JAZ01865", "Machine Serial Number",
                     "Machine Build Date", "Engine Serial Number", "Engine Build Date",
                     "2014-08-02", "2014-06-30", "PRH04588",
                     f"Product - Entire Group ({SERIAL})", "444-5867", "General AR",
                     "Part Number | Serial Number | Part Name"):
        assert expected in text, expected


# ── secrets ─────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("key", [
    "password", "SIS_PASSWORD", "sessionToken", "cookie", "Authorization",
    "storage_state", "mfa_code", "api_key",
])
def test_credential_shaped_keys_never_reach_disk(key: str) -> None:
    assert scrub({key: "hunter2"})[key] == REDACTED


def test_credential_shaped_text_is_masked_wherever_it_appears() -> None:
    out = scrub({"note": "logged in with password=hunter2 and token: abc.def"})
    assert "hunter2" not in out["note"]
    assert "abc.def" not in out["note"]


@pytest.mark.asyncio
async def test_the_live_credential_values_are_searched_for_and_masked(
        tmp_path: Path) -> None:
    """Belt and braces: even a value that arrives under an innocent key."""
    store = LocalJsonRepository(tmp_path / "s", secrets=("V7#mQ9!xLp2@Rt8Z",))
    await store.upsert(_record(), extraction=_artifact(
        fields={"note": "raw page text containing V7#mQ9!xLp2@Rt8Z somehow"}))
    body = (store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8")
    assert "V7#mQ9!xLp2@Rt8Z" not in body
    assert REDACTED in body


# ── reads: the store-first flow ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_what_was_written_is_what_comes_back(store: LocalJsonRepository) -> None:
    await store.upsert(_record(), extraction=_artifact())
    current = await store.get_current(SERIAL, "cat_sis")
    assert current is not None
    assert current["machine_serial_number"] == SERIAL
    assert current["engine_build_date"] == "2014-06-30"
    assert current["parts_data"]["total_rows"] == 2


@pytest.mark.asyncio
async def test_an_unknown_serial_reads_as_absent_not_as_an_error(
        store: LocalJsonRepository) -> None:
    assert await store.get_current("ZZZ00000", "cat_sis") is None


@pytest.mark.asyncio
async def test_a_second_identical_lookup_is_not_a_new_version(
        store: LocalJsonRepository) -> None:
    first = await store.upsert(_record(), extraction=_artifact())
    same = _record()
    same.automation_run_id = "run_02"
    same.data_hash = None
    second = await store.upsert(same, extraction=_artifact())
    assert first is True and second is False      # nothing changed, so no new version
    assert len(list(store.results.glob(f"{SERIAL}_*.json"))) == 2   # both runs kept


@pytest.mark.asyncio
async def test_a_not_found_is_stored_as_a_real_answer(store: LocalJsonRepository) -> None:
    await store.mark_not_found("ZZZ00000", "cat_sis", "run_03")
    # It comes back like any other row, marked NOT_FOUND — that is what lets the
    # freshness policy answer "no such serial" without driving a browser again.
    current = await store.get_current("ZZZ00000", "cat_sis")
    assert current is not None
    assert current["status"] == RecordStatus.NOT_FOUND.value
    doc = json.loads((store.results / "ZZZ00000_run_03.json").read_text(encoding="utf-8"))
    assert doc["extraction_status"] == "NOT_FOUND"


@pytest.mark.asyncio
async def test_runs_are_stored_and_readable(store: LocalJsonRepository) -> None:
    run = RunRecord(automation_run_id=RUN_ID, serial_number=SERIAL, source="cat_sis",
                    status=RunStatus.SUCCESS, trigger="user_request",
                    started_at=datetime.now(timezone.utc))
    await store.save_run(run)
    assert (await store.get_run(RUN_ID))["automation_run_id"] == RUN_ID


@pytest.mark.asyncio
async def test_health_means_writable_not_merely_present(tmp_path: Path) -> None:
    store = LocalJsonRepository(tmp_path / "s")
    assert await store.health() is True


# ── a write that fails is a failed lookup ───────────────────────────────────
@pytest.mark.asyncio
async def test_an_unwritable_store_fails_the_lookup_it_cannot_save(
        store: LocalJsonRepository) -> None:
    """The exact requirement: extraction succeeded, saving did not, so the
    caller must NOT be told the lookup succeeded."""
    from app.services.equipment_service import EquipmentService

    class Broken:
        async def upsert(self, record, *, extraction=None):
            raise OSError("No space left on device")

    service = EquipmentService.__new__(EquipmentService)
    service.repo = Broken()
    with pytest.raises(AutomationError) as exc:
        await service._persist(_record(), _artifact(), serial=SERIAL, source="cat_sis")
    assert exc.value.code is ErrorCode.PERSISTENCE_FAILED
    assert "No space left" in exc.value.details["error"]


def test_persistence_failed_is_not_retried_and_is_not_a_success() -> None:
    from app.core.errors import is_retryable, max_attempts

    assert is_retryable(ErrorCode.PERSISTENCE_FAILED) is False
    assert max_attempts(ErrorCode.PERSISTENCE_FAILED) == 1


def test_persist_runs_before_the_run_is_marked_successful() -> None:
    """Order matters: a run recorded as SUCCESS whose data was never saved is a
    lie told to every later reader."""
    source = (Path(__file__).resolve().parents[1]
              / "app" / "services" / "equipment_service.py").read_text(encoding="utf-8")
    persist_at = source.index('recorder.step("PERSIST")')
    succeed_at = source.index("await recorder.succeed(")
    assert persist_at < succeed_at


# ── a record is labelled by the source that produced it ─────────────────────
@pytest.mark.asyncio
async def test_a_sis_record_is_labelled_caterpillar_sis(
        store: LocalJsonRepository) -> None:
    await store.upsert(_record(), extraction=_artifact())
    doc = json.loads((store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8"))
    assert doc["source"] == "Caterpillar SIS"
    assert doc["source_system"] == "cat_sis"


@pytest.mark.asyncio
async def test_fixture_data_is_never_labelled_as_caterpillar_sis(
        tmp_path: Path) -> None:
    """The one mislabelling that would make every other guarantee worthless."""
    store = LocalJsonRepository(
        tmp_path / "s",
        source_labels={"local_fixture": "Local test fixture (NOT Caterpillar SIS)"})
    await store.upsert(_record(source_system="local_fixture"), extraction=_artifact())
    doc = json.loads((store.results / f"{SERIAL}_{RUN_ID}.json").read_text(encoding="utf-8"))
    assert doc["source"] == "Local test fixture (NOT Caterpillar SIS)"
    assert "Caterpillar SIS" not in doc["source"].replace("NOT Caterpillar SIS", "")
    text = (store.results / f"{SERIAL}_{RUN_ID}.txt").read_text(encoding="utf-8")
    assert "NOT Caterpillar SIS" in text


def test_an_unregistered_source_keeps_its_own_id(tmp_path: Path) -> None:
    store = LocalJsonRepository(tmp_path / "s")
    assert store.label_for("cat_sis") == "Caterpillar SIS"
    assert store.label_for("something_else") == "something_else"


# ── rendering ───────────────────────────────────────────────────────────────
def test_the_readable_file_states_that_null_means_not_published() -> None:
    text = render_text({"serial_number": SERIAL, "source": "Caterpillar SIS"})
    assert "NOT published by the source" in text
    assert "null" in text


# ── a degraded store must not lose a correct answer ─────────────────────────
class _DiskFull:
    """A file-backed store on a full disk: reads and writes raise OSError, not
    AutomationError. The service must survive everything except the one write
    that defines success."""

    async def get_current(self, serial_number, source):
        raise OSError("No space left on device")

    async def mark_not_found(self, serial_number, source, run_id):
        raise OSError("No space left on device")

    async def save_run(self, run):
        raise OSError("No space left on device")


@pytest.mark.asyncio
async def test_an_unreadable_store_falls_through_to_a_live_lookup() -> None:
    from app.services.equipment_service import EquipmentService

    service = EquipmentService.__new__(EquipmentService)
    service.repo = _DiskFull()
    assert await service._safe_get_current(SERIAL, "cat_sis") is None


@pytest.mark.asyncio
async def test_failing_to_cache_a_negative_does_not_replace_the_negative() -> None:
    """SERIAL_NOT_FOUND is the true answer; an error while caching it must not
    become the error the user sees."""
    from app.services.equipment_service import EquipmentService

    service = EquipmentService.__new__(EquipmentService)
    service.repo = _DiskFull()
    await service._safe_mark_not_found(SERIAL, "cat_sis", RUN_ID)   # must not raise


@pytest.mark.asyncio
async def test_losing_the_audit_row_does_not_lose_the_answer() -> None:
    from app.services.run_recorder import RunRecorder

    recorder = RunRecorder(_DiskFull(), serial_number=SERIAL, source="cat_sis",
                           trigger="user_request", requested_by="test")
    await recorder.start()          # must not raise
