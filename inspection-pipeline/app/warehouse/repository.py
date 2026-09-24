"""Snowflake persistence: run audit, RAW append, MERGE into INSPECTIONS.

Load strategy (per run, in a single transaction):

1. INSERT the run's validated records into RAW_INSPECTIONS (append-only,
   batched multi-row INSERT ... SELECT so VARIANT columns can use PARSE_JSON).
2. MERGE the run's RAW rows into INSPECTIONS on the business key
   (SOURCE, INSPECTION_NUMBER):
   * not matched               -> INSERT (FIRST/LAST_SEEN = this run)
   * matched, hash changed     -> UPDATE content, LAST_CHANGED_RUN_ID, UPDATED_AT
   * matched, hash unchanged   -> only LAST_SEEN_RUN_ID / LAST_SEEN_AT move
3. COMMIT. Any failure rolls back both steps, so a rerun is always safe.

Parameters use the connector's default ``pyformat`` style. All SQL is plain
enough to be executed verbatim by DuckDB in the test-suite.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import resources
from typing import Any
from uuid import UUID

from app.errors import WarehouseError
from app.logger import redact
from app.models import Inspection, PipelineRun, ValidationFailure

logger = logging.getLogger(__name__)

MAX_STATEMENT_BYTES = 700_000  # stay well below Snowflake's statement size limit

RAW_COLUMNS = [
    "RUN_ID", "SOURCE", "COLLECTOR_TYPE", "COLLECTED_AT", "INSPECTION_NUMBER", "SERIAL_NUMBER",
    "STATUS", "INSPECTION_DATE", "SUMMARY", "ATTACHMENTS", "ATTACHMENT_COUNT", "RECORD_HASH",
    "RAW_JSON", "LOADED_AT",
]

RAW_INSERT_TEMPLATE = """
INSERT INTO RAW_INSPECTIONS (
    RUN_ID, SOURCE, COLLECTOR_TYPE, COLLECTED_AT, INSPECTION_NUMBER, SERIAL_NUMBER, STATUS,
    INSPECTION_DATE, SUMMARY, ATTACHMENTS, ATTACHMENT_COUNT, RECORD_HASH, RAW_JSON, CREATED_AT, UPDATED_AT
)
SELECT
    v.RUN_ID, v.SOURCE, v.COLLECTOR_TYPE, CAST(v.COLLECTED_AT AS TIMESTAMPTZ), v.INSPECTION_NUMBER,
    v.SERIAL_NUMBER, v.STATUS, CAST(v.INSPECTION_DATE AS TIMESTAMPTZ), PARSE_JSON(v.SUMMARY),
    PARSE_JSON(v.ATTACHMENTS), CAST(v.ATTACHMENT_COUNT AS INTEGER), v.RECORD_HASH, PARSE_JSON(v.RAW_JSON),
    CAST(v.LOADED_AT AS TIMESTAMPTZ), CAST(v.LOADED_AT AS TIMESTAMPTZ)
FROM (VALUES {rows}) AS v ({columns})
"""

ERROR_COLUMNS = ["RUN_ID", "SOURCE", "RECORD_IDENTIFIER", "ERROR_TYPE", "ERROR_MESSAGE", "RAW_JSON",
                 "FAILED_AT", "LOADED_AT"]

ERROR_INSERT_TEMPLATE = """
INSERT INTO VALIDATION_ERRORS (
    RUN_ID, SOURCE, RECORD_IDENTIFIER, ERROR_TYPE, ERROR_MESSAGE, RAW_JSON, FAILED_AT, CREATED_AT
)
SELECT
    v.RUN_ID, v.SOURCE, v.RECORD_IDENTIFIER, v.ERROR_TYPE, v.ERROR_MESSAGE, PARSE_JSON(v.RAW_JSON),
    CAST(v.FAILED_AT AS TIMESTAMPTZ), CAST(v.LOADED_AT AS TIMESTAMPTZ)
