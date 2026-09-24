"""Runs the repository's real Snowflake SQL (MERGE, INSERT...SELECT FROM VALUES) on DuckDB."""

import json
from datetime import datetime, timezone

import pytest

from app.errors import WarehouseError
from app.models import Attachment, Inspection, PipelineRun, RunStatus, ValidationFailure
from app.warehouse.repository import InspectionRepository, split_statements


def _insp(run_id, n, status="Completed", **kw):
    return Inspection(run_id=run_id, source="website", collector_type="api", inspection_number=str(n),
                      serial_number=f"SN{n}", status=status, summary={"passed": 75},
                      attachments=[Attachment(name=f"r{n}.pdf", url=f"/f/{n}")],
                      collected_at=datetime.now(timezone.utc), raw_data={"n": n, "status": status}, **kw)


def _run(repo):
    run = PipelineRun(source="website", collector_type="api")
    repo.start_run(run)
    return run


def test_schema_splits_into_create_statements():
    from importlib import resources
    sql = resources.files("app.warehouse").joinpath("schema.sql").read_text()
    statements = split_statements(sql)
    assert len(statements) == 4 and all(s.startswith("CREATE TABLE IF NOT EXISTS") for s in statements)


def test_first_load_inserts_raw_and_current(duck):
    repo = InspectionRepository(duck, batch_size=2)
    run = _run(repo)
    stats = repo.load(run.run_id, [_insp(run.run_id, n) for n in range(5)])
    assert (stats.loaded, stats.inserted, stats.updated, stats.unchanged) == (5, 5, 0, 0)
    assert duck.rows("SELECT COUNT(*) FROM RAW_INSPECTIONS") == [(5,)]
    row = duck.rows("SELECT SERIAL_NUMBER, ATTACHMENT_COUNT, FIRST_SEEN_RUN_ID, RAW_JSON FROM INSPECTIONS "
                    "WHERE INSPECTION_NUMBER = '3'")[0]
    assert row[0] == "SN3" and row[1] == 1 and row[2] == str(run.run_id)
    assert json.loads(row[3]) == {"n": 3, "status": "Completed"}
    # batching: 5 rows / batch_size 2 -> 3 INSERT statements
    assert sum("INSERT INTO RAW_INSPECTIONS" in s for s in duck.statements) == 3


def test_rerun_is_idempotent_and_changes_are_merged(duck):
    repo = InspectionRepository(duck)
    run1 = _run(repo)
    repo.load(run1.run_id, [_insp(run1.run_id, n) for n in range(3)])

    run2 = _run(repo)
    stats = repo.load(run2.run_id, [_insp(run2.run_id, n) for n in range(3)])
    assert (stats.inserted, stats.updated, stats.unchanged) == (0, 0, 3)

    run3 = _run(repo)
    records = [_insp(run3.run_id, 0), _insp(run3.run_id, 1, status="Re-opened"), _insp(run3.run_id, 7)]
    stats = repo.load(run3.run_id, records)
    assert (stats.inserted, stats.updated, stats.unchanged) == (1, 1, 1)

    assert duck.rows("SELECT COUNT(*) FROM INSPECTIONS") == [(4,)]          # no business-key duplicates
    assert duck.rows("SELECT COUNT(*) FROM RAW_INSPECTIONS") == [(9,)]      # full history kept
    changed = duck.rows("SELECT STATUS, FIRST_SEEN_RUN_ID, LAST_CHANGED_RUN_ID, LAST_SEEN_RUN_ID "
                        "FROM INSPECTIONS WHERE INSPECTION_NUMBER = '1'")[0]
    assert changed == ("Re-opened", str(run1.run_id), str(run3.run_id), str(run3.run_id))
    untouched = duck.rows("SELECT LAST_CHANGED_RUN_ID, LAST_SEEN_RUN_ID FROM INSPECTIONS "
                          "WHERE INSPECTION_NUMBER = '2'")[0]
    assert untouched == (str(run1.run_id), str(run2.run_id))  # not seen in run3, so not moved


def test_load_failure_rolls_back_raw_rows(duck):
    repo = InspectionRepository(duck)
    run = _run(repo)
    duck.fail_on = "MERGE INTO"
    with pytest.raises(WarehouseError, match="injected"):
        repo.load(run.run_id, [_insp(run.run_id, 1)])
    duck.fail_on = None
    assert duck.rows("SELECT COUNT(*) FROM RAW_INSPECTIONS") == [(0,)]


def test_run_tracking_and_failures(duck):
    repo = InspectionRepository(duck)
    run = _run(repo)
    assert duck.rows("SELECT STATUS FROM PIPELINE_RUNS") == [("STARTED",)]
    failure = ValidationFailure(run_id=run.run_id, source="website", record_identifier="42",
                                error_type="VALIDATION", error_message="status: is required",
                                raw_data={"id": 42, "text": "quote ' and \" chars"})
    repo.record_failures([failure])
    run.status, run.records_found, run.records_failed = RunStatus.PARTIAL_SUCCESS, 10, 1
    run.end_time = datetime.now(timezone.utc)
    repo.finish_run(run)
    assert duck.rows("SELECT STATUS, RECORDS_FOUND, RECORDS_FAILED FROM PIPELINE_RUNS") == [("PARTIAL_SUCCESS", 10, 1)]
    row = duck.rows("SELECT RECORD_IDENTIFIER, RAW_JSON FROM VALIDATION_ERRORS")[0]
    assert row[0] == "42" and json.loads(row[1])["text"] == "quote ' and \" chars"
    assert repo.has_successful_run_on("website", datetime.now(timezone.utc)) is False
    run.status = RunStatus.SUCCESS
    repo.finish_run(run)
    assert repo.has_successful_run_on("website", datetime.now(timezone.utc)) is True
