-- Migration 004: one row per part, the PRODUCT heading, the normalized record,
-- and what Maia's role needs to call Snowflake Cortex.
--
-- Additive and safe to re-run. EQUIPMENT_DATA and EQUIPMENT_DATA_HISTORY stay
-- as they are; the equivalents the spec calls EQUIPMENT_PARTS and
-- AUTOMATION_RUNS are this table and the existing AUTOMATION_RUNS.

ALTER TABLE EQUIPMENT_DATA ADD COLUMN IF NOT EXISTS PRODUCT STRING;
ALTER TABLE EQUIPMENT_DATA ADD COLUMN IF NOT EXISTS NORMALIZED_DATA VARIANT;

-- One row per part row the source page published, for the CURRENT version of
-- each (serial, source). Earlier versions live in EQUIPMENT_DATA_HISTORY.
-- A column is filled only when the page had a header for it; every cell,
-- mapped or not, is kept in RAW_DATA. Nothing here is inferred.
CREATE TABLE IF NOT EXISTS EQUIPMENT_PARTS (
    SERIAL_NUMBER      STRING       NOT NULL,
    SOURCE_SYSTEM      STRING       NOT NULL,
    GROUP_NAME         STRING,                 -- the page's heading, minus the serial
    GROUP_TITLE        STRING,                 -- the heading exactly as written
    GROUP_SERIAL       STRING,
    GROUP_PART         STRING,
    PART_NUMBER        STRING,
    PART_NAME          STRING,
    QUANTITY_REQUIRED  FLOAT,                  -- NULL when not published or not numeric
    QUANTITY_TEXT      STRING,                 -- the cell as published
    WHERE_USED         STRING,
    SERVICE_ARTICLE    STRING,
    SN_APPLICABILITY   STRING,
    COMPONENT_SERIAL   STRING,
    PART_OF            STRING,
    DESCRIPTION        STRING,
    ROW_LOCATOR        STRING,
    SOURCE             STRING,                 -- source label, e.g. 'Caterpillar SIS'
    RETRIEVED_AT       TIMESTAMP_TZ,
    AUTOMATION_RUN_ID  STRING,
    RAW_DATA           VARIANT,
    LOADED_AT          TIMESTAMP_TZ DEFAULT CURRENT_TIMESTAMP()
);

ALTER TABLE EQUIPMENT_PARTS CLUSTER BY (SERIAL_NUMBER, SOURCE_SYSTEM);

-- ── Cortex access (admin, --with-grants) ────────────────────────────────
-- AI_COMPLETE and Cortex Agents need a Cortex database role on Maia's role.
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE MAIA_APP;