FROM (VALUES {rows}) AS v ({columns})
"""

MERGE_SQL = """
MERGE INTO INSPECTIONS t
USING (
    SELECT SOURCE, INSPECTION_NUMBER, SERIAL_NUMBER, STATUS, INSPECTION_DATE, SUMMARY, ATTACHMENTS,
           ATTACHMENT_COUNT, RECORD_HASH, RAW_JSON, RUN_ID, COLLECTED_AT
    FROM RAW_INSPECTIONS
    WHERE RUN_ID = %(run_id)s
    QUALIFY ROW_NUMBER() OVER (PARTITION BY SOURCE, INSPECTION_NUMBER ORDER BY COLLECTED_AT DESC) = 1
) s
ON t.SOURCE = s.SOURCE AND t.INSPECTION_NUMBER = s.INSPECTION_NUMBER
WHEN MATCHED AND t.RECORD_HASH <> s.RECORD_HASH THEN UPDATE SET
    SERIAL_NUMBER = s.SERIAL_NUMBER,
    STATUS = s.STATUS,
    INSPECTION_DATE = s.INSPECTION_DATE,
    SUMMARY = s.SUMMARY,
    ATTACHMENTS = s.ATTACHMENTS,
    ATTACHMENT_COUNT = s.ATTACHMENT_COUNT,
    RECORD_HASH = s.RECORD_HASH,
    RAW_JSON = s.RAW_JSON,
    LAST_SEEN_RUN_ID = s.RUN_ID,
    LAST_SEEN_AT = s.COLLECTED_AT,
    LAST_CHANGED_RUN_ID = s.RUN_ID,
    UPDATED_AT = CAST(%(loaded_at)s AS TIMESTAMPTZ)
WHEN MATCHED THEN UPDATE SET
    LAST_SEEN_RUN_ID = s.RUN_ID,
    LAST_SEEN_AT = s.COLLECTED_AT
WHEN NOT MATCHED THEN INSERT (
    SOURCE, INSPECTION_NUMBER, SERIAL_NUMBER, STATUS, INSPECTION_DATE, SUMMARY, ATTACHMENTS,
    ATTACHMENT_COUNT, RECORD_HASH, RAW_JSON, FIRST_SEEN_RUN_ID, FIRST_SEEN_AT, LAST_SEEN_RUN_ID,
    LAST_SEEN_AT, LAST_CHANGED_RUN_ID, CREATED_AT, UPDATED_AT
) VALUES (
    s.SOURCE, s.INSPECTION_NUMBER, s.SERIAL_NUMBER, s.STATUS, s.INSPECTION_DATE, s.SUMMARY, s.ATTACHMENTS,
    s.ATTACHMENT_COUNT, s.RECORD_HASH, s.RAW_JSON, s.RUN_ID, s.COLLECTED_AT, s.RUN_ID,
    s.COLLECTED_AT, s.RUN_ID, CAST(%(loaded_at)s AS TIMESTAMPTZ), CAST(%(loaded_at)s AS TIMESTAMPTZ)
)
"""

MERGE_STATS_SQL = """
SELECT
    COALESCE(SUM(CASE WHEN FIRST_SEEN_RUN_ID = %(run_id)s THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN LAST_CHANGED_RUN_ID = %(run_id)s AND FIRST_SEEN_RUN_ID <> %(run_id)s THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN LAST_CHANGED_RUN_ID <> %(run_id)s THEN 1 ELSE 0 END), 0)
