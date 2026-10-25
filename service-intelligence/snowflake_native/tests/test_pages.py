"""Smoke tests: each Streamlit page renders without exceptions on a FAKE data layer (no Snowflake)."""
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
st_testing = pytest.importorskip("streamlit.testing.v1")

from si_native import ui  # noqa: E402
from si_native.analysis import Analytics  # noqa: E402
from si_native.data import FakeRunner  # noqa: E402
from test_native import REG, responder  # noqa: E402


class _Row:
    def __init__(self, d):
        self._d = d

    def as_dict(self):
        return self._d


class FakeSession:
    """Accepts plan-store statements; returns empty plans and a sequence value."""
    def __init__(self):
        self.statements = []

    def sql(self, q, params=None):
        self.statements.append((q, params))

        class _Q:
            def collect(self_inner):
                if "NEXTVAL" in q:
                    return [_Row({"N": 101})]
                if q.lstrip().upper().startswith("UPDATE"):
                    return [_Row({"number of rows updated": 1})]
                return []
        return _Q()


@pytest.fixture
def fake(monkeypatch):
    sess = FakeSession()
    monkeypatch.setattr(ui, "session", lambda: sess)
    monkeypatch.setattr(ui, "analytics", lambda: Analytics(REG, FakeRunner(REG, responder), "tester"))
    monkeypatch.setattr(ui, "app_role", lambda email: "admin")
    monkeypatch.setattr(ui, "user_email", lambda: "tester@example.com")
    monkeypatch.setattr(ui, "registry", lambda: REG)
    return sess


@pytest.mark.parametrize("page", ["streamlit_app.py", "pages/2_Recommendations.py", "pages/3_Assistant.py"])
def test_page_renders(fake, page):
    at = st_testing.AppTest.from_file(str(APP / page), default_timeout=30).run()
    assert not at.exception, [e.message for e in at.exception]
    assert any("si-top" in m.value for m in at.markdown)          # the page header rendered


def test_recommendations_page_runs_detectors_and_saves(fake):
    at = st_testing.AppTest.from_file(str(APP / "pages/2_Recommendations.py"), default_timeout=30).run()
    next(b for b in at.button if b.label == "Run detectors on the latest data").click().run()
    assert not at.exception
    assert any("Investigate repeat" in m.value for m in at.markdown)
    save = next(b for b in at.button if b.label == "Save to plan")
    save.click().run()
    assert not at.exception
    inserts = [q for q, _ in fake.statements if "INSERT INTO SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATIONS" in q]
    assert inserts and any("RECOMMENDATION_EVENTS" in q for q, _ in fake.statements)


def test_assistant_answers_with_the_registry_figure(fake):
    at = st_testing.AppTest.from_file(str(APP / "pages/3_Assistant.py"), default_timeout=30).run()
    at.chat_input[0].set_value("How many assets had high-severity faults last month?").run()
    assert not at.exception
    assert any("1,974" in m.value for m in at.markdown)
    assert any("CHAT_LOG" in q for q, _ in fake.statements)


def test_top_machines_tab_lists_and_saves(fake):
    at = st_testing.AppTest.from_file(str(APP / "pages/2_Recommendations.py"), default_timeout=30).run()
    assert not at.exception
    assert any("si-lrow" in m.value for m in at.markdown), "the top-100 list renders on the first tab"
    pick = next(m for m in at.multiselect if m.label == "Add to the plan (by serial)")
    pick.set_value(["S00001"]).run()
    next(b for b in at.button if b.label == "Save selected to plan").click().run()
    assert not at.exception
    saved = [p for q, p in fake.statements if "INSERT INTO SERVICE_INTELLIGENCE.SI_APP.RECOMMENDATIONS" in q]
    assert saved and saved[0][1] == "top_machine/1"


def test_top_machines_tab_switches_signal_class(fake):
    at = st_testing.AppTest.from_file(str(APP / "pages/2_Recommendations.py"), default_timeout=30).run()
    radio = next(r for r in at.radio if r.label == "Show")
    radio.set_value("Telemetry only").run()
    assert not at.exception and any("Telemetry only" in m.value and "si-lrow" in m.value for m in at.markdown)
