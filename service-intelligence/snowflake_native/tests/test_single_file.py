"""The single-file build (dist/streamlit_app.py) must render all three pages, on FAKE data, without the app/ folder."""
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "streamlit_app.py"
st_testing = pytest.importorskip("streamlit.testing.v1")


def test_build_is_current():
    spec = importlib.util.spec_from_file_location("builder", ROOT / "tools" / "build_single_file.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    assert DIST.read_text(encoding="utf-8") == builder.build(), "run: python tools/build_single_file.py"


def test_single_file_renders_every_page_on_fake_data():
    # run in a clean interpreter so the embedded si_native package does not mix with app/si_native
    code = f"""
import importlib.util, sys
sys.path.insert(0, {str(ROOT / 'tests')!r})
spec = importlib.util.spec_from_file_location("bundle", {str(DIST)!r}); b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)                       # installs the embedded si_native (no render: __name__ != __main__)
import si_native
assert si_native.SINGLE_FILE and "app" not in str(getattr(si_native.ui, "__file__", ""))
from si_native import ui
from si_native.analysis import Analytics
from si_native.data import FakeRunner
from si_native.metrics import Registry
REG = Registry(b.REGISTRY)
import fake_data
ui.session = lambda: fake_data.FakeSession()
ui.analytics = lambda: Analytics(REG, FakeRunner(REG, fake_data.responder), "tester")
ui.app_role = lambda email: "admin"
ui.user_email = lambda: "tester@example.com"
ui.registry = lambda: REG
from streamlit.testing.v1 import AppTest
for page in ("Analysis", "Recommendations", "Assistant"):
    at = AppTest.from_file({str(DIST)!r}, default_timeout=60)
    at.session_state["si_page"] = page
    at.run()
    assert not at.exception, (page, [e.message for e in at.exception])
    assert any("si-top" in m.value for m in at.markdown), page
print("OK")
"""
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout[-2000:] + r.stderr[-4000:]
