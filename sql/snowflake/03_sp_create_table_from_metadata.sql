/* =====================================================================
   03_sp_create_table_from_metadata.sql
   ---------------------------------------------------------------------
   Creates (or extends) a Snowflake table from the column metadata that
   ADF read out of the Oracle data dictionary.

   Input P_COLUMNS is the JSON array produced by the ADF Lookup activity
   against ORA_META.V_TABLE_METADATA, e.g.

     [ { "COLUMN_NAME": "CUSTOMER_ID",
         "ORDINAL_POSITION": 1,
         "ORACLE_DATA_TYPE": "NUMBER",
         "SF_DATA_TYPE": "NUMBER(10,0)",
         "IS_NULLABLE": "N" }, ... ]

   Behaviour
     * table missing            -> CREATE TABLE
     * table exists, same cols  -> no-op
     * table exists, new cols   -> ALTER TABLE ADD COLUMN (always nullable,
                                   because existing rows have no value)
     * existing column dropped or retyped at source -> reported in the
       return message, never applied automatically (that would lose data)
   ===================================================================== */

USE DATABASE ORACLE_LANDING;
USE SCHEMA   CTL;

CREATE OR REPLACE PROCEDURE CTL.SP_CREATE_TABLE_FROM_METADATA(
    P_TARGET_DATABASE STRING,
    P_TARGET_SCHEMA   STRING,
    P_TARGET_TABLE    STRING,
    P_COLUMNS         VARIANT,
    P_ADD_AUDIT_COLS  BOOLEAN
)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    v_fqn         STRING;
    v_json        STRING;
    v_cols        STRING;
    v_audit       STRING DEFAULT '';
    v_ddl         STRING;
    v_missing     STRING DEFAULT NULL;
    v_changed     STRING DEFAULT NULL;
    v_existed     BOOLEAN DEFAULT FALSE;
    v_count       NUMBER;
    v_sql         STRING;
    rs            RESULTSET;
