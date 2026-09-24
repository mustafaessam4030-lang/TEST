from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.models import Inspection, SourceRecord
from app.services.validation import validate_records
from tests.conftest import synthetic_record


def _validate(records, source_config, **kw):
    return validate_records(
        [SourceRecord(raw=r, locator=f"page=1,index={i}") for i, r in enumerate(records)],
        mapping=source_config.api.fields, mode="path", run_id=uuid4(), source="website",
        collector_type="api", collected_at=datetime.now(timezone.utc), **kw,
    )


def _inspection(**kw):
    base = dict(run_id=uuid4(), source="website", collector_type="api", inspection_number="1",
                serial_number="SN", status="Completed", collected_at=datetime.now(timezone.utc), raw_data={})
    base.update(kw)
    return Inspection(**base)


def test_valid_records_pass(source_config):
    outcome = _validate([synthetic_record(1), synthetic_record(2)], source_config)
    assert len(outcome.valid) == 2 and not outcome.failures
    assert outcome.valid[0].raw_data == synthetic_record(1)  # original payload preserved


@pytest.mark.parametrize("override,message", [
    ({"inspectionNo": "  "}, "inspection_number"),
    ({"status": None}, "status"),
    ({"completedAt": "2099-01-01T00:00:00Z"}, "future"),
    ({"completedAt": "not a date"}, "unrecognised"),
    ({"results": {"critical": -1, "warning": 0, "passed": 1}}, "summary"),
    ({"attachments": [{"fileName": None, "downloadUrl": None}]}, "attachment"),
    ({"attachments": "oops"}, "attachments"),
])
def test_invalid_records_are_recorded_not_dropped(source_config, override, message):
    outcome = _validate([synthetic_record(1, **override), synthetic_record(2)], source_config)
    assert len(outcome.valid) == 1
    assert len(outcome.failures) == 1
    failure = outcome.failures[0]
    assert message in failure.error_message
    assert failure.raw_data["serialNo"] == "SYW00001"
    assert failure.record_identifier in ("30080001", "<unknown:page=1,index=0>")


def test_identical_duplicates_counted_conflicts_recorded(source_config):
    records = [synthetic_record(1), synthetic_record(1), synthetic_record(1, status="Open")]
    outcome = _validate(records, source_config)
    assert len(outcome.valid) == 1
    assert outcome.duplicates == 1
    assert [f.error_type for f in outcome.failures] == ["DUPLICATE_CONFLICT"]


def test_allowed_statuses(source_config):
    outcome = _validate([synthetic_record(1, status="Weird")], source_config, allowed_statuses=["Completed"])
    assert outcome.failures[0].error_message.startswith("status")


def test_naive_collected_at_rejected():
    with pytest.raises(ValidationError):
        _inspection(collected_at=datetime.now())


def test_record_hash_is_stable_and_content_sensitive():
    a = _inspection(raw_data={"x": 1}, run_id=uuid4())
    b = _inspection(raw_data={"x": 1}, run_id=uuid4(), collected_at=datetime.now(timezone.utc) + timedelta(days=1))
    c = _inspection(raw_data={"x": 2})
    assert a.record_hash == b.record_hash  # run_id / collected_at do not affect the hash
    assert a.record_hash != c.record_hash
