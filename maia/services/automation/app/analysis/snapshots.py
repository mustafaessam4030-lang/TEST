"""Verified SIS results, read from the local store. Read-only, never SIS itself.

The store writes one JSON file per successful retrieval:

    logs/sis-results/<SERIAL>_<RUN_ID>.json     store_format "maia.local-json/1"

Each file is a *snapshot*. Several snapshots of one serial are its history.

Only verified Caterpillar SIS results are analysed: the canonical `record`
must be present, name the serial, come from `cat_sis`, and not be a NOT_FOUND
marker. The offline test fixture is excluded unless asked for explicitly. A
file that cannot be parsed is reported as an issue, never guessed at.

`SnapshotSource` is the seam for later: a Snowflake-backed source only has to
implement `serials()` and `snapshots(serial)`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

VERIFIED_SOURCES = ("cat_sis",)


@dataclass(frozen=True)
class Snapshot:
    serial: str
    run_id: str
    retrieved_at: datetime | None
    file: str
    source_system: str
    source_label: str
    record: dict[str, Any]
    document: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def retrieved_at_iso(self) -> str | None:
        return self.retrieved_at.isoformat() if self.retrieved_at else None

    @property
    def parts_data(self) -> dict[str, Any] | None:
        pd = self.record.get("parts_data")
        if pd is None:
            pd = self.document.get("parts_data")
        return pd if isinstance(pd, dict) else None

    @property
    def metadata(self) -> dict[str, Any]:
        d = self.document
        return {"selector_version": d.get("selector_version"),
                "schema_version": self.record.get("schema_version"),
                "final_url": d.get("final_url") or self.record.get("source_url"),
                "page_title": d.get("page_title"),
                "extraction_status": d.get("extraction_status"),
                "data_hash": self.record.get("data_hash") or d.get("data_hash")}


class SnapshotSource(Protocol):
    def serials(self) -> list[str]: ...

    def snapshots(self, serial: str) -> list[Snapshot]: ...


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def snapshot_from_document(doc: Any, file: str, *, include_fixture: bool = False,
                           include_samples: bool = False) -> tuple[Snapshot | None, str | None]:
    """(snapshot, None) or (None, why it was not accepted)."""
    if not isinstance(doc, dict):
        return None, "not a JSON object"
    if doc.get("sample") and not include_samples:
        # A demo/test document is never analysed as a real SIS retrieval.
        return None, "a SAMPLE document, not a real SIS retrieval"
    record = doc.get("record")
    if not isinstance(record, dict) or not record.get("serial_number"):
        return None, "no verified record in the file"
    source_system = str(record.get("source_system") or doc.get("source_system") or "")
    if source_system not in VERIFIED_SOURCES and not include_fixture:
        return None, f"source '{source_system}' is not Caterpillar SIS"
    if str(record.get("status") or "") == "NOT_FOUND":
        return None, "a not-found marker, not a result"
    return Snapshot(
        serial=str(record["serial_number"]).upper(),
        run_id=str(doc.get("run_id") or record.get("automation_run_id") or "unknown"),
        retrieved_at=_parse_time(record.get("retrieved_at") or doc.get("retrieved_at")),
        file=file, source_system=source_system,
        source_label=str(doc.get("source") or "Caterpillar SIS"),
        record=record, document=doc), None


class LocalStoreSnapshots:
    """Reads `<root>/<SERIAL>_*.json`. Never writes, never contacts SIS."""

    def __init__(self, root: str | Path, *, include_fixture: bool = False,
                 include_samples: bool = False) -> None:
        self.root = Path(root)
        self.include_fixture = include_fixture
        self.include_samples = include_samples
        #: file → why it was skipped (corrupted, not SIS, …), for the report
        self.issues: dict[str, str] = {}

    def _files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.glob("*.json")
                      if not p.name.startswith("_") and "_" in p.stem)

    def _load(self, path: Path) -> Snapshot | None:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.issues[path.name] = f"unreadable: {type(exc).__name__}"
            return None
        snap, why = snapshot_from_document(doc, path.name,
                                           include_fixture=self.include_fixture,
                                           include_samples=self.include_samples)
        if snap is None:
            self.issues[path.name] = why or "rejected"
        return snap

    def serials(self) -> list[str]:
        out = set()
        for path in self._files():
            snap = self._load(path)
            if snap:
                out.add(snap.serial)
        return sorted(out)

    def snapshots(self, serial: str) -> list[Snapshot]:
        """Oldest first. Only files whose record names this serial."""
        serial = serial.upper()
        prefix = f"{serial}_"
        snaps = [s for p in self._files() if p.name.upper().startswith(prefix)
                 for s in [self._load(p)] if s is not None and s.serial == serial]
        return sorted(snaps, key=lambda s: (s.retrieved_at or datetime.min.replace(
            tzinfo=timezone.utc), s.file))

    def issues_for(self, serial: str) -> dict[str, str]:
        prefix = f"{serial.upper()}_"
        return {f: why for f, why in sorted(self.issues.items()) if f.upper().startswith(prefix)}


class InMemorySnapshots:
    """For tests and for callers that already hold documents."""

    def __init__(self, documents: list[tuple[str, dict[str, Any]]], *,
                 include_fixture: bool = False, include_samples: bool = True) -> None:
        self.issues: dict[str, str] = {}
        self._snaps: list[Snapshot] = []
        for name, doc in documents:
            snap, why = snapshot_from_document(doc, name, include_fixture=include_fixture,
                                               include_samples=include_samples)
            if snap:
                self._snaps.append(snap)
            else:
                self.issues[name] = why or "rejected"

    def serials(self) -> list[str]:
        return sorted({s.serial for s in self._snaps})

    def snapshots(self, serial: str) -> list[Snapshot]:
        return sorted((s for s in self._snaps if s.serial == serial.upper()),
                      key=lambda s: (s.retrieved_at or datetime.min.replace(tzinfo=timezone.utc),
                                     s.file))

    def issues_for(self, serial: str) -> dict[str, str]:
        return dict(self.issues)
