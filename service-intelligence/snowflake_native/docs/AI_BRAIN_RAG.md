# VELA AI — RAG architecture on Snowflake

Everything runs inside the Snowflake account: the data, the search indexes, the models and the app.
Nothing is copied out. The app runs as `DEV_ADMIN` (owner's rights) on warehouse `SI_WH`.

```
 SOURCES (Snowflake)                    INDEX / RETRIEVE                          GENERATE (Cortex)
 ─────────────────────────              ──────────────────────────────            ─────────────────────────────
 Training PDFs + diagrams  ──chunks──▶  TRAINING_KB_SEARCH (Cortex Search) ─┐
 (TRAINING_STG, 471 docs)               11k pages, hybrid vector+keyword    │
 Training videos           ──AI_TRANSCRIBE─▶ VIDEO_SEGMENTS ─▶ VIDEO_KB_SEARCH ┤    1. PLAN  (Claude): which tools
 (TRAINING_STG/videos)                  ~45 s timed segments                │       for this question
 Repair history            ──────────▶  REPAIR_SEARCH (optional, meaning)  ├──▶ 2. RETRIEVE: run them
 (REPAIR_KNOWLEDGE, 1.4M)               + keyword SQL (counts, parts, hrs)  │    3. ANSWER (Claude) from the
 Fleet, OLGA, faults, SOS,  ─────────▶  guarded read-only SQL + metrics     │       retrieved rows only, with
 inspections, letters, CVA              registry (approved KPIs)            │       citations; numbers checked
 Brain rules               ──────────▶  injected into every prompt          │
 Photos from the chat      ──vision──▶  findings (AI_COMPLETE + image)      │
 Public web                ──────────▶  Cortex web search (no company data) ┘
```

## Layers

| Layer | What | Snowflake objects |
|---|---|---|
| Ingest | Files on internal stages (SSE, directory tables) | `TRAINING_STG`, `PHOTO_STG` |
| Process | Page chunks (done), video transcripts with timestamps, re-labelled repair notes | `TRAINING_CHUNKS`, `VIDEO_LIBRARY`, `VIDEO_SEGMENTS`, `V_REPAIR_STORY`, `SP_INDEX_NEW_VIDEOS` |
| Index | Cortex Search (arctic-embed-l-v2.0, hybrid semantic + keyword, incremental refresh) | `TRAINING_KB_SEARCH`, `VIDEO_KB_SEARCH`, `REPAIR_SEARCH` (optional) |
| Retrieve | Agent tools in `si_native/agent.py`: `manual` (pages + video clips), `repairs`, `machine`, `metric`, `sql`, `top_list`, `web`, `video` | `SEARCH_PREVIEW`, guarded SELECTs |
| Generate | Claude via `AI_COMPLETE` / `COMPLETE`: plan as JSON, then answer only from retrieved rows | model `claude-sonnet-5` (fallback `claude-sonnet-4-6`) |
| Ground | Every figure is checked against the rows; assumptions and general knowledge are labelled; sources listed | `unverified_numbers`, "How I got this" |
| Present | Text, tables, training page pictures, video clips at the right second, web video cards | Streamlit in Snowflake |
| Govern | Read-only guard (approved tables, no DDL/DML), telematics policy (no EG telematics), web queries scrubbed | `check_spec`, `adhoc_spec`, `scrub_web_query` |

## Setup scripts
- `sql/20_ai_brain_videos.sql` — video tables, `SP_INDEX_NEW_VIDEOS`, `VIDEO_KB_SEARCH` (created on the first indexed video).
- `sql/21_ai_brain_repair_search.sql` — optional semantic search over repair history (`V_REPAIR_STORY`, `REPAIR_SEARCH`).

The app works before and after these are run: missing search services are skipped.
