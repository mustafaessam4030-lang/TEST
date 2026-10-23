"""KPI calculations against hand-computed SYNTHETIC fixtures (conftest.KNOWN_ROWS)."""
from datetime import date

import pytest

from conftest import AS_OF, KNOWN_ROWS, MARCH, build
from si.query import Period

EXPECTED_MARCH_UTC = {
    "requests_created": (7, 7),            # K1 K2 K3 K5 K7 K8 K9
    "requests_closed": (6, 6),             # K1 K2 K3 K4 K8 K12 (close time in March)
    "avg_resolution_hours": (56.8, 5),     # (10 + 48 + 4 + 216 + 6) / 5; K8 closed before it opened is excluded
    "median_resolution_hours": (10.0, 5),
    "p90_resolution_hours": (148.8, 5),    # 48 + 0.6 * (216 - 48)
    "sla_compliance_rate": (1 / 3, 3),     # K1 met; K2 and K4 breached; K3 K12 have no target
    "sla_breach_rate": (2 / 3, 3),
}
EXPECTED_SNAPSHOT = {
    "open_backlog": (2, 2),                # K5 K6 (K9's status is unmapped; K2's stale duplicate is removed)
    "aged_backlog": (1, 1),                # K6, 89 days old
    "aged_backlog_share": (0.5, 2),
    "avg_backlog_age_days": (52.5, 2),     # (16 + 89) / 2
    "open_past_due": (1, 1),               # K5 target 16 March
}


@pytest.mark.parametrize("metric_id,expected", list(EXPECTED_MARCH_UTC.items()))
def test_period_metrics_match_hand_calculation(known, metric_id, expected):
    r = known.analytics.metric(metric_id, MARCH, {}, {}, AS_OF, compare=False)
    assert r["value"] == pytest.approx(expected[0])
    assert r["n"] == expected[1]
    assert r["evidence"]["query_id"].startswith("fixture-")


@pytest.mark.parametrize("metric_id,expected", list(EXPECTED_SNAPSHOT.items()))
def test_snapshot_metrics_match_hand_calculation(known, metric_id, expected):
    r = known.analytics.metric(metric_id, None, {}, {}, AS_OF)
    assert r["value"] == pytest.approx(expected[0])
    assert r["n"] == expected[1]
    assert r["period"] is None and "previous" not in r


def test_compliance_and_breach_are_complementary(known):
    a = known.analytics.metric("sla_compliance_rate", MARCH, {}, {}, AS_OF, compare=False)["value"]
    b = known.analytics.metric("sla_breach_rate", MARCH, {}, {}, AS_OF, compare=False)["value"]
    assert a + b == pytest.approx(1.0)


def test_period_boundaries_are_inclusive_start_exclusive_end(known):
    # K4 closes exactly at 2026-03-01 00:00 UTC: inside March, outside February.
    feb = Period(date(2026, 2, 1), date(2026, 3, 1))
    closed_feb = known.analytics.metric("requests_closed", feb, {}, {}, AS_OF, compare=False)
    assert closed_feb["n"] == 0
    one_day = Period(date(2026, 3, 1), date(2026, 3, 2))
    assert known.analytics.metric("requests_closed", one_day, {}, {}, AS_OF, compare=False)["n"] == 2   # K4, K12


def test_reporting_time_zone_moves_period_boundaries(tmp_path):
    cairo = build(tmp_path, KNOWN_ROWS, tz="Africa/Cairo")
    r = cairo.analytics.metric("requests_created", MARCH, {}, {}, AS_OF, compare=False)
    assert r["value"] == 8          # K12 opened 01:30 on 1 March local time


def test_series_buckets_in_reporting_time_zone(tmp_path):
    cairo = build(tmp_path, KNOWN_ROWS, tz="Africa/Cairo")
    s = cairo.analytics.series("requests_created", MARCH, "day", {}, {}, AS_OF)
    first = s["points"][0]
    assert first["bucket"].startswith("2026-03-01") and first["value"] == 1    # K12


def test_previous_period_comparison(known):
    r = known.analytics.metric("requests_created", MARCH, {}, {}, AS_OF)
    prev = r["previous"]
    assert prev["start"] == "2026-01-29" and prev["end"] == "2026-03-01"
    assert prev["value"] == 2          # K4 (20 Feb), K12 (28 Feb)
    assert r["change"]["abs"] == 5 and r["change"]["pct"] == pytest.approx(2.5)
    # Below min_sample on either side, no change is reported.
    med = known.analytics.metric("median_resolution_hours", MARCH, {}, {}, AS_OF)
    assert "change" not in med


def test_null_dimension_is_its_own_bucket_and_filterable(known):
    b = known.analytics.breakdown("requests_created", "priority", MARCH, {}, {}, AS_OF)
    segs = {r["segment"]: r["value"] for r in b["rows"]}
    assert segs["(not set)"] == 1      # K9 has no priority
    f = known.analytics.metric("requests_created", MARCH, {"priority": ("(not set)",)}, {}, AS_OF, compare=False)
    assert f["value"] == 1


def test_filters_and_scope_both_apply(known):
    north = known.analytics.metric("requests_created", MARCH, {"region": ("North",)}, {}, AS_OF, compare=False)
    assert north["value"] == 5
    both = known.analytics.metric("requests_created", MARCH, {"region": ("South",)}, {"region": ("North",)}, AS_OF,
                                  compare=False)
    assert both["value"] == 0          # a filter can never widen the user's scope


def test_small_samples_are_flagged(known):
    r = known.analytics.metric("median_resolution_hours", MARCH, {}, {}, AS_OF, compare=False)
    assert r["sufficient"] is False and "at least 10" in r["reason"]


def test_empty_period_returns_no_data_not_zero(known):
    empty = Period(date(2020, 1, 1), date(2020, 2, 1))
    r = known.analytics.metric("avg_resolution_hours", empty, {}, {}, AS_OF, compare=False)
    assert r["value"] is None and r["n"] == 0


def test_data_quality_profile(known):
    q = known.analytics.quality({})
    assert q["total_rows"] == 10
    issues = {i["check"]: i["count"] for i in q["issues"]}
    assert issues["duplicate_ids"] == 0
    assert issues["unmapped_status"] == 1
    assert issues["closed_before_created"] == 1
    comp = {c["column"]: c for c in q["completeness"]}
    assert comp["priority"]["missing"] == 1 and comp["region"]["missing"] == 1


def test_freshness_and_incomplete_history_notes(known):
    fresh = known.analytics.freshness({}, now=AS_OF)
    assert fresh["as_of"] == AS_OF and fresh["basis"] == "load time" and fresh["stale"] is False
    notes = known.analytics.period_notes(Period(date(2025, 12, 1), date(2026, 4, 5)), fresh)
    assert any("earliest record" in n for n in notes) and any("last data refresh" in n for n in notes)
    later = known.analytics.freshness({}, now=AS_OF.replace(month=4, day=5))
    assert later["stale"] is True


def test_drilldown_returns_the_records_behind_a_figure(known):
    d = known.analytics.drilldown("aged_backlog", None, {}, {}, AS_OF, limit=10, offset=0)
    assert [r["request_id"] for r in d["rows"]] == ["K6"]
    assert "customer_name" not in d["columns"] and "problem_description" not in d["columns"]
    page = known.analytics.drilldown("requests_created", MARCH, {}, {}, AS_OF, limit=3, offset=0)
    assert len(page["rows"]) == 3 and page["has_more"] is True
