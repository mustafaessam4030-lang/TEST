/* =====================================================================
   01_setup_database.sql
   ---------------------------------------------------------------------
   One-time Snowflake setup for the Oracle -> Snowflake ADF framework.

   Run as ACCOUNTADMIN (or a role that can create databases/warehouses).
   Adjust the object names in the "Configuration" block to your standards.
   ===================================================================== */

-- ---------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------
SET target_database  = 'ORACLE_LANDING';   -- database that receives the tables
SET raw_schema       = 'RAW';              -- schema that receives the tables
SET control_schema   = 'CTL';              -- schema holding control/audit objects
SET warehouse_name   = 'WH_ADF_LOAD';
SET adf_role         = 'ADF_LOADER';
SET adf_user         = 'SVC_ADF';

USE ROLE ACCOUNTADMIN;

-- ---------------------------------------------------------------------
-- Warehouse
-- ---------------------------------------------------------------------
CREATE WAREHOUSE IF NOT EXISTS IDENTIFIER($warehouse_name)
    WAREHOUSE_SIZE      = 'SMALL'
    AUTO_SUSPEND        = 60
    AUTO_RESUME         = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT             = 'Warehouse used by the ADF Oracle ingestion framework';

-- ---------------------------------------------------------------------
-- Database and schemas
-- ---------------------------------------------------------------------
CREATE DATABASE IF NOT EXISTS IDENTIFIER($target_database);
USE DATABASE IDENTIFIER($target_database);

CREATE SCHEMA IF NOT EXISTS IDENTIFIER($raw_schema)
    COMMENT = 'Landing zone - one table per Oracle source table, structure mirrored 1:1';
CREATE SCHEMA IF NOT EXISTS IDENTIFIER($control_schema)
    COMMENT = 'Control, watermark and audit objects for the ADF framework';

-- ---------------------------------------------------------------------
-- Role for the ADF service account
-- ---------------------------------------------------------------------
CREATE ROLE IF NOT EXISTS IDENTIFIER($adf_role);

GRANT USAGE            ON WAREHOUSE IDENTIFIER($warehouse_name)  TO ROLE IDENTIFIER($adf_role);
GRANT OPERATE          ON WAREHOUSE IDENTIFIER($warehouse_name)  TO ROLE IDENTIFIER($adf_role);
GRANT USAGE            ON DATABASE  IDENTIFIER($target_database) TO ROLE IDENTIFIER($adf_role);

GRANT USAGE, CREATE TABLE, CREATE VIEW, CREATE STAGE, CREATE FILE FORMAT
    ON SCHEMA RAW TO ROLE IDENTIFIER($adf_role);
GRANT USAGE, CREATE TABLE, CREATE PROCEDURE, CREATE SEQUENCE
    ON SCHEMA CTL TO ROLE IDENTIFIER($adf_role);

-- The framework creates tables at runtime, so future grants matter more
-- than current ones.
GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA RAW TO ROLE IDENTIFIER($adf_role);
GRANT ALL PRIVILEGES ON ALL    TABLES IN SCHEMA RAW TO ROLE IDENTIFIER($adf_role);
GRANT ALL PRIVILEGES ON FUTURE TABLES IN SCHEMA CTL TO ROLE IDENTIFIER($adf_role);
GRANT ALL PRIVILEGES ON ALL    TABLES IN SCHEMA CTL TO ROLE IDENTIFIER($adf_role);
GRANT USAGE ON FUTURE PROCEDURES IN SCHEMA CTL TO ROLE IDENTIFIER($adf_role);
GRANT USAGE ON ALL    PROCEDURES IN SCHEMA CTL TO ROLE IDENTIFIER($adf_role);

-- ---------------------------------------------------------------------
-- Service user for ADF
--   Key-pair authentication is strongly preferred over a password.
--   Generate the key pair with:
--     openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out adf_key.p8 -nocrypt
--     openssl rsa -in adf_key.p8 -pubout -out adf_key.pub
--   Then paste the public key body (no header/footer lines) below.
-- ---------------------------------------------------------------------
CREATE USER IF NOT EXISTS IDENTIFIER($adf_user)
    DEFAULT_ROLE      = $adf_role
    DEFAULT_WAREHOUSE = $warehouse_name
    TYPE              = SERVICE
    COMMENT           = 'Service account used by Azure Data Factory';

-- ALTER USER IDENTIFIER($adf_user) SET RSA_PUBLIC_KEY = 'MIIBIjANBgkq...';

GRANT ROLE IDENTIFIER($adf_role) TO USER IDENTIFIER($adf_user);
