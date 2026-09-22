"""Snowflake repository. MERGE for current state, append-only history, run audit.

Runs blocking driver calls in a thread so the async API stays responsive.
Credentials come from `snowflake_config` (environment or a local snowflake.txt),
never from the prompt and never from a request, and are masked out of every
error message this module writes.

The driver's connection is not thread-safe, so every statement holds one lock.
A session that expired while the service sat idle is re-opened once and the
statement retried; anything else is a DATABASE_ERROR.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.core.errors import AutomationError, ErrorCode
from app.core.hashing import data_hash
from app.core.logging import log
from app.domain.parts import flatten_parts, product_of
from app.models.schemas import EquipmentRecord, RecordStatus, RunRecord
from app.repositories.base import ExtractionArtifact

logger = logging.getLogger(__name__)

MERGE_EQUIPMENT = """
MERGE INTO EQUIPMENT_DATA t
USING (SELECT %(serial_number)s AS SERIAL_NUMBER, %(source_system)s AS SOURCE_SYSTEM) s
   ON t.SERIAL_NUMBER = s.SERIAL_NUMBER AND t.SOURCE_SYSTEM = s.SOURCE_SYSTEM
WHEN MATCHED THEN UPDATE SET
    EQUIPMENT_MODEL      = %(equipment_model)s,
    EQUIPMENT_TYPE       = %(equipment_type)s,
    MANUFACTURER         = %(manufacturer)s,
    BUILD_DATE           = %(build_date)s,
    MACHINE_SERIAL_NUMBER = %(machine_serial_number)s,
    PRODUCT              = %(product)s,
    NORMALIZED_DATA      = PARSE_JSON(%(normalized_data)s),
    MACHINE_BUILD_DATE   = %(machine_build_date)s,
    ENGINE_SERIAL_NUMBER = %(engine_serial_number)s,
    ENGINE_BUILD_DATE    = %(engine_build_date)s,
    ENGINE_FAMILY        = PARSE_JSON(%(engine_family)s),
    SPECIFICATIONS       = PARSE_JSON(%(specifications)s),
    PARTS_DATA           = PARSE_JSON(%(parts_data)s),
    PARTS_MANUAL_URL     = %(parts_manual_url)s,
    OPERATION_MANUAL_URL = %(operation_manual_url)s,
    SOURCE_URL           = %(source_url)s,
    RAW_DATA             = PARSE_JSON(%(raw_data)s),
    FIELD_PROVENANCE     = PARSE_JSON(%(field_provenance)s),
    QUALITY_SCORE        = %(quality_score)s,
    DATA_HASH            = %(data_hash)s,
    SCHEMA_VERSION       = %(schema_version)s,
    STATUS               = %(status)s,
    RETRIEVED_AT         = %(retrieved_at)s,
    LAST_VERIFIED_AT     = CURRENT_TIMESTAMP(),
    UPDATED_AT           = CURRENT_TIMESTAMP(),
    AUTOMATION_RUN_ID    = %(automation_run_id)s
WHEN NOT MATCHED THEN INSERT (
    ID, SERIAL_NUMBER, SOURCE_SYSTEM, EQUIPMENT_MODEL, EQUIPMENT_TYPE, MANUFACTURER,
    BUILD_DATE, MACHINE_SERIAL_NUMBER, PRODUCT, NORMALIZED_DATA, MACHINE_BUILD_DATE, ENGINE_SERIAL_NUMBER,
    ENGINE_BUILD_DATE, ENGINE_FAMILY, SPECIFICATIONS, PARTS_DATA, PARTS_MANUAL_URL,
    OPERATION_MANUAL_URL, SOURCE_URL, RAW_DATA, FIELD_PROVENANCE, QUALITY_SCORE,
    DATA_HASH, SCHEMA_VERSION, STATUS, RETRIEVED_AT, LAST_VERIFIED_AT, UPDATED_AT,
    AUTOMATION_RUN_ID
) VALUES (
    UUID_STRING(), %(serial_number)s, %(source_system)s, %(equipment_model)s,
    %(equipment_type)s, %(manufacturer)s, %(build_date)s,
    %(machine_serial_number)s, %(product)s, PARSE_JSON(%(normalized_data)s), %(machine_build_date)s, %(engine_serial_number)s,
    %(engine_build_date)s, PARSE_JSON(%(engine_family)s),
    PARSE_JSON(%(specifications)s), PARSE_JSON(%(parts_data)s), %(parts_manual_url)s,
    %(operation_manual_url)s, %(source_url)s, PARSE_JSON(%(raw_data)s),
    PARSE_JSON(%(field_provenance)s), %(quality_score)s, %(data_hash)s,
    %(schema_version)s, %(status)s, %(retrieved_at)s, CURRENT_TIMESTAMP(),
    CURRENT_TIMESTAMP(), %(automation_run_id)s
)
"""

INSERT_HISTORY = """
INSERT INTO EQUIPMENT_DATA_HISTORY
  (ID, SERIAL_NUMBER, SOURCE_SYSTEM, SNAPSHOT, DATA_HASH, QUALITY_SCORE,
   RETRIEVED_AT, AUTOMATION_RUN_ID, VERSION_AT)
