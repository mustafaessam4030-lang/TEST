/* =====================================================================
   05_seed_control_table.sql
   ---------------------------------------------------------------------
   This is the "define all table structures" step: you declare here which
   Oracle tables are in scope. Everything downstream - the Snowflake DDL
   and the data copy - is driven off these rows. Nothing else needs to be
   edited to onboard a table.
   ===================================================================== */

USE DATABASE ORACLE_LANDING;
USE SCHEMA   CTL;

-- ---------------------------------------------------------------------
-- Option A: register tables one by one
-- ---------------------------------------------------------------------
CALL CTL.SP_REGISTER_TABLE('SALES', 'CUSTOMERS',  'RAW', 'FULL',        NULL,           'NIGHTLY');
CALL CTL.SP_REGISTER_TABLE('SALES', 'ORDERS',     'RAW', 'INCREMENTAL', 'LAST_UPDATED', 'NIGHTLY');
CALL CTL.SP_REGISTER_TABLE('SALES', 'ORDER_LINE', 'RAW', 'INCREMENTAL', 'LAST_UPDATED', 'NIGHTLY');
CALL CTL.SP_REGISTER_TABLE('HR',    'EMPLOYEES',  'RAW', 'FULL',        NULL,           'DAILY');

-- ---------------------------------------------------------------------
-- Option B: bulk insert with the full set of options
-- ---------------------------------------------------------------------
INSERT INTO CTL.PIPELINE_CONTROL
    (SOURCE_SCHEMA, SOURCE_TABLE, TARGET_DATABASE, TARGET_SCHEMA, TARGET_TABLE,
     LOAD_TYPE, WATERMARK_COLUMN, PARTITION_COLUMN, LOAD_GROUP, PRIORITY)
SELECT column1, column2, column3, column4, column5,
       column6, column7, column8, column9, column10
FROM VALUES
    -- A large fact table: incremental, and read in parallel ranges on an
    -- indexed numeric column so the Oracle read is not single-threaded.
    ('SALES', 'INVOICE_LINE', 'ORACLE_LANDING', 'RAW', 'INVOICE_LINE',
     'INCREMENTAL', 'LAST_UPDATED', 'INVOICE_LINE_ID', 'NIGHTLY', 10)
WHERE NOT EXISTS (
    SELECT 1 FROM CTL.PIPELINE_CONTROL
    WHERE SOURCE_SCHEMA = 'SALES' AND SOURCE_TABLE = 'INVOICE_LINE'
);

-- ---------------------------------------------------------------------
-- Option C: onboard every table in an Oracle schema in one go.
--   Run PL_DISCOVER_ORACLE_SCHEMA in ADF instead - it reads
--   ALL_TABLES for the schema and calls SP_REGISTER_TABLE per table.
-- ---------------------------------------------------------------------

-- ---------------------------------------------------------------------
-- Seed an initial watermark so the first incremental run does not read
-- the full history. Leave it out to backfill everything on run one.
-- ---------------------------------------------------------------------
MERGE INTO CTL.LOAD_WATERMARK t
USING (
    SELECT CONTROL_ID, '1900-01-01 00:00:00' AS HIGH_WATERMARK
    FROM CTL.PIPELINE_CONTROL
    WHERE LOAD_TYPE = 'INCREMENTAL'
) s
   ON t.CONTROL_ID = s.CONTROL_ID
WHEN NOT MATCHED THEN INSERT (CONTROL_ID, HIGH_WATERMARK)
    VALUES (s.CONTROL_ID, s.HIGH_WATERMARK);

SELECT * FROM CTL.V_LAST_LOAD_STATUS ORDER BY SOURCE_OBJECT;
