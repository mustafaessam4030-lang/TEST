"""AI Brain (SERVICE_INTELLIGENCE.AI_BRAIN): the knowledge VELA AI reasons with besides the curated fleet tables.

- CAT technician training library: Cortex Search service TRAINING_KB_SEARCH (11k page-level passages).
- Mantrac repair history: REPAIR_KNOWLEDGE (1.4M ManPRO job segments with technician notes and parts).
  The note columns are shifted in the source view V_JOB_STORY (ManPRO note type C = Correction, I = Incident,
  R = Root cause, mapped there as complaint/cause/correction), so every query here re-labels them:
      CAUSE -> complaint (the incident), CORRECTION -> root_cause, COMPLAINT -> work_done.
- Machine profile: fleet record, recent repairs, oil samples, inspections, service letters, CAT alerts and (only
  where the territory policy allows telematics) fault codes.
- Photos: images uploaded to PHOTO_STG and read by a Cortex vision model.
Every table query is a parameterised SELECT that passes check_spec; Cortex Search and vision calls are function
calls built from escaped literals. Nothing here changes company data (photos and their log are app uploads).
"""
from __future__ import annotations

import io
import json
import re
import uuid
from datetime import datetime, timezone

from .metrics import Spec

DB = "SERVICE_INTELLIGENCE.AI_BRAIN"
T_REPAIR = f"{DB}.REPAIR_KNOWLEDGE"
T_FLEET = f"{DB}.MACHINE_FLEET"
T_SOS = f"{DB}.V_SOS"
T_INSP = f"{DB}.V_INSPECTIONS"
T_LETTERS = f"{DB}.V_SERVICE_LETTERS"
T_ALERTS = f"{DB}.V_CAT_ALERTS"
T_FAULTS = f"{DB}.V_FAULT_CODES"
T_RULES = f"{DB}.BRAIN_RULES"
KB_SERVICE = f"{DB}.TRAINING_KB_SEARCH"
PHOTO_STAGE = f"@{DB}.PHOTO_STG"
T_PHOTO_LOG = f"{DB}.PHOTO_LOG"
KB_SYSTEMS = ("Basic Service", "Electrical & Electronics", "Diesel Engine", "Hydraulics", "Powertrain",
              "Battery Electric Machines", "Wheel Loaders", "Brakes", "Air Conditioning")
MAX_PHOTOS = 4
PHOTO_SIDE = 1568                      # longest side sent to the vision model

# re-labelled job-story columns (see module docstring), only segments with a real story
STORY = ("LEFT(cause, 300) AS complaint, LEFT(correction, 200) AS root_cause, LEFT(complaint, 400) AS work_done, "
         "LEFT(parts_used, 200) AS parts_used")
GOOD_STORY = ("LENGTH(complaint) >= 15 AND LENGTH(cause) >= 5 AND UPPER(TRIM(complaint)) NOT IN ('N/A', 'NA', 'X') "
              "AND complaint <> cause")


def sql_str(text: str) -> str:
    """A Snowflake single-quoted literal (backslashes and quotes escaped)."""
    return "'" + str(text).replace("\\", "\\\\").replace("'", "''") + "'"


# --- repair history ----------------------------------------------------------------------------------
def _terms(keywords) -> list[list[str]]:
    """["alternator|charging", "voltage"] -> [["alternator", "charging"], ["voltage"]]: groups are ANDed, '|' ORs."""
    groups = []
    for k in (keywords if isinstance(keywords, list) else [keywords])[:4]:
        alts = [a.strip()[:40] for a in str(k or "").split("|") if len(a.strip()) >= 2][:3]
        if alts:
            groups.append(alts)
    return groups


def _repair_where(keywords, model: str | None, serial: str | None) -> tuple[str, list]:
    where, params = [GOOD_STORY], []
    for alts in _terms(keywords):
        where.append("(" + " OR ".join("search_text ILIKE ?" for _ in alts) + ")")
        params += [f"%{a}%" for a in alts]
    if model:
        where.append("model_cd ILIKE ?")
        params.append(f"{str(model).strip()[:20]}%")
    if serial:
        where.append("serial_no = ?")
        params.append(str(serial).strip().upper()[:30])
    return " AND ".join(where), params


def repairs_spec(keywords, model: str | None = None, serial: str | None = None, n: int = 12) -> Spec:
    """Similar Mantrac jobs, newest first, with the total match count and the median labour hours of the matches."""
    where, params = _repair_where(keywords, model, serial)
    return Spec(f"SELECT terrid, serial_no, model_cd AS model, open_date, job_type, segment_desc AS job, {STORY}, "
                f"labour_hours, COUNT(*) OVER () AS similar_jobs, COUNT(NULLIF(labour_hours, 0)) OVER () AS jobs_with_hours, "
                f"MEDIAN(NULLIF(labour_hours, 0)) OVER () AS median_labour_hours FROM {T_REPAIR} WHERE {where} "
                f"ORDER BY open_date DESC LIMIT {max(1, min(int(n), 25))}", tuple(params), "brain_repairs", None)


