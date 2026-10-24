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
        outer = self

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
    assert at.title[0].value


def test_recommendations_page_runs_detectors_and_saves(fake):
    at = st_testing.AppTest.from_file(str(APP / "pages/2_Recommendations.py"), default_timeout=30).run()
    at.button[0].click().run()                     # Run detectors
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
