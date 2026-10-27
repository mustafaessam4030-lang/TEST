-- ATLAS on Snowflake Cortex: a service user that can call Cortex and nothing else.
-- Run as ACCOUNTADMIN in account PECFYLF-FY56654, once (granting a SNOWFLAKE
-- database role and creating the network rule need it).
-- Replace <SERVER_PUBLIC_IP> with the tower server's outbound address
-- (on MAN-ATAAZ-01: Invoke-RestMethod https://api.ipify.org).

USE ROLE ACCOUNTADMIN;

-- 1. A role that holds Cortex and no data.
CREATE ROLE IF NOT EXISTS ATLAS_CORTEX_ROLE
  COMMENT = 'ATLAS (ATA Control Tower): Snowflake Cortex chat only, no data access';
GRANT DATABASE ROLE SNOWFLAKE.CORTEX_REST_API_USER TO ROLE ATLAS_CORTEX_ROLE;

-- 2. The service user (no password; it signs in with a programmatic access token).
CREATE USER IF NOT EXISTS ATLAS_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = ATLAS_CORTEX_ROLE
  COMMENT = 'ATLAS on MAN-ATAAZ-01 (ATA Control Tower): Cortex wording model';
GRANT ROLE ATLAS_CORTEX_ROLE TO USER ATLAS_SVC;

-- 3. Only the tower server may use it (a programmatic access token for a
--    service user needs a network policy). Network rules live in a schema.
--    The server's IP changed later? ALTER NETWORK RULE GOVERNANCE.NETWORK.ATLAS_SERVER_IP
--    SET VALUE_LIST = ('<new IP>');   (IF NOT EXISTS below never updates it.)
CREATE DATABASE IF NOT EXISTS GOVERNANCE;
CREATE SCHEMA IF NOT EXISTS GOVERNANCE.NETWORK;
CREATE NETWORK RULE IF NOT EXISTS GOVERNANCE.NETWORK.ATLAS_SERVER_IP
  MODE = INGRESS TYPE = IPV4 VALUE_LIST = ('<SERVER_PUBLIC_IP>');
CREATE NETWORK POLICY IF NOT EXISTS ATLAS_SVC_POLICY
  ALLOWED_NETWORK_RULE_LIST = ('GOVERNANCE.NETWORK.ATLAS_SERVER_IP');
ALTER USER ATLAS_SVC SET NETWORK_POLICY = ATLAS_SVC_POLICY;

-- 4. The token. Its secret is shown ONCE in the output (token_secret): give it
--    to the tower's operator to type into STORE_SNOWFLAKE_TOKEN.bat on the
--    server - never by email or chat. To renew (this ADD fails once it exists):
--    ALTER USER ATLAS_SVC ROTATE PROGRAMMATIC ACCESS TOKEN ATLAS_CORTEX;
--    then STORE_SNOWFLAKE_TOKEN.bat again with the new secret.
ALTER USER ATLAS_SVC ADD PROGRAMMATIC ACCESS TOKEN ATLAS_CORTEX
  ROLE_RESTRICTION = 'ATLAS_CORTEX_ROLE'
  DAYS_TO_EXPIRY = 180
  COMMENT = 'ATA Control Tower - ATLAS wording model';

-- Cost visibility (Cortex is billed in credits):
--   SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_USAGE_HISTORY ORDER BY START_TIME DESC;
-- Switch it off at any time:
--   ALTER USER ATLAS_SVC SET DISABLED = TRUE;