BEGIN
    v_fqn  := '"' || UPPER(:P_TARGET_DATABASE) || '"."' || UPPER(:P_TARGET_SCHEMA) || '"."' || UPPER(:P_TARGET_TABLE) || '"';
    v_json := TO_JSON(:P_COLUMNS);

    -- -----------------------------------------------------------------
    -- Column list straight from the source metadata
    -- -----------------------------------------------------------------
    SELECT LISTAGG('"' || UPPER(value:COLUMN_NAME::STRING) || '" ' ||
                   value:SF_DATA_TYPE::STRING ||
                   IFF(UPPER(NVL(value:IS_NULLABLE::STRING, 'Y')) = 'N', ' NOT NULL', ''),
                   ', ')
             WITHIN GROUP (ORDER BY value:ORDINAL_POSITION::NUMBER)
      INTO :v_cols
      FROM TABLE(FLATTEN(input => PARSE_JSON(:v_json)));

    IF (v_cols IS NULL OR v_cols = '') THEN
        RETURN 'ERROR: no column metadata supplied for ' || v_fqn;
    END IF;

    IF (:P_ADD_AUDIT_COLS) THEN
        v_audit := ', "_ADF_LOAD_TS" TIMESTAMP_NTZ DEFAULT CURRENT_TIMESTAMP()' ||
                   ', "_ADF_RUN_ID" VARCHAR(100)' ||
                   ', "_ADF_SOURCE"  VARCHAR(400)';
    END IF;

    -- -----------------------------------------------------------------
    -- Does the table already exist?
    -- -----------------------------------------------------------------
    v_sql := 'SELECT COUNT(*) AS N FROM "' || UPPER(:P_TARGET_DATABASE) || '".INFORMATION_SCHEMA.TABLES ' ||
             'WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?';
    rs := (EXECUTE IMMEDIATE :v_sql USING (P_TARGET_SCHEMA, P_TARGET_TABLE));
    LET c_exists CURSOR FOR rs;
    FOR r IN c_exists DO
        v_count := r.N;
    END FOR;
    v_existed := (v_count > 0);

    -- -----------------------------------------------------------------
    -- Create it if it is not there
    -- -----------------------------------------------------------------
    IF (NOT v_existed) THEN
        v_ddl := 'CREATE TABLE ' || v_fqn || ' (' || v_cols || v_audit || ')';
        EXECUTE IMMEDIATE :v_ddl;
        RETURN 'CREATED ' || v_fqn;
    END IF;

    -- -----------------------------------------------------------------
    -- It exists: reconcile the structure (additive only)
    -- -----------------------------------------------------------------
    v_sql :=
        'SELECT LISTAGG(''"'' || m.COL_NAME || ''" '' || m.COL_TYPE, '', '')' ||
        '         WITHIN GROUP (ORDER BY m.ORD) AS MISSING_COLS ' ||
        'FROM ( SELECT UPPER(value:COLUMN_NAME::STRING)  AS COL_NAME, ' ||
        '              value:SF_DATA_TYPE::STRING        AS COL_TYPE, ' ||
        '              value:ORDINAL_POSITION::NUMBER    AS ORD ' ||
        '       FROM TABLE(FLATTEN(input => PARSE_JSON(?))) ) m ' ||
        'LEFT JOIN "' || UPPER(:P_TARGET_DATABASE) || '".INFORMATION_SCHEMA.COLUMNS c ' ||
        '       ON c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ? AND c.COLUMN_NAME = m.COL_NAME ' ||
        'WHERE c.COLUMN_NAME IS NULL';
    rs := (EXECUTE IMMEDIATE :v_sql USING (v_json, P_TARGET_SCHEMA, P_TARGET_TABLE));
    LET c_missing CURSOR FOR rs;
    FOR r IN c_missing DO
        v_missing := r.MISSING_COLS;
    END FOR;

    IF (v_missing IS NOT NULL AND v_missing <> '') THEN
        -- New source columns are added as NULLable: existing rows predate them.
        v_ddl := 'ALTER TABLE ' || v_fqn || ' ADD COLUMN ' || v_missing;
        EXECUTE IMMEDIATE :v_ddl;
    END IF;

    -- Columns whose declared type no longer matches the source. Reported
    -- only - widening/narrowing a populated column is a human decision.
    v_sql :=
        'SELECT LISTAGG(m.COL_NAME || '' ('' || c.DATA_TYPE || '' -> '' || m.COL_TYPE || '')'', '', '') AS CHANGED_COLS ' ||
        'FROM ( SELECT UPPER(value:COLUMN_NAME::STRING) AS COL_NAME, ' ||
        '              value:SF_DATA_TYPE::STRING       AS COL_TYPE ' ||
        '       FROM TABLE(FLATTEN(input => PARSE_JSON(?))) ) m ' ||
        'JOIN "' || UPPER(:P_TARGET_DATABASE) || '".INFORMATION_SCHEMA.COLUMNS c ' ||
        '  ON c.TABLE_SCHEMA = ? AND c.TABLE_NAME = ? AND c.COLUMN_NAME = m.COL_NAME ' ||
        'WHERE SPLIT_PART(UPPER(m.COL_TYPE), ''('', 1) <> UPPER(c.DATA_TYPE) ' ||
        '  AND NOT (SPLIT_PART(UPPER(m.COL_TYPE), ''('', 1) = ''VARCHAR'' AND UPPER(c.DATA_TYPE) = ''TEXT'') ' ||
        '  AND NOT (SPLIT_PART(UPPER(m.COL_TYPE), ''('', 1) = ''NUMBER''  AND UPPER(c.DATA_TYPE) = ''NUMBER'') ' ||
        '  AND NOT (SPLIT_PART(UPPER(m.COL_TYPE), ''('', 1) LIKE ''TIMESTAMP%'' AND UPPER(c.DATA_TYPE) LIKE ''TIMESTAMP%'')';
    rs := (EXECUTE IMMEDIATE :v_sql USING (v_json, P_TARGET_SCHEMA, P_TARGET_TABLE));
    LET c_changed CURSOR FOR rs;
    FOR r IN c_changed DO
        v_changed := r.CHANGED_COLS;
    END FOR;

    RETURN 'EXISTS ' || v_fqn ||
           IFF(v_missing IS NULL OR v_missing = '', ' | no new columns', ' | added: ' || v_missing) ||
           IFF(v_changed IS NULL OR v_changed = '', '', ' | TYPE DRIFT (not applied): ' || v_changed);
END;
$$;
