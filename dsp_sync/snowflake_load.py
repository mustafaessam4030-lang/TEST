"""Load a DSP asset CSV into Snowflake as a dated daily snapshot.

Every CSV column lands as VARCHAR under an UPPER_SNAKE_CASE name, plus
SNAPSHOT_DATE and LOADED_AT. Re-running a day replaces that day's rows, so a
retry never duplicates data. Columns DSP adds later are added to the table
automatically. A <TABLE>_LATEST view always shows the newest snapshot.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
import snowflake.connector
from snowflake.connector.pandas_tools import write_pandas

log = logging.getLogger(__name__)

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
RESERVED = {"SNAPSHOT_DATE", "LOADED_AT"}


def normalize_columns(cols: list[str]) -> list[str]:
    """'Serial Number' -> 'SERIAL_NUMBER', unique and safe as identifiers."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for raw in cols:
        name = re.sub(r"[^0-9A-Za-z]+", "_", str(raw)).strip("_").upper() or "COLUMN"
        if name[0].isdigit():
            name = "C_" + name
        if name in RESERVED:
            name = "SRC_" + name
        n = seen.get(name, 0)
        seen[name] = n + 1
        out.append(name if n == 0 else f"{name}_{n + 1}")
    return out


def read_export(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = normalize_columns(list(df.columns))
    return df


def _q(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe Snowflake identifier: {name!r}")
    return f'"{name.upper()}"'


def connect():
    """Connect using SNOWFLAKE_* environment variables (key-pair or password)."""
    params = {
        "account": os.environ["SNOWFLAKE_ACCOUNT"],
        "user": os.environ["SNOWFLAKE_USER"],
        "warehouse": os.environ["SNOWFLAKE_WAREHOUSE"],
        "database": os.environ["SNOWFLAKE_DATABASE"],
        "schema": os.environ["SNOWFLAKE_SCHEMA"],
        "application": "dsp_sync",
    }
    if os.environ.get("SNOWFLAKE_ROLE"):
        params["role"] = os.environ["SNOWFLAKE_ROLE"]
    if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PATH"):
        params["private_key_file"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]
        if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
            params["private_key_file_pwd"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"]
    else:
        params["password"] = os.environ["SNOWFLAKE_PASSWORD"]
    return snowflake.connector.connect(**params)


def load_snapshot(conn, df: pd.DataFrame, table: str, snapshot_date: date) -> int:
    """Replace ``snapshot_date``'s rows in ``table`` with ``df``. Returns row count."""
    if df.empty:
        raise ValueError("DSP export is empty; refusing to overwrite the snapshot")
    table = table.upper()
    cols = list(df.columns)
    cur = conn.cursor()
    try:
        col_defs = ", ".join(f"{_q(c)} VARCHAR" for c in cols)
        cur.execute(
            f"CREATE TABLE IF NOT EXISTS {_q(table)} ({col_defs}, "
            '"SNAPSHOT_DATE" DATE NOT NULL, "LOADED_AT" TIMESTAMP_NTZ NOT NULL)'
        )
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = CURRENT_SCHEMA() AND table_name = %s",
            (table,),
        )
        existing = {r[0] for r in cur.fetchall()}
        for c in cols:
            if c not in existing:
                log.info("New DSP column %s: adding it to %s", c, table)
                cur.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(c)} VARCHAR")

        # Stage into a session temp table first. write_pandas runs DDL, which
        # would auto-commit an open transaction, so the swap below stays atomic.
        stage = f"{table}_STAGE"
        ok, _, nrows, _ = write_pandas(
            conn, df, stage, auto_create_table=True, overwrite=True,
            table_type="temporary", quote_identifiers=True,
        )
        if not ok or nrows != len(df):
            raise RuntimeError(f"Staging upload incomplete: {nrows}/{len(df)} rows")

        col_list = ", ".join(_q(c) for c in cols)
        cur.execute("BEGIN")
        cur.execute(f'DELETE FROM {_q(table)} WHERE "SNAPSHOT_DATE" = %s', (snapshot_date,))
        cur.execute(
            f'INSERT INTO {_q(table)} ({col_list}, "SNAPSHOT_DATE", "LOADED_AT") '
            f"SELECT {col_list}, %s, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ FROM {_q(stage)}",
            (snapshot_date,),
        )
        cur.execute("COMMIT")
        cur.execute(
            f"CREATE OR REPLACE VIEW {_q(table + '_LATEST')} AS SELECT * FROM {_q(table)} "
            f'WHERE "SNAPSHOT_DATE" = (SELECT MAX("SNAPSHOT_DATE") FROM {_q(table)})'
        )
        log.info("Loaded %d rows into %s for %s", len(df), table, snapshot_date)
        return len(df)
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except Exception:
            log.warning("ROLLBACK failed", exc_info=True)
        raise
    finally:
        cur.close()
