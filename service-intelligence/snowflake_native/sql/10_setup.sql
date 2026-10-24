-- =====================================================================================
-- Service Intelligence (Snowflake-native): curated layer, app tables, daily refresh, roles
-- Account FY56654. Scope: ONLY the tables in SERVICE_INTELLIGENCE (copies of CAT_SHARE DOMAIN_TELEMETRY and OLGA).
-- Run in Snowsight, top to bottom, as ACCOUNTADMIN (it switches to DEV_ADMIN, which owns SERVICE_INTELLIGENCE).
-- Re-runnable. It never writes to CAT_SHARE or any other database.
--
-- Every view below was executed read-only against the live copies on 24 Oct 2026; the measured counts are in
-- snowflake_native/README.md. Cleaning rules come from SERVICE_DATA_CATALOG.md §0.1.
-- =====================================================================================

-- 0. Roles (PROPOSED names; rename consistently if your admins prefer).
USE ROLE ACCOUNTADMIN;
CREATE ROLE IF NOT EXISTS SI_APP_OWNER  COMMENT = 'Owns the Service Intelligence Streamlit app: reads CURATED, writes SI_APP';
CREATE ROLE IF NOT EXISTS SI_APP_VIEWER COMMENT = 'Can open the Service Intelligence app (what they may change is decided in SI_APP.APP_USERS)';
GRANT ROLE SI_APP_OWNER TO ROLE DEV_ADMIN;
GRANT ROLE SI_APP_VIEWER TO ROLE SI_APP_OWNER;
GRANT EXECUTE TASK ON ACCOUNT TO ROLE DEV_ADMIN;                 -- for the daily refresh task (section 5)

CREATE WAREHOUSE IF NOT EXISTS SI_WH WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE STATEMENT_TIMEOUT_IN_SECONDS = 120 STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 60
  COMMENT = 'Service Intelligence app queries (interactive)';
GRANT USAGE ON WAREHOUSE SI_WH TO ROLE SI_APP_OWNER;
GRANT USAGE ON WAREHOUSE SI_WH TO ROLE DEV_ADMIN;
-- Optional credit cap:
-- CREATE RESOURCE MONITOR SI_MONITOR WITH CREDIT_QUOTA = 30 FREQUENCY = MONTHLY START_TIMESTAMP = IMMEDIATELY
--   TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND;
-- ALTER WAREHOUSE SI_WH SET RESOURCE_MONITOR = SI_MONITOR;

USE ROLE DEV_ADMIN;
USE WAREHOUSE DEVELOPMENT_WH;

-- 1. Schemas.
CREATE SCHEMA IF NOT EXISTS SERVICE_INTELLIGENCE.CURATED COMMENT = 'Cleaned, de-duplicated models the app reads (views + daily materialized tables)';
CREATE SCHEMA IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP  COMMENT = 'Service Intelligence app: recommendation plan, audit, users, chat log';
CREATE SCHEMA IF NOT EXISTS SERVICE_INTELLIGENCE.SI_ADMIN COMMENT = 'Service Intelligence load log and application state';

-- 2. Curated views (logic). Names, freshness and cleaning rules are documented on each view.
CREATE OR REPLACE VIEW SERVICE_INTELLIGENCE.CURATED.V_FAULT_EVENTS
  COMMENT = 'One row per fault event from FAULT_CODE_HISTORY. 37% of source rows repeat an event (same asset+ECU+codes+time): latest receipt kept. Future-dated events dropped. Times are UTC (assumed: Cat telematics, no zone in the text).'
