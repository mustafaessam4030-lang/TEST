"""Conversational assistant: SQL guard, plan -> run -> answer flow and the number check (FAKE model, FAKE data)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from si_native.agent import adhoc_spec, ask, unverified_numbers  # noqa: E402
from si_native.analysis import Analytics  # noqa: E402
from si_native.data import FakeRunner  # noqa: E402
from si_native.metrics import QueryError  # noqa: E402
from test_native import REG, responder  # noqa: E402


@pytest.fixture
def an():
    return Analytics(REG, FakeRunner(REG, responder), "tester")


@pytest.mark.parametrize("sql", [
    "SELECT branch, COUNT(*) AS n FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES GROUP BY branch ORDER BY n DESC LIMIT 10",
    "WITH f AS (SELECT asset_serial FROM SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS WHERE severity = 'HIGH') "
    "SELECT COUNT(DISTINCT f.asset_serial) AS n FROM f JOIN SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES o ON o.asset_serial = f.asset_serial",
    "SELECT EXTRACT(year FROM occurred_at) AS y, COUNT(*) AS n FROM SERVICE_INTELLIGENCE.CURATED.FAULT_EVENTS GROUP BY 1",
])
def test_guard_accepts_read_only_selects_on_approved_tables(an, sql):
    spec = adhoc_spec(sql, an)
    assert spec.sql.endswith("LIMIT 200") and spec.params == ()


@pytest.mark.parametrize("sql", [
    "DELETE FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES",
    "SELECT * FROM SERVICE_INTELLIGENCE.SI_APP.APP_USERS",
    "SELECT * FROM APP_USERS",
    "SELECT * FROM SNOWFLAKE.ACCOUNT_USAGE.USERS",
    "SELECT 1; DROP TABLE X",
    "SELECT * FROM TABLE(RESULT_SCAN(LAST_QUERY_ID()))",
    "SELECT * FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES a, SERVICE_INTELLIGENCE.SI_APP.CHAT_LOG b",
    "SELECT * FROM @SERVICE_INTELLIGENCE.SI_APP.APP_STAGE",
])
def test_guard_refuses_writes_and_other_objects(an, sql):
    with pytest.raises(QueryError):
        adhoc_spec(sql, an)


def _model(plan):
    def complete(msgs, n):
        if "ONE JSON object" in msgs[0]["content"]:
            return json.dumps(plan)
        return "There were **1,974** assets with high-severity faults, about 12,345 more than ever."
    return complete


def test_data_question_runs_the_registry_metric_and_flags_unsupported_figures(an):
    runner = FakeRunner(REG, responder)
    out = ask("how many assets had high faults?", [], an, _model(
        {"type": "data", "steps": [{"tool": "metric", "metric_id": "high_severity_fault_assets", "days": 30}]}),
        lambda spec: runner.run(spec))
    assert out["kind"] == "data" and out["results"][0]["value"] == 1974
    assert out["evidence"] and out["unverified"] == ["12,345"]       # 1,974 verified; the invented figure is flagged


def test_direct_reply_runs_no_query(an):
    runner = FakeRunner(REG, responder)
    out = ask("hi", [], an, _model({"type": "reply", "text": "Hello! Ask me about faults or opportunities."}),
              lambda spec: runner.run(spec))
    assert out["kind"] == "reply" and not runner.calls and "Hello" in out["text"]


def test_model_written_sql_is_guarded_before_it_runs(an):
    runner = FakeRunner(REG, responder)
    out = ask("delete everything", [], an, _model(
        {"type": "data", "steps": [{"tool": "sql", "sql": "DELETE FROM SERVICE_INTELLIGENCE.CURATED.OPPORTUNITIES"}]}),
        lambda spec: runner.run(spec))
    assert out["kind"] == "error" and not runner.calls


def test_number_check_allows_rounding_and_percentages():
    results = [{"value": 1708000000.0, "previous_value": 1500000.0, "change_pct": 13.9, "rows": [{"value": 427}, {"value": 1974}]}]
    assert unverified_numbers("Open value is 1.7bn (+13.9%); 427 of 1,974 assets (22%) repeat.", results) == []
    assert unverified_numbers("Open value is 2.4bn.", results) == ["2.4bn"]


def test_followups_are_split_from_the_answer():
    from si_native.agent import split_followups
    text, fu = split_followups("**12** machines.\n\nFOLLOWUPS: By branch? | Which are overdue? | Show the top 5")
    assert text == "**12** machines." and fu == ["By branch?", "Which are overdue?", "Show the top 5"]
    assert split_followups("No suggestions here.") == ("No suggestions here.", [])


def test_machine_detail_queries_pass_the_guard_with_the_serial_bound():
    from datetime import date
    from si_native import machine
    from si_native.metrics import check_spec
    for spec in (machine.olga_jobs("X'; DROP"), machine.faults("X'; DROP", date(2026, 10, 23)),
                 machine.hours("X'; DROP", date(2026, 10, 23))):
        check_spec(spec, REG)
        assert "DROP" not in spec.sql and spec.params[0] == "X'; DROP"


def test_a_plain_text_reply_is_shown_not_replaced(an):
    runner = FakeRunner(REG, responder)
    out = ask("haha what", [], an, lambda msgs, n: "Ha! I mean I can look up your fleet data. What should I check?",
              lambda spec: runner.run(spec))
    assert out["kind"] == "reply" and out["text"].startswith("Ha! I mean") and not runner.calls


def test_model_codes_are_not_taken_for_figures():
    results = [{"rows": [{"model": "785D", "due_value": 106719}]}]
    assert unverified_numbers("The 785D at Goldfields has 106,719 due.", results) == []
    assert unverified_numbers("EID 875 fires often.", [{"rows": [{"repeat_fault": "Low System Voltage (EID 875)"}]}]) == []


# --- AI Brain: training library, repair history, machine profile, photos -------------------------------
def brain_responder(spec):
    if spec.purpose == "brain_repairs":
        return [{"model": "777G", "open_date": "2025-12-09", "complaint": "LOW SYSTEM VOLTAGE", "root_cause": "TORN",
                 "work_done": "ALTERNATOR BELT REPLACED", "parts_used": "3748480 BELT x1", "labour_hours": 2.0,
                 "similar_jobs": 31, "jobs_with_hours": 10, "median_labour_hours": 4.0}]
    if spec.purpose == "brain_repair_parts":
        return [{"part": "1417116 BELT", "jobs": 11}]
    if spec.purpose == "brain_machine":
        eg = "TNW" in spec.params[0]
        return [{"terrid": "EG" if eg else "GH", "serial_no": spec.params[0], "model_cd": "785",
                 "telemetry_allowed": not eg, "latest_smu": 32438.6}]
    if spec.purpose == "brain_machine_faults":
        return [{"description": "Diff Lube Pres", "severity_level": 3, "events": 214, "last_seen": "2026-10-24"}]
    if spec.purpose.startswith("brain_machine"):
        return []
    return responder(spec)


def test_brain_queries_pass_the_guard_with_values_bound():
    from si_native import brain
    from si_native.metrics import check_spec
    specs = [brain.repairs_spec(["alternator|charging", "x'; DROP"], "777'", "S1"), brain.repair_parts_spec(["belt"], "777"),
             brain.faults_spec("X'; DROP"), brain.rules_spec(), *brain.machine_specs("X'; DROP").values()]
    for spec in specs:
        check_spec(spec, REG)
        assert "DROP" not in spec.sql
    assert "cause, 300) AS complaint" in specs[0].sql and "correction, 200) AS root_cause" in specs[0].sql


def test_adhoc_sql_reaches_the_brain_but_not_logs_or_unfiltered_telematics(an):
    adhoc_spec("SELECT model_cd, COUNT(*) AS n FROM SERVICE_INTELLIGENCE.AI_BRAIN.REPAIR_KNOWLEDGE GROUP BY 1", an)
    for t in ("V_FAULT_CODES", "PHOTO_LOG", "CHAT_LOG", "AI_SUMMARY_CACHE"):
        with pytest.raises(QueryError):
            adhoc_spec(f"SELECT * FROM SERVICE_INTELLIGENCE.AI_BRAIN.{t}", an)


def test_labour_hours_need_a_model_and_three_jobs():
    from si_native import brain
    rows = [{"similar_jobs": 5, "jobs_with_hours": 4, "median_labour_hours": 3.5}]
    assert brain.summarize_repairs(rows, [], "777")["median_labour_hours"] == 3.5
    assert brain.summarize_repairs(rows, [], None)["median_labour_hours"] is None
    assert brain.summarize_repairs([{**rows[0], "jobs_with_hours": 2}], [], "777")["median_labour_hours"] is None


def test_egypt_machine_profile_skips_telematics(an):
    from si_native.agent import run_step
    runner = FakeRunner(REG, brain_responder)
    eg = run_step({"tool": "machine", "serial": "TNW00180"}, an, lambda s: runner.run(s))
    assert "not allowed" in eg["fault_codes_90d"] and not any(c.purpose == "brain_machine_faults" for c in runner.calls)
    gh = run_step({"tool": "machine", "serial": "MSY00912"}, an, lambda s: runner.run(s))
    assert gh["fault_codes_90d"][0]["events"] == 214


def test_photo_question_uses_vision_manual_and_repairs(an):
    runner = FakeRunner(REG, brain_responder)
    seen = {}

    def complete(msgs, n):
        if "ONE JSON object" in msgs[0]["content"]:
            seen["planner"] = msgs[-1]["content"]
            return json.dumps({"type": "data", "steps": [
                {"tool": "manual", "query": "alternator belt", "system": "Electrical & Electronics"},
                {"tool": "repairs", "keywords": ["alternator|belt", "voltage"], "model": "777"}]})
        seen["answer"] = msgs[-1]["content"]
        return "Likely a torn belt: **31** similar jobs, median **4.0** h (p. 78).\nFOLLOWUPS: a | b | c"

    out = ask("what's wrong here?", [], an, complete, lambda s: runner.run(s),
              search=lambda q, system, n: [{"course": "EESF", "module": "M08", "page": "78", "text": "Check belt tension"}],
              look=lambda paths, q: "- Torn alternator belt on a 777G", photos=["2026-10/a.jpg"])
    assert "Torn alternator belt" in seen["planner"] and "Check belt tension" in seen["answer"]
    assert [r["tool"] for r in out["results"]] == ["photo", "manual", "repairs"]
    assert out["unverified"] == [] and out["results"][2]["common_parts"][0]["part"] == "1417116 BELT"


def test_photo_with_a_plain_reply_still_answers_from_the_findings(an):
    out = ask("hi", [], an, lambda msgs, n: "plain words" if "ONE JSON object" in msgs[0]["content"] else "It is a cracked hose.",
              lambda s: None, look=lambda paths, q: "- Cracked hydraulic hose", photos=["p.jpg"])
    assert out["kind"] == "data" and out["results"][0]["tool"] == "photo"


def test_vision_and_search_sql_escape_user_text():
    from si_native import brain
    sql = brain.vision_sql("claude-sonnet-5", "it's {0} \\ leaking", ["2026-10/a.jpg"])
    assert "it''s (0) \\\\ leaking" in sql and "TO_FILE('@SERVICE_INTELLIGENCE.AI_BRAIN.PHOTO_STG', '2026-10/a.jpg')" in sql
    assert "''" in brain.kb_sql("driver's seat", "Brakes") and '"@eq"' in brain.kb_sql("x", "Brakes")
    assert '"filter"' not in brain.kb_sql("x", "Not a system")


def test_web_search_is_cited_and_never_carries_serials_or_emails(an):
    from si_native import brain
    from si_native.agent import scrub_web_query
    assert scrub_web_query("777G oil interval MSY00912 for a.b@mantracgroup.com C175-16") == "777G oil interval for C175-16"
    seen = {}

    def complete(msgs, n):
        if "ONE JSON object" in msgs[0]["content"]:
            return json.dumps({"type": "data", "steps": [{"tool": "web", "query": "Cat 777G oil change interval tnw00180"}]})
        return "Per [Toromont](https://t.example/x), intervals depend on S·O·S results.\nFOLLOWUPS: a | b | c"

    def web(q, n):
        seen["q"] = q
        return [{"title": "Toromont", "url": "https://t.example/x", "text": "oil drain intervals"}]
    out = ask("search the web for the 777G oil interval", [], an, complete, lambda s: None, web=web)
    assert seen["q"] == "Cat 777G oil change interval" and out["results"][0]["tool"] == "web"
    raw = {"content": [{"tool_use": {}}, {"tool_result": {"content": [{"json": {"search_results": [
        {"doc_id": "https://a.example", "doc_title": "A", "text": "x  y"}, {"doc_id": "https://a.example"}]}}]}}]}
    assert brain.parse_web(json.dumps(raw)) == [{"title": "A", "url": "https://a.example", "text": "x y"}]
    assert "''" in brain.web_sql("driver's cab") and '"web_search"' in brain.web_sql("x")


def test_training_pictures_come_from_the_pages_found():
    import io
    from PIL import Image
    from si_native import brain
    sql = brain.kb_sql("fuel system", "Diesel Engine", 5, visual=True)
    assert '"@and"' in sql and "Diagram / slide (OCR)" in sql and "FILE_PATH" in sql
    rows = [{"course": "C", "module": "M", "page": 79, "path": "a/b.pdf"}, {"course": "C", "module": "M", "page": 79, "path": "a/b.pdf"},
            {"course": "D", "module": "N", "page": 1, "path": "x/Fault Example.JPG"}, {"page": 3, "path": "z.dxr"}]
    vis = brain.training_visuals(rows)
    assert [(v["kind"], v["page"]) for v in vis] == [("pdf", 79), ("image", None)] and vis[0]["caption"] == "C · M · p. 79"
    buf = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buf, "PDF")
    png = brain.pdf_page_png(buf.getvalue(), 1, width=300)
    assert png is None or Image.open(io.BytesIO(png)).size[0] == 300


def test_video_search_returns_youtube_cards(an):
    from si_native import brain
    rows = [{"url": "https://www.youtube.com/watch?v=RHQy8mi9jOA", "title": "Cat 777G Haul Truck Service Walkaround - YouTube"},
            {"url": "https://m.youtube.com/watch?v=A0TBspHrug0&pp=yg", "title": "777G"},
            {"url": "https://youtu.be/RHQy8mi9jOA", "title": "dup"}, {"url": "https://example.com/x", "title": "page"}]
    v = brain.video_results(rows)
    assert [x["video_id"] for x in v] == ["RHQy8mi9jOA", "A0TBspHrug0"] and v[0]["title"] == "Cat 777G Haul Truck Service Walkaround"
    assert v[0]["thumb"] == "https://i.ytimg.com/vi/RHQy8mi9jOA/hqdefault.jpg"
    from si_native.agent import run_step
    out = run_step({"tool": "video", "query": "777G walkaround"}, an, None, web=lambda q, n: rows)
    assert out["tool"] == "video" and len(out["rows"]) == 2
