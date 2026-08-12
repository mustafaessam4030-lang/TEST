/* =====================================================================
   02_control_tables.sql
   ---------------------------------------------------------------------
   Metadata-driven control layer.

   PIPELINE_CONTROL  - the single place where you declare which Oracle
                       tables are in scope and how each one is loaded.
   LOAD_WATERMARK    - high-water mark per table for incremental loads.
   LOAD_AUDIT        - one row per table per pipeline run.
   ===================================================================== */

USE DATABASE ORACLE_LANDING;
USE SCHEMA   CTL;

-- ---------------------------------------------------------------------
-- Control table: the driver for the whole framework
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CTL.PIPELINE_CONTROL (
    CONTROL_ID            NUMBER(10,0)  IDENTITY(1,1) PRIMARY KEY,
    SOURCE_SYSTEM         VARCHAR(50)   NOT NULL DEFAULT 'ORACLE',
    SOURCE_SCHEMA         VARCHAR(128)  NOT NULL,
    SOURCE_TABLE          VARCHAR(128)  NOT NULL,
    TARGET_DATABASE       VARCHAR(128)  NOT NULL DEFAULT 'ORACLE_LANDING',
    TARGET_SCHEMA         VARCHAR(128)  NOT NULL DEFAULT 'RAW',
    TARGET_TABLE          VARCHAR(128)  NOT NULL,

    -- FULL        : truncate the target, then reload everything
    -- INCREMENTAL : append rows where WATERMARK_COLUMN > last high-water mark
    LOAD_TYPE             VARCHAR(20)   NOT NULL DEFAULT 'FULL',
    WATERMARK_COLUMN      VARCHAR(128),

    -- Optional. When set, ADF splits the Oracle read into parallel ranges
    -- on this column. Use a numeric or date column with an index.
    PARTITION_COLUMN      VARCHAR(128),

    -- Optional. Overrides the generated SELECT (e.g. to filter or to
    -- cast an exotic column). Must return the target's column set.
    SOURCE_QUERY_OVERRIDE VARCHAR(16777216),

    -- Free-form grouping so you can run subsets: 'FINANCE', 'NIGHTLY', ...
    LOAD_GROUP            VARCHAR(50)   NOT NULL DEFAULT 'DEFAULT',

    IS_ACTIVE             BOOLEAN       NOT NULL DEFAULT TRUE,
    PRIORITY              NUMBER(5,0)   NOT NULL DEFAULT 100,
    CREATED_AT            TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),
    UPDATED_AT            TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP(),

    CONSTRAINT UQ_PIPELINE_CONTROL UNIQUE (SOURCE_SCHEMA, SOURCE_TABLE, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE)
);

-- ---------------------------------------------------------------------
-- Watermarks for incremental loads
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CTL.LOAD_WATERMARK (
    CONTROL_ID       NUMBER(10,0)  NOT NULL PRIMARY KEY,
    HIGH_WATERMARK   VARCHAR(64),           -- stored as 'YYYY-MM-DD HH24:MI:SS' or a numeric string
    WATERMARK_TYPE   VARCHAR(20)   NOT NULL DEFAULT 'TIMESTAMP',  -- TIMESTAMP | NUMBER
    LAST_LOADED_AT   TIMESTAMP_NTZ,
    UPDATED_AT       TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Run audit
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS CTL.LOAD_AUDIT (
    AUDIT_ID         NUMBER(18,0)  IDENTITY(1,1) PRIMARY KEY,
    CONTROL_ID       NUMBER(10,0),
    ADF_FACTORY      VARCHAR(200),
    ADF_PIPELINE     VARCHAR(200),
    ADF_RUN_ID       VARCHAR(100),   -- run id of the per-table child pipeline
    ADF_PARENT_RUN_ID VARCHAR(100),  -- run id of PL_MASTER_ORACLE_TO_SNOWFLAKE, so one batch is one filter
    SOURCE_OBJECT    VARCHAR(400),
    TARGET_OBJECT    VARCHAR(400),
    PHASE            VARCHAR(30),   -- DDL | DATA
    LOAD_TYPE        VARCHAR(20),
    STATUS           VARCHAR(20),   -- SUCCEEDED | FAILED
    ROWS_READ        NUMBER(18,0),
    ROWS_WRITTEN     NUMBER(18,0),
    DURATION_SEC     NUMBER(12,2),
    ERROR_MESSAGE    VARCHAR(16777216),
    LOGGED_AT        TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- ---------------------------------------------------------------------
-- Convenience view: latest outcome per table
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW CTL.V_LAST_LOAD_STATUS AS
SELECT c.CONTROL_ID,
       c.SOURCE_SCHEMA || '.' || c.SOURCE_TABLE                                AS SOURCE_OBJECT,
       c.TARGET_DATABASE || '.' || c.TARGET_SCHEMA || '.' || c.TARGET_TABLE    AS TARGET_OBJECT,
       c.LOAD_TYPE,
       c.IS_ACTIVE,
       a.STATUS          AS LAST_STATUS,
       a.ROWS_WRITTEN    AS LAST_ROWS_WRITTEN,
       a.DURATION_SEC    AS LAST_DURATION_SEC,
       a.ERROR_MESSAGE   AS LAST_ERROR,
       a.LOGGED_AT       AS LAST_RUN_AT,
       w.HIGH_WATERMARK
FROM CTL.PIPELINE_CONTROL c
LEFT JOIN CTL.LOAD_WATERMARK w
       ON w.CONTROL_ID = c.CONTROL_ID
LEFT JOIN (
    SELECT *
    FROM (
        SELECT a.*,
               ROW_NUMBER() OVER (PARTITION BY CONTROL_ID ORDER BY LOGGED_AT DESC, AUDIT_ID DESC) AS rn
        FROM CTL.LOAD_AUDIT a
        WHERE PHASE = 'DATA'
    )
    WHERE rn = 1
) a ON a.CONTROL_ID = c.CONTROL_ID;
