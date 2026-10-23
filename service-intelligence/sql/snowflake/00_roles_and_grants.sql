-- Service Intelligence: least-privilege roles, warehouse and service user.
-- PROPOSED names in <ANGLE_BRACKETS> — replace with names your Snowflake admins approve.
-- Run as a role with CREATE ROLE / CREATE WAREHOUSE / CREATE USER (typically SECURITYADMIN + SYSADMIN).
-- Nothing here grants access to raw company tables: the application reads only the contract view.

-- 1. A small, auto-suspending warehouse with hard statement limits.
CREATE WAREHOUSE IF NOT EXISTS <SI_WH>
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE
  STATEMENT_TIMEOUT_IN_SECONDS = 120
  STATEMENT_QUEUED_TIMEOUT_IN_SECONDS = 60
  COMMENT = 'Service Intelligence app queries';

-- Optional (ACCOUNTADMIN): cap monthly credits for the app warehouse.
-- CREATE RESOURCE MONITOR <SI_MONITOR> WITH CREDIT_QUOTA = <N> FREQUENCY = MONTHLY START_TIMESTAMP = IMMEDIATELY
--   TRIGGERS ON 80 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND;
-- ALTER WAREHOUSE <SI_WH> SET RESOURCE_MONITOR = <SI_MONITOR>;

-- 2. Roles.
CREATE ROLE IF NOT EXISTS <SI_READER>       COMMENT = 'Service Intelligence: SELECT on the contract view only';
CREATE ROLE IF NOT EXISTS <SI_PLAN_WRITER>  COMMENT = 'Service Intelligence: write recommendation plan tables only';
CREATE ROLE IF NOT EXISTS <SI_VIEW_OWNER>   COMMENT = 'Owns the contract view; reads the mapped source table';

GRANT USAGE ON WAREHOUSE <SI_WH> TO ROLE <SI_READER>;
GRANT USAGE ON WAREHOUSE <SI_WH> TO ROLE <SI_PLAN_WRITER>;

-- 3. Contract view: owned by SI_VIEW_OWNER, which alone reads the source table.
GRANT USAGE ON DATABASE <SI_DB> TO ROLE <SI_VIEW_OWNER>;
GRANT USAGE, CREATE VIEW ON SCHEMA <SI_DB>.<SERVICE_INTEL> TO ROLE <SI_VIEW_OWNER>;
GRANT USAGE ON DATABASE <SOURCE_DB> TO ROLE <SI_VIEW_OWNER>;
GRANT USAGE ON SCHEMA <SOURCE_DB>.<SOURCE_SCHEMA> TO ROLE <SI_VIEW_OWNER>;
GRANT SELECT ON TABLE <SOURCE_DB>.<SOURCE_SCHEMA>.<SOURCE_TABLE> TO ROLE <SI_VIEW_OWNER>;
-- Then, as SI_VIEW_OWNER, run the DDL printed by:  python -m si.mapping ddl
-- (a SECURE VIEW; existing row-access and masking policies on the source still apply).

GRANT USAGE ON DATABASE <SI_DB> TO ROLE <SI_READER>;
GRANT USAGE ON SCHEMA <SI_DB>.<SERVICE_INTEL> TO ROLE <SI_READER>;
GRANT SELECT ON VIEW <SI_DB>.<SERVICE_INTEL>.V_SERVICE_REQUESTS TO ROLE <SI_READER>;

-- 4. Plan persistence (see 02_app_schema.sql for the tables).
GRANT USAGE ON DATABASE <SI_DB> TO ROLE <SI_PLAN_WRITER>;
GRANT USAGE ON SCHEMA <SI_DB>.SI_APP TO ROLE <SI_PLAN_WRITER>;
GRANT SELECT, INSERT, UPDATE ON TABLE <SI_DB>.SI_APP.RECOMMENDATIONS TO ROLE <SI_PLAN_WRITER>;
GRANT SELECT, INSERT ON TABLE <SI_DB>.SI_APP.RECOMMENDATION_EVENTS TO ROLE <SI_PLAN_WRITER>;   -- append-only audit
GRANT USAGE ON SEQUENCE <SI_DB>.SI_APP.RECOMMENDATION_SEQ TO ROLE <SI_PLAN_WRITER>;

-- 5. Optional: Snowflake Cortex for the assistant's question planner (SI_CORTEX_ENABLED=1).
-- Confirm Cortex is enabled for the account and region, and agree the model with your admins first.
-- GRANT DATABASE ROLE SNOWFLAKE.CORTEX_USER TO ROLE <SI_READER>;

-- 6. Service user with key-pair authentication (no password).
CREATE USER IF NOT EXISTS <SI_SVC>
  TYPE = SERVICE
  DEFAULT_ROLE = <SI_READER>
  DEFAULT_WAREHOUSE = <SI_WH>
  RSA_PUBLIC_KEY = '<PUBLIC_KEY_BODY>'
  COMMENT = 'Service Intelligence application';
GRANT ROLE <SI_READER> TO USER <SI_SVC>;
GRANT ROLE <SI_PLAN_WRITER> TO USER <SI_SVC>;
-- Add a network policy restricting <SI_SVC> to the application host's egress IPs if your account uses them.
