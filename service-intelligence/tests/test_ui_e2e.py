"""Browser tests of the critical workflows on SYNTHETIC data (skipped when Chromium is unavailable)."""
import os
import socket
import threading
import time

import pytest

from conftest import TOKENS, build

pytestmark = pytest.mark.e2e
playwright = pytest.importorskip("playwright.sync_api")
CHROMIUM = os.environ.get("SI_E2E_CHROMIUM", "/opt/pw-browsers/chromium")
if not os.path.exists(CHROMIUM):
    pytest.skip("Chromium not available", allow_module_level=True)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn
    from si.api import create_app
    svc = build(tmp_path_factory.mktemp("e2e"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(create_app(svc.settings, svc), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture(scope="module")
def browser():
    with playwright.sync_playwright() as p:
        b = p.chromium.launch(executable_path=CHROMIUM)
        yield b
        b.close()


def signed_in(browser, server, who="admin", viewport=None):
    page = browser.new_page(viewport=viewport or {"width": 1300, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/#/analysis")
    page.fill("#token", TOKENS[who])
    page.click("#signin button[type=submit]")
    page.wait_for_selector(".kpi .value")
    return page, errors


def test_sign_in_is_required(browser, server):
    page = browser.new_page()
    page.goto(server + "/")
    page.wait_for_selector("#signin", state="visible")
    assert page.is_hidden("#page-analysis")
    page.close()


def test_analysis_kpi_to_breakdown_to_records(browser, server):
    page, errors = signed_in(browser, server)
    assert "SYNTHETIC" in page.inner_text("#source-banner")
    assert "Data as of" in page.inner_text("#freshness")
    assert page.locator(".kpi").count() == 12
    page.click(".kpi:has-text('Median resolution time')")
    page.wait_for_selector("#breakdown svg .bar")
    page.select_option("#dimension", "region")
    page.wait_for_selector("#breakdown h4:has-text('by region')")
    page.locator("#breakdown .bar").first.click()
    page.wait_for_selector("#drill table tbody tr")
    assert "Records 1–" in page.inner_text("#drill caption")
    assert "customer" not in page.inner_text("#drill thead").lower()
    assert not errors
    page.close()


def test_filters_change_figures(browser, server):
    page, _ = signed_in(browser, server)
    before = page.inner_text(".kpi:has-text('Requests created') .value")
    page.select_option("#dim-region", "North")
    page.click("#apply")
    page.wait_for_function("(b) => document.querySelector('.kpi .value') && document.querySelector('.kpi .value').textContent !== b", arg=before)
    assert page.inner_text(".kpi:has-text('Requests created') .value") != before
    page.close()


def test_generate_save_and_approve_recommendation(browser, server):
    page, errors = signed_in(browser, server, "admin")
    page.click("a[data-page=recommendations]")
    page.click("#generate")
    page.wait_for_selector(".cand")
    first = page.locator(".cand").first
    first.locator("text=Evidence and plan details").click()
    assert "Projection:" in first.inner_text() and "Query id" in first.inner_text()
    first.locator("text=Save to plan").click()
    page.wait_for_selector("#plan table button")
    page.locator("#plan table button").first.click()
    page.wait_for_selector("#plan-detail .history li")
    page.click("#plan-detail >> text=Mark reviewed")
    page.wait_for_selector("#plan-detail .status:has-text('reviewed')")
    page.click("#plan-detail >> text=Mark approved")
    page.wait_for_selector("#plan-detail .status:has-text('approved')")
    assert page.locator("#plan-detail .history li").count() == 3
    assert not errors
    page.close()


def test_viewer_cannot_save_or_drill(browser, server):
    page, _ = signed_in(browser, server, "viewer")
    page.click("a[data-page=recommendations]")
    page.click("#generate")
    page.wait_for_selector(".cand")
    assert page.locator("text=Save to plan").count() == 0
    page.close()


def test_assistant_answers_with_sources_and_refuses_sql(browser, server):
    page, errors = signed_in(browser, server)
    page.click("a[data-page=assistant]")
    page.fill("#message", "Which categories contribute most to the current backlog?")
    page.click("#send")
    page.wait_for_selector(".msg.status-answered")
    ans = page.locator(".msg.status-answered").last
    assert "Highest" in ans.inner_text() and "Sources" in ans.inner_text()
    page.fill("#message", "DROP TABLE service_requests")
    page.click("#send")
    page.wait_for_selector(".msg.status-restricted")
    assert not errors
    page.close()


def test_mobile_layout_has_no_horizontal_scroll(browser, server):
    page, _ = signed_in(browser, server, viewport={"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    page.close()
