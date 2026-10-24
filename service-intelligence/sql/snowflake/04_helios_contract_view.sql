-- =====================================================================================
-- Service Intelligence: first live data source (Helios service orders)
-- Account FY56654. Fills in 00_roles_and_grants.sql for the Helios mapping
-- (config/source_mapping.helios.yaml). Role, warehouse and user names are PROPOSED; rename them if your
-- admins prefer, and keep SI_CONTRACT_VIEW in the app's environment in step.
-- Run in Snowsight as ACCOUNTADMIN (or SECURITYADMIN for roles/users + SYSADMIN for objects).
-- Nothing here writes to MANDL_CDM_PROD: the app only reads a secure view over it.
-- =====================================================================================
USE ROLE ACCOUNTADMIN;

-- 1. Warehouse with hard limits (X-Small; the 9 KPI queries plus a breakdown took seconds on X-Small).
CREATE WAREHOUSE IF NOT EXISTS SI_WH WAREHOUSE_SIZE = 'XSMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE STATEMENT_TIMEOUT_IN_SECONDS = 120 STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 60
  COMMENT = 'Service Intelligence app queries';

-- 2. Roles: view owner reads the source; reader sees only the view; plan writer writes plan tables only.
CREATE ROLE IF NOT EXISTS SI_VIEW_OWNER  COMMENT = 'Owns the Service Intelligence contract view; reads the mapped source';
CREATE ROLE IF NOT EXISTS SI_READER      COMMENT = 'Service Intelligence: SELECT on the contract view only';
CREATE ROLE IF NOT EXISTS SI_PLAN_WRITER COMMENT = 'Service Intelligence: write recommendation plan tables only';
GRANT ROLE SI_VIEW_OWNER TO ROLE SYSADMIN;
GRANT ROLE SI_READER TO ROLE SYSADMIN;
GRANT ROLE SI_PLAN_WRITER TO ROLE SYSADMIN;
GRANT USAGE ON WAREHOUSE SI_WH TO ROLE SI_VIEW_OWNER;
GRANT USAGE ON WAREHOUSE SI_WH TO ROLE SI_READER;
GRANT USAGE ON WAREHOUSE SI_WH TO ROLE SI_PLAN_WRITER;

-- 3. Schema for the contract view (the database already exists, created by 01_phase1_cat_share_copy.sql).
CREATE SCHEMA IF NOT EXISTS SERVICE_INTELLIGENCE.SERVICE_INTEL COMMENT = 'Service Intelligence governed contract views';
GRANT USAGE ON DATABASE SERVICE_INTELLIGENCE TO ROLE SI_VIEW_OWNER;
GRANT USAGE, CREATE VIEW ON SCHEMA SERVICE_INTELLIGENCE.SERVICE_INTEL TO ROLE SI_VIEW_OWNER;
GRANT USAGE ON DATABASE MANDL_CDM_PROD TO ROLE SI_VIEW_OWNER;
GRANT USAGE ON SCHEMA MANDL_CDM_PROD.MANCDM TO ROLE SI_VIEW_OWNER;
GRANT SELECT ON TABLE MANDL_CDM_PROD.MANCDM.SERVICEORDER TO ROLE SI_VIEW_OWNER;

-- 4. The contract view. Generated, do not edit by hand:
--    SI_MAPPING_PATH=config/source_mapping.helios.yaml \
--    SI_CONTRACT_VIEW=SERVICE_INTELLIGENCE.SERVICE_INTEL.V_SERVICE_REQUESTS python -m si.mapping ddl
USE ROLE SI_VIEW_OWNER;
USE WAREHOUSE SI_WH;
CREATE OR REPLACE SECURE VIEW SERVICE_INTELLIGENCE.SERVICE_INTEL.V_SERVICE_REQUESTS
  COMMENT = 'Service Intelligence contract view generated from a reviewed source mapping'
AS
SELECT
  CAST(SERVICEORDERID AS VARCHAR) AS request_id,
  CAST(OPENDATE AS TIMESTAMP_NTZ) AS created_at,
  CAST(LASTCLOSEDATE AS TIMESTAMP_NTZ) AS closed_at,
  CAST(NULL AS TIMESTAMP_NTZ) AS due_at,
  CAST(LASTUPDATEDATE AS TIMESTAMP_NTZ) AS updated_at,
  CAST(LASTUPDATEDDATETIME AS TIMESTAMP_NTZ) AS loaded_at,
  CAST(SERVICEORDERSTATUS AS VARCHAR) AS status,
  CASE WHEN UPPER(TRIM(SERVICEORDERSTATUS)) IN ('O') THEN 'OPEN' WHEN UPPER(TRIM(SERVICEORDERSTATUS)) IN ('I', 'C') THEN 'CLOSED' ELSE 'UNMAPPED' END AS status_class,
  CAST(SERVICEORDERPRIORITY AS VARCHAR) AS priority,
  CAST(DIVISIONCODE AS VARCHAR) AS category,
  CAST(SERVICEORDERTYPE AS VARCHAR) AS service_type,
  CAST(STORECODE AS VARCHAR) AS team,
  CAST(NULL AS VARCHAR) AS region
FROM MANDL_CDM_PROD.MANCDM.SERVICEORDER
WHERE SERVICEORDERID IS NOT NULL
  AND COALESCE(UPPER(TRIM(ROWOPERATION)), '') NOT IN ('DELETE')
  AND OPENDATE >= '2000-01-01';

USE ROLE ACCOUNTADMIN;
GRANT USAGE ON DATABASE SERVICE_INTELLIGENCE TO ROLE SI_READER;
GRANT USAGE ON SCHEMA SERVICE_INTELLIGENCE.SERVICE_INTEL TO ROLE SI_READER;
GRANT SELECT ON VIEW SERVICE_INTELLIGENCE.SERVICE_INTEL.V_SERVICE_REQUESTS TO ROLE SI_READER;

-- 5. Check (as SI_READER): expect 65,639 rows on 24 Oct 2026, unique request_id, no UNMAPPED status.
-- USE ROLE SI_READER;
-- SELECT status_class, COUNT(*), COUNT(DISTINCT request_id) FROM SERVICE_INTELLIGENCE.SERVICE_INTEL.V_SERVICE_REQUESTS GROUP BY 1;

-- 6. Plan tables: run 02_app_schema.sql with <SI_DB> = SERVICE_INTELLIGENCE, then section 4 of 00_roles_and_grants.sql
--    with <SI_PLAN_WRITER> = SI_PLAN_WRITER.

-- 7. Service user (key pair, no password). Generate the key pair locally; paste only the PUBLIC key here.
--    The private key goes to the app host's secret store (SNOWFLAKE_PRIVATE_KEY_PATH), never to the repository.
-- CREATE USER IF NOT EXISTS SI_SVC TYPE = SERVICE DEFAULT_ROLE = SI_READER DEFAULT_WAREHOUSE = SI_WH
--   RSA_PUBLIC_KEY = '<PUBLIC_KEY_BODY>' COMMENT = 'Service Intelligence application';
-- GRANT ROLE SI_READER TO USER SI_SVC;
-- GRANT ROLE SI_PLAN_WRITER TO USER SI_SVC;
