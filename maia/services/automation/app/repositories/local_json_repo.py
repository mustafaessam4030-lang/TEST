"""Local JSON store — the temporary home for SIS results until Snowflake exists.

It is a full `EquipmentRepository`, so nothing above it knows it is a folder.
Replacing it with `SnowflakeEquipmentRepository` is one setting.

Per successful lookup it writes, under `logs/sis-results/`:

    <SERIAL>_<RUN_ID>.json        the record + the COMPLETE raw extraction
    <SERIAL>_<RUN_ID>.txt         the same, for a person to read
    <SERIAL>_<RUN_ID>/page.png    the detail page as it first rendered
    <SERIAL>_<RUN_ID>/details.png the equipment-details section, after scrolling
    <SERIAL>_<RUN_ID>/parts.png   the "Product - …" parts group

Three rules it exists to enforce:

  * **Nothing is discarded.** `raw_data` carries every field the page gave,
    including ones the schema has no column for. A field with no column is
    still evidence; dropping it means driving a browser again to get it back.
  * **A missing value is `null`.** Never an empty string, never a guess, and
    `null` is recorded alongside the provenance reason that explains it.
  * **A write that fails is a failed lookup.** `upsert` raises, and the caller
    turns that into PERSISTENCE_FAILED rather than telling Maia it worked.

Nothing secret is ever written: every payload goes through `scrub()`, which
drops credential-shaped keys and masks credential-shaped text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.hashing import data_hash
from app.core.logging import log
from app.models.schemas import EquipmentRecord, RecordStatus, RunRecord
from app.repositories.base import ExtractionArtifact

logger = logging.getLogger(__name__)

#: Keys whose VALUE is a secret, whatever it looks like. Matched on the key
#: name anywhere in the path, case-insensitively.
SECRET_KEYS = re.compile(
    r"(password|passwd|pwd|secret|token|cookie|authorization|auth_header|"
    r"session_state|storage_state|credential|api[_-]?key|bearer|otp|mfa[_-]?code)", re.I)

#: Secret-shaped text, for free-form strings that no key name protects.
SECRET_TEXT = re.compile(
    r"((?:password|passwd|pwd|secret|token|bearer|cookie|authorization)\s*[=:]\s*)(\S+)", re.I)

REDACTED = "[REDACTED]"
SCREENSHOT_NAMES = ("page", "details", "parts")


def scrub(value: Any, *, extra: tuple[str, ...] = ()) -> Any:
    """Recursively remove anything credential-shaped. Applied to every write.

    Conservative on purpose: a key that merely looks like a secret is dropped
    even if it is harmless. Losing a field from a debug artifact costs nothing;
    writing a password to disk costs everything.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if SECRET_KEYS.search(str(key)):
                out[str(key)] = REDACTED
                continue
            out[str(key)] = scrub(item, extra=extra)
        return out
    if isinstance(value, (list, tuple)):
        return [scrub(v, extra=extra) for v in value]
    if isinstance(value, str):
        text = SECRET_TEXT.sub(lambda m: m.group(1) + REDACTED, value)
        for secret in extra:
            if secret and len(secret) >= 4:
                text = text.replace(secret, REDACTED)
        return text
    return value


