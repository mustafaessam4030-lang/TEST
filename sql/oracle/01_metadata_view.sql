/* =====================================================================
   01_metadata_view.sql
   ---------------------------------------------------------------------
   The Oracle -> Snowflake type mapping lives here, in one place.

   ADF reads this view once per table and hands the result to
   CTL.SP_CREATE_TABLE_FROM_METADATA on the Snowflake side, which turns
   it into DDL. If you disagree with a mapping, change it here and the
   next run picks it up - no pipeline edit required.

   Create it in a small dedicated schema owned by the ADF read account.
   If that account may not create objects, use
   02_metadata_query_inline.sql instead and paste the SELECT into the
   pipeline's Lookup activity.
   ===================================================================== */

CREATE USER ora_meta IDENTIFIED BY "<change-me>";   -- skip if the schema exists
GRANT CREATE SESSION, CREATE VIEW TO ora_meta;
ALTER SESSION SET CURRENT_SCHEMA = ora_meta;

CREATE OR REPLACE VIEW ora_meta.v_table_metadata AS
SELECT
    c.owner                      AS schema_name,
    c.table_name                 AS table_name,
    c.column_name                AS column_name,
    c.column_id                  AS ordinal_position,
    c.data_type                  AS oracle_data_type,
    c.data_length                AS oracle_length,
    c.data_precision             AS oracle_precision,
    c.data_scale                 AS oracle_scale,
    c.nullable                   AS is_nullable,          -- 'Y' / 'N'
    CASE
        /* ---- character types -------------------------------------- */
        WHEN c.data_type IN ('VARCHAR2', 'NVARCHAR2', 'CHAR', 'NCHAR')
            THEN 'VARCHAR(' ||
                 LEAST(GREATEST(NVL(c.char_length, 0), c.data_length, 1), 16777216) || ')'

        WHEN c.data_type IN ('CLOB', 'NCLOB', 'LONG')
            THEN 'VARCHAR(16777216)'

        /* ---- numeric types ---------------------------------------- */
        -- NUMBER with no precision: unconstrained in Oracle. 38,10 keeps
        -- integers exact and gives a sane default for decimals.
        WHEN c.data_type = 'NUMBER' AND c.data_precision IS NULL
            THEN 'NUMBER(38,10)'
        -- Oracle allows negative scale (NUMBER(5,-2)); Snowflake does not.
        WHEN c.data_type = 'NUMBER' AND NVL(c.data_scale, 0) < 0
            THEN 'NUMBER(38,0)'
        WHEN c.data_type = 'NUMBER'
            THEN 'NUMBER(' || LEAST(c.data_precision, 38) || ',' ||
                 LEAST(NVL(c.data_scale, 0), LEAST(c.data_precision, 38)) || ')'

        WHEN c.data_type IN ('FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE')
            THEN 'FLOAT'

        /* ---- date and time ----------------------------------------- */
        -- Oracle DATE carries a time component, so DATE -> TIMESTAMP_NTZ,
        -- never Snowflake DATE (that would silently drop the time).
        WHEN c.data_type = 'DATE'
            THEN 'TIMESTAMP_NTZ(0)'
        WHEN c.data_type LIKE 'TIMESTAMP%LOCAL TIME ZONE'
            THEN 'TIMESTAMP_LTZ(' || LEAST(NVL(c.data_scale, 9), 9) || ')'
        WHEN c.data_type LIKE 'TIMESTAMP%TIME ZONE'
            THEN 'TIMESTAMP_TZ('  || LEAST(NVL(c.data_scale, 9), 9) || ')'
        WHEN c.data_type LIKE 'TIMESTAMP%'
            THEN 'TIMESTAMP_NTZ(' || LEAST(NVL(c.data_scale, 9), 9) || ')'
        WHEN c.data_type LIKE 'INTERVAL%'
            THEN 'VARCHAR(64)'

        /* ---- binary ------------------------------------------------ */
        WHEN c.data_type IN ('RAW', 'LONG RAW', 'BLOB')
            THEN 'BINARY'

        /* ---- everything else --------------------------------------- */
        WHEN c.data_type IN ('ROWID', 'UROWID')
            THEN 'VARCHAR(64)'
        WHEN c.data_type = 'XMLTYPE'
            THEN 'VARCHAR(16777216)'
        ELSE 'VARCHAR(16777216)'
    END                          AS sf_data_type
FROM all_tab_columns c
JOIN all_tables t
      ON  t.owner      = c.owner
      AND t.table_name = c.table_name
WHERE c.column_id IS NOT NULL          -- ALL_TAB_COLUMNS already hides system/hidden columns
  AND c.data_type NOT IN ('BFILE', 'ANYDATA', 'SDO_GEOMETRY');
  -- Unsupported-by-copy types are excluded rather than mapped: a column
  -- that ADF cannot read would fail the load for the whole table. Handle
  -- them with SOURCE_QUERY_OVERRIDE in CTL.PIPELINE_CONTROL if needed.

GRANT SELECT ON ora_meta.v_table_metadata TO adf_reader;