FROM INSPECTIONS
WHERE LAST_SEEN_RUN_ID = %(run_id)s
"""

RUN_INSERT_SQL = """
INSERT INTO PIPELINE_RUNS (
    RUN_ID, SOURCE, COLLECTOR_TYPE, FILTERS, START_TIME, STATUS, RECORDS_FOUND, RECORDS_VALID,
    RECORDS_LOADED, RECORDS_FAILED, RECORDS_DUPLICATE, RECORDS_INSERTED, RECORDS_UPDATED,
    RECORDS_UNCHANGED, CREATED_AT, UPDATED_AT
)
SELECT %(run_id)s, %(source)s, %(collector_type)s, PARSE_JSON(%(filters)s), CAST(%(start_time)s AS TIMESTAMPTZ),
       %(status)s, 0, 0, 0, 0, 0, 0, 0, 0, CAST(%(now)s AS TIMESTAMPTZ), CAST(%(now)s AS TIMESTAMPTZ)
"""

RUN_UPDATE_SQL = """
UPDATE PIPELINE_RUNS SET
    STATUS = %(status)s,
    END_TIME = CAST(%(end_time)s AS TIMESTAMPTZ),
    RECORDS_FOUND = %(records_found)s,
    RECORDS_VALID = %(records_valid)s,
    RECORDS_LOADED = %(records_loaded)s,
    RECORDS_FAILED = %(records_failed)s,
    RECORDS_DUPLICATE = %(records_duplicate)s,
    RECORDS_INSERTED = %(records_inserted)s,
    RECORDS_UPDATED = %(records_updated)s,
    RECORDS_UNCHANGED = %(records_unchanged)s,
    EXPECTED_TOTAL = %(expected_total)s,
    ERROR_MESSAGE = %(error_message)s,
    UPDATED_AT = CAST(%(now)s AS TIMESTAMPTZ)
WHERE RUN_ID = %(run_id)s
"""

SUCCESS_TODAY_SQL = """
SELECT COUNT(*) FROM PIPELINE_RUNS
WHERE SOURCE = %(source)s AND STATUS = 'SUCCESS'
  AND START_TIME >= CAST(%(day_start)s AS TIMESTAMPTZ) AND START_TIME < CAST(%(day_end)s AS TIMESTAMPTZ)
