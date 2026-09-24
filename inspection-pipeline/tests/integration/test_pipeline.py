"""End-to-end pipeline runs with a fake collector and the DuckDB-backed repository."""

import json

import pytest

from app.collectors.base import CollectionResult, InspectionCollector
from app.errors import AuthenticationError, WarehouseConnectionError
from app.models import RunStatus, SourceRecord
from app.pipeline import PipelineDeps, run_pipeline
from app.warehouse.repository import InspectionRepository
from tests.conftest import make_settings, synthetic_record


class FakeCollector(InspectionCollector):
    collector_type = "api"
    lookup_mode = "path"

    def __init__(self, source, records=None, error=None, expected_total=None):
        self.field_mapping = source.api.fields
        self.records, self.error, self.expected_total = records or [], error, expected_total
        self.calls = []

    async def collect(self, serial_number=None, inspection_number=None):
        self.calls.append((serial_number, inspection_number))
        if self.error:
            raise self.error
        return CollectionResult(
            records=[SourceRecord(raw=r, locator=f"index={i}") for i, r in enumerate(self.records)],
            pages_fetched=1, expected_total=self.expected_total)


def _deps(duck, source_config, collector, connect=None):
    return PipelineDeps(
        load_source=lambda path: source_config,
        build_collector=lambda s, c, d: collector,
        connect=connect or (lambda s, run_id: duck),
        repository=InspectionRepository,
    )


@pytest.fixture
def settings(tmp_path):
    return make_settings(diagnostics_dir=tmp_path)


def _runs(duck):
    return duck.rows("SELECT RUN_ID, STATUS, RECORDS_FOUND, RECORDS_VALID, RECORDS_LOADED, RECORDS_FAILED, "
                     "ERROR_MESSAGE, END_TIME IS NOT NULL FROM PIPELINE_RUNS ORDER BY START_TIME")


async def test_success_then_idempotent_rerun(duck, settings, source_config):
    records = [synthetic_record(i) for i in range(4)]
    run = await run_pipeline(settings, deps=_deps(duck, source_config, FakeCollector(source_config, records)))
    assert run.status == RunStatus.SUCCESS
    assert (run.records_found, run.records_valid, run.records_loaded, run.records_inserted) == (4, 4, 4, 4)
    run2 = await run_pipeline(settings, deps=_deps(duck, source_config, FakeCollector(source_config, records)))
    assert run2.run_id != run.run_id and run2.records_unchanged == 4
    assert duck.rows("SELECT COUNT(*) FROM INSPECTIONS") == [(4,)]
    assert [r[1] for r in _runs(duck)] == ["SUCCESS", "SUCCESS"]
    lineage = duck.rows("SELECT DISTINCT RUN_ID FROM RAW_INSPECTIONS")
    assert {r[0] for r in lineage} == {str(run.run_id), str(run2.run_id)}


async def test_partial_success_records_failures(duck, settings, source_config):
    records = [synthetic_record(1), synthetic_record(2, serialNo=""), synthetic_record(3)]
    run = await run_pipeline(settings, deps=_deps(duck, source_config, FakeCollector(source_config, records)))
    assert run.status == RunStatus.PARTIAL_SUCCESS
    assert (run.records_valid, run.records_failed, run.records_loaded) == (2, 1, 2)
    assert "VALIDATION_ERRORS" in run.error_message
    assert duck.rows("SELECT RUN_ID, RECORD_IDENTIFIER FROM VALIDATION_ERRORS") == [(str(run.run_id), "30080002")]


async def test_incomplete_collection_is_partial(duck, settings, source_config):
    collector = FakeCollector(source_config, [synthetic_record(1)], expected_total=3)
    run = await run_pipeline(settings, deps=_deps(duck, source_config, collector))
    assert run.status == RunStatus.PARTIAL_SUCCESS and "reported 3" in run.error_message


