"""Shared fixtures.

The API payloads below are SYNTHETIC test fixtures. Their field names
(``inspectionNo``, ``serialNo``, ...) are invented for tests only and say
nothing about the real website, whose structure must come from discovery.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import duckdb
import pytest

from app.config import Settings
from app.source_config import SourceConfig

pytest_plugins: list[str] = []


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "website_base_url": "https://source.test",
        "auth_mode": "none",
        "retry_base_delay_seconds": 0.0,
        "http_max_attempts": 3,
        "source_name": "website",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(diagnostics_dir=tmp_path / "diagnostics")


def synthetic_record(n: int, **overrides: Any) -> dict[str, Any]:
    record = {
        "inspectionNo": f"3008{n:04d}",
        "serialNo": f"SYW{n:05d}",
        "status": "Completed",
        "completedAt": "2026-09-01T08:30:00Z",
        "results": {"critical": 0, "warning": n % 3, "passed": 75},
        "attachments": [{"fileName": f"report-{n}.pdf", "downloadUrl": f"/files/{n}"}],
    }
    record.update(overrides)
    return record


SOURCE_DICT: dict[str, Any] = {
    "auth": {
        "login_path": "/login",
        "username_label": "Username",
        "password_label": "Password",
        "submit_button_name": "Sign in",
        "success_url_pattern": r".*/inspections.*",
    },
    "api": {
        "request": {
            "method": "GET",
            "path": "/api/inspections",
            "filter_params": {"serial_number": "sn", "inspection_number": "no"},
        },
        "records_path": "data.items",
        "pagination": {"type": "page", "page_param": "page", "size_param": "size", "page_size": 2,
                       "start_page": 1, "total_path": "data.total"},
        "fields": {
            "inspection_number": "inspectionNo",
            "serial_number": "serialNo",
            "status": "status",
            "inspection_date": "completedAt",
            "summary": {"critical": "results.critical", "warning": "results.warning", "passed": "results.passed"},
            "attachments": "attachments",
            "attachment_fields": {"name": "fileName", "url": "downloadUrl"},
        },
    },
    "browser": {
        "inspections_path": "/inspections",
        "search": {"serial_number_label": "Serial number", "submit_button_name": "Search"},
        "table": {"role": "table", "name": "Inspections"},
        "next_page": {"role": "button", "name": "Next page"},
        "fields": {
            "inspection_number": "Inspection No.",
            "serial_number": "S/N",
            "status": "Status",
            "inspection_date": "Date",
            "summary": {"critical": "Critical", "warning": "Warning", "passed": "Passed"},
            "attachments": "Attachments",
        },
    },
    "date_formats": ["%d/%m/%Y %H:%M"],
}


@pytest.fixture
def source_config() -> SourceConfig:
    return SourceConfig.model_validate(SOURCE_DICT)


@pytest.fixture
def now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- DuckDB
class DuckCursor:
    """DB-API shim: runs the repository's Snowflake SQL verbatim on DuckDB.

    Only two things are translated: parameter placeholders (pyformat -> DuckDB)
    and column types in CREATE TABLE (VARIANT/TIMESTAMP_TZ/NUMBER).
    """

    def __init__(self, conn: DuckConnection) -> None:
        self.owner = conn

    def execute(self, sql: str, params: Any = None) -> DuckCursor:
        if self.owner.fail_on and self.owner.fail_on in sql:
            raise RuntimeError(f"injected failure on {self.owner.fail_on}")
        self.owner.statements.append(sql)
        sql = re.sub(r"%\((\w+)\)s", r"$\1", sql).replace("%s", "?")
        if sql.lstrip().upper().startswith("CREATE TABLE"):
            sql = (sql.replace("VARIANT", "JSON").replace("TIMESTAMP_TZ", "TIMESTAMPTZ")
                   .replace("NUMBER(38,0)", "BIGINT"))
        if params is None:
            self.owner.db.execute(sql)
        else:
            self.owner.db.execute(sql, params)
        return self

    def fetchone(self) -> Any:
        return self.owner.db.fetchone()


class DuckConnection:
    def __init__(self) -> None:
        self.db = duckdb.connect()
        self.db.execute("CREATE MACRO PARSE_JSON(x) AS CAST(x AS JSON)")
        self.statements: list[str] = []
        self.fail_on: str | None = None
        self.closed = False

    def cursor(self) -> DuckCursor:
        return DuckCursor(self)

    def close(self) -> None:
        self.closed = True

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        return self.db.execute(sql).fetchall()


@pytest.fixture
def duck() -> DuckConnection:
    from app.warehouse.repository import InspectionRepository

    conn = DuckConnection()
    InspectionRepository(conn).ensure_schema(include_views=False)
    conn.statements.clear()
    return conn
