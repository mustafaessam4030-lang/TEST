-- ════════════════════════════════════════════════════════════════════════
--  Maia Control Tower — core schema (Snowflake)
--  Run as an admin role once per environment.
-- ════════════════════════════════════════════════════════════════════════
CREATE DATABASE IF NOT EXISTS MAIA_PROD;
CREATE SCHEMA   IF NOT EXISTS MAIA_PROD.CORE;
USE SCHEMA MAIA_PROD.CORE;

-- ── Source registry ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS SOURCE_REGISTRY (
    SOURCE_ID           STRING       NOT NULL PRIMARY KEY,
    LABEL               STRING       NOT NULL,
    KIND                STRING       NOT NULL,          -- browser | api | jdbc | stream
    BASE_URL            STRING,
    ENABLED             BOOLEAN      DEFAULT TRUE,
    PRECEDENCE          NUMBER(5,0)  DEFAULT 100,
    TTL_DAYS            NUMBER(6,0)  DEFAULT 30,
    SELECTOR_VERSION    STRING,
    SLA_MS              NUMBER(9,0),
    UPDATED_AT          TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()
);

-- ── Current equipment state ─────────────────────────────────────────────
-- Grain: one row per (SERIAL_NUMBER, SOURCE_SYSTEM). The same serial described
-- by two sources is two claims, reconciled at read time by precedence — never
-- silently overwritten, because that would destroy provenance.
CREATE TABLE IF NOT EXISTS EQUIPMENT_DATA (
    ID                   STRING        NOT NULL DEFAULT UUID_STRING(),
    TENANT_ID            STRING        DEFAULT 'mantrac_eg',
    SERIAL_NUMBER        STRING        NOT NULL,
    SOURCE_SYSTEM        STRING        NOT NULL,
    EQUIPMENT_MODEL      STRING,
    EQUIPMENT_TYPE       STRING,
    MANUFACTURER         STRING,
    BUILD_DATE           STRING,                        -- ISO-8601, partial precision allowed
    ENGINE_FAMILY        VARIANT,
    SPECIFICATIONS       VARIANT,
    PARTS_DATA           VARIANT,
    PARTS_MANUAL_URL     STRING,
    OPERATION_MANUAL_URL STRING,
    SOURCE_URL           STRING,
    RAW_DATA             VARIANT,                       -- exactly what the source returned
    FIELD_PROVENANCE     VARIANT,                       -- per-field origin + confidence
    QUALITY_SCORE        FLOAT,
    DATA_HASH            STRING,                        -- SHA-256 over business fields only
    SCHEMA_VERSION       STRING        DEFAULT '1.1.0',
    STATUS               STRING        DEFAULT 'ACTIVE',-- ACTIVE|NOT_FOUND|STALE|QUARANTINED|SUPERSEDED
    RETRIEVED_AT         TIMESTAMP_TZ  NOT NULL,
    LAST_VERIFIED_AT     TIMESTAMP_TZ,
    UPDATED_AT           TIMESTAMP_TZ  DEFAULT CURRENT_TIMESTAMP(),
    AUTOMATION_RUN_ID    STRING,
    CONSTRAINT PK_EQUIPMENT_DATA PRIMARY KEY (SERIAL_NUMBER, SOURCE_SYSTEM)
);

CREATE TABLE IF NOT EXISTS EQUIPMENT_DATA_HISTORY (
    ID                STRING       NOT NULL DEFAULT UUID_STRING(),
    SERIAL_NUMBER     STRING       NOT NULL,
    SOURCE_SYSTEM     STRING       NOT NULL,
    SNAPSHOT          VARIANT      NOT NULL,            -- the full normalized record
    DATA_HASH         STRING,
    QUALITY_SCORE     FLOAT,
    RETRIEVED_AT      TIMESTAMP_TZ,
    AUTOMATION_RUN_ID STRING,
    VERSION_AT        TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP()
);
-- Only written when DATA_HASH changes, so history records changes, not polls.