async def test_all_invalid_is_failed(duck, settings, source_config):
    collector = FakeCollector(source_config, [synthetic_record(1, status="")])
    run = await run_pipeline(settings, deps=_deps(duck, source_config, collector))
    assert run.status == RunStatus.FAILED and run.records_failed == 1


async def test_min_expected_records(duck, tmp_path, source_config):
    settings = make_settings(min_expected_records=1, diagnostics_dir=tmp_path)
    run = await run_pipeline(settings, deps=_deps(duck, source_config, FakeCollector(source_config, [])))
    assert run.status == RunStatus.FAILED and "expected at least 1" in run.error_message


async def test_collector_failure_is_recorded(duck, settings, source_config):
    collector = FakeCollector(source_config, error=AuthenticationError("Login did not succeed"))
    run = await run_pipeline(settings, deps=_deps(duck, source_config, collector))
    assert run.status == RunStatus.FAILED
    row = _runs(duck)[0]
    assert row[1] == "FAILED" and "AuthenticationError" in row[6] and row[7] is True


async def test_load_failure_marks_run_failed_and_keeps_audit(duck, settings, source_config):
    duck.fail_on = "MERGE INTO"  # the MERGE fails; the run row must still be finalised
    run = await run_pipeline(settings, deps=_deps(duck, source_config, FakeCollector(source_config, [synthetic_record(1)])))
    assert run.status == RunStatus.FAILED and run.records_loaded == 0
    assert "WarehouseError" in run.error_message
    assert duck.rows("SELECT COUNT(*) FROM RAW_INSPECTIONS") == [(0,)]  # rolled back
    assert _runs(duck)[0][1] == "FAILED"


async def test_snowflake_unreachable_fails_without_collecting(settings, source_config):
    collector = FakeCollector(source_config, [synthetic_record(1)])

    def connect(s, run_id):
        raise WarehouseConnectionError("Snowflake unreachable")

    run = await run_pipeline(settings, deps=_deps(None, source_config, collector, connect=connect))
    assert run.status == RunStatus.FAILED and collector.calls == []


async def test_skip_if_succeeded_today(duck, settings, source_config):
    records = [synthetic_record(1)]
    await run_pipeline(settings, deps=_deps(duck, source_config, FakeCollector(source_config, records)))
    collector = FakeCollector(source_config, records)
    assert await run_pipeline(settings, skip_if_succeeded_today=True,
                              deps=_deps(duck, source_config, collector)) is None
    assert collector.calls == []


async def test_dry_run_writes_output_and_skips_snowflake(settings, source_config, tmp_path):
    out = tmp_path / "out.jsonl"
    collector = FakeCollector(source_config, [synthetic_record(1), synthetic_record(2, status=None)])

    def connect(s, run_id):
        raise AssertionError("dry run must not connect")

    run = await run_pipeline(settings, dry_run=True, output_path=out,
                             deps=_deps(None, source_config, collector, connect=connect))
    lines = [json.loads(line) for line in out.read_text().splitlines()]
    assert [line["type"] for line in lines] == ["inspection", "failure"]
    assert run.status == RunStatus.PARTIAL_SUCCESS


async def test_filters_are_passed_and_audited(duck, settings, source_config):
    collector = FakeCollector(source_config, [synthetic_record(1)])
    await run_pipeline(settings, serial_number="SYW00001", deps=_deps(duck, source_config, collector))
    assert collector.calls == [("SYW00001", None)]
    assert json.loads(duck.rows("SELECT FILTERS FROM PIPELINE_RUNS")[0][0]) == {"serial_number": "SYW00001"}


def test_cli_exit_codes(monkeypatch):
    from app import collect
    from app.models import PipelineRun

    async def fake_run(settings, **kw):
        return PipelineRun(source="w", collector_type="api", status=RunStatus.PARTIAL_SUCCESS)

    monkeypatch.setattr(collect, "run_pipeline", fake_run)
    monkeypatch.setattr(collect, "Settings", lambda: make_settings())
    assert collect.main([]) == 2
