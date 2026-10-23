"""DuckDB stand-in for Snowflake, loaded with SYNTHETIC data only.

It runs the same Snowflake SQL the production backend runs (transpiled with
sqlglot), including the contract view generated from the mapping, so KPI logic
is exercised end to end. Every result it returns is labelled synthetic.
"""
from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import duckdb
import pandas
import sqlglot

from .. import fixtures
from ..mapping import SourceMapping, view_select_sql
from .base import Backend, BackendError, Result

APP_SCHEMA = "SI_LOCAL.SI_APP"


def to_duckdb(sql: str) -> str:
    return sqlglot.transpile(sql, read="snowflake", write="duckdb")[0]


class FixtureBackend(Backend):
    kind = "fixture"
    synthetic = True
    label = fixtures.SYNTHETIC_LABEL

    def __init__(self, mapping: SourceMapping, contract_view: str, rows: list[tuple] | None = None,
                 plans_path: Path | None = None):
        if not mapping.synthetic:
            raise BackendError("refused", "The fixture backend only runs with a mapping marked synthetic: true")
        self._lock = threading.Lock()
        self.con = duckdb.connect()
        self.con.execute("SET TimeZone = 'UTC'")
        db, schema, view = contract_view.split(".")
        raw_db, raw_schema, _ = fixtures.RAW_TABLE.split(".")
        for name in {db, raw_db}:
            self.con.execute(f"ATTACH ':memory:' AS {name}")
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {raw_db}.{raw_schema}")
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {db}.{schema}")
        types = ["VARCHAR", "TIMESTAMP", "TIMESTAMP", "TIMESTAMP", "TIMESTAMP", "TIMESTAMP", "VARCHAR", "VARCHAR",
                 "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR", "VARCHAR"]
        cols = ", ".join(f"{c} {t}" for c, t in zip(fixtures.RAW_COLUMNS, types))
        self.con.execute(f"CREATE TABLE {fixtures.RAW_TABLE} ({cols})")
        data = fixtures.generate() if rows is None else rows
        if data:
            frame = pandas.DataFrame(data, columns=list(fixtures.RAW_COLUMNS))
            self.con.register("_fixture_rows", frame)
            self.con.execute(f"INSERT INTO {fixtures.RAW_TABLE} SELECT * FROM _fixture_rows")
            self.con.unregister("_fixture_rows")
        self.con.execute(f"CREATE VIEW {contract_view} AS {to_duckdb(view_select_sql(mapping))}")
        plans = str(plans_path) if plans_path else ":memory:"
        if plans_path:
            Path(plans_path).parent.mkdir(parents=True, exist_ok=True)
        self.con.execute(f"ATTACH '{plans}' AS SI_LOCAL")
        self.con.execute(f"CREATE SCHEMA IF NOT EXISTS {APP_SCHEMA}")
        self.fail_next: BackendError | None = None       # tests inject warehouse failures here

    def _execute(self, sql: str, params: tuple, timeout_s: int, max_rows: int, native: bool = False) -> Result:
        if self.fail_next is not None:
            err, self.fail_next = self.fail_next, None
            raise err
        started = time.monotonic()
        with self._lock:
            cur = self.con.cursor()
            timer = threading.Timer(timeout_s, cur.interrupt)
            timer.start()
            try:
                cur.execute(sql if native else to_duckdb(sql), list(params))
                cols = [d[0].lower() for d in cur.description or []]
                rows = cur.fetchmany(max_rows + 1) if cur.description else []
            except duckdb.InterruptException:
                raise BackendError("timeout", "The query exceeded its time limit and was cancelled.") from None
            except duckdb.CatalogException as exc:
                raise BackendError("permission_denied", f"Object not found or not authorised: {str(exc).splitlines()[0][:200]}") from None
            except duckdb.Error as exc:
                raise BackendError("query_failed", f"Query failed: {str(exc).splitlines()[0][:200]}") from None
            finally:
                timer.cancel()
                cur.close()
        return Result(cols, [tuple(r) for r in rows[:max_rows]], f"fixture-{uuid.uuid4().hex[:12]}",
                      int((time.monotonic() - started) * 1000), len(rows) > max_rows)

    def query(self, sql, params, *, timeout_s, max_rows, tag) -> Result:
        return self._execute(sql, params, timeout_s, max_rows)

    def write(self, sql, params, *, tag, native=False) -> Result:
        return self._execute(sql, params, 60, 1000, native)

    def close(self) -> None:
        self.con.close()