SELECT UUID_STRING(), %(serial_number)s, %(source_system)s, PARSE_JSON(%(snapshot)s),
       %(data_hash)s, %(quality_score)s, %(retrieved_at)s, %(automation_run_id)s,
       CURRENT_TIMESTAMP()
"""

# Explicit columns, not OBJECT_CONSTRUCT(*): the driver then hands back real
# datetimes for the TIMESTAMP_TZ columns instead of a display string the
# freshness policy would have to guess the format of.
# The parts of the current version replace the previous set in one pass.
DELETE_PARTS = """
DELETE FROM EQUIPMENT_PARTS
 WHERE SERIAL_NUMBER = %(serial_number)s AND SOURCE_SYSTEM = %(source_system)s
"""
INSERT_PARTS = """
INSERT INTO EQUIPMENT_PARTS
  (SERIAL_NUMBER, SOURCE_SYSTEM, GROUP_NAME, GROUP_TITLE, GROUP_SERIAL, GROUP_PART,
   PART_NUMBER, PART_NAME, QUANTITY_REQUIRED, QUANTITY_TEXT, WHERE_USED, SERVICE_ARTICLE,
   SN_APPLICABILITY, COMPONENT_SERIAL, PART_OF, DESCRIPTION, ROW_LOCATOR, SOURCE,
   RETRIEVED_AT, AUTOMATION_RUN_ID, RAW_DATA)
SELECT %(serial_number)s, %(source_system)s,
       f.value:group_name::STRING, f.value:group_title::STRING, f.value:group_serial::STRING,
       f.value:group_part::STRING, f.value:part_number::STRING, f.value:part_name::STRING,
       f.value:quantity_required::FLOAT, f.value:quantity_text::STRING,
       f.value:where_used::STRING, f.value:service_article::STRING,
       f.value:sn_applicability::STRING, f.value:component_serial::STRING,
       f.value:part_of::STRING, f.value:description::STRING, f.value:locator::STRING,
       %(source)s, %(retrieved_at)s, %(automation_run_id)s, f.value:raw
  FROM TABLE(FLATTEN(input => PARSE_JSON(%(rows)s))) f
"""

CURRENT_COLUMNS = """
SERIAL_NUMBER, SOURCE_SYSTEM, EQUIPMENT_MODEL, EQUIPMENT_TYPE, MANUFACTURER, BUILD_DATE,
MACHINE_SERIAL_NUMBER, MACHINE_BUILD_DATE, ENGINE_SERIAL_NUMBER, ENGINE_BUILD_DATE,
ENGINE_FAMILY, SPECIFICATIONS, PARTS_DATA, PARTS_MANUAL_URL, OPERATION_MANUAL_URL,
SOURCE_URL, RAW_DATA, FIELD_PROVENANCE, QUALITY_SCORE, DATA_HASH, SCHEMA_VERSION, STATUS,
RETRIEVED_AT, LAST_VERIFIED_AT, UPDATED_AT, AUTOMATION_RUN_ID, PRODUCT
"""

SELECT_CURRENT = f"""
SELECT {CURRENT_COLUMNS}
  FROM EQUIPMENT_DATA
 WHERE SERIAL_NUMBER = %(serial_number)s AND SOURCE_SYSTEM = %(source_system)s
