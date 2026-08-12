/* =====================================================================
   04_sp_audit_and_watermark.sql
   ---------------------------------------------------------------------
   Helper procedures called by the ADF pipelines.

   SP_LOG_LOAD_AUDIT   - writes one CTL.LOAD_AUDIT row per table per run.
   SP_UPDATE_WATERMARK - recomputes the high-water mark from the data that
                         actually landed, so a partial load can never
                         advance the mark past rows that were not copied.
   SP_REGISTER_TABLE   - convenience upsert into CTL.PIPELINE_CONTROL.
   ===================================================================== */

USE DATABASE ORACLE_LANDING;
USE SCHEMA   CTL;

-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE CTL.SP_LOG_LOAD_AUDIT(
    P_CONTROL_ID    NUMBER,
    P_FACTORY       STRING,
    P_PIPELINE      STRING,
    P_RUN_ID        STRING,
    P_PARENT_RUN_ID STRING,
    P_SOURCE_OBJECT STRING,
    P_TARGET_OBJECT STRING,
    P_PHASE         STRING,
    P_LOAD_TYPE     STRING,
    P_STATUS        STRING,
    P_ROWS_READ     NUMBER,
    P_ROWS_WRITTEN  NUMBER,
    P_DURATION_SEC  FLOAT,
    P_ERROR_MESSAGE STRING
)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    INSERT INTO CTL.LOAD_AUDIT (
        CONTROL_ID, ADF_FACTORY, ADF_PIPELINE, ADF_RUN_ID, ADF_PARENT_RUN_ID,
        SOURCE_OBJECT, TARGET_OBJECT, PHASE, LOAD_TYPE, STATUS,
        ROWS_READ, ROWS_WRITTEN, DURATION_SEC, ERROR_MESSAGE
    )
    VALUES (
        :P_CONTROL_ID, :P_FACTORY, :P_PIPELINE, :P_RUN_ID, :P_PARENT_RUN_ID,
        :P_SOURCE_OBJECT, :P_TARGET_OBJECT, :P_PHASE, :P_LOAD_TYPE, :P_STATUS,
        :P_ROWS_READ, :P_ROWS_WRITTEN, :P_DURATION_SEC, :P_ERROR_MESSAGE
    );
    RETURN 'LOGGED ' || :P_STATUS || ' for ' || :P_TARGET_OBJECT;
END;
$$;

-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE CTL.SP_UPDATE_WATERMARK(
    P_CONTROL_ID       NUMBER,
    P_TARGET_DATABASE  STRING,
    P_TARGET_SCHEMA    STRING,
    P_TARGET_TABLE     STRING,
    P_WATERMARK_COLUMN STRING
)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
DECLARE
    v_fqn      STRING;
    v_sql      STRING;
    v_col_type STRING DEFAULT NULL;
    v_wm_type  STRING;
    v_new      STRING DEFAULT NULL;
    rs         RESULTSET;
