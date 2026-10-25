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
  OPPORTUNITY_ID                                                            AS opportunity_id,
  UPPER(TRIM(SERIAL_NUMBER))                                                AS asset_serial,
  CUSTOMER_NUMBER                                                           AS customer_number,   -- identifier only; names excluded
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
  TARGET_SMU                                                                AS target_smu,        -- service-meter hours at which the job is due
  TOTAL_VALUE                                                               AS total_value,
  LABOR_VALUE                                                               AS labor_value,
  LEAD_SCORE                                                                AS lead_score,        -- OLGA 0-15; null on 72 % of rows
  CONFIDENCE_INDEX                                                          AS confidence_index,
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

-- Daily top-100 opportunity ranking (per machine, OLGA + telemetry). One ranking per run_date; history kept.
-- Rows stored per day: the top N overall plus the top N of each signal class (so each class can be listed).
CREATE TABLE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES (
  run_date             DATE          NOT NULL,      -- UTC date of the run
  overall_rank         NUMBER,                      -- 1..N across all machines (NULL if only in a class list)
  signal_class         VARCHAR       NOT NULL,      -- OLGA + telemetry / OLGA only / Telemetry only
  class_rank           NUMBER        NOT NULL,
  is_new_today         BOOLEAN       NOT NULL,      -- not in the previous run's overall top N
  asset_serial         VARCHAR       NOT NULL,
  model                VARCHAR,
  product_family       VARCHAR,
  branch               VARCHAR,
  customer_number      VARCHAR,
  score                FLOAT         NOT NULL,      -- value_points + urgency_points + condition_points (0-100)
  value_points         FLOAT,                       -- 35 x percentile of due OLGA value among machines with value
  urgency_points       FLOAT,                       -- 30 x (1 if due within 30 days or overdue, falling to 0 at 90 days)
  condition_points     FLOAT,                       -- 35 x min(1, days with the same HIGH fault in 30 days / 15); 0.2 floor if any
  due_value            FLOAT,                       -- OLGA value of opportunities due in [run-30, run+90] days (effective date)
  due_opportunities    NUMBER,
  top_job              VARCHAR,
  earliest_due         DATE,
  days_to_due          NUMBER,
  due_basis            VARCHAR,                     -- telemetry hours / OLGA date
  telemetry_timed_opps NUMBER,
  smu_now              FLOAT,
  hours_per_day        FLOAT,
  last_reading         DATE,
  max_fault_days       NUMBER,
  repeat_fault         VARCHAR,
  high_fault_events    NUMBER,
  last_fault_at        TIMESTAMP_NTZ,
  reason               VARCHAR,
  generated_at         TIMESTAMP_NTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG (
  asked_at   TIMESTAMP_NTZ DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ,
  user_email VARCHAR,
  question   VARCHAR,
  planner    VARCHAR,                                      -- rules / cortex
  plan       VARIANT,                                      -- metric, period, dimension, filters
  query_ids  VARIANT,
  outcome    VARCHAR                                       -- answered / refused / no_data / error
);

-- 5a. Daily top-N opportunity ranking per machine. Transparent rule, no model:
--   OLGA opportunities are re-timed with live telemetry: if the machine reports hours, effective due date =
--   last reading + (OLGA target hours - current hours) / average hours per day over the last 30 days; else OLGA's date.
--   Opportunities with an effective due date in [run - 30, run + HORIZON_DAYS] count towards the machine's due value.
--   Condition = HIGH-severity fault events (de-duplicated) in the last 30 days.
--   score = 35 x value percentile + 30 x urgency + 35 x condition (see column comments). Re-running a day replaces it.
CREATE OR REPLACE PROCEDURE SERVICE_INTELLIGENCE.SI_ADMIN.BUILD_DAILY_TOP_OPPORTUNITIES(TOP_N NUMBER, HORIZON_DAYS NUMBER)
RETURNS VARCHAR
LANGUAGE SQL
EXECUTE AS OWNER
AS
$$
DECLARE
  run_dt DATE DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::DATE;
  n NUMBER;
BEGIN
  DELETE FROM SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES WHERE run_date = :run_dt;
  INSERT INTO SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES
  WITH usage AS (
    SELECT asset_serial, MAX_BY(smh, reading_date) AS smu_now, MAX(reading_date) AS last_reading,
           (MAX_BY(smh, reading_date) - MIN_BY(smh, reading_date)) / NULLIF(DATEDIFF('day', MIN(reading_date), MAX(reading_date)), 0) AS hours_per_day
    FROM SERVICE_INTELLIGENCE.CURATED.ASSET_DAILY
    WHERE smh IS NOT NULL AND reading_date BETWEEN DATEADD('day', -30, :run_dt) AND :run_dt
    GROUP BY asset_serial HAVING COUNT(*) >= 5),
  opp AS (
    SELECT o.*, u.smu_now, u.hours_per_day, u.last_reading,
           IFF(u.smu_now IS NOT NULL AND o.target_smu > 0 AND u.hours_per_day BETWEEN 0.1 AND 24,
               DATEADD('day', GREATEST(-3650, LEAST(ROUND((o.target_smu - u.smu_now) / u.hours_per_day), 3650))::INT, u.last_reading),
               NULL) AS tel_due
    FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES o
    LEFT JOIN usage u ON u.asset_serial = o.asset_serial
    WHERE o.snapshot_date = (SELECT MAX(snapshot_date) FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES) AND o.total_value > 0),
  opp_due AS (
    SELECT *, COALESCE(tel_due, target_date) AS eff_due, IFF(tel_due IS NOT NULL, 'telemetry hours', 'OLGA date') AS due_basis
    FROM opp WHERE COALESCE(tel_due, target_date) BETWEEN DATEADD('day', -30, :run_dt) AND DATEADD('day', :horizon_days, :run_dt)),
  opp_asset AS (
    SELECT asset_serial, ANY_VALUE(model) AS model, ANY_VALUE(product_family) AS product_family, MODE(branch) AS branch,
           MODE(customer_number) AS customer_number, SUM(total_value) AS due_value, COUNT(*) AS due_opportunities,
           MAX_BY(INITCAP(COALESCE(job, '')) || ' ' || INITCAP(COALESCE(component, '')), total_value) AS top_job,
           MIN(eff_due) AS earliest_due, MIN_BY(due_basis, eff_due) AS due_basis,
           COUNT_IF(due_basis = 'telemetry hours') AS telemetry_timed_opps,
           ANY_VALUE(smu_now) AS smu_now, ANY_VALUE(hours_per_day) AS hours_per_day, ANY_VALUE(last_reading) AS last_reading
    FROM opp_due GROUP BY asset_serial),
  fault_by_code AS (
    SELECT asset_serial, fault_description, COUNT(DISTINCT occurred_at::DATE) AS days, COUNT(*) AS events, MAX(occurred_at) AS last_at,
           ANY_VALUE(model) AS model, ANY_VALUE(product_family) AS product_family, ANY_VALUE(dealer_code) AS dealer_code
    FROM SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS
    WHERE severity = 'HIGH' AND occurred_at > DATEADD('day', -30, :run_dt) AND occurred_at < DATEADD('day', 1, :run_dt)
    GROUP BY asset_serial, fault_description),
  faults AS (
    SELECT asset_serial, MAX(days) AS max_fault_days, MAX_BY(fault_description, days) AS repeat_fault, SUM(events) AS high_fault_events,
           MAX(last_at) AS last_fault_at, ANY_VALUE(model) AS model, ANY_VALUE(product_family) AS product_family,
           ANY_VALUE(dealer_code) AS dealer_code
    FROM fault_by_code GROUP BY asset_serial),
  cand AS (
    SELECT COALESCE(a.asset_serial, f.asset_serial) AS asset_serial, COALESCE(a.model, f.model) AS model,
           COALESCE(f.product_family, a.product_family) AS product_family, COALESCE(a.branch, 'Dealer ' || f.dealer_code) AS branch,
           a.customer_number, COALESCE(a.due_value, 0) AS due_value, COALESCE(a.due_opportunities, 0) AS due_opportunities,
           a.top_job, a.earliest_due, a.due_basis, a.telemetry_timed_opps, a.smu_now, a.hours_per_day, a.last_reading,
           f.max_fault_days, f.repeat_fault, f.high_fault_events, f.last_fault_at
    FROM opp_asset a FULL OUTER JOIN faults f ON f.asset_serial = a.asset_serial),
  scored AS (
    SELECT c.*,
           ROUND(35 * IFF(c.due_value > 0, PERCENT_RANK() OVER (PARTITION BY (c.due_value > 0) ORDER BY c.due_value), 0), 1) AS value_points,
           ROUND(30 * IFF(c.earliest_due IS NULL, 0,
                 LEAST(1, GREATEST(0, 1 - (DATEDIFF('day', :run_dt, c.earliest_due) - 30) / 60))), 1) AS urgency_points,
           ROUND(35 * IFF(c.max_fault_days IS NULL, 0, LEAST(1, GREATEST(0.2, c.max_fault_days / 15))), 1) AS condition_points,
           CASE WHEN c.due_value > 0 AND c.max_fault_days IS NOT NULL THEN 'OLGA + telemetry'
                WHEN c.due_value > 0 THEN 'OLGA only' ELSE 'Telemetry only' END AS signal_class
    FROM cand c),
  ranked AS (
    SELECT s.*, value_points + urgency_points + condition_points AS score,
           ROW_NUMBER() OVER (ORDER BY value_points + urgency_points + condition_points DESC, due_value DESC, asset_serial) AS overall_rank,
           ROW_NUMBER() OVER (PARTITION BY signal_class
                              ORDER BY value_points + urgency_points + condition_points DESC, due_value DESC, high_fault_events DESC NULLS LAST, asset_serial) AS class_rank
    FROM scored s),
  prev AS (
    SELECT asset_serial FROM SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES
    WHERE overall_rank IS NOT NULL
      AND run_date = (SELECT MAX(run_date) FROM SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES WHERE run_date < :run_dt))
  SELECT :run_dt, IFF(r.overall_rank <= :top_n, r.overall_rank, NULL), r.signal_class, r.class_rank, (p.asset_serial IS NULL),
         r.asset_serial, r.model, r.product_family, r.branch, r.customer_number, r.score, r.value_points, r.urgency_points,
         r.condition_points, r.due_value, r.due_opportunities, r.top_job, r.earliest_due, DATEDIFF('day', :run_dt, r.earliest_due),
         r.due_basis, r.telemetry_timed_opps, r.smu_now, r.hours_per_day, r.last_reading, r.max_fault_days, r.repeat_fault,
         r.high_fault_events, r.last_fault_at,
         ARRAY_TO_STRING(ARRAY_CONSTRUCT_COMPACT(
           IFF(r.due_value > 0, 'OLGA value due ' || TO_VARCHAR(ROUND(r.due_value), 'FM999,999,999,999') || ' (' || r.due_opportunities || ' jobs, top: ' || r.top_job || ')', NULL),
           IFF(r.earliest_due IS NOT NULL, IFF(r.earliest_due < :run_dt, 'overdue since ', 'due ') || r.earliest_due || ' by ' || r.due_basis, NULL),
           IFF(r.max_fault_days IS NOT NULL, 'HIGH fault "' || r.repeat_fault || '" on ' || r.max_fault_days || ' of the last 30 days', NULL)), '; '),
         CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP())::TIMESTAMP_NTZ
  FROM ranked r LEFT JOIN prev p ON p.asset_serial = r.asset_serial
  WHERE r.overall_rank <= :top_n OR r.class_rank <= :top_n;
  SELECT COUNT(*) INTO :n FROM SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES WHERE run_date = :run_dt AND overall_rank IS NOT NULL;
  RETURN 'top machines for ' || run_dt || ': ' || n;
