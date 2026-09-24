"""Real Chromium against the controlled fixture site (no external network)."""

import json
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.auth.session import BrowserSessionAuthenticator
from app.collectors.api import ApiInspectionCollector
from app.collectors.browser import PlaywrightInspectionCollector
from app.diagnostics import Diagnostics
from app.errors import AuthenticationError, PageStructureError
from app.services.validation import validate_records
from app.source_config import SourceConfig
from tests.conftest import SOURCE_DICT, make_settings
from tests.fixtures.site.server import PASSWORD, SESSION, USERNAME, FixtureServer

pytestmark = pytest.mark.browser


@pytest.fixture
def site(chromium_available):
    with FixtureServer() as url:
        yield url


def _settings(site, tmp_path, **kw):
    base = dict(website_base_url=site, auth_mode="browser_session", website_username=USERNAME,
                website_password=PASSWORD, browser_timeout_ms=5000, diagnostics_dir=tmp_path / "diag")
    base.update(kw)
    return make_settings(**base)


def _source(**browser_overrides):
    data = json.loads(json.dumps(SOURCE_DICT))
    data["browser"].update(browser_overrides)
    return SourceConfig.model_validate(data)


def _collector(settings, source, run_id="run-test"):
    return PlaywrightInspectionCollector(settings, source.browser, source.auth,
                                         Diagnostics(settings.diagnostics_dir, run_id))


async def test_login_extract_paginate_and_attachments(site, tmp_path):
    settings, source = _settings(site, tmp_path), _source()
    result = await _collector(settings, source).collect()
    assert result.pages_fetched == 3 and len(result.records) == 5
    first = result.records[0].raw
    assert first["Inspection No."] == "30080001" and first["S/N"] == "SYW00001"
    assert first["_links"]["Attachments"] == [{"name": "report-1.pdf", "url": f"{site}/files/report-1.pdf"}]
    assert result.records[2].raw["_links"] == {}  # record 3 has no attachments

    outcome = validate_records(result.records, mapping=source.browser.fields, mode="key", run_id=uuid4(),
                               source="website", collector_type="playwright",
                               collected_at=datetime.now(timezone.utc), date_formats=source.date_formats)
    assert len(outcome.valid) == 5 and not outcome.failures
    assert outcome.valid[1].summary == {"critical": 0, "warning": 0, "passed": 72}
    assert outcome.valid[0].inspection_date == datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


async def test_search_filter(site, tmp_path):
    result = await _collector(_settings(site, tmp_path), _source()).collect(serial_number="SYW00004")
    assert [r.raw["Status"] for r in result.records] == ["In Progress"]


async def test_bad_credentials_fail_with_diagnostics_and_no_secret(site, tmp_path):
    settings = _settings(site, tmp_path, website_password="wrong-password-value", browser_timeout_ms=2000)
    with pytest.raises(AuthenticationError):
        await _collector(settings, _source(), run_id="run-auth").collect()
    files = list((tmp_path / "diag" / "run-auth").iterdir())
    assert {f.suffix for f in files} == {".json", ".png"}
    info = json.loads(next(f for f in files if f.suffix == ".json").read_text())
    assert info["run_id"] == "run-auth" and info["stage"] == "login_result" and info["url"].endswith("/login")
    assert "wrong-password-value" not in json.dumps(info)


async def test_changed_page_structure_is_detected(site, tmp_path):
    data = json.loads(json.dumps(SOURCE_DICT))
    data["browser"]["fields"]["serial_number"] = "Serial"  # header that does not exist
    source = SourceConfig.model_validate(data)
    with pytest.raises(PageStructureError, match="Serial"):
        await _collector(_settings(site, tmp_path), source, run_id="run-struct").collect()
    assert any(p.suffix == ".png" for p in (tmp_path / "diag" / "run-struct").iterdir())


async def test_missing_table_times_out_as_structure_error(site, tmp_path):
    source = _source(table={"role": "grid", "name": "Nope"})
    settings = _settings(site, tmp_path, browser_timeout_ms=1500)
    with pytest.raises(PageStructureError, match="extract"):
        await _collector(settings, source).collect()


async def test_api_collector_with_browser_session_cookies(site, tmp_path):
    settings, source = _settings(site, tmp_path), _source()
    auth = BrowserSessionAuthenticator(settings, source.auth, Diagnostics(settings.diagnostics_dir, "r"))
    result = await ApiInspectionCollector(settings, source.api, auth, source.auth).collect()
    assert len(result.records) == 5 and result.expected_total == 5
    assert result.records[0].raw["serialNo"] == "SYW00001"


async def test_session_cookie_is_registered_for_redaction(site, tmp_path):
    from app.logger import redact

    settings, source = _settings(site, tmp_path), _source()
    auth = BrowserSessionAuthenticator(settings, source.auth, Diagnostics(settings.diagnostics_dir, "r"))
    await auth.authenticate()
    assert SESSION not in redact(f"cookie value {SESSION}")