BEGIN
    IF (P_WATERMARK_COLUMN IS NULL OR P_WATERMARK_COLUMN = '') THEN
        RETURN 'SKIPPED: no watermark column configured';
    END IF;

    v_fqn := '"' || UPPER(:P_TARGET_DATABASE) || '"."' || UPPER(:P_TARGET_SCHEMA) || '"."' || UPPER(:P_TARGET_TABLE) || '"';

    -- The stored mark is a string, so it has to be formatted the way the
    -- Oracle predicate will read it back. A date/timestamp column needs a
    -- fixed format; a numeric one must not be given one at all.
    v_sql := 'SELECT DATA_TYPE AS DT FROM "' || UPPER(:P_TARGET_DATABASE) || '".INFORMATION_SCHEMA.COLUMNS ' ||
             'WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?';
    rs := (EXECUTE IMMEDIATE :v_sql USING (P_TARGET_SCHEMA, P_TARGET_TABLE, P_WATERMARK_COLUMN));
    LET c_type CURSOR FOR rs;
    FOR r IN c_type DO
        v_col_type := r.DT;
    END FOR;

    IF (v_col_type IS NULL) THEN
        RETURN 'SKIPPED: column ' || UPPER(:P_WATERMARK_COLUMN) || ' not found on ' || v_fqn;
    END IF;

    v_wm_type := IFF(v_col_type LIKE 'TIMESTAMP%' OR v_col_type = 'DATE', 'TIMESTAMP', 'NUMBER');

    IF (v_wm_type = 'TIMESTAMP') THEN
        v_sql := 'SELECT TO_CHAR(MAX("' || UPPER(:P_WATERMARK_COLUMN) || '"), ''YYYY-MM-DD HH24:MI:SS'') AS WM FROM ' || v_fqn;
    ELSE
        v_sql := 'SELECT TO_VARCHAR(MAX("' || UPPER(:P_WATERMARK_COLUMN) || '")) AS WM FROM ' || v_fqn;
    END IF;

    rs := (EXECUTE IMMEDIATE :v_sql);
    LET c_val CURSOR FOR rs;
    FOR r IN c_val DO
        v_new := r.WM;
    END FOR;

    IF (v_new IS NULL) THEN
        RETURN 'SKIPPED: target is empty, watermark left unchanged';
    END IF;

    MERGE INTO CTL.LOAD_WATERMARK t
    USING (SELECT :P_CONTROL_ID AS CONTROL_ID,
                  :v_new        AS HIGH_WATERMARK,
                  :v_wm_type    AS WATERMARK_TYPE) s
       ON t.CONTROL_ID = s.CONTROL_ID
    WHEN MATCHED THEN UPDATE
        SET t.HIGH_WATERMARK = s.HIGH_WATERMARK,
            t.WATERMARK_TYPE = s.WATERMARK_TYPE,
            t.LAST_LOADED_AT = CURRENT_TIMESTAMP(),
            t.UPDATED_AT     = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT (CONTROL_ID, HIGH_WATERMARK, WATERMARK_TYPE, LAST_LOADED_AT)
        VALUES (s.CONTROL_ID, s.HIGH_WATERMARK, s.WATERMARK_TYPE, CURRENT_TIMESTAMP());

    RETURN 'WATERMARK ' || v_fqn || ' = ' || v_new || ' (' || v_wm_type || ')';
END;
$$;

-- ---------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE CTL.SP_REGISTER_TABLE(
    P_SOURCE_SCHEMA    STRING,
    P_SOURCE_TABLE     STRING,
    P_TARGET_SCHEMA    STRING,
    P_LOAD_TYPE        STRING,
    P_WATERMARK_COLUMN STRING,
    P_LOAD_GROUP       STRING
)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS CALLER
AS
$$
BEGIN
    MERGE INTO CTL.PIPELINE_CONTROL t
    USING (SELECT UPPER(:P_SOURCE_SCHEMA) AS SOURCE_SCHEMA,
                  UPPER(:P_SOURCE_TABLE)  AS SOURCE_TABLE,
                  UPPER(:P_TARGET_SCHEMA) AS TARGET_SCHEMA,
                  UPPER(:P_SOURCE_TABLE)  AS TARGET_TABLE) s
       ON  t.SOURCE_SCHEMA = s.SOURCE_SCHEMA
       AND t.SOURCE_TABLE  = s.SOURCE_TABLE
       AND t.TARGET_SCHEMA = s.TARGET_SCHEMA
       AND t.TARGET_TABLE  = s.TARGET_TABLE
    WHEN MATCHED THEN UPDATE
        SET t.LOAD_TYPE        = UPPER(:P_LOAD_TYPE),
            t.WATERMARK_COLUMN = UPPER(:P_WATERMARK_COLUMN),
            t.LOAD_GROUP       = UPPER(:P_LOAD_GROUP),
            t.IS_ACTIVE        = TRUE,
            t.UPDATED_AT       = CURRENT_TIMESTAMP()
    WHEN NOT MATCHED THEN INSERT
        (SOURCE_SCHEMA, SOURCE_TABLE, TARGET_SCHEMA, TARGET_TABLE, LOAD_TYPE, WATERMARK_COLUMN, LOAD_GROUP)
        VALUES (s.SOURCE_SCHEMA, s.SOURCE_TABLE, s.TARGET_SCHEMA, s.TARGET_TABLE,
                UPPER(:P_LOAD_TYPE), UPPER(:P_WATERMARK_COLUMN), UPPER(:P_LOAD_GROUP));

    RETURN 'REGISTERED ' || UPPER(:P_SOURCE_SCHEMA) || '.' || UPPER(:P_SOURCE_TABLE);
END;
$$;
