from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.normalization import MISSING, LINKS_KEY, MappingError, get_path, map_record, parse_datetime
from tests.conftest import synthetic_record


def test_get_path_handles_nesting_lists_and_missing():
    doc = {"data": {"items": [{"id": 1}, {"id": 2}]}}
    assert get_path(doc, "data.items.1.id") == 2
    assert get_path(doc, "") is doc
    assert get_path(doc, "data.nope") is MISSING
    assert get_path(doc, "data.items.9.id") is MISSING


def test_map_api_record(source_config):
    out = map_record(synthetic_record(1), source_config.api.fields, mode="path", date_formats=[], tz=timezone.utc)
    assert out["inspection_number"] == "30080001"
    assert out["serial_number"] == "SYW00001"
    assert out["summary"] == {"critical": 0, "warning": 1, "passed": 75}
    assert out["attachments"] == [{"name": "report-1.pdf", "url": "/files/1"}]
    assert out["inspection_date"] == datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)


def test_missing_required_path_raises(source_config):
    record = synthetic_record(1)
    del record["serialNo"]
    with pytest.raises(MappingError, match="serial_number"):
        map_record(record, source_config.api.fields, mode="path", date_formats=[], tz=timezone.utc)


def test_map_browser_row_uses_exact_headers_and_links(source_config):
    row = {"Inspection No.": "30081769", "S/N": "SYW57101", "Status": "Completed", "Date": "01/09/2026 10:15",
           "Critical": "0", "Warning": "", "Passed": "1,075", "Attachments": "report.pdf",
           LINKS_KEY: {"Attachments": [{"name": "report.pdf", "url": "https://x/files/1"}]}}
    out = map_record(row, source_config.browser.fields, mode="key", date_formats=["%d/%m/%Y %H:%M"],
                     tz=ZoneInfo("Africa/Cairo"))
    assert out["inspection_number"] == "30081769"  # header containing a dot is not split
    assert out["summary"] == {"critical": "0", "passed": "1075"}  # blank counter omitted
    assert out["attachments"] == [{"name": "report.pdf", "url": "https://x/files/1"}]
    assert out["inspection_date"].tzinfo == ZoneInfo("Africa/Cairo")


@pytest.mark.parametrize("value,expected", [
    ("2026-09-01T08:30:00+02:00", datetime(2026, 9, 1, 6, 30, tzinfo=timezone.utc)),
    (1788251400, datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)),
    (1788251400000, datetime(2026, 9, 1, 8, 30, tzinfo=timezone.utc)),
    ("", None),
    (None, None),
])
def test_parse_datetime(value, expected):
    assert parse_datetime(value, [], timezone.utc) == expected


def test_parse_datetime_rejects_garbage():
    with pytest.raises(ValueError, match="unrecognised"):
        parse_datetime("next tuesday", [], timezone.utc)
