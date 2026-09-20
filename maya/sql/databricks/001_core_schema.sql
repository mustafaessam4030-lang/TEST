-- Maya Control Tower — Databricks / Delta Lake variant.
-- Same grain and semantics as the Snowflake schema; VARIANT becomes STRING
-- holding JSON (or the native VARIANT type on DBR 15.3+).
CREATE CATALOG IF NOT EXISTS maya;
CREATE SCHEMA  IF NOT EXISTS maya.core;
USE maya.core;

CREATE TABLE IF NOT EXISTS equipment_data (
    id                   STRING,
    tenant_id            STRING,
    serial_number        STRING NOT NULL,
    source_system        STRING NOT NULL,
    equipment_model      STRING,
    equipment_type       STRING,
    manufacturer         STRING,
    build_date           STRING,
    engine_family        STRING,   -- JSON
    specifications       STRING,   -- JSON
    parts_data           STRING,   -- JSON
    parts_manual_url     STRING,
    operation_manual_url STRING,
    source_url           STRING,
    raw_data             STRING,   -- JSON
    field_provenance     STRING,   -- JSON
    quality_score        DOUBLE,
    data_hash            STRING,
    schema_version       STRING,
    status               STRING,
    retrieved_at         TIMESTAMP,
    last_verified_at     TIMESTAMP,
    updated_at           TIMESTAMP,
    automation_run_id    STRING
) USING DELTA
  PARTITIONED BY (source_system)
  TBLPROPERTIES (delta.enableChangeDataFeed = true,
                 delta.autoOptimize.optimizeWrite = true);

CREATE TABLE IF NOT EXISTS equipment_data_history (
    id STRING, serial_number STRING, source_system STRING, snapshot STRING,
    data_hash STRING, quality_score DOUBLE, retrieved_at TIMESTAMP,
    automation_run_id STRING, version_at TIMESTAMP
) USING DELTA PARTITIONED BY (source_system);

CREATE TABLE IF NOT EXISTS automation_runs (
    automation_run_id STRING, tenant_id STRING, serial_number STRING, source STRING,
    trigger STRING, requested_by STRING, started_at TIMESTAMP, completed_at TIMESTAMP,
    status STRING, error_code STRING, error_message STRING, retry_count INT,
    execution_time_ms BIGINT, steps_executed STRING, extracted_field_count INT,
    quality_score DOUBLE, selector_version STRING, artifact_uri STRING, trace_id STRING
) USING DELTA PARTITIONED BY (source);

CREATE TABLE IF NOT EXISTS automation_run_steps (
    automation_run_id STRING, seq INT, step STRING, status STRING, duration_ms BIGINT,
    url STRING, error_code STRING, artifact_uri STRING, created_at TIMESTAMP
) USING DELTA;

-- MERGE pattern used by the repository (equivalent to the Snowflake MERGE):
-- MERGE INTO equipment_data t
-- USING source_df s
--    ON t.serial_number = s.serial_number AND t.source_system = s.source_system
-- WHEN MATCHED AND t.data_hash <> s.data_hash THEN UPDATE SET *
-- WHEN NOT MATCHED THEN INSERT *;

CREATE OR REPLACE VIEW v_equipment_current AS
SELECT *,
       DATEDIFF(CURRENT_TIMESTAMP(), retrieved_at) AS age_days,
       CASE WHEN status = 'NOT_FOUND'
                 AND DATEDIFF(CURRENT_TIMESTAMP(), retrieved_at) <= 7 THEN 'NEGATIVE_CACHED'
            WHEN status <> 'ACTIVE'                                   THEN 'MISSING'
            WHEN DATEDIFF(CURRENT_TIMESTAMP(), retrieved_at) <= 90    THEN 'FRESH'
            WHEN DATEDIFF(CURRENT_TIMESTAMP(), retrieved_at) > 730    THEN 'HARD_STALE'
            ELSE 'STALE' END AS freshness
  FROM equipment_data;
