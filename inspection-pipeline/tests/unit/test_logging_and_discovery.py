import logging

from app.config import Settings
from app.discovery import find_value_paths, list_candidates, scrub, scrub_query, split_record_path
from app.logger import REDACTED, configure_logging, redact, set_run_id


def test_registered_secrets_and_patterns_are_redacted():
    Settings(_env_file=None, website_password="hunter2-secret", snowflake_password="sf-pa55word")
    text = redact("login hunter2-secret sf-pa55word Authorization: Bearer abc.def.ghijklmnop password=xyz token=\"t\"")
    assert "hunter2-secret" not in text and "sf-pa55word" not in text
    assert "abc.def" not in text and "xyz" not in text
    assert text.count(REDACTED) >= 4
    assert redact("basic information about bearer tokens") == "basic information about bearer tokens"


def test_log_lines_carry_run_id_and_are_redacted(capsys):
    Settings(_env_file=None, website_password="another-secret")
    configure_logging("INFO")
    set_run_id("run-123")
    logging.getLogger("t").info("Using %s", "another-secret")
    out = capsys.readouterr().out
    assert "run_id=run-123" in out and "another-secret" not in out


def test_settings_repr_hides_secrets():
    s = Settings(_env_file=None, website_password="p@ssw0rd!!")
    assert "p@ssw0rd!!" not in repr(s)


def test_discovery_helpers():
    doc = {"data": {"rows": [{"sn": "SYW57101", "token": "abc"}, {"sn": "X"}], "total": 2}, "password": "p"}
    assert find_value_paths(doc, "SYW57101") == ["data.rows.0.sn"]
    assert split_record_path("data.rows.0.sn") == ("data.rows", "sn")
    assert list_candidates(doc)[0]["records_path"] == "data.rows"
    scrubbed = scrub(doc)
    assert scrubbed["password"] == REDACTED and scrubbed["data"]["rows"][0]["token"] == REDACTED
    assert scrub_query("https://x/api?page=2&access_token=zzz") == {"page": "2", "access_token": REDACTED}