"""

SELECT_ANY_SOURCE = f"""
SELECT {CURRENT_COLUMNS}
  FROM EQUIPMENT_DATA
 WHERE SERIAL_NUMBER = %(serial_number)s
"""

VARIANT_COLUMNS = ("engine_family", "specifications", "parts_data", "raw_data",
                   "field_provenance", "steps_executed", "snapshot")

# Primary keys are not enforced in Snowflake, so an expired claim left behind
# by a crashed run is removed first rather than letting a second row appear.
DELETE_EXPIRED_CLAIM = """
DELETE FROM AUTOMATION_RUN_CLAIMS
 WHERE CLAIM_KEY = %(key)s AND EXPIRES_AT <= CURRENT_TIMESTAMP()
"""

CLAIM_IDEMPOTENCY = """
MERGE INTO AUTOMATION_RUN_CLAIMS t
USING (SELECT %(key)s AS CLAIM_KEY) s ON t.CLAIM_KEY = s.CLAIM_KEY
   AND t.EXPIRES_AT > CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (CLAIM_KEY, RUN_ID, CLAIMED_AT, EXPIRES_AT)
     VALUES (%(key)s, %(run_id)s, CURRENT_TIMESTAMP(),
             DATEADD(second, %(ttl_s)s, CURRENT_TIMESTAMP()))
