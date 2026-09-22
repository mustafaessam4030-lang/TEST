"""Snowflake repository. MERGE for current state, append-only history, run audit.

Runs blocking driver calls in a thread so the async API stays responsive.
Credentials come from a key reference resolved at startup, never from the prompt
and never from a request.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.errors import AutomationError, ErrorCode
from app.core.hashing import data_hash
from app.core.logging import log
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
    BUILD_DATE, MACHINE_SERIAL_NUMBER, MACHINE_BUILD_DATE, ENGINE_SERIAL_NUMBER,
    ENGINE_BUILD_DATE, ENGINE_FAMILY, SPECIFICATIONS, PARTS_DATA, PARTS_MANUAL_URL,
    OPERATION_MANUAL_URL, SOURCE_URL, RAW_DATA, FIELD_PROVENANCE, QUALITY_SCORE,
    DATA_HASH, SCHEMA_VERSION, STATUS, RETRIEVED_AT, LAST_VERIFIED_AT, UPDATED_AT,
    AUTOMATION_RUN_ID
) VALUES (
    UUID_STRING(), %(serial_number)s, %(source_system)s, %(equipment_model)s,
    %(equipment_type)s, %(manufacturer)s, %(build_date)s,
    %(machine_serial_number)s, %(machine_build_date)s, %(engine_serial_number)s,
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

SELECT_CURRENT = """
SELECT OBJECT_CONSTRUCT(*) AS ROW_JSON
  FROM EQUIPMENT_DATA
 WHERE SERIAL_NUMBER = %(serial_number)s AND SOURCE_SYSTEM = %(source_system)s
"""

CLAIM_IDEMPOTENCY = """
MERGE INTO AUTOMATION_RUN_CLAIMS t
USING (SELECT %(key)s AS CLAIM_KEY) s ON t.CLAIM_KEY = s.CLAIM_KEY
   AND t.EXPIRES_AT > CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (CLAIM_KEY, RUN_ID, CLAIMED_AT, EXPIRES_AT)
     VALUES (%(key)s, %(run_id)s, CURRENT_TIMESTAMP(),
             DATEADD(second, %(ttl_s)s, CURRENT_TIMESTAMP()))
"""


class SnowflakeEquipmentRepository:
    def __init__(self, connect_kwargs: dict[str, Any]) -> None:
        self._connect_kwargs = connect_kwargs
        self._conn: Any = None

    # ── plumbing ────────────────────────────────────────────────────────────
    def _connect(self) -> Any:
        import snowflake.connector  # imported lazily so dev/test need no driver

        if self._conn is None or self._conn.is_closed():
            self._conn = snowflake.connector.connect(**self._connect_kwargs)
        return self._conn

    async def _execute(self, sql: str, params: dict[str, Any] | None = None,
                       fetch: str | None = None) -> Any:
        def _run() -> Any:
            try:
                with self._connect().cursor() as cur:
                    cur.execute(sql, params or {})
                    if fetch == "one":
                        return cur.fetchone()
                    if fetch == "all":
                        return cur.fetchall()
                    return cur.rowcount
            except Exception as exc:  # driver-specific errors are all one thing to us
                log(logger, logging.ERROR, "snowflake.error", error=str(exc)[:500])
                raise AutomationError(ErrorCode.DATABASE_ERROR, "Data store operation failed.",
                                      details={"driver_error": str(exc)[:200]}) from exc

        return await asyncio.to_thread(_run)

    @staticmethod
    def _j(value: Any) -> str:
        return json.dumps(value if value is not None else None, default=str, ensure_ascii=False)

    # ── reads ───────────────────────────────────────────────────────────────
    async def get_current(self, serial_number: str, source: str) -> dict[str, Any] | None:
        row = await self._execute(SELECT_CURRENT,
                                  {"serial_number": serial_number, "source_system": source},
                                  fetch="one")
        if not row:
            return None
        payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        return {k.lower(): v for k, v in payload.items()}

    async def get_any_source(self, serial_number: str) -> list[dict[str, Any]]:
        rows = await self._execute(
            "SELECT OBJECT_CONSTRUCT(*) FROM EQUIPMENT_DATA WHERE SERIAL_NUMBER = %(sn)s",
            {"sn": serial_number}, fetch="all") or []
        out = []
        for row in rows:
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            out.append({k.lower(): v for k, v in payload.items()})
        return out

    async def history(self, serial_number: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]:
        sql = ("SELECT VERSION_AT, DATA_HASH, QUALITY_SCORE, RETRIEVED_AT, AUTOMATION_RUN_ID, "
               "SOURCE_SYSTEM, SNAPSHOT FROM EQUIPMENT_DATA_HISTORY "
               "WHERE SERIAL_NUMBER = %(sn)s "
               + ("AND SOURCE_SYSTEM = %(src)s " if source else "")
               + "ORDER BY VERSION_AT DESC LIMIT %(lim)s")
        rows = await self._execute(sql, {"sn": serial_number, "src": source, "lim": limit},
                                   fetch="all") or []
        return [{"version_at": r[0], "data_hash": r[1], "quality_score": r[2],
                 "retrieved_at": r[3], "automation_run_id": r[4], "source_system": r[5],
                 "snapshot": json.loads(r[6]) if isinstance(r[6], str) else r[6]} for r in rows]

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
        # RAW_DATA is the VARIANT column that keeps what the page gave us
        # beyond the typed fields — the same content the local JSON store
        # writes under `raw_data`.
        if extraction is not None:
            payload["raw_data"] = {
                "extracted_fields": extraction.fields,
                "specifications": extraction.specifications,
                "parts_data": extraction.parts_data,
                "final_url": extraction.final_url,
                "page_title": extraction.page_title,
                "payload_kind": extraction.payload_kind,
                "selector_version": extraction.selector_version,
            }
        digest = record.data_hash or data_hash(payload)
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
            "machine_build_date": record.machine_build_date,
            "engine_serial_number": record.engine_serial_number,
            "engine_build_date": record.engine_build_date,
            "engine_family": self._j(payload.get("engine_family")),
            "specifications": self._j(payload.get("specifications")),
            "parts_data": self._j(payload.get("parts_data")),
            "parts_manual_url": record.parts_manual_url,
            "operation_manual_url": record.operation_manual_url,
            "source_url": record.source_url,
            "raw_data": self._j(payload.get("raw_data")),
            "field_provenance": self._j(payload.get("field_provenance")),
            "quality_score": record.quality.score,
            "data_hash": digest,
            "schema_version": record.schema_version,
            "status": record.status.value,
            "retrieved_at": record.retrieved_at,
            "automation_run_id": record.automation_run_id,
        }
        await self._execute(MERGE_EQUIPMENT, params)
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
        row = await self._execute(
            "SELECT OBJECT_CONSTRUCT(*) FROM AUTOMATION_RUNS WHERE AUTOMATION_RUN_ID = %(id)s",
            {"id": run_id}, fetch="one")
        if not row:
            return None
        payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        return {k.lower(): v for k, v in payload.items()}

    async def find_active_run(self, idempotency_key: str) -> str | None:
        row = await self._execute(
            """SELECT RUN_ID FROM AUTOMATION_RUN_CLAIMS
                WHERE CLAIM_KEY = %(key)s AND EXPIRES_AT > CURRENT_TIMESTAMP()""",
            {"key": idempotency_key}, fetch="one")
        return row[0] if row else None

    async def claim_idempotency(self, idempotency_key: str, run_id: str,
                                ttl_s: int = 120) -> bool:
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
