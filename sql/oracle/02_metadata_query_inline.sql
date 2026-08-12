/* =====================================================================
   02_metadata_query_inline.sql
   ---------------------------------------------------------------------
   Same logic as ora_meta.v_table_metadata, as a standalone SELECT for
   sites where the ADF read account cannot create objects in Oracle.

   Paste it into the Lookup activity of PL_LOAD_TABLE, replacing the
   "SELECT ... FROM ora_meta.v_table_metadata" query, and keep the two
   bind placeholders as ADF expressions:

       WHERE c.owner      = '@{pipeline().parameters.pSourceSchema}'
         AND c.table_name = '@{pipeline().parameters.pSourceTable}'
   ===================================================================== */

SELECT
    c.column_name                AS COLUMN_NAME,
    c.column_id                  AS ORDINAL_POSITION,
    c.data_type                  AS ORACLE_DATA_TYPE,
    c.nullable                   AS IS_NULLABLE,
    CASE
        WHEN c.data_type IN ('VARCHAR2', 'NVARCHAR2', 'CHAR', 'NCHAR')
            THEN 'VARCHAR(' || LEAST(GREATEST(NVL(c.char_length, 0), c.data_length, 1), 16777216) || ')'
        WHEN c.data_type IN ('CLOB', 'NCLOB', 'LONG')          THEN 'VARCHAR(16777216)'
        WHEN c.data_type = 'NUMBER' AND c.data_precision IS NULL THEN 'NUMBER(38,10)'
        WHEN c.data_type = 'NUMBER' AND NVL(c.data_scale, 0) < 0 THEN 'NUMBER(38,0)'
        WHEN c.data_type = 'NUMBER'
            THEN 'NUMBER(' || LEAST(c.data_precision, 38) || ',' ||
                 LEAST(NVL(c.data_scale, 0), LEAST(c.data_precision, 38)) || ')'
        WHEN c.data_type IN ('FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE') THEN 'FLOAT'
        WHEN c.data_type = 'DATE'                              THEN 'TIMESTAMP_NTZ(0)'
        WHEN c.data_type LIKE 'TIMESTAMP%LOCAL TIME ZONE'      THEN 'TIMESTAMP_LTZ(' || LEAST(NVL(c.data_scale, 9), 9) || ')'
        WHEN c.data_type LIKE 'TIMESTAMP%TIME ZONE'            THEN 'TIMESTAMP_TZ('  || LEAST(NVL(c.data_scale, 9), 9) || ')'
        WHEN c.data_type LIKE 'TIMESTAMP%'                     THEN 'TIMESTAMP_NTZ(' || LEAST(NVL(c.data_scale, 9), 9) || ')'
        WHEN c.data_type LIKE 'INTERVAL%'                      THEN 'VARCHAR(64)'
        WHEN c.data_type IN ('RAW', 'LONG RAW', 'BLOB')        THEN 'BINARY'
        WHEN c.data_type IN ('ROWID', 'UROWID')                THEN 'VARCHAR(64)'
        ELSE 'VARCHAR(16777216)'
    END                          AS SF_DATA_TYPE
FROM all_tab_columns c
WHERE c.owner      = :p_schema
  AND c.table_name = :p_table
  AND c.column_id IS NOT NULL
  AND c.data_type NOT IN ('BFILE', 'ANYDATA', 'SDO_GEOMETRY')
ORDER BY c.column_id
