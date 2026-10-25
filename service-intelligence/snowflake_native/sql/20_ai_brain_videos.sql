-- =====================================================================================
-- AI Brain: training VIDEOS for VELA AI (video library, timed transcript segments, search)
-- Run in a Snowsight SQL worksheet as DEV_ADMIN (owner of SERVICE_INTELLIGENCE.AI_BRAIN and of the app).
-- Re-runnable. Creates new objects only; it does not change any existing table, view or search service.
--
-- How it works
--   1. Upload videos (MP4 best; MOV/WEBM/MKV/OGV also work; speech needed; < 60 min, browser upload < 250 MB)
--      to stage AI_BRAIN.TRAINING_STG under the folder  videos/<topic>/  e.g. videos/777G/brake_check.mp4
--   2. CALL SERVICE_INTELLIGENCE.AI_BRAIN.SP_INDEX_NEW_VIDEOS();
--        - registers new files in VIDEO_LIBRARY (title from the file name, topic from the folder)
--        - AI_TRANSCRIBE turns the speech into text with a timestamp per word
--        - words are grouped into ~45-second segments in VIDEO_SEGMENTS (start/end seconds)
--        - creates the Cortex Search service VIDEO_KB_SEARCH on the first run that has segments
--   3. VELA AI searches the segments and plays the matching part of the video inside the chat.
-- Cost: AI_TRANSCRIBE is billed per second of audio, once per video; re-indexing only happens for status NEW.
-- =====================================================================================
USE ROLE DEV_ADMIN;
USE WAREHOUSE CORTEX_WH;
USE SCHEMA SERVICE_INTELLIGENCE.AI_BRAIN;

-- 1. One row per video ------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS VIDEO_LIBRARY (
    VIDEO_ID        STRING        NOT NULL,          -- md5 of the stage path
    STAGE_PATH      STRING        NOT NULL,          -- path inside TRAINING_STG, e.g. videos/777G/brake_check.mp4
    TITLE           STRING,                          -- from the file name (editable)
    TOPIC           STRING,                          -- first folder under videos/ (editable)
    SYSTEM          STRING,                          -- optional: same values as the training library SYSTEM
    MODEL           STRING,                          -- optional: machine model the video is about
    SPOKEN_LANGUAGE STRING,                          -- optional
    DESCRIPTION     STRING,                          -- optional
    SIZE_BYTES      NUMBER,
    UPLOADED_AT     TIMESTAMP_NTZ,
    DURATION_S      FLOAT,
    SEGMENTS        NUMBER,
    STATUS          STRING        DEFAULT 'NEW',     -- NEW | INDEXED | FAILED | SKIP
    ERROR           STRING,
    INDEXED_AT      TIMESTAMP_NTZ,
    CONSTRAINT PK_VIDEO_LIBRARY PRIMARY KEY (VIDEO_ID)
) COMMENT = 'AI Brain - training videos on TRAINING_STG/videos (one row per file)';

-- 2. Timed transcript segments (what the search indexes) --------------------------------------------
CREATE TABLE IF NOT EXISTS VIDEO_SEGMENTS (
    SEGMENT_ID      STRING        NOT NULL,          -- video_id-segment_no
    VIDEO_ID        STRING        NOT NULL,
    SEGMENT_NO      NUMBER        NOT NULL,
    START_S         FLOAT,                           -- seconds from the start of the video
    END_S           FLOAT,
    TEXT            STRING,                          -- what is said in this part
    TITLE           STRING,
    TOPIC           STRING,
    SYSTEM          STRING,
    MODEL           STRING,
    STAGE_PATH      STRING,
    SEARCH_TEXT     STRING,                          -- title + topic + text (searched)
    CONSTRAINT PK_VIDEO_SEGMENTS PRIMARY KEY (SEGMENT_ID)
) COMMENT = 'AI Brain - ~45 s transcript segments of the training videos (AI_TRANSCRIBE word timestamps)';

-- 3. Index new videos ---------------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE SP_INDEX_NEW_VIDEOS(MAX_VIDEOS NUMBER DEFAULT 10)
RETURNS STRING
LANGUAGE SQL
EXECUTE AS OWNER
COMMENT = 'Registers new files under TRAINING_STG/videos, transcribes them (AI_TRANSCRIBE) and fills VIDEO_SEGMENTS'
AS
$$
DECLARE
    indexed INTEGER DEFAULT 0;
    failed  INTEGER DEFAULT 0;
    vid     STRING;
    vpath   STRING;
    new_videos RESULTSET;