"""


# Driver error numbers that mean "the session is gone", not "the SQL is wrong".
# 390114 auth token expired · 390112/390111 session gone · 250001/250002
# connection failed/closed · 08001 SQLSTATE connection failure.
RECONNECT_ERRNOS = {390114, 390112, 390111, 250001, 250002}
RECONNECT_TEXT = ("authentication token has expired", "connection is closed",
                  "session no longer exists", "session does not exist")


def _needs_reconnect(exc: Exception) -> bool:
    if getattr(exc, "errno", None) in RECONNECT_ERRNOS:
        return True
    if getattr(exc, "sqlstate", None) == "08001":
        return True
    text = str(exc).lower()
    return any(t in text for t in RECONNECT_TEXT)


class SnowflakeEquipmentRepository:
    def __init__(self, connect_kwargs: dict[str, Any], *,
                 secrets: tuple[str, ...] = ()) -> None:
        self._connect_kwargs = connect_kwargs
        self._conn: Any = None
        self._lock = threading.Lock()
        # Values that must never appear in a log line, even inside a driver
        # error. The driver does not echo passwords, but this does not rely on it.
        self._secrets = tuple(s for s in secrets if s)
        # Same attribute the local store has, so wiring code needs no branch.
        self.source_labels: dict[str, str] = {}

    # ── plumbing ────────────────────────────────────────────────────────────
    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text

    def _connect(self) -> Any:
        import snowflake.connector  # imported lazily so dev/test need no driver

        if self._conn is None or self._conn.is_closed():
            self._conn = snowflake.connector.connect(**self._connect_kwargs)
        return self._conn

    def _drop_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - it is already broken; that is the point
                pass

    def _run_sync(self, sql: str, params: dict[str, Any] | None, fetch: str | None) -> Any:
        """One statement, on the calling thread, holding the connection lock."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    with self._connect().cursor() as cur:
                        cur.execute(sql, params or {})
                        if fetch == "one":
                            return cur.fetchone()
                        if fetch == "all":
                            return cur.fetchall()
                        if fetch == "dicts":
                            names = [d[0].lower() for d in (cur.description or [])]
                            return [dict(zip(names, row)) for row in cur.fetchall()]
                        return cur.rowcount
                except Exception as exc:  # driver-specific errors are all one thing to us
                    if attempt == 1 and _needs_reconnect(exc):
                        log(logger, logging.WARNING, "snowflake.reconnect",
                            reason=self._scrub(str(exc))[:160])
                        self._drop_connection()
                        continue
                    message = self._scrub(str(exc))
                    log(logger, logging.ERROR, "snowflake.error", error=message[:500],
                        errno=getattr(exc, "errno", None))
                    raise AutomationError(ErrorCode.DATABASE_ERROR,
                                          "Data store operation failed.",
                                          details={"store": "snowflake",
                                                   "driver_error": message[:200]}) from exc
        return None  # pragma: no cover - the loop always returns or raises

    async def _execute(self, sql: str, params: dict[str, Any] | None = None,
                       fetch: str | None = None) -> Any:
        return await asyncio.to_thread(self._run_sync, sql, params, fetch)

    def close(self) -> None:
        with self._lock:
            self._drop_connection()

    @staticmethod
    def _j(value: Any) -> str:
        return json.dumps(value if value is not None else None, default=str, ensure_ascii=False)

    @staticmethod
    def _variant(value: Any) -> Any:
        """VARIANT columns arrive as JSON text; hand back the value."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except ValueError:
                return value
        return value

    @classmethod
    def _row_to_record(cls, row: dict[str, Any]) -> dict[str, Any]:
        """A row → the record shape every store hands back.

        Successful writes keep the full canonical record in
        RAW_DATA:normalized_record, so a read returns exactly what was
        validated — quality, provenance, specifications and all — instead of a
        lossy rebuild from columns. The columns still win for what the table
        itself owns: status, hash and timestamps (a later NOT_FOUND or a
        re-verification changes those without rewriting the record).
        """
        row = {k: (cls._variant(v) if k in VARIANT_COLUMNS else v) for k, v in row.items()}
        raw = row.get("raw_data") if isinstance(row.get("raw_data"), dict) else {}
        normalized = raw.get("normalized_record") if isinstance(raw, dict) else None
        if isinstance(normalized, dict) and normalized.get("serial_number"):
            record = dict(normalized)
        else:
            # Rows written before normalized_record existed, or NOT_FOUND
            # markers: rebuild from the columns, and say what the score was.
            record = {k: v for k, v in row.items()
                      if k not in ("raw_data", "quality_score", "last_verified_at",
                                   "updated_at") and v is not None}
            if row.get("quality_score") is not None:
                record["quality"] = {"score": float(row["quality_score"])}
        for key in ("status", "data_hash", "retrieved_at", "automation_run_id"):
            if row.get(key) is not None:
                record[key] = row[key]
        for key in ("updated_at", "last_verified_at"):
            if row.get(key) is not None:
                record[key] = row[key]
        return record

    # ── reads ───────────────────────────────────────────────────────────────
    async def get_current(self, serial_number: str, source: str) -> dict[str, Any] | None:
        rows = await self._execute(SELECT_CURRENT,
                                   {"serial_number": serial_number, "source_system": source},
                                   fetch="dicts")
        return self._row_to_record(rows[0]) if rows else None

    async def get_any_source(self, serial_number: str) -> list[dict[str, Any]]:
        rows = await self._execute(SELECT_ANY_SOURCE, {"serial_number": serial_number},
                                   fetch="dicts") or []
        return [self._row_to_record(r) for r in rows]

    async def history(self, serial_number: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]:
        sql = ("SELECT VERSION_AT, DATA_HASH, QUALITY_SCORE, RETRIEVED_AT, AUTOMATION_RUN_ID, "
               "SOURCE_SYSTEM, SNAPSHOT FROM EQUIPMENT_DATA_HISTORY "
               "WHERE SERIAL_NUMBER = %(sn)s "
               + ("AND SOURCE_SYSTEM = %(src)s " if source else "")
               + "ORDER BY VERSION_AT DESC LIMIT %(lim)s")
        rows = await self._execute(sql, {"sn": serial_number, "src": source, "lim": int(limit)},
                                   fetch="dicts") or []
        return [{**r, "snapshot": self._variant(r.get("snapshot"))} for r in rows]

    async def known_serials(self, limit: int = 500) -> list[str]:
        rows = await self._execute(
            "SELECT DISTINCT SERIAL_NUMBER FROM EQUIPMENT_DATA "
            "WHERE STATUS <> 'NOT_FOUND' ORDER BY SERIAL_NUMBER LIMIT %(row_limit)s",
            {"row_limit": int(limit)}, fetch="all")
        return [row[0] for row in (rows or []) if row and row[0]]

    # ── writes ──────────────────────────────────────────────────────────────
    async def upsert(self, record: EquipmentRecord, *,
                     extraction: ExtractionArtifact | None = None) -> bool:
        payload = record.model_dump(mode="json")
        digest = record.data_hash or data_hash(payload)
        # RAW_DATA is the VARIANT column that keeps what the page gave us
        # beyond the typed fields — the same content the local JSON store
        # writes under `raw_data` — plus the validated record itself, which is
        # what a read hands back.
        raw: dict[str, Any] = {"normalized_record": {**payload, "data_hash": digest}}
        if extraction is not None:
            raw.update({
                "extracted_fields": extraction.fields,
                "specifications": extraction.specifications,
                "parts_data": extraction.parts_data,
                "final_url": extraction.final_url,
                "page_title": extraction.page_title,
                "payload_kind": extraction.payload_kind,
                "selector_version": extraction.selector_version,
            })
        existing = await self.get_current(record.serial_number, record.source_system)
        changed = not existing or existing.get("data_hash") != digest

        params = {
            "serial_number": record.serial_number,
            "source_system": record.source_system,
            "equipment_model": record.equipment_model,
            "equipment_type": record.equipment_type,
            "manufacturer": record.manufacturer,
            "build_date": record.build_date,
            "machine_serial_number": record.machine_serial_number,
            "product": product_of(payload.get("parts_data")),
            "normalized_data": self._j({**payload, "data_hash": digest}),
            "machine_build_date": record.machine_build_date,
            "engine_serial_number": record.engine_serial_number,
            "engine_build_date": record.engine_build_date,
            "engine_family": self._j(payload.get("engine_family")),
            "specifications": self._j(payload.get("specifications")),
            "parts_data": self._j(payload.get("parts_data")),
            "parts_manual_url": record.parts_manual_url,
            "operation_manual_url": record.operation_manual_url,
            "source_url": record.source_url,
            "raw_data": self._j(raw),
            "field_provenance": self._j(payload.get("field_provenance")),
            "quality_score": record.quality.score,
            "data_hash": digest,
            "schema_version": record.schema_version,
            "status": record.status.value,
            "retrieved_at": record.retrieved_at,
            "automation_run_id": record.automation_run_id,
        }
        await self._execute(MERGE_EQUIPMENT, params)
        # One row per part for the current version. A failure here fails the
        # write, and so the lookup: a record without its parts is incomplete.
        rows = flatten_parts(payload.get("parts_data"))
        await self._execute(DELETE_PARTS, {"serial_number": record.serial_number,
                                           "source_system": record.source_system})
        if rows:
            await self._execute(INSERT_PARTS, {
                "serial_number": record.serial_number, "source_system": record.source_system,
                "source": self.source_labels.get(record.source_system, record.source_system),
                "retrieved_at": record.retrieved_at,
                "automation_run_id": record.automation_run_id, "rows": self._j(rows)})
        if changed:
            await self._execute(INSERT_HISTORY, {
                "serial_number": record.serial_number,
                "source_system": record.source_system,
                "snapshot": self._j(payload),
                "data_hash": digest,
                "quality_score": record.quality.score,
                "retrieved_at": record.retrieved_at,
                "automation_run_id": record.automation_run_id,
            })
        return changed

    async def mark_not_found(self, serial_number: str, source: str, run_id: str) -> None:
        await self._execute(
            """MERGE INTO EQUIPMENT_DATA t
               USING (SELECT %(sn)s AS SERIAL_NUMBER, %(src)s AS SOURCE_SYSTEM) s
                  ON t.SERIAL_NUMBER = s.SERIAL_NUMBER AND t.SOURCE_SYSTEM = s.SOURCE_SYSTEM
               WHEN MATCHED THEN UPDATE SET STATUS = %(status)s,
                    RETRIEVED_AT = CURRENT_TIMESTAMP(), UPDATED_AT = CURRENT_TIMESTAMP(),
                    AUTOMATION_RUN_ID = %(run_id)s
               WHEN NOT MATCHED THEN INSERT (ID, SERIAL_NUMBER, SOURCE_SYSTEM, STATUS,
                    RETRIEVED_AT, UPDATED_AT, AUTOMATION_RUN_ID)
                    VALUES (UUID_STRING(), %(sn)s, %(src)s, %(status)s,
                            CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP(), %(run_id)s)""",
            {"sn": serial_number, "src": source, "status": RecordStatus.NOT_FOUND.value,
             "run_id": run_id})

    async def save_run(self, run: RunRecord) -> None:
        payload = run.model_dump(mode="json")
        await self._execute(
            """MERGE INTO AUTOMATION_RUNS t
               USING (SELECT %(run_id)s AS AUTOMATION_RUN_ID) s
                  ON t.AUTOMATION_RUN_ID = s.AUTOMATION_RUN_ID
               WHEN MATCHED THEN UPDATE SET COMPLETED_AT = %(completed_at)s,
                    STATUS = %(status)s, ERROR_CODE = %(error_code)s,
                    ERROR_MESSAGE = %(error_message)s, RETRY_COUNT = %(retry_count)s,
                    EXECUTION_TIME_MS = %(execution_time_ms)s,
                    STEPS_EXECUTED = PARSE_JSON(%(steps)s),
                    EXTRACTED_FIELD_COUNT = %(fields)s, QUALITY_SCORE = %(quality)s,
                    SELECTOR_VERSION = %(selector_version)s, ARTIFACT_URI = %(artifact_uri)s
               WHEN NOT MATCHED THEN INSERT (AUTOMATION_RUN_ID, SERIAL_NUMBER, SOURCE,
                    TRIGGER, REQUESTED_BY, STARTED_AT, COMPLETED_AT, STATUS, ERROR_CODE,
                    ERROR_MESSAGE, RETRY_COUNT, EXECUTION_TIME_MS, STEPS_EXECUTED,
                    EXTRACTED_FIELD_COUNT, QUALITY_SCORE, SELECTOR_VERSION, ARTIFACT_URI,
                    TRACE_ID)
                    VALUES (%(run_id)s, %(serial_number)s, %(source)s, %(trigger)s,
                    %(requested_by)s, %(started_at)s, %(completed_at)s, %(status)s,
                    %(error_code)s, %(error_message)s, %(retry_count)s, %(execution_time_ms)s,
                    PARSE_JSON(%(steps)s), %(fields)s, %(quality)s, %(selector_version)s,
                    %(artifact_uri)s, %(trace_id)s)""",
            {"run_id": run.automation_run_id, "serial_number": run.serial_number,
             "source": run.source, "trigger": run.trigger, "requested_by": run.requested_by,
             "started_at": run.started_at, "completed_at": run.completed_at,
             "status": run.status.value, "error_code": run.error_code,
             "error_message": (run.error_message or "")[:1000], "retry_count": run.retry_count,
             "execution_time_ms": run.execution_time_ms,
             "steps": self._j(payload.get("steps_executed")),
             "fields": run.extracted_field_count, "quality": run.quality_score,
             "selector_version": run.selector_version, "artifact_uri": run.artifact_uri,
             "trace_id": run.trace_id})

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        rows = await self._execute(
            "SELECT * FROM AUTOMATION_RUNS WHERE AUTOMATION_RUN_ID = %(id)s",
            {"id": run_id}, fetch="dicts")
        if not rows:
            return None
        return {k: (self._variant(v) if k in VARIANT_COLUMNS else v)
                for k, v in rows[0].items()}

    async def find_active_run(self, idempotency_key: str) -> str | None:
        row = await self._execute(
            """SELECT RUN_ID FROM AUTOMATION_RUN_CLAIMS
                WHERE CLAIM_KEY = %(key)s AND EXPIRES_AT > CURRENT_TIMESTAMP()""",
            {"key": idempotency_key}, fetch="one")
        return row[0] if row else None

    async def claim_idempotency(self, idempotency_key: str, run_id: str,
                                ttl_s: int = 120) -> bool:
        await self._execute(DELETE_EXPIRED_CLAIM, {"key": idempotency_key})
        rowcount = await self._execute(CLAIM_IDEMPOTENCY,
                                       {"key": idempotency_key, "run_id": run_id, "ttl_s": ttl_s})
        return bool(rowcount)

    async def release_idempotency(self, idempotency_key: str) -> None:
        await self._execute("DELETE FROM AUTOMATION_RUN_CLAIMS WHERE CLAIM_KEY = %(key)s",
                            {"key": idempotency_key})

    async def health(self) -> bool:
        try:
            await self._execute("SELECT 1", fetch="one")
            return True
        except AutomationError:
            return False

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)
