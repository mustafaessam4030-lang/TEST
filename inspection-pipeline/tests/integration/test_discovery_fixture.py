import argparse
import json

import yaml

from app.discovery import discover
from tests.conftest import SOURCE_DICT
from tests.fixtures.site.server import PASSWORD, SESSION, USERNAME, FixtureServer


async def test_discovery_finds_endpoint_and_paths(tmp_path, monkeypatch, chromium_available):
    cfg = tmp_path / "source.yaml"
    cfg.write_text(yaml.safe_dump(SOURCE_DICT))
    with FixtureServer() as url:
        for key, value in {"WEBSITE_BASE_URL": url, "WEBSITE_USERNAME": USERNAME, "WEBSITE_PASSWORD": PASSWORD,
                           "SOURCE_CONFIG_PATH": str(cfg), "DIAGNOSTICS_DIR": str(tmp_path / "d")}.items():
            monkeypatch.setenv(key, value)
        monkeypatch.chdir(tmp_path)  # no stray .env is picked up
        args = argparse.Namespace(base_url=None, navigate="/inspections", probe=["SYW00001"], auto_login=True,
                                  duration=2, headless=True, out=tmp_path / "disc")
        report = await discover(args)
    text = report.read_text()
    assert "GET /api/inspections" in text
    assert "records_path `data.items`, field path `serialNo`" in text
    captured = (report.parent / "requests.json").read_text()
    assert SESSION not in captured and PASSWORD not in captured
    entry = json.loads(captured)[0]
    assert "cookie" in entry["request_header_names"] and "sid" not in json.dumps(entry["query"])
    assert (report.parent / "aria_snapshot.yaml").read_text().count("Inspection No.") >= 1