def repair_parts_spec(keywords, model: str | None = None, serial: str | None = None) -> Spec:
    """Parts most often used on the matching jobs."""
    where, params = _repair_where(keywords, model, serial)
    return Spec(f"SELECT TRIM(REGEXP_REPLACE(p.value, ' x[0-9]+$', '')) AS part, COUNT(*) AS jobs FROM {T_REPAIR} r, "
                f"LATERAL SPLIT_TO_TABLE(r.parts_used, ';') p WHERE {where.replace('search_text', 'r.search_text')} "
                f"AND r.parts_used IS NOT NULL AND TRIM(p.value) <> '' GROUP BY 1 ORDER BY jobs DESC LIMIT 8",
                tuple(params), "brain_repair_parts", None)


def summarize_repairs(rows: list[dict], parts: list[dict], model: str | None) -> dict:
    """Labour hours are quoted only as the median of >= 3 similar jobs on one model (BRAIN_RULES)."""
    first = rows[0] if rows else {}
    with_hours = int(first.get("jobs_with_hours") or 0)
    median = first.get("median_labour_hours") if model and with_hours >= 3 else None
    jobs = [{k: v for k, v in r.items() if k not in ("similar_jobs", "jobs_with_hours", "median_labour_hours")} for r in rows]
    return {"similar_jobs": int(first.get("similar_jobs") or 0), "median_labour_hours": median,
            "jobs_with_hours": with_hours,
            "hours_note": None if median is not None else
            "No labour-hours figure: needs a model and at least 3 matching jobs with hours.",
            "common_parts": parts, "rows": jobs}


# --- machine profile -----------------------------------------------------------------------------------
def machine_specs(serial: str) -> dict[str, Spec]:
    s = (str(serial).strip().upper()[:30],)
    return {
        "fleet": Spec(f"SELECT terrid, serial_no, make_cd, model_cd, product_type, build_year, delivery_date, customer_no, "
                      f"customer_name, engine_model, telemetry_allowed, is_connected, latest_smu, latest_read_at, latest_source, "
                      f"hrs_per_day_180d, jobs_total, last_job_date, last_inspection, last_insp_actions, last_sos, last_sos_eval, "
                      f"sos_action_12m, open_pip_psp, open_pip_hours, fault_codes_30d, max_fault_level_30d, attention_score, "
                      f"recently_active, last_job_site FROM {T_FLEET} WHERE serial_no = ? LIMIT 5", s, "brain_machine", None),
        "recent_jobs": Spec(f"SELECT open_date, job_type, segment_desc AS job, {STORY}, labour_hours FROM {T_REPAIR} "
                            f"WHERE serial_no = ? AND {GOOD_STORY} ORDER BY open_date DESC LIMIT 10", s, "brain_machine_jobs", None),
        "oil_samples": Spec(f"SELECT sample_date, smu, compartment, eval_code, eval_meaning, LEFT(lab_comment, 300) AS lab_comment "
                            f"FROM {T_SOS} WHERE serial_no = ? ORDER BY sample_date DESC LIMIT 8", s, "brain_machine_sos", None),
        "inspections": Spec(f"SELECT completedate, formname, smu, action_items, monitor_items, normal_items, inspectorname "
                            f"FROM {T_INSP} WHERE serial_no = ? ORDER BY completedate DESC LIMIT 3", s, "brain_machine_insp", None),
        "service_letters": Spec(f"SELECT program_number, description, status, service_letter_type, letter_date, est_labor_hours "
                                f"FROM {T_LETTERS} WHERE serial_no = ? AND snapshot_date = (SELECT MAX(snapshot_date) FROM "
                                f"{T_LETTERS}) LIMIT 10", s, "brain_machine_letters", None),
        "cat_alerts": Spec(f"SELECT published_date, alert_category_type, LEFT(notification_description, 300) AS alert, "
                           f"LEFT(potential_action_description, 300) AS potential_action, notification_status FROM {T_ALERTS} "
                           f"WHERE serial_no = ? ORDER BY last_update DESC LIMIT 5", s, "brain_machine_alerts", None),
    }


def faults_spec(serial: str) -> Spec:
    """Fault codes of the last 90 days of the machine's own fault data; only call when telematics is allowed."""
    s = str(serial).strip().upper()[:30]
    return Spec(f"SELECT description, severity_level, COUNT(*) AS events, MAX(occurred_at) AS last_seen FROM {T_FAULTS} "
                f"WHERE serial_no = ? AND occurred_at >= DATEADD(day, -90, (SELECT MAX(occurred_at) FROM {T_FAULTS} "
                f"WHERE serial_no = ?)) GROUP BY description, severity_level ORDER BY events DESC LIMIT 15",
                (s, s), "brain_machine_faults", None)


def telematics_allowed(fleet_rows: list[dict]) -> bool:
    """Territory policy (TERRITORY_POLICY / BRAIN_RULES, confirmed): no telematics for Egypt; unknown machine -> no."""
    return bool(fleet_rows) and all(r.get("telemetry_allowed") is True for r in fleet_rows)


