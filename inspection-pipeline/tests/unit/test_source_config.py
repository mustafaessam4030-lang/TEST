from pathlib import Path

import pytest
import yaml

from app.errors import ConfigurationError
from app.source_config import load_source_config

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "source.example.yaml"


def test_example_config_parses_but_reports_every_unknown():
    cfg = load_source_config(EXAMPLE)
    with pytest.raises(ConfigurationError) as info:
        cfg.require("api", "browser_session")
    message = str(info.value)
    for item in ("api.request.path", "api.records_path", "api.fields.serial_number", "auth.login_path"):
        assert item in message
    assert "browser." not in message  # only sections needed for this run are checked


def test_complete_config_passes(source_config):
    source_config.require("api", "browser_session")
    source_config.require("playwright", "none")


def test_missing_file_and_bad_yaml(tmp_path):
    with pytest.raises(ConfigurationError, match="not found"):
        load_source_config(tmp_path / "nope.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"api": {"unknown_key": 1}}))
    with pytest.raises(ConfigurationError, match="Invalid"):
        load_source_config(bad)