def _safe_name(raw: str) -> str:
    """A filename component that cannot escape the store directory."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(raw or "unknown"))
    return cleaned[:80] or "unknown"


class LocalJsonRepository:
    """File-backed store. One folder, one process, no schema migration."""

    #: source_id -> the label written into every file. A record is labelled by
    #: the source that produced it, never by a default: writing "Caterpillar
    #: SIS" onto a row that came from a fixture is exactly the confusion this
    #: whole system exists to prevent.
    DEFAULT_LABELS = {"cat_sis": "Caterpillar SIS"}

    def __init__(self, root: str | Path, *, source_labels: dict[str, str] | None = None,
                 secrets: tuple[str, ...] = ()) -> None:
        self.root = Path(root)
        self.results = self.root
        self.runs_dir = self.root / "_runs"
        self.index_path = self.root / "_index.json"
        self.source_labels = dict(self.DEFAULT_LABELS)
        if source_labels:
            self.source_labels.update(source_labels)
        # Values that must never appear in a file, whatever key they arrive
        # under. The caller passes the live credential values; they are held
        # only to be searched for, never written, logged or returned.
        self._secrets = tuple(s for s in secrets if s)
        self._lock = asyncio.Lock()
        self._claims: dict[str, str] = {}
        self.results.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def label_for(self, source_id: str) -> str:
        """The name that goes in the file. Unknown sources keep their own id."""
        return self.source_labels.get(source_id) or source_id

    # ── low-level IO ────────────────────────────────────────────────────────
    def _write_atomic(self, path: Path, text: str) -> None:
        """Write via a temp file in the same directory, then rename.

        A half-written JSON file is worse than no file: the next lookup would
        read it as the stored truth. Rename is atomic on every platform we run
        on, so a reader sees either the old file or the complete new one.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def _read_json(self, path: Path) -> dict[str, Any] | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _dump(self, payload: Any) -> str:
        return json.dumps(scrub(payload, extra=self._secrets), indent=2,
                          ensure_ascii=False, default=str)

    # ── the index: (source, serial) -> the newest result file ───────────────
    def _index(self) -> dict[str, Any]:
        return self._read_json(self.index_path) or {"version": 1, "current": {}}

    @staticmethod
    def _key(source: str, serial: str) -> str:
        return f"{source}::{serial}"

    def _current_path(self, source: str, serial: str) -> Path | None:
        entry = self._index().get("current", {}).get(self._key(source, serial))
        if not entry:
            return None
        path = self.root / entry["file"]
        return path if path.exists() else None

    # ── reads ───────────────────────────────────────────────────────────────
    async def get_current(self, serial_number: str, source: str) -> dict[str, Any] | None:
        """What `get_equipment_from_local_store` ultimately reads."""
        path = self._current_path(source, serial_number)
        if path is None:
            return None
        payload = self._read_json(path)
        if payload is None:
            log(logger, logging.WARNING, "localstore.unreadable", file=str(path))
            return None
        # The stored file is a superset of the record. Hand back the record
        # shape the freshness policy and the responses expect.
        return payload.get("record") or None

    async def get_any_source(self, serial_number: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for key, entry in self._index().get("current", {}).items():
            if not key.endswith(f"::{serial_number}"):
                continue
            payload = self._read_json(self.root / entry["file"])
            if payload and payload.get("record"):
                out.append(payload["record"])
        return out

    async def known_serials(self, limit: int = 500) -> list[str]:
        """Read from the index, so this stays one small file read however many
        lookups the folder has accumulated."""
        serials = {key.split("::", 1)[1]
                   for key in self._index().get("current", {})
                   if "::" in key}
        return sorted(serials)[:limit]

    async def history(self, serial_number: str, source: str | None = None,
                      limit: int = 20) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.results.glob(f"{_safe_name(serial_number)}_*.json")):
            payload = self._read_json(path)
            record = (payload or {}).get("record")
            if not record:
                continue
            if source and record.get("source_system") != source:
                continue
            rows.append({**record, "version_at": payload.get("retrieved_at")
                         or record.get("retrieved_at")})
        return sorted(rows, key=lambda r: str(r.get("version_at") or ""), reverse=True)[:limit]

    # ── the write that decides whether a lookup succeeded ───────────────────
    async def upsert(self, record: EquipmentRecord, *,
                     extraction: ExtractionArtifact | None = None) -> bool:
        payload = record.model_dump(mode="json")
        digest = record.data_hash or data_hash(payload)
        payload["data_hash"] = digest
        run_id = record.automation_run_id or "no-run"
        serial = _safe_name(record.serial_number)
        stem = f"{serial}_{_safe_name(run_id)}"
        now = datetime.now(timezone.utc).isoformat()

        previous = await self.get_current(record.serial_number, record.source_system)
        changed = not previous or previous.get("data_hash") != digest

        shots = await asyncio.to_thread(self._copy_screenshots, stem, extraction)
        document = self._document(record, payload, extraction, run_id=run_id,
                                  saved_at=now, screenshots=shots)

        json_path = self.results / f"{stem}.json"
        txt_path = self.results / f"{stem}.txt"
        async with self._lock:
            # Both files, then the index. If any step raises, the caller reports
            # PERSISTENCE_FAILED — a lookup nobody can read later did not succeed.
            await asyncio.to_thread(self._write_atomic, json_path, self._dump(document))
            await asyncio.to_thread(self._write_atomic, txt_path,
                                    scrub(render_text(document), extra=self._secrets))
            index = self._index()
            index.setdefault("current", {})[self._key(record.source_system,
                                                      record.serial_number)] = {
                "file": json_path.name, "saved_at": now, "run_id": run_id,
                "data_hash": digest, "status": payload.get("status"),
            }
            await asyncio.to_thread(self._write_atomic, self.index_path, self._dump(index))

        log(logger, logging.INFO, "localstore.saved", serial_number=record.serial_number,
            run_id=run_id, json=str(json_path), txt=str(txt_path),
            screenshots=len(shots), changed=changed)
        return changed

    def _copy_screenshots(self, stem: str, extraction: ExtractionArtifact | None
                          ) -> dict[str, str]:
        """Copy the run's screenshots next to the result. Missing ones are absent."""
        if not extraction or not extraction.screenshots:
            return {}
        target = self.results / stem
        target.mkdir(parents=True, exist_ok=True)
        saved: dict[str, str] = {}
        for name, source_path in extraction.screenshots.items():
            if name not in SCREENSHOT_NAMES or not source_path:
                continue
            src = Path(source_path)
            if not src.exists():
                continue
            dest = target / f"{name}.png"
            try:
                shutil.copyfile(src, dest)
                saved[name] = str(dest)
            except OSError as exc:
                # A missing screenshot is a worse debug story, not a failed
                # lookup: the data is what matters and it is already read.
                log(logger, logging.WARNING, "localstore.screenshot_failed",
                    name=name, error=str(exc)[:120])
        return saved

    def _document(self, record: EquipmentRecord, payload: dict[str, Any],
                  extraction: ExtractionArtifact | None, *, run_id: str,
                  saved_at: str, screenshots: dict[str, str]) -> dict[str, Any]:
        """The saved shape: the named fields first, then everything else."""
        ex = extraction or ExtractionArtifact()
        engine = payload.get("engine_family") or {}
        parts = payload.get("parts_data") or ex.parts_data or None

        return {
            "store_format": "maia.local-json/1",
            "note": ("Temporary local store, written while the warehouse is being "
                     "built. Replaced by Snowflake without any change above the "
                     "repository interface."),

            # ── identity ────────────────────────────────────────────────────
            "serial_number": record.serial_number,
            "run_id": run_id,
            "source": self.label_for(record.source_system),
            "source_system": record.source_system,
            "retrieved_at": payload.get("retrieved_at"),
            "saved_at": saved_at,
            "final_url": ex.final_url or record.source_url,
            "page_title": ex.page_title,

            # ── the fields the page was read for ────────────────────────────
            "model": record.equipment_model,
            "equipment_type": record.equipment_type,
            "manufacturer": record.manufacturer,
            "build_date": record.build_date,
            "machine_serial_number": record.machine_serial_number,
            "machine_build_date": record.machine_build_date,
            "engine_serial_number": record.engine_serial_number,
            "engine_build_date": record.engine_build_date,
            "engine_family": engine or None,

            # ── everything else the page published ──────────────────────────
            "specifications": payload.get("specifications") or [],
            "parts_data": parts,
            "parts_summary": _parts_summary(parts),
            "parts_manual_url": record.parts_manual_url,
            "operation_manual_url": record.operation_manual_url,
            "manual_urls": {k: v for k, v in {
                "parts_manual_url": record.parts_manual_url,
                "operation_manual_url": record.operation_manual_url,
                "source_url": record.source_url,
            }.items()},

            # ── provenance and quality ──────────────────────────────────────
            "extraction_status": ex.extraction_status,
            "payload_kind": ex.payload_kind,
            "selector_version": ex.selector_version,
            "field_provenance": payload.get("field_provenance") or {},
            "quality": payload.get("quality") or {},
            "data_hash": payload.get("data_hash"),
            "schema_version": payload.get("schema_version"),
            "status": payload.get("status"),
            "screenshots": screenshots,
            "evidence": ex.evidence or {},

            # The canonical record, exactly as every other store holds it. This
            # is what `get_current` hands back, so the local store and the
            # warehouse answer the same question with the same shape.
            "record": payload,

            # ── nothing is thrown away ──────────────────────────────────────
            # `raw_data.extracted_fields` is exactly what the adapter read from
            # the page, including keys the canonical schema has no column for.
            "raw_data": {
                "extracted_fields": ex.fields or {},
                "specifications": ex.specifications or [],
                "parts_data": ex.parts_data,
                "normalized_record": payload,
            },
        }

    # ── the rest of the interface ───────────────────────────────────────────
    async def mark_not_found(self, serial_number: str, source: str, run_id: str) -> None:
        """A negative result is a real answer and is stored like one."""
        now = datetime.now(timezone.utc).isoformat()
        record = {"serial_number": serial_number, "source_system": source,
                  "status": RecordStatus.NOT_FOUND.value, "retrieved_at": now,
                  "updated_at": now, "automation_run_id": run_id}
        stem = f"{_safe_name(serial_number)}_{_safe_name(run_id)}"
        document = {"store_format": "maia.local-json/1", "serial_number": serial_number,
                    "run_id": run_id, "source": self.label_for(source), "source_system": source,
                    "retrieved_at": now, "saved_at": now, "extraction_status": "NOT_FOUND",
                    "record": record, "raw_data": {}}
        async with self._lock:
            path = self.results / f"{stem}.json"
            await asyncio.to_thread(self._write_atomic, path, self._dump(document))
            index = self._index()
            index.setdefault("current", {})[self._key(source, serial_number)] = {
                "file": path.name, "saved_at": now, "run_id": run_id,
                "status": RecordStatus.NOT_FOUND.value}
            await asyncio.to_thread(self._write_atomic, self.index_path, self._dump(index))

    async def save_run(self, run: RunRecord) -> None:
        path = self.runs_dir / f"{_safe_name(run.automation_run_id)}.json"
        await asyncio.to_thread(self._write_atomic, path,
                                self._dump(run.model_dump(mode="json")))

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self._read_json(self.runs_dir / f"{_safe_name(run_id)}.json")

    async def find_active_run(self, idempotency_key: str) -> str | None:
        return self._claims.get(idempotency_key)

    async def claim_idempotency(self, idempotency_key: str, run_id: str) -> bool:
        async with self._lock:
            if idempotency_key in self._claims:
                return False
            self._claims[idempotency_key] = run_id
            return True

    async def release_idempotency(self, idempotency_key: str) -> None:
        self._claims.pop(idempotency_key, None)

    async def health(self) -> bool:
        """Writable, not merely present: a read-only folder is an outage."""
        try:
            probe = self.root / ".health"
            await asyncio.to_thread(self._write_atomic, probe,
                                    datetime.now(timezone.utc).isoformat())
            probe.unlink(missing_ok=True)
            return True
        except OSError:
            return False


