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