AS
SELECT
  MD5(CONCAT_WS('|', ASSET_SERIAL_NUMBER, COALESCE(FAULT_ECU_ID,''), COALESCE(SPN_CODE,''), COALESCE(FMI_CODE,''),
                COALESCE(CID_CODE,''), COALESCE(EID_CODE,''), OCCURRENCE_DATETIME)) AS event_key,
  UPPER(TRIM(ASSET_SERIAL_NUMBER))                                          AS asset_serial,
  STANDARD_MANUFACTURER_CODE                                                AS manufacturer,
  SALES_MODEL_AND_MODIFIER_NUMBER                                           AS model,
  COALESCE(NULLIF(TRIM(PRODUCT_FAMILY_DESCRIPTION), ''), '(not set)')       AS product_family,
  DEALER_CODE_SHARE                                                         AS dealer_code,
  FAULT_ECU_ID                                                              AS ecu_id,
  FAULT_ECU_DESCRIPTION                                                     AS ecu_description,
  CASE WHEN SPN_CODE IS NOT NULL THEN 'J1939 SPN' WHEN CID_CODE IS NOT NULL THEN 'CAT CID'
       WHEN EID_CODE IS NOT NULL THEN 'CAT EID' END                         AS code_system,
  CASE WHEN SPN_CODE IS NOT NULL THEN 'SPN ' || SPN_CODE || COALESCE(' FMI ' || FMI_CODE, '')
       WHEN CID_CODE IS NOT NULL THEN 'CID ' || CID_CODE || COALESCE(' FMI ' || FMI_CODE, '')
       WHEN EID_CODE IS NOT NULL THEN 'EID ' || EID_CODE END                AS fault_code,
  DESCRIPTION                                                               AS fault_description,
  FAULT_TYPE_DESCRIPTION                                                    AS fault_type,
  SEVERITY                                                                  AS severity,
  TRY_TO_NUMBER(OCCURRENCE_COUNT)                                           AS occurrence_count,
  TRY_TO_TIMESTAMP_NTZ(OCCURRENCE_DATETIME)                                 AS occurred_at,
  TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME)                                   AS received_at,
  IFF(TRY_TO_DOUBLE(TRUSTED_MASTER_SMH_TOTAL) BETWEEN 0 AND 200000,
      TRY_TO_DOUBLE(TRUSTED_MASTER_SMH_TOTAL), NULL)                        AS smh_at_fault,
  TRY_TO_TIMESTAMP_NTZ(LAST_UPDATE_TIMESTAMP)                               AS loaded_at
FROM SERVICE_INTELLIGENCE.DOMAIN_TELEMETRY.FAULT_CODE_HISTORY
WHERE TRY_TO_TIMESTAMP_NTZ(OCCURRENCE_DATETIME) IS NOT NULL
  AND TRY_TO_TIMESTAMP_NTZ(OCCURRENCE_DATETIME) <= DATEADD('day', 1, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ)
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY ASSET_SERIAL_NUMBER, FAULT_ECU_ID, SPN_CODE, FMI_CODE, CID_CODE, EID_CODE, OCCURRENCE_DATETIME
  ORDER BY TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME) DESC, TRY_TO_TIMESTAMP_NTZ(LAST_UPDATE_TIMESTAMP) DESC) = 1;

CREATE OR REPLACE VIEW SERVICE_INTELLIGENCE.CURATED.V_ASSET_DAILY
  COMMENT = 'One service-meter reading per asset per UTC day (latest valid reading). VALID_FLAG = V only; hours outside 0-200,000 set to NULL; future-dated readings dropped.'
AS
SELECT
  UPPER(TRIM(ASSET_SERIAL_NUMBER))                                          AS asset_serial,
  STANDARD_MANUFACTURER_CODE                                                AS manufacturer,
  SALES_MODEL_AND_MODIFIER_NUMBER                                           AS model,
  TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME)::DATE                             AS reading_date,
  TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME)                                   AS received_at,
  IFF(TRY_TO_DOUBLE(SERVICE_METER_HOURS_VALUE) BETWEEN 0 AND 200000,
      TRY_TO_DOUBLE(SERVICE_METER_HOURS_VALUE), NULL)                       AS smh,
  IFF(TRY_TO_DOUBLE(IDLE_SERVICE_METER_HOURS_VALUE) BETWEEN 0 AND 200000,
      TRY_TO_DOUBLE(IDLE_SERVICE_METER_HOURS_VALUE), NULL)                  AS idle_smh,
  LAST_UPDATE_TIMESTAMP                                                     AS loaded_at
FROM SERVICE_INTELLIGENCE.DOMAIN_TELEMETRY.TELEMETRY_SMU_LOCATION_BASIC_DAILY
WHERE VALID_FLAG = 'V'
  AND TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME) <= DATEADD('day', 1, CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ)
QUALIFY ROW_NUMBER() OVER (PARTITION BY UPPER(TRIM(ASSET_SERIAL_NUMBER)), TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME)::DATE
                           ORDER BY TRY_TO_TIMESTAMP_NTZ(RECEIVED_DATETIME) DESC) = 1;

