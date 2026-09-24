-- Inspection pipeline tables (Snowflake).
-- Applied with:  python -m app.collect --init-schema
-- Idempotent: every statement is CREATE ... IF NOT EXISTS.
-- Primary keys are informational in Snowflake (not enforced); the pipeline
-- guarantees them by de-duplicating in Python and using MERGE.

-- One row per execution. Answers "did today's run happen and how did it go?"
CREATE TABLE IF NOT EXISTS PIPELINE_RUNS (
    RUN_ID              VARCHAR(36)    NOT NULL,
    SOURCE              VARCHAR(100)   NOT NULL,
    COLLECTOR_TYPE      VARCHAR(20)    NOT NULL,
    FILTERS             VARIANT,
    START_TIME          TIMESTAMP_TZ   NOT NULL,
    END_TIME            TIMESTAMP_TZ,
    STATUS              VARCHAR(20)    NOT NULL,
    RECORDS_FOUND       NUMBER(38,0),
    RECORDS_VALID       NUMBER(38,0),
    RECORDS_LOADED      NUMBER(38,0),
    RECORDS_FAILED      NUMBER(38,0),
    RECORDS_DUPLICATE   NUMBER(38,0),
    RECORDS_INSERTED    NUMBER(38,0),
    RECORDS_UPDATED     NUMBER(38,0),
    RECORDS_UNCHANGED   NUMBER(38,0),
    EXPECTED_TOTAL      NUMBER(38,0),
    ERROR_MESSAGE       VARCHAR,
    CREATED_AT          TIMESTAMP_TZ   NOT NULL,
    UPDATED_AT          TIMESTAMP_TZ   NOT NULL,
    CONSTRAINT PK_PIPELINE_RUNS PRIMARY KEY (RUN_ID)
);

-- Append-only landing table: one row per inspection per run (full history).
CREATE TABLE IF NOT EXISTS RAW_INSPECTIONS (
    RUN_ID              VARCHAR(36)    NOT NULL,
    SOURCE              VARCHAR(100)   NOT NULL,
    COLLECTOR_TYPE      VARCHAR(20)    NOT NULL,
    COLLECTED_AT        TIMESTAMP_TZ   NOT NULL,
    INSPECTION_NUMBER   VARCHAR(200)   NOT NULL,
    SERIAL_NUMBER       VARCHAR(200)   NOT NULL,
    STATUS              VARCHAR(200)   NOT NULL,
    INSPECTION_DATE     TIMESTAMP_TZ,
    SUMMARY             VARIANT,
    ATTACHMENTS         VARIANT,
    ATTACHMENT_COUNT    NUMBER(38,0)   NOT NULL,
    RECORD_HASH         VARCHAR(64)    NOT NULL,
    RAW_JSON            VARIANT        NOT NULL,
    CREATED_AT          TIMESTAMP_TZ   NOT NULL,
    UPDATED_AT          TIMESTAMP_TZ   NOT NULL,
    CONSTRAINT PK_RAW_INSPECTIONS PRIMARY KEY (RUN_ID, SOURCE, INSPECTION_NUMBER)
);

-- Records that failed mapping/validation, with the reason and the raw payload.
CREATE TABLE IF NOT EXISTS VALIDATION_ERRORS (
    RUN_ID              VARCHAR(36)    NOT NULL,
    SOURCE              VARCHAR(100)   NOT NULL,
    RECORD_IDENTIFIER   VARCHAR(500)   NOT NULL,
    ERROR_TYPE          VARCHAR(50)    NOT NULL,
    ERROR_MESSAGE       VARCHAR        NOT NULL,
    RAW_JSON            VARIANT,
    FAILED_AT           TIMESTAMP_TZ   NOT NULL,
    CREATED_AT          TIMESTAMP_TZ   NOT NULL
);

-- Current state: one row per business key (SOURCE, INSPECTION_NUMBER).
CREATE TABLE IF NOT EXISTS INSPECTIONS (
    SOURCE              VARCHAR(100)   NOT NULL,
    INSPECTION_NUMBER   VARCHAR(200)   NOT NULL,
    SERIAL_NUMBER       VARCHAR(200)   NOT NULL,
    STATUS              VARCHAR(200)   NOT NULL,
    INSPECTION_DATE     TIMESTAMP_TZ,
    SUMMARY             VARIANT,
    ATTACHMENTS         VARIANT,
    ATTACHMENT_COUNT    NUMBER(38,0)   NOT NULL,
    RECORD_HASH         VARCHAR(64)    NOT NULL,
    RAW_JSON            VARIANT        NOT NULL,
    FIRST_SEEN_RUN_ID   VARCHAR(36)    NOT NULL,
    FIRST_SEEN_AT       TIMESTAMP_TZ   NOT NULL,
    LAST_SEEN_RUN_ID    VARCHAR(36)    NOT NULL,
    LAST_SEEN_AT        TIMESTAMP_TZ   NOT NULL,
    LAST_CHANGED_RUN_ID VARCHAR(36)    NOT NULL,
    CREATED_AT          TIMESTAMP_TZ   NOT NULL,
    UPDATED_AT          TIMESTAMP_TZ   NOT NULL,
    CONSTRAINT PK_INSPECTIONS PRIMARY KEY (SOURCE, INSPECTION_NUMBER)
);
