"""Discovery helpers against canned metadata and the SYNTHETIC fixture (no Snowflake)."""
import pytest

from si.discover import check_mapping_with, inventory, profile, score_table, to_markdown
from si.fixtures import RAW_TABLE


def fake_runner(tables, columns, fail_db=None):
    def run(sql, params):
        if sql.startswith("SELECT CURRENT_ACCOUNT"):
            return [{"account": "ACC", "region": "AZURE_WESTEUROPE", "role": "SI_READER", "warehouse": "SI_WH",
                     "available_roles": '["SI_READER"]', "version": "9.0"}]
        if sql == "SHOW WAREHOUSES":
            return [{"name": "SI_WH", "state": "SUSPENDED", "size": "X-Small", "auto_suspend": 60, "auto_resume": "true"}]
        if sql == "SHOW DATABASES":
            return [{"name": "OPS"}, {"name": "SNOWFLAKE"}, {"name": "LOCKED"}]
        if '"LOCKED"' in sql:
            raise PermissionError("not authorized")
        if "INFORMATION_SCHEMA.TABLES" in sql:
            return tables
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return columns
        raise AssertionError(sql)
    return run


TABLES = [{"table_schema": "SVC", "table_name": "WORK_ORDERS", "table_type": "BASE TABLE", "row_count": 1200, "bytes": 1, "last_altered": "2026-10-01"},
          {"table_schema": "HR", "table_name": "EMPLOYEES", "table_type": "BASE TABLE", "row_count": 50, "bytes": 1, "last_altered": "2026-09-01"}]
COLUMNS = [{"table_schema": "SVC", "table_name": "WORK_ORDERS", "column_name": c, "data_type": t, "is_nullable": "YES"}
           for c, t in [("WO_ID", "NUMBER"), ("CREATED_DATE", "TIMESTAMP_NTZ"), ("COMPLETED_DATE", "TIMESTAMP_NTZ"),
                        ("STATUS", "TEXT"), ("PRIORITY", "TEXT"), ("CUSTOMER_NAME", "TEXT")]] + \
          [{"table_schema": "HR", "table_name": "EMPLOYEES", "column_name": c, "data_type": "TEXT", "is_nullable": "YES"}
           for c in ("EMP_ID", "FULL_NAME")]


def test_inventory_ranks_service_like_tables_and_flags_sensitive_columns():
    inv = inventory(fake_runner(TABLES, COLUMNS))
    assert inv["databases"] == ["OPS", "SNOWFLAKE", "LOCKED"]
    top = inv["tables"][0]
    assert top["name"] == "OPS.SVC.WORK_ORDERS" and top["score"] > inv["tables"][1]["score"]
    assert "CREATED_DATE" in top["candidate_columns"]["created"]
    assert "CUSTOMER_NAME" in top["possibly_sensitive"]
    assert any("LOCKED" in e for e in inv["errors"])
    md = to_markdown(inv)
    assert "OPS.SVC.WORK_ORDERS" in md and "SI_WH" in md and "Not readable" in md


def test_score_requires_timestamp_types_for_time_roles():
    s = score_table("THING", [{"column_name": "CREATED_BY", "data_type": "TEXT"}])
    assert "created" not in s["candidate_columns"]


def test_profile_runs_aggregate_only_sql(known):
    def run(sql, params):
        assert "SELECT *" not in sql
        return known.backend.query(sql, params, timeout_s=10, max_rows=1000, tag="t").records()
    p = profile(run, RAW_TABLE, "TICKET_NO", "OPENED_TS", "STATUS_TXT", ["PRIO", "REGION_NAME"])
    assert p["total_rows"] == 11 and p["distinct_ids"] == 10 and p["duplicate_rows"] == 1
    assert p["null_share"]["PRIO"] == pytest.approx(1 / 11)
    assert {s["value"] for s in p["status_values"]} >= {"Closed", "Pending Review"}
    with pytest.raises(ValueError):
        profile(run, "x; drop", "a", "b", None, [])


def test_mapping_check_reports_missing_columns_and_unmapped_status(mapping):
    cols = [{"column_name": c} for c in ("TICKET_NO", "OPENED_TS", "RESOLVED_TS", "TARGET_TS", "LAST_MODIFIED_TS",
                                         "ETL_LOADED_TS", "STATUS_TXT", "PRIO", "ISSUE_CATEGORY", "SVC_TYPE", "WORK_CENTRE")]

    def run(sql, params):
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return cols
        return [{"value": "PENDING REVIEW", "n": 3}, {"value": "CLOSED", "n": 10}]
    problems = check_mapping_with(run, mapping)
    assert problems == ["region: column REGION_NAME not found in SI_SYNTHETIC.RAW.SERVICE_REQUESTS"]
    cols.append({"column_name": "REGION_NAME"})
    assert check_mapping_with(run, mapping) == ["status value 'PENDING REVIEW' (3 rows) is not in status_classes"]