END;
$$;

-- 5b. Daily refresh: re-copy the 10 tables from CAT_SHARE, rebuild CURATED tables, build the top-100 list, log row counts.
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
  CALL SERVICE_INTELLIGENCE.SI_ADMIN.BUILD_DAILY_TOP_OPPORTUNITIES(100, 90);
  RETURN 'refreshed';
END;
$$;

-- First list now (later lists come from the daily task).
CALL SERVICE_INTELLIGENCE.SI_ADMIN.BUILD_DAILY_TOP_OPPORTUNITIES(100, 90);

-- Cat refreshes the share at about 12:00 UTC; refresh at 13:30 UTC daily. Suspended until you resume it.
CREATE OR REPLACE TASK SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_DAILY
  WAREHOUSE = DEVELOPMENT_WH
  SCHEDULE = 'USING CRON 30 13 * * * UTC'
  COMMENT = 'Re-copy CAT_SHARE phase-1 tables and rebuild CURATED for Service Intelligence'
AS CALL SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_ALL();
-- ALTER TASK SERVICE_INTELLIGENCE.SI_ADMIN.REFRESH_DAILY RESUME;

-- 6. Grants for the app owner: read CURATED only (not the raw copies), write SI_APP only.
-- Future grants need MANAGE GRANTS, which DEV_ADMIN does not have, so this section runs as ACCOUNTADMIN.
USE ROLE ACCOUNTADMIN;
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
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.SI_APP.DAILY_TOP_OPPORTUNITIES TO ROLE SI_APP_OWNER;      -- read-only for the app
GRANT USAGE ON SEQUENCE SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATION_SEQ TO ROLE SI_APP_OWNER;
GRANT USAGE ON SCHEMA SERVICE_INTELLIGENCE.SI_ADMIN TO ROLE SI_APP_OWNER;
GRANT SELECT ON TABLE SERVICE_INTELLIGENCE.SI_ADMIN.COPY_LOG TO ROLE SI_APP_OWNER;                       -- freshness panel
-- Rebuilt tables lose grants on CREATE OR REPLACE; keep them with COPY GRANTS-free future grants:
GRANT SELECT ON FUTURE TABLES IN SCHEMA SERVICE_INTELLIGENCE.CURATED TO ROLE SI_APP_OWNER;
-- Cortex (assistant planner): PUBLIC already holds SNOWFLAKE.CORTEX_USER in this account; claude-sonnet-4-6 is in
-- CORTEX_MODELS_ALLOWLIST. Nothing to grant unless your admins remove PUBLIC's grant.

-- 7. Deploy the Streamlit app: see snowflake_native/README.md (upload app/ to the stage, then CREATE STREAMLIT).
