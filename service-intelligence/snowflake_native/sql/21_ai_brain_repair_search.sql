-- =====================================================================================
-- AI Brain (OPTIONAL): semantic search over Mantrac repair history for VELA AI
-- Run in a Snowsight SQL worksheet as DEV_ADMIN. Re-runnable. New objects only.
--
-- Today VELA finds similar repairs by keywords (ILIKE). This service lets it find them by MEANING:
-- "truck won't charge" also matches "alternator undercharging", typos and different wording.
-- Source: the ~765k job segments of REPAIR_KNOWLEDGE that have a real story, with the note columns
-- re-labelled (the source view V_JOB_STORY maps ManPRO note types C/I/R to the wrong names:
-- CAUSE holds the complaint, CORRECTION the root cause, COMPLAINT the work done).
--
-- Cost (estimate, check against your contract): embedding ~45M tokens once (snowflake-arctic-embed-l-v2.0),
-- plus Cortex Search serving, billed per GB of index per month, plus the warehouse for refreshes.
-- VELA uses the service automatically once it exists, and falls back to keywords if it does not.
-- =====================================================================================
USE ROLE DEV_ADMIN;
USE WAREHOUSE CORTEX_WH;
USE SCHEMA SERVICE_INTELLIGENCE.AI_BRAIN;

CREATE OR REPLACE VIEW V_REPAIR_STORY
COMMENT = 'Repair history with a real story, note columns re-labelled (complaint / root_cause / work_done)'
AS
SELECT TERRID || '-' || TRIM(JOBNO) || '-' || TRIM(SEGNO) AS STORY_ID,
       TERRID, SERIAL_NO, MAKE_CD, MODEL_CD, OPEN_DATE, JOB_TYPE, SEGMENT_DESC AS JOB,
       CAUSE AS COMPLAINT, CORRECTION AS ROOT_CAUSE, COMPLAINT AS WORK_DONE, PARTS_USED, LABOUR_HOURS,
       CONCAT_WS(' | ', MODEL_CD, SEGMENT_DESC, CAUSE, CORRECTION, COMPLAINT) AS STORY_TEXT
FROM REPAIR_KNOWLEDGE
WHERE LENGTH(COMPLAINT) >= 15 AND LENGTH(CAUSE) >= 5 AND UPPER(TRIM(COMPLAINT)) NOT IN ('N/A', 'NA', 'X')
  AND COMPLAINT <> CAUSE;

CREATE CORTEX SEARCH SERVICE IF NOT EXISTS REPAIR_SEARCH
    ON STORY_TEXT
    ATTRIBUTES TERRID, MAKE_CD, MODEL_CD, JOB_TYPE
    WAREHOUSE = CORTEX_WH
    TARGET_LAG = '7 days'
    EMBEDDING_MODEL = 'snowflake-arctic-embed-l-v2.0'
    COMMENT = 'AI Brain - Mantrac repair history (semantic search)'
AS SELECT STORY_ID, TERRID, SERIAL_NO, MAKE_CD, MODEL_CD, OPEN_DATE, JOB_TYPE, JOB, COMPLAINT, ROOT_CAUSE, WORK_DONE,
          PARTS_USED, LABOUR_HOURS, STORY_TEXT
   FROM V_REPAIR_STORY;

-- Check (after a few minutes):
-- SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW('SERVICE_INTELLIGENCE.AI_BRAIN.REPAIR_SEARCH',
--   '{"query": "truck will not charge battery", "columns": ["MODEL_CD", "COMPLAINT", "ROOT_CAUSE", "WORK_DONE"], "limit": 5}');