-- ── Automation audit ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS AUTOMATION_RUNS (
    AUTOMATION_RUN_ID     STRING       NOT NULL PRIMARY KEY,
    TENANT_ID             STRING       DEFAULT 'mantrac_eg',
    SERIAL_NUMBER         STRING       NOT NULL,
    SOURCE                STRING       NOT NULL,
    TRIGGER               STRING,                        -- user_request|stale_refresh|backfill
    REQUESTED_BY          STRING,
    STARTED_AT            TIMESTAMP_TZ NOT NULL,
    COMPLETED_AT          TIMESTAMP_TZ,
    STATUS                STRING       NOT NULL,         -- RUNNING|SUCCESS|FAILED|CANCELLED
    ERROR_CODE            STRING,
    ERROR_MESSAGE         STRING,
    RETRY_COUNT           NUMBER(3,0)  DEFAULT 0,
    EXECUTION_TIME_MS     NUMBER(10,0),
    STEPS_EXECUTED        VARIANT,
    EXTRACTED_FIELD_COUNT NUMBER(5,0),
    QUALITY_SCORE         FLOAT,
    SELECTOR_VERSION      STRING,
    ARTIFACT_URI          STRING,
    TRACE_ID              STRING
);

CREATE TABLE IF NOT EXISTS AUTOMATION_RUN_STEPS (
    AUTOMATION_RUN_ID STRING       NOT NULL,
    SEQ               NUMBER(3,0)  NOT NULL,
    STEP              STRING       NOT NULL,
    STATUS            STRING       NOT NULL,
    DURATION_MS       NUMBER(9,0),
    URL               STRING,
    ERROR_CODE        STRING,
    ARTIFACT_URI      STRING,
    CREATED_AT        TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_RUN_STEPS PRIMARY KEY (AUTOMATION_RUN_ID, SEQ)
);

-- Distributed idempotency: two users asking for the same serial at the same
-- moment share one browser run instead of racing.
CREATE TABLE IF NOT EXISTS AUTOMATION_RUN_CLAIMS (
    CLAIM_KEY  STRING       NOT NULL PRIMARY KEY,
    RUN_ID     STRING       NOT NULL,
    CLAIMED_AT TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    EXPIRES_AT TIMESTAMP_TZ NOT NULL
);

CREATE TABLE IF NOT EXISTS SELECTOR_VERSIONS (
    SOURCE_ID        STRING       NOT NULL,
    SELECTOR_VERSION STRING       NOT NULL,
    CONTRACT         VARIANT      NOT NULL,
    CONFIRMED_BY     STRING,
    CONFIRMED_AT     TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP(),
    CONSTRAINT PK_SELECTOR_VERSIONS PRIMARY KEY (SOURCE_ID, SELECTOR_VERSION)
);

-- ── Clustering & retention ──────────────────────────────────────────────
ALTER TABLE EQUIPMENT_DATA         CLUSTER BY (SOURCE_SYSTEM, SERIAL_NUMBER);
ALTER TABLE EQUIPMENT_DATA_HISTORY CLUSTER BY (SERIAL_NUMBER, VERSION_AT);
ALTER TABLE AUTOMATION_RUNS        CLUSTER BY (TO_DATE(STARTED_AT), SOURCE);
ALTER TABLE AUTOMATION_RUNS        SET DATA_RETENTION_TIME_IN_DAYS = 90;

INSERT INTO SOURCE_REGISTRY (SOURCE_ID, LABEL, KIND, BASE_URL, PRECEDENCE, TTL_DAYS)
SELECT 'cat_sis', 'Caterpillar SIS', 'browser', 'https://sis2.cat.com/#/', 10, 90
WHERE NOT EXISTS (SELECT 1 FROM SOURCE_REGISTRY WHERE SOURCE_ID = 'cat_sis');
