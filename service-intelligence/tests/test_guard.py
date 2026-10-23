"""SQL validation: only one bounded read-only SELECT over approved objects and columns."""
from datetime import datetime

import pytest

from conftest import MARCH, VIEW
from si import contract
from si.guard import GuardError, Policy, validate

POLICY = Policy(relations=frozenset({VIEW}), columns=frozenset(contract.NAMES) | {"metric_value", "n", "segment", "bucket", "value"},
                max_rows=5000, denied_columns=frozenset({"customer_name", "problem_description"}))

REFUSED = [
    ("DROP TABLE " + VIEW, "Only SELECT"),
    ("DELETE FROM " + VIEW + " WHERE 1=1", "Only SELECT"),
    ("UPDATE " + VIEW + " SET status = 'x'", "Only SELECT"),
    ("INSERT INTO " + VIEW + " (request_id) VALUES ('x')", "Only SELECT"),
    ("MERGE INTO " + VIEW + " t USING " + VIEW + " s ON t.request_id = s.request_id WHEN MATCHED THEN DELETE", "Only SELECT"),
    ("CREATE TABLE X.Y.Z AS SELECT request_id FROM " + VIEW, "Only SELECT"),
    ("GRANT SELECT ON " + VIEW + " TO ROLE PUBLIC", ""),
    ("CALL SYSTEM$WAIT(10)", ""),
    ("USE ROLE ACCOUNTADMIN", ""),
    ("ALTER SESSION SET QUERY_TAG = 'x'", ""),
    (f"SELECT COUNT(*) FROM {VIEW}; DROP TABLE {VIEW}", "Exactly one statement"),
    (f"SELECT request_id FROM {VIEW} LIMIT 10; SELECT 1", "Exactly one statement"),
    (f"SELECT * FROM {VIEW} LIMIT 10", "SELECT *"),
    ("SELECT request_id FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY LIMIT 10", "not approved"),
    ("SELECT request_id FROM V_SERVICE_REQUESTS LIMIT 10", "fully qualified"),
    (f"SELECT customer_name FROM {VIEW} LIMIT 10", "restricted"),
    (f"SELECT password FROM {VIEW} LIMIT 10", "not approved"),
    (f"SELECT request_id FROM {VIEW}", "LIMIT"),
    (f"SELECT request_id FROM {VIEW} LIMIT 100000", "LIMIT"),
    (f"SELECT request_id FROM {VIEW} LIMIT ?", "LIMIT"),
    (f"SELECT SYSTEM$WHITELIST() AS n FROM {VIEW}", "System function"),
    (f"SELECT MY_UDF(request_id) AS n FROM {VIEW}", "not allowed"),
    ("SELECT n FROM TABLE(RESULT_SCAN(LAST_QUERY_ID())) LIMIT 1", ""),
    (f"SELECT request_id FROM {VIEW} UNION SELECT request_id FROM OTHER.DB.SECRETS LIMIT 5", "not approved"),
    ("WITH x AS (SELECT request_id FROM OTHER.DB.T) SELECT request_id FROM x LIMIT 5", "not approved"),
    ("", "Empty"),
    ("SELEC request_id FRM", ""),
]


@pytest.mark.parametrize("sql,why", REFUSED)
def test_prohibited_statements_are_refused(sql, why):
    with pytest.raises(GuardError, match=why or None):
        validate(sql, POLICY)


def test_aggregate_without_limit_is_allowed():
    validate(f"SELECT COUNT(DISTINCT request_id) AS metric_value, COUNT(*) AS n FROM {VIEW} WHERE status_class = 'OPEN'", POLICY)


def test_every_registry_query_passes_the_guard(known):
    b, as_of = known.builder, datetime(2026, 3, 31)
    for m in known.registry.metrics.values():
        period = MARCH if m.kind == "period" else None
        specs = [b.value(m.id, period, {"region": ("North", "(not set)")}, {"team": ("A",)}, as_of),
                 b.breakdown(m.id, "category", period, {}, {}, as_of),
                 b.drilldown(m.id, period, {}, {}, as_of)]
        if m.kind == "period":
            specs.append(b.series(m.id, MARCH, "week", {}, {}, as_of))
        for s in specs:
            validate(s.sql, POLICY)
    validate(b.quality({}).sql, POLICY)
    validate(b.dimension_values("region", {}).sql, POLICY)


def test_user_values_are_bound_never_inlined(known):
    evil = "x' OR '1'='1"
    s = known.builder.value("requests_created", MARCH, {"region": (evil,)}, {}, datetime(2026, 3, 31))
    assert evil not in s.sql and evil in s.params


def test_refused_query_never_reaches_the_warehouse(known):
    from si.backends.base import BackendError
    from si.query import QuerySpec
    before = known.executor.warehouse_queries
    with pytest.raises(BackendError) as exc:
        known.executor.run(QuerySpec(f"DELETE FROM {VIEW}", (), "test"))
    assert exc.value.kind == "refused"
    assert known.executor.warehouse_queries == before
    assert known.executor.recent[-1]["status"] == "refused"