"""


@dataclass(frozen=True)
class MergeStats:
    loaded: int
    inserted: int
    updated: int
    unchanged: int


def _json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def split_statements(sql: str) -> list[str]:
    statements = []
    for chunk in re.split(r";\s*(?:\n|$)", sql):
        body = "\n".join(line for line in chunk.splitlines() if not line.strip().startswith("--")).strip()
        if body:
            statements.append(body)
    return statements


def _batches(rows: list[list[Any]], batch_size: int) -> list[list[list[Any]]]:
    """Split by row count and by approximate statement size."""
    batches: list[list[list[Any]]] = []
    current: list[list[Any]] = []
    size = 0
    for row in rows:
        row_size = sum(len(str(v)) + 8 for v in row)
        if current and (len(current) >= batch_size or size + row_size > MAX_STATEMENT_BYTES):
            batches.append(current)
            current, size = [], 0
        current.append(row)
        size += row_size
    if current:
        batches.append(current)
    return batches


class InspectionRepository:
    def __init__(self, connection: Any, batch_size: int = 500) -> None:
        self.conn = connection
        self.batch_size = batch_size

    # ------------------------------------------------------------ primitives
    def _execute(self, sql: str, params: Any = None) -> Any:
        cursor = self.conn.cursor()
        try:
            cursor.execute(sql, params) if params is not None else cursor.execute(sql)
            return cursor
        except WarehouseError:
            raise
        except Exception as exc:  # connector-specific error classes
            first_line = sql.strip().splitlines()[0][:80]
            raise WarehouseError(f"SQL failed ({first_line}...): {redact(str(exc))[:1000]}") from exc

    def _insert_values(self, template: str, columns: list[str], rows: list[list[Any]]) -> None:
        for batch in _batches(rows, self.batch_size):
            placeholders = ", ".join("(" + ", ".join(["%s"] * len(columns)) + ")" for _ in batch)
            sql = template.format(rows=placeholders, columns=", ".join(columns))
            self._execute(sql, [value for row in batch for value in row])

    # ---------------------------------------------------------------- schema
    def ensure_schema(self, include_views: bool = True) -> None:
        files = ["schema.sql"] + (["views.sql"] if include_views else [])
        for name in files:
            sql = resources.files("app.warehouse").joinpath(name).read_text(encoding="utf-8")
            for statement in split_statements(sql):
                self._execute(statement)
        logger.info("Snowflake schema ensured (%s)", ", ".join(files))

    # ------------------------------------------------------------------ runs
    def start_run(self, run: PipelineRun) -> None:
        self._execute(RUN_INSERT_SQL, {
            "run_id": str(run.run_id),
            "source": run.source,
            "collector_type": run.collector_type,
            "filters": _json(run.filters),
            "start_time": _iso(run.start_time),
            "status": run.status.value,
            "now": _iso(datetime.now(timezone.utc)),
        })

    def finish_run(self, run: PipelineRun) -> None:
        self._execute(RUN_UPDATE_SQL, {
            "run_id": str(run.run_id),
            "status": run.status.value,
            "end_time": _iso(run.end_time),
            "records_found": run.records_found,
            "records_valid": run.records_valid,
            "records_loaded": run.records_loaded,
            "records_failed": run.records_failed,
            "records_duplicate": run.records_duplicate,
            "records_inserted": run.records_inserted,
            "records_updated": run.records_updated,
            "records_unchanged": run.records_unchanged,
            "expected_total": run.expected_total,
            "error_message": run.error_message,
            "now": _iso(datetime.now(timezone.utc)),
        })

    def has_successful_run_on(self, source: str, day: datetime) -> bool:
        start = day.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self._execute(SUCCESS_TODAY_SQL, {
            "source": source, "day_start": _iso(start), "day_end": _iso(start + timedelta(days=1)),
        }).fetchone()
        return bool(row and row[0])

    # -------------------------------------------------------------- failures
    def record_failures(self, failures: list[ValidationFailure]) -> None:
        if not failures:
            return
        now = _iso(datetime.now(timezone.utc))
        rows = [
            [str(f.run_id), f.source, f.record_identifier[:500], f.error_type, f.error_message,
             _json(f.raw_data), _iso(f.failed_at), now]
            for f in failures
        ]
        self._insert_values(ERROR_INSERT_TEMPLATE, ERROR_COLUMNS, rows)
        logger.info("Recorded %d validation failures", len(failures))

    # ------------------------------------------------------------------ load
    def load(self, run_id: UUID, inspections: list[Inspection]) -> MergeStats:
        """Append to RAW and MERGE into INSPECTIONS atomically."""
        if not inspections:
            return MergeStats(0, 0, 0, 0)
        loaded_at = _iso(datetime.now(timezone.utc))
        rows = [
            [str(i.run_id), i.source, i.collector_type, _iso(i.collected_at), i.inspection_number,
             i.serial_number, i.status, _iso(i.inspection_date), _json(i.summary),
             _json([a.model_dump(mode="json") for a in i.attachments]), len(i.attachments),
             i.record_hash, _json(i.raw_data), loaded_at]
            for i in inspections
        ]
        self._execute("BEGIN")
        try:
            self._insert_values(RAW_INSERT_TEMPLATE, RAW_COLUMNS, rows)
            self._execute(MERGE_SQL, {"run_id": str(run_id), "loaded_at": loaded_at})
            inserted, updated, unchanged = self._execute(MERGE_STATS_SQL, {"run_id": str(run_id)}).fetchone()
            self._execute("COMMIT")
        except BaseException:
            try:
                self._execute("ROLLBACK")
            except WarehouseError as rollback_exc:
                logger.error("Rollback failed: %s", rollback_exc)
            raise
        stats = MergeStats(len(rows), int(inserted), int(updated), int(unchanged))
        logger.info(
            "Loaded %d records into Snowflake (inserted=%d, updated=%d, unchanged=%d)",
            stats.loaded, stats.inserted, stats.updated, stats.unchanged,
        )
        return stats
