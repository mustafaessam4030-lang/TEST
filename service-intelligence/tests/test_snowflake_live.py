"""Live Snowflake checks. Skipped unless RUN_SNOWFLAKE_TESTS=1 and SI_BACKEND=snowflake are set.

They are read-only: connectivity, the contract view's shape, one KPI through the
same code path as the dashboard, and refusal of a write by the reader role.
"""
import os

import pytest

pytestmark = [pytest.mark.snowflake,
              pytest.mark.skipif(os.getenv("RUN_SNOWFLAKE_TESTS") != "1", reason="set RUN_SNOWFLAKE_TESTS=1 to run")]


@pytest.fixture(scope="module")
def live():
    from si.config import load_settings
    from si.service import ServiceIntel
    s = load_settings()
    assert s.backend == "snowflake", "SI_BACKEND must be snowflake"
    return ServiceIntel(s)


def test_contract_view_is_readable_and_fresh(live):
    fresh = live.analytics.freshness({})
    assert fresh["total_rows"] > 0 and fresh["as_of"] is not None
    assert fresh["quality"]["evidence"]["query_id"]


def test_one_kpi_end_to_end(live):
    fresh = live.analytics.freshness({})
    period = live.analytics.default_period(fresh["as_of"])
    r = live.analytics.metric("requests_created", period, {}, {}, fresh["as_of"])
    assert r["value"] is not None and r["evidence"]["query_id"]


def test_reader_role_cannot_write(live):
    from si.backends.base import BackendError
    with pytest.raises(BackendError):
        live.backend.query(f"DELETE FROM {live.settings.contract_view}", (), timeout_s=30, max_rows=1, tag="live_test")
