"""The investigator's browser code, run in a REAL Chromium against a local replica
of the SIS record layout (from the user's screenshots). This proves Maia's own
browser logic — tab finding, section expansion, row reading, viewer
instrumentation. It does NOT prove anything about SIS itself: that is
scripts/e2e/investigate_real.py on a machine signed in to SIS.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

REPLICA = Path(__file__).parent / "fixtures" / "sis_tabs_replica.html"
CHROME = os.environ.get("MAIA_CHROMIUM_PATH") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


@pytest.mark.asyncio
async def test_investigator_reads_troubleshooting_and_inspects_the_viewer(tmp_path) -> None:
    playwright = pytest.importorskip("playwright.async_api")
    if not Path(CHROME).exists():
        pytest.skip("no Chromium available")
    from app.adapters.browser import RunContext
    from app.adapters.sis_investigation import SisInvestigator

    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch(executable_path=CHROME, headless=True)
        page = await browser.new_page()
        await page.goto(REPLICA.as_uri())
        ctx = RunContext(run_id="t", page=page, context=None, source_id="cat_sis",
                         artifact_dir=tmp_path, step_timeout_ms=5000, captured_xhr=[])
        inv, shots = await SisInvestigator({"investigation": {"canvas_wait_ms": 3000}}).run(
            ctx, "JAZ01865", ["troubleshooting", "model_3d"])
        await browser.close()

    tr = inv["troubleshooting"]
    assert tr["status"] == "CAPTURED" and tr["panel_title"] == "Codes, Events, & Symptoms"
    by = {s["section"]: s for s in tr["sections"]}
    assert by["Troubleshooting"]["rows"] == [
        "36-1-5 Cylinder #1 Injector Current Below Normal",
        "36-2-6 Cylinder #2 Injector Current Above Normal",
        "36-1-2 Cylinder #1 Injector Erratic, Intermittent, or Incorrect"]
    assert by["Advanced Troubleshooting"]["items_read"] == 2
    assert by["Symptoms"]["rows"] == ["Engine Misfires, Runs Rough or Is Unstable", "Low Power"]
    assert all(s["count_displayed"] == s["items_read"] for s in tr["sections"])
    m = inv["model_3d"]
    assert m["tab"]["opened"] and len(m["viewer"]["canvases"]) == 1
    assert m["status"] == "VISUAL_ONLY" and m["component_names"] == []
    assert "mapping" not in inv                         # nothing to map against: no guess
    assert {"troubleshooting", "model3d"} <= set(shots)