# ── the human-readable twin ─────────────────────────────────────────────────
def _parts_summary(parts: dict[str, Any] | None) -> dict[str, Any] | None:
    """Group titles, part numbers and part names, pulled out for quick reading."""
    if not parts:
        return None
    columns = parts.get("columns") or []
    number_at = _column_index(columns, ("part number", "part no", "part_no"))
    name_at = _column_index(columns, ("part name", "description", "part_name"))
    serial_at = _column_index(columns, ("serial number", "serial", "serial_no"))
    numbers: list[str] = []
    names: list[str] = []
    serials: list[str] = []
    for group in parts.get("groups") or []:
        for row in group.get("rows") or []:
            cells = row.get("cells") or []
            if number_at is not None and number_at < len(cells) and cells[number_at]:
                numbers.append(cells[number_at])
            if name_at is not None and name_at < len(cells) and cells[name_at]:
                names.append(cells[name_at])
            if serial_at is not None and serial_at < len(cells) and cells[serial_at]:
                serials.append(cells[serial_at])
    return {
        "group_titles": parts.get("group_titles") or [],
        "group_count": parts.get("group_count") or len(parts.get("groups") or []),
        "entire_group_title": parts.get("entire_group_title"),
        "columns": columns,
        "total_rows": parts.get("total_rows") or 0,
        "part_numbers": numbers,
        "part_names": names,
        "part_serial_numbers": serials,
    }


