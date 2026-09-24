"""Map a raw source record onto the canonical inspection fields.

The mapping comes from ``config/source.yaml``; nothing here knows the real
field names. Two lookup modes exist:

* ``path`` (API): dotted JSON path, list indices allowed (``items.0.sn``).
* ``key``  (Playwright): exact column header text; header text may itself
  contain dots ("Insp. No."), so it is never split.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.source_config import FieldMapping

LookupMode = Literal["path", "key"]
MISSING: Any = object()
LINKS_KEY = "_links"  # Playwright rows keep cell hyperlinks under this key


class MappingError(ValueError):
    """A required field is not present where the source configuration says it is."""


def get_path(obj: Any, path: str) -> Any:
    if path == "":
        return obj
    current = obj
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.lstrip("-").isdigit() and -len(current) <= int(part) < len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def lookup(record: dict[str, Any], location: str, mode: LookupMode) -> Any:
    if mode == "key":
        return record.get(location, MISSING)
    return get_path(record, location)


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        return None
    return value


def parse_datetime(value: Any, formats: list[str], tz: ZoneInfo | timezone) -> datetime | None:
    """Parse ISO-8601, epoch seconds/milliseconds, or one of the configured formats."""
    value = _blank_to_none(value)
    if value is None:
        return None
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000 if value > 100_000_000_000 else value
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    elif isinstance(value, str):
        text = value.strip()
        for fmt in formats:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"unrecognised date/time value {text!r}") from exc
    else:
        raise ValueError(f"unsupported date/time value of type {type(value).__name__}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed


def _map_attachments(record: dict[str, Any], mapping: FieldMapping, mode: LookupMode) -> list[Any]:
    if not mapping.attachments:
        return []
    if mode == "key":
        links = record.get(LINKS_KEY, {}).get(mapping.attachments, [])
        return list(links)
    value = get_path(record, mapping.attachments)
    if value is MISSING or value is None:
        return []
    if not isinstance(value, list):
        # Surfaced by validation as an attachment-structure error.
        return value  # type: ignore[return-value]
    if not mapping.attachment_fields:
        return value
    mapped = []
    for element in value:
        if not isinstance(element, dict):
            mapped.append(element)
            continue
        item = {}
        for attr, path in mapping.attachment_fields.items():
            found = get_path(element, path)
            item[attr] = None if found is MISSING else found
        mapped.append(item)
    return mapped


def map_record(
    record: dict[str, Any],
    mapping: FieldMapping,
    *,
    mode: LookupMode,
    date_formats: list[str],
    tz: ZoneInfo | timezone,
) -> dict[str, Any]:
    """Return canonical fields. Raises MappingError / ValueError on structural problems."""
    out: dict[str, Any] = {}
    for name in ("inspection_number", "serial_number", "status"):
        location = getattr(mapping, name)
        value = lookup(record, location, mode)
        if value is MISSING:
            raise MappingError(f"{name}: not found at {location!r}")
        out[name] = value

    if mapping.inspection_date:
        value = lookup(record, mapping.inspection_date, mode)
        out["inspection_date"] = None if value is MISSING else parse_datetime(value, date_formats, tz)

    if mapping.summary:
        summary = {}
        for counter, location in mapping.summary.items():
            value = _blank_to_none(lookup(record, location, mode))
            if value is not MISSING and value is not None:
                summary[counter] = value.replace(",", "").strip() if isinstance(value, str) else value
        out["summary"] = summary or None

    out["attachments"] = _map_attachments(record, mapping, mode)
    return out
