"""Optional smoke test against a real Snowflake schema.

Skipped unless RUN_SNOWFLAKE_TESTS=1 and SNOWFLAKE_* variables point at a
disposable schema. It creates the tables, loads two runs and checks MERGE.
"""

import os
from datetime import datetime, timezone

import pytest

from app.config import Settings
from app.models import Inspection, PipelineRun, RunStatus
from app.warehouse.connection import connect
from app.warehouse.repository import InspectionRepository

pytestmark = [
    pytest.mark.snowflake,
    pytest.mark.skipif(os.getenv("RUN_SNOWFLAKE_TESTS") != "1", reason="set RUN_SNOWFLAKE_TESTS=1 to run"),
]


def test_live_merge_roundtrip():
    settings = Settings()
    conn = connect(settings, "live-test")
    repo = InspectionRepository(conn)
    try:
        repo.ensure_schema()
        for _ in range(2):
            run = PipelineRun(source="live-test", collector_type="api")
            repo.start_run(run)
            rec = Inspection(run_id=run.run_id, source="live-test", collector_type="api",
                             inspection_number="LIVE-1", serial_number="SN", status="Completed",
                             summary={"passed": 1}, collected_at=datetime.now(timezone.utc), raw_data={"a": 1})
            stats = repo.load(run.run_id, [rec])
            run.status, run.end_time = RunStatus.SUCCESS, datetime.now(timezone.utc)
            repo.finish_run(run)
        assert stats.unchanged == 1
    finally:
        conn.close()