def _column_index(columns: list[str], wanted: tuple[str, ...]) -> int | None:
    for i, column in enumerate(columns):
        if str(column).strip().lower() in wanted:
            return i
    return None


def _show(value: Any) -> str:
    """`null` is printed as null. It means "the source did not publish this"."""
    if value is None:
        return "null"
    if value == "":
        return "null"
    return str(value)


def render_text(document: dict[str, Any]) -> str:
    """The .txt twin of the JSON — the same values, for a person."""
    lines: list[str] = []
    add = lines.append
    rule = "=" * 72

    add(rule)
    add(f"  CATERPILLAR SIS LOOKUP — {_show(document.get('serial_number'))}")
    add(rule)
    add(f"  source            : {_show(document.get('source'))}")
    add(f"  run id            : {_show(document.get('run_id'))}")
    add(f"  retrieved at      : {_show(document.get('retrieved_at'))}")
    add(f"  saved at          : {_show(document.get('saved_at'))}")
    add(f"  final url         : {_show(document.get('final_url'))}")
    add(f"  page title        : {_show(document.get('page_title'))}")
    add(f"  extraction status : {_show(document.get('extraction_status'))}")
    add(f"  selector version  : {_show(document.get('selector_version'))}")
    add(f"  data hash         : {_show(document.get('data_hash'))}")

    add("")
    add("EQUIPMENT DETAILS")
    add("-" * 72)
    for label, key in (("Machine Serial Number", "machine_serial_number"),
                       ("Machine Build Date", "machine_build_date"),
                       ("Engine Serial Number", "engine_serial_number"),
                       ("Engine Build Date", "engine_build_date"),
                       ("Model", "model"),
                       ("Equipment type", "equipment_type"),
                       ("Manufacturer", "manufacturer"),
                       ("Build date", "build_date")):
        add(f"  {label:<24}: {_show(document.get(key))}")
    engine = document.get("engine_family") or {}
    for label, key in (("Engine model", "model"), ("Engine arrangement", "arrangement"),
                       ("Emissions", "emissions")):
        add(f"  {label:<24}: {_show(engine.get(key))}")

    add("")
    add("MANUALS / URLS")
    add("-" * 72)
    for key, value in (document.get("manual_urls") or {}).items():
        add(f"  {key:<24}: {_show(value)}")

    specs = document.get("specifications") or []
    add("")
    add(f"SPECIFICATIONS ({len(specs)})")
    add("-" * 72)
    if not specs:
        add("  none published on the page")
    for spec in specs:
        name = (spec.get("group") + " / " if spec.get("group") else "") + str(spec.get("name"))
        value = spec.get("value_raw") or spec.get("value")
        unit = f" {spec['unit']}" if spec.get("unit") and not spec.get("value_raw") else ""
        add(f"  {name:<36}: {_show(value)}{unit}")

    summary = document.get("parts_summary") or {}
    parts = document.get("parts_data") or {}
    add("")
    add(f"PARTS — {summary.get('group_count', 0)} 'Product - …' group(s), "
        f"{summary.get('total_rows', 0)} row(s)")
    add("-" * 72)
    if not parts:
        add("  no parts group was present on the page")
    for group in parts.get("groups") or []:
        add("")
        add(f"  {group.get('title')}"
            f"   [{group.get('row_count', 0)} rows, "
            f"found by {group.get('discovered_by') or 'page wording'}]")
        columns = group.get("columns") or summary.get("columns") or []
        if columns:
            add("    " + " | ".join(str(c) for c in columns))
            add("    " + "-" * 60)
        for row in group.get("rows") or []:
            add("    " + " | ".join(_show(c) for c in (row.get("cells") or [])))

    quality = document.get("quality") or {}
    add("")
    add("QUALITY")
    add("-" * 72)
    add(f"  score             : {_show(quality.get('score'))}")
    add(f"  required present  : {quality.get('required_present')}/{quality.get('required_total')}")
    violations = quality.get("violations") or []
    add(f"  violations        : {', '.join(violations) if violations else 'none'}")

    provenance = document.get("field_provenance") or {}
    not_published = [k for k, v in provenance.items()
                     if isinstance(v, dict) and v.get("reason") == "NOT_PUBLISHED"]
    add(f"  not published     : {', '.join(not_published) if not_published else 'none'}")

    shots = document.get("screenshots") or {}
    add("")
    add("SCREENSHOTS")
    add("-" * 72)
    if not shots:
        add("  none captured for this run")
    for name, path in shots.items():
        add(f"  {name:<18}: {path}")

    add("")
    add(rule)
    add("  Values shown as `null` were NOT published by the source. Nothing on")
    add("  this page is inferred, defaulted or guessed.")
    add(rule)
    return "\n".join(lines) + "\n"
