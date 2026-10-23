-- Service Intelligence plan persistence. Run once as the schema owner (not as the app's roles).
-- Matches the statements in si/plans.py. PROPOSED names in <ANGLE_BRACKETS>.
CREATE SCHEMA IF NOT EXISTS <SI_DB>.SI_APP COMMENT = 'Service Intelligence application state (plans, audit)';

CREATE SEQUENCE IF NOT EXISTS <SI_DB>.SI_APP.RECOMMENDATION_SEQ START = 101 INCREMENT = 1;

CREATE TABLE IF NOT EXISTS <SI_DB>.SI_APP.RECOMMENDATIONS (
  rec_id            VARCHAR       NOT NULL PRIMARY KEY,   -- R-101 ...
  candidate_key     VARCHAR       NOT NULL,               -- detector:dimension=segment
  title             VARCHAR       NOT NULL,
  status            VARCHAR       NOT NULL,               -- proposed|reviewed|approved|in_progress|completed|rejected|blocked
  priority_score    FLOAT,
  priority_label    VARCHAR,
  owner             VARCHAR,
  created_at        TIMESTAMP_NTZ NOT NULL,               -- UTC
  created_by        VARCHAR       NOT NULL,
  updated_at        TIMESTAMP_NTZ NOT NULL,
  updated_by        VARCHAR       NOT NULL,
  snapshot_id       VARCHAR       NOT NULL,               -- analysis snapshot the evidence came from
  registry_version  VARCHAR       NOT NULL,
  data_source       VARCHAR       NOT NULL,               -- snowflake | synthetic
  payload           VARCHAR       NOT NULL,               -- JSON: evidence, query ids, SQL, assumptions
  version           INTEGER       NOT NULL                -- optimistic concurrency
);

CREATE TABLE IF NOT EXISTS <SI_DB>.SI_APP.RECOMMENDATION_EVENTS (
  event_id     VARCHAR       NOT NULL PRIMARY KEY,
  rec_id       VARCHAR       NOT NULL,
  event_at     TIMESTAMP_NTZ NOT NULL,                    -- UTC
  actor        VARCHAR       NOT NULL,
  action       VARCHAR       NOT NULL,                    -- created|status_changed|assigned|commented
  from_status  VARCHAR,
  to_status    VARCHAR,
  note         VARCHAR,
  detail       VARCHAR                                    -- JSON
);
-- The writer role has no DELETE or UPDATE on RECOMMENDATION_EVENTS, so the audit trail is append-only.
-- Consider Time Travel / a longer DATA_RETENTION_TIME_IN_DAYS on SI_APP for recovery.