CREATE OR REPLACE VIEW SERVICE_INTELLIGENCE.CURATED.V_OPPORTUNITIES
  COMMENT = 'OLGA opportunities, one row per OPPORTUNITY_KEY. Placeholder target date 2099-12-31 removed. snapshot_date = last day of END_OF_MONTH (text "Month YYYY"). Values are OLGA TOTAL_VALUE; currency to be confirmed with the OLGA owner. Customer and sales-rep names, state, county, postal code excluded.'
AS
SELECT
  OPPORTUNITY_KEY                                                           AS opportunity_key,
  UPPER(TRIM(SERIAL_NUMBER))                                                AS asset_serial,
  MODEL                                                                     AS model,
  COALESCE(NULLIF(TRIM(PRODUCT_FAMILY), ''), '(not set)')                   AS product_family,
  DEALER_CODE                                                               AS dealer_code,
  COALESCE(NULLIF(TRIM(BRANCH_DESC), ''), NULLIF(TRIM(BRANCH_CODE), ''), '(not set)') AS branch,
  COALESCE(NULLIF(TRIM(SMCS_GROUP_DESC), ''), '(not set)')                  AS smcs_group,
  COMPONENT_DESC                                                            AS component,
  JOB_DESC                                                                  AS job,
  OPPORTUNITY_SOURCE                                                        AS source,
  CONNECTED_ASSET                                                           AS connected_asset,
  LAST_DAY(TRY_TO_DATE(END_OF_MONTH, 'MMMM YYYY'))                          AS snapshot_date,
  TARGET_DATE::DATE                                                         AS target_date,
  TOTAL_VALUE                                                               AS total_value,
  LABOR_VALUE                                                               AS labor_value,
  LAST_UPDATE_TIMESTAMP                                                     AS loaded_at
FROM SERVICE_INTELLIGENCE.OLGA.OPPORTUNITIES_TABLE
WHERE TARGET_DATE < '2099-01-01';

CREATE OR REPLACE VIEW SERVICE_INTELLIGENCE.CURATED.V_LOST_OPPORTUNITY_MONTHLY
  COMMENT = 'OLGA lost-opportunity report aggregated per month, dealer, branch and SMCS group. Exact duplicate source rows (272) removed. lost = opportunity - sales exactly (negative = sold above forecast). Values in OLGA SCP; currency to be confirmed.'
AS
WITH src AS (SELECT DISTINCT * FROM SERVICE_INTELLIGENCE.OLGA.LOST_OPPORTUNITY_REPORT_TABLE)
SELECT
  TRY_TO_DATE(YEAR_MONTH_DATE)                                              AS month_start,
  DEALER_CODE                                                               AS dealer_code,
  COALESCE(NULLIF(TRIM(BRANCH_DESCRIPTION), ''), NULLIF(TRIM(BRANCH_CODE), ''), '(not set)') AS branch,
  COALESCE(NULLIF(TRIM(SMCS_GROUP_DESCRIPTION), ''), '(not set)')           AS smcs_group,
  COUNT(DISTINCT SERIAL_NUMBER)                                             AS serials,
  SUM(SALES_SCP)                                                            AS sales,
  SUM(OPPORTUNITY_SCP)                                                      AS opportunity,
  SUM(LOST_OPPORTUNITY_SCP)                                                 AS lost
FROM src
GROUP BY 1, 2, 3, 4;

-- 3. Materialized tables the app reads (rebuilt daily by section 5; the views above stay the source of logic).
CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS            AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_FAULT_EVENTS;
CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY             AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_ASSET_DAILY;
CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES           AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_OPPORTUNITIES;
CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.LOST_OPPORTUNITY_MONTHLY AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_LOST_OPPORTUNITY_MONTHLY;

-- 4. App tables: plan, append-only audit, app users (authorization), chat log.
CREATE SEQUENCE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATION_SEQ START = 101;