def rules_spec() -> Spec:
    return Spec(f"SELECT area, name, value, unit, status FROM {T_RULES} ORDER BY area, name LIMIT 100", (), "brain_rules", None)


# --- training library (Cortex Search) -------------------------------------------------------------------
def kb_sql(query: str, system: str | None = None, limit: int = 5) -> str:
    req = {"query": str(query)[:300], "limit": max(1, min(int(limit), 8)),
           "columns": ["CHUNK_TEXT", "COURSE_TITLE", "MODULE", "SYSTEM", "DOC_TYPE", "FILE_NAME", "PAGE_NO"]}
    if system in KB_SYSTEMS:
        req["filter"] = {"@eq": {"SYSTEM": system}}
    return f"SELECT SNOWFLAKE.CORTEX.SEARCH_PREVIEW({sql_str(KB_SERVICE)}, {sql_str(json.dumps(req))}) AS r"


def parse_kb(raw) -> list[dict]:
    data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    out = []
    for r in data.get("results") or []:
        text = re.sub(r"\s+", " ", str(r.get("CHUNK_TEXT") or "")).strip()
        out.append({"course": r.get("COURSE_TITLE"), "module": r.get("MODULE"), "system": r.get("SYSTEM"),
                    "doc_type": r.get("DOC_TYPE"), "file": r.get("FILE_NAME"), "page": r.get("PAGE_NO"),
                    "text": text[:900]})
    return out


def kb_search(session, query: str, system: str | None = None, limit: int = 5) -> list[dict]:
    row = session.sql(kb_sql(query, system, limit)).collect()[0]
    return parse_kb(list(row.as_dict().values())[0])


# --- photos ------------------------------------------------------------------------------------------
VISION_PROMPT = (
    "You are a senior Caterpillar field service technician looking at photos sent by a colleague. "
    "Their message: \"{q}\".\n{photos}\n"
    "For each photo report, in compact markdown bullets (max 200 words in total):\n"
    "- What it shows: machine type/model if visible, system and component.\n"
    "- Visible condition and issues: wear, leaks, cracks, breakage, corrosion, contamination, burning, loose or "
    "missing parts, wrong fitting; where exactly.\n"
    "- Text you can read: part numbers, serial/model plates, fault codes or messages on screens, gauge values.\n"
    "- Severity: safety-critical / stop the machine / repair soon / monitor, and why.\n"
    "Describe only what is visible; mark anything inferred as 'likely'. Say what is unclear and what extra photo "
    "would help. If a photo is not related to equipment, say so briefly.")


def prepare_photo(data: bytes) -> bytes:
    """JPEG, longest side <= PHOTO_SIDE (smaller upload, within the vision model's limits)."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGB")
    img.thumbnail((PHOTO_SIDE, PHOTO_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def save_photo(session, data: bytes, user: str, chat_id: str, serial: str | None = None) -> str:
    """Upload one prepared JPEG to PHOTO_STG and log it; returns the stage-relative path."""
    pid = str(uuid.uuid4())
    path = f"{datetime.now(timezone.utc):%Y-%m}/{pid}.jpg"
    session.file.put_stream(io.BytesIO(data), f"{PHOTO_STAGE}/{path}", auto_compress=False, overwrite=True)
    try:
        session.sql(f"INSERT INTO {T_PHOTO_LOG} (photo_id, stage_path, mime, bytes, uploaded_at, user_name, serial_no, chat_id) "
                    "SELECT ?, ?, 'image/jpeg', ?, CURRENT_TIMESTAMP()::TIMESTAMP_NTZ, ?, ?, ?",
                    params=[pid, path, len(data), user, serial, chat_id]).collect()
    except Exception:  # noqa: BLE001 - the log is optional; the photo is still analysed
        pass
    return path


def vision_sql(model: str, question: str, paths: list[str]) -> str:
    paths = paths[:MAX_PHOTOS]
    labels = " ".join(f"Photo {i + 1}: {{{i}}}" for i in range(len(paths)))
    prompt = VISION_PROMPT.format(q=str(question)[:600].replace("{", "(").replace("}", ")"), photos=labels)
    files = ", ".join(f"TO_FILE({sql_str(PHOTO_STAGE)}, {sql_str(p)})" for p in paths)
    return f"SELECT AI_COMPLETE({sql_str(model)}, PROMPT({sql_str(prompt)}, {files})) AS r"


def look(session, paths: list[str], question: str, models: tuple[str, ...]) -> str:
    """What the photos show, from a Cortex vision model (the newest that answers)."""
    for i, model in enumerate(models):
        try:
            raw = list(session.sql(vision_sql(model, question, paths)).collect()[0].as_dict().values())[0]
            break
        except Exception:  # noqa: BLE001
            if i == len(models) - 1:
                raise
    if isinstance(raw, str) and raw.startswith('"'):
        raw = json.loads(raw)
    return str(raw or "").strip()