BEGIN
    ALTER STAGE SERVICE_INTELLIGENCE.AI_BRAIN.TRAINING_STG REFRESH;

    INSERT INTO SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_LIBRARY (VIDEO_ID, STAGE_PATH, TITLE, TOPIC, SIZE_BYTES, UPLOADED_AT, STATUS)
    SELECT MD5(d.RELATIVE_PATH), d.RELATIVE_PATH,
           INITCAP(TRIM(REGEXP_REPLACE(REGEXP_REPLACE(SPLIT_PART(d.RELATIVE_PATH, '/', -1), '\\.[A-Za-z0-9]+$', ''), '[_-]+', ' '))),
           IFF(ARRAY_SIZE(SPLIT(d.RELATIVE_PATH, '/')) > 2, REPLACE(SPLIT_PART(d.RELATIVE_PATH, '/', 2), '_', ' '), NULL),
           d.SIZE, d.LAST_MODIFIED::TIMESTAMP_NTZ, 'NEW'
    FROM DIRECTORY(@SERVICE_INTELLIGENCE.AI_BRAIN.TRAINING_STG) d
    WHERE d.RELATIVE_PATH ILIKE 'videos/%'
      AND LOWER(SPLIT_PART(d.RELATIVE_PATH, '.', -1)) IN ('mp4', 'mov', 'webm', 'mkv', 'ogv')
      AND NOT EXISTS (SELECT 1 FROM SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_LIBRARY v WHERE v.VIDEO_ID = MD5(d.RELATIVE_PATH));

    new_videos := (SELECT VIDEO_ID, STAGE_PATH FROM SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_LIBRARY
                   WHERE STATUS = 'NEW' QUALIFY ROW_NUMBER() OVER (ORDER BY UPLOADED_AT) <= :MAX_VIDEOS);
    LET c CURSOR FOR new_videos;
    FOR r IN c DO
        vid := r.VIDEO_ID;
        vpath := r.STAGE_PATH;
        BEGIN
            CREATE OR REPLACE TEMPORARY TABLE _VIDEO_TRANSCRIPT AS
            SELECT PARSE_JSON(TO_VARCHAR(AI_TRANSCRIBE(TO_FILE('@SERVICE_INTELLIGENCE.AI_BRAIN.TRAINING_STG', :vpath),
                                                       {'timestamp_granularity': 'word'}))) AS T;

            DELETE FROM SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_SEGMENTS WHERE VIDEO_ID = :vid;
            INSERT INTO SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_SEGMENTS
                (SEGMENT_ID, VIDEO_ID, SEGMENT_NO, START_S, END_S, TEXT, TITLE, TOPIC, SYSTEM, MODEL, STAGE_PATH, SEARCH_TEXT)
            SELECT :vid || '-' || s.SEG, :vid, s.SEG, s.START_S, s.END_S, s.TXT, v.TITLE, v.TOPIC, v.SYSTEM, v.MODEL, v.STAGE_PATH,
                   CONCAT_WS(' | ', v.TITLE, v.TOPIC, v.MODEL, s.TXT)
            FROM (
                SELECT FLOOR(w.value:start::FLOAT / 45)::INTEGER AS SEG, MIN(w.value:start::FLOAT) AS START_S, MAX(w.value:end::FLOAT) AS END_S,
                       TRIM(REGEXP_REPLACE(LISTAGG(w.value:text::STRING, ' ') WITHIN GROUP (ORDER BY w.value:start::FLOAT), '\\s+', ' ')) AS TXT
                FROM _VIDEO_TRANSCRIPT t, LATERAL FLATTEN(t.T:segments) w
                GROUP BY 1
            ) s
            JOIN SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_LIBRARY v ON v.VIDEO_ID = :vid
            WHERE LENGTH(s.TXT) > 0;

            UPDATE SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_LIBRARY
               SET STATUS = 'INDEXED', ERROR = NULL, INDEXED_AT = CURRENT_TIMESTAMP()::TIMESTAMP_NTZ,
                   DURATION_S = (SELECT T:audio_duration::FLOAT FROM _VIDEO_TRANSCRIPT),
                   SEGMENTS = (SELECT COUNT(*) FROM SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_SEGMENTS WHERE VIDEO_ID = :vid)
             WHERE VIDEO_ID = :vid;
            indexed := indexed + 1;
        EXCEPTION
            WHEN OTHER THEN
                LET err STRING := SQLERRM;
                UPDATE SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_LIBRARY SET STATUS = 'FAILED', ERROR = LEFT(:err, 1000)
                 WHERE VIDEO_ID = :vid;
                failed := failed + 1;
        END;
    END FOR;

    -- the search service is created once there is something to index (then refreshes itself)
    LET segment_rows INTEGER := (SELECT COUNT(*) FROM SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_SEGMENTS);
    IF (segment_rows > 0) THEN
        CREATE CORTEX SEARCH SERVICE IF NOT EXISTS SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_KB_SEARCH
            ON SEARCH_TEXT
            ATTRIBUTES TOPIC, SYSTEM, MODEL
            WAREHOUSE = CORTEX_WH
            TARGET_LAG = '1 hour'
            EMBEDDING_MODEL = 'snowflake-arctic-embed-l-v2.0'
            COMMENT = 'AI Brain - training video transcript segments'
        AS SELECT SEGMENT_ID, VIDEO_ID, SEGMENT_NO, START_S, END_S, TEXT, TITLE, TOPIC, SYSTEM, MODEL, STAGE_PATH, SEARCH_TEXT
           FROM SERVICE_INTELLIGENCE.AI_BRAIN.VIDEO_SEGMENTS;
    END IF;

    RETURN indexed || ' video(s) indexed, ' || failed || ' failed. See VIDEO_LIBRARY for details.';
END;
$$;

-- 4. Optional: index new uploads automatically every morning (06:10 Cairo). Uncomment to enable.
-- CREATE OR REPLACE TASK VIDEO_INDEX_DAILY WAREHOUSE = CORTEX_WH SCHEDULE = 'USING CRON 10 6 * * * Africa/Cairo'
--   AS CALL SERVICE_INTELLIGENCE.AI_BRAIN.SP_INDEX_NEW_VIDEOS(10);
-- ALTER TASK VIDEO_INDEX_DAILY RESUME;

-- 5. After uploading videos:
-- CALL SERVICE_INTELLIGENCE.AI_BRAIN.SP_INDEX_NEW_VIDEOS();
-- SELECT TITLE, TOPIC, STATUS, ROUND(DURATION_S / 60, 1) AS MINUTES, SEGMENTS, ERROR FROM VIDEO_LIBRARY ORDER BY UPLOADED_AT DESC;