CREATE TABLE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATIONS (
  rec_id          VARCHAR      NOT NULL PRIMARY KEY,      -- R-101, R-102, ...
  detector        VARCHAR      NOT NULL,
  candidate_key   VARCHAR      NOT NULL,                   -- stable key of the finding (detector + segment)
  title           VARCHAR      NOT NULL,
  explanation     VARCHAR,
  problem         VARCHAR,
  metric_id       VARCHAR,
  segment         VARIANT,                                 -- filters that reproduce the headline number
  evidence        VARIANT,                                 -- measured figures, SQL, binds, query ids
  priority        VARCHAR,                                 -- High / Medium / Low
  score           FLOAT,
  score_breakdown VARIANT,
  expected_impact VARCHAR,                                 -- hypothesis text with its assumptions
  actions         VARIANT,
  risks           VARCHAR,
  confidence      VARCHAR,
  owner           VARCHAR,
  status          VARCHAR      NOT NULL DEFAULT 'proposed',
  data_as_of      TIMESTAMP_NTZ,
  analysis_version VARCHAR,
  created_by      VARCHAR      NOT NULL,
  created_at      TIMESTAMP_NTZ NOT NULL DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ,
  updated_at      TIMESTAMP_NTZ,
  version         NUMBER       NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATION_EVENTS (
  event_at   TIMESTAMP_NTZ NOT NULL DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ,
  rec_id     VARCHAR NOT NULL,
  actor      VARCHAR NOT NULL,
  action     VARCHAR NOT NULL,                             -- created / status / assign / comment
  from_status VARCHAR,
  to_status  VARCHAR,
  note       VARCHAR
);

CREATE TABLE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.APP_USERS (
  email    VARCHAR NOT NULL PRIMARY KEY,                   -- as reported by Streamlit (st.user.email), lower case
  app_role VARCHAR NOT NULL,                               -- viewer / analyst / approver / admin
  added_at TIMESTAMP_NTZ DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ
);
MERGE INTO SERVICE_INTELLIGENCE.SI_APP.APP_USERS t
USING (SELECT 'memabrouk@mantracgroup.com' email, 'admin' app_role) s ON t.email = s.email
WHEN NOT MATCHED THEN INSERT (email, app_role) VALUES (s.email, s.app_role);

CREATE TABLE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG (
  asked_at   TIMESTAMP_NTZ DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ,
  user_email VARCHAR,
  question   VARCHAR,
  planner    VARCHAR,                                      -- rules / cortex
  plan       VARIANT,                                      -- metric, period, dimension, filters
  query_ids  VARIANT,
  outcome    VARCHAR                                       -- answered / refused / no_data / error
);

-- 5. Daily refresh: re-copy the 10 tables from CAT_SHARE, rebuild CURATED tables, log row counts.
CREATE OR REPLACE PROCEDURE SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_ALL()
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
BEGIN
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.DOMAIN_TELEMETRY.FAULT_CODE_BASIC_DAILY AS SELECT * FROM CAT_SHARE.DOMAIN_TELEMETRY.FAULT_CODE_BASIC_DAILY;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.DOMAIN_TELEMETRY.FAULT_CODE_HISTORY AS SELECT * FROM CAT_SHARE.DOMAIN_TELEMETRY.FAULT_CODE_HISTORY;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.DOMAIN_TELEMETRY.TELEMETRY_FUEL_BASIC_DAILY AS SELECT * FROM CAT_SHARE.DOMAIN_TELEMETRY.TELEMETRY_FUEL_BASIC_DAILY;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.DOMAIN_TELEMETRY.TELEMETRY_SMU_LOCATION_BASIC_DAILY AS SELECT * FROM CAT_SHARE.DOMAIN_TELEMETRY.TELEMETRY_SMU_LOCATION_BASIC_DAILY;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.OLGA.COMPANY_CONFIG_TABLE AS SELECT * FROM CAT_SHARE.OLGA.COMPANY_CONFIG_TABLE;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.OLGA.CUSTOMER_TOTALS_TABLE AS SELECT * FROM CAT_SHARE.OLGA.CUSTOMER_TOTALS_TABLE;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.OLGA.EQUIPMENT_REPORT_DETAIL_TABLE AS SELECT * FROM CAT_SHARE.OLGA.EQUIPMENT_REPORT_DETAIL_TABLE;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.OLGA.LOST_OPPORTUNITY_REPORT_TABLE AS SELECT * FROM CAT_SHARE.OLGA.LOST_OPPORTUNITY_REPORT_TABLE;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.OLGA.OPPORTUNITIES_TABLE AS SELECT * FROM CAT_SHARE.OLGA.OPPORTUNITIES_TABLE;
  CREATE OR REPLACE TRANSIENT TABLE SERVICE_INTELLIGENCE.OLGA.OPPORTUNITY_MAJORCLASS_SUBCATEGORY_PARTCOST_TABLE AS SELECT * FROM CAT_SHARE.OLGA.OPPORTUNITY_MAJORCLASS_SUBCATEGORY_PARTCOST_TABLE;

  CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_FAULT_EVENTS;
  CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_ASSET_DAILY;
  CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_OPPORTUNITIES;
  CREATE OR REPLACE TABLE SERVICE_INTELLIGENCE.CURATED.LOST_OPPORTUNITY_MONTHLY AS SELECT * FROM SERVICE_INTELLIGENCE.CURATED.V_LOST_OPPORTUNITY_MONTHLY;

  INSERT INTO SERVICE_INTELLIGENCE.SI_ADMIN.COPY_LOG (source_table, target_table, source_rows, target_rows)
    SELECT 'CURATED', 'FAULT_EVENTS', NULL, COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS
    UNION ALL SELECT 'CURATED', 'ASSET_DAILY', NULL, COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY
    UNION ALL SELECT 'CURATED', 'OPPORTUNITIES', NULL, COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES
    UNION ALL SELECT 'CURATED', 'LOST_OPPORTUNITY_MONTHLY', NULL, COUNT(*) FROM SERVICE_INTELLIGENCE.CURATED.LOST_OPPORTUNITY_MONTHLY;
  RETURN 'refreshed';
END;
$$;

-- Cat refreshes the share at about 12:00 UTC; refresh at 13:30 UTC daily. Suspended until you resume it.
CREATE OR REPLACE TASK SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_DAILY
  WAREHOUSE = DEVELOPMENT_WH
  SCHEDULE = 'USING CRON 30 13 * * * UTC'
  COMMENT = 'Re-copy CAT_SHARE phase-1 tables and rebuild CURATED for Service Intelligence'
AS CALL SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_ALL();
-- ALTER TASK SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_DAILY RESUME;

-- 6. Grants for the app owner: read CURATED only (not the raw copies), write SI_APP only.
GRANT USAGE ON DATABASE SERVICE_INTELLIGENCE TO ROLE SI_APP_OWNER;
GRANT USAGE ON SCHEMA SERVICE_INTELLIGENCE.CURATED TO ROLE SI_APP_OWNER;
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS TO ROLE SI_APP_OWNER;
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY TO ROLE SI_APP_OWNER;
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES TO ROLE SI_APP_OWNER;
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.CURATED.LOST_OPPORTUNITY_MONTHLY TO ROLE SI_APP_OWNER;
GRANT USAGE, CREATE STREAMLIT, CREATE STAGE ON SCHEMA SERVICE_INTELLIGENCE.SI_APP TO ROLE SI_APP_OWNER;
GRANT SELECT, INSERT, UPDATE ON TABLE SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATIONS TO ROLE SI_APP_OWNER;
GRANT SELECT, INSERT ON TABLE SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATION_EVENTS TO ROLE SI_APP_OWNER;   -- append-only
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.SI_APP.APP_USERS TO ROLE SI_APP_OWNER;                       -- admins manage it in SQL
GRANT INSERT ON TABLE SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG TO ROLE SI_APP_OWNER;
GRANT USAGE ON SEQUENCE SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATION_SEQ TO ROLE SI_APP_OWNER;
GRANT USAGE ON SCHEMA SERVICE_INTELLIGENCE.SI_ADMIN TO ROLE SI_APP_OWNER;
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.SI_ADMIN.COPY_LOG TO ROLE SI_APP_OWNER;                       -- freshness panel
-- Rebuilt tables lose grants on CREATE OR REPLACE; keep them with COPY GRANTS-free future grants:
GRANT SELECT ON FUTURE TABLES IN SCHEMA SERVICE_INTELLIGENCE.CURATED TO ROLE SI_APP_OWNER;
-- Cortex (assistant planner): PUBLIC already holds SNOWFLAKE.CORTEX_USER in this account; claude-sonnet-4-6 is in
-- CORTEX_MODELS_ALLOWLIST. Nothing to grant unless your admins remove PUBLIC's grant.

-- 7. Deploy the Streamlit app: see snowflake_native/README.md (upload app/ to the stage, then CREATE STREAMLIT).
