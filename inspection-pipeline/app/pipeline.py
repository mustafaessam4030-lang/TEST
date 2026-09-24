"""Pipeline orchestration: collect -> validate -> RAW -> MERGE -> audit.

Contains no scheduling logic: it performs exactly one run when called and
keeps no state between runs (every run gets a fresh run_id).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.collectors import InspectionCollector, build_collector
from app.config import Settings
from app.diagnostics import Diagnostics
from app.logger import redact, set_run_id
from app.models import PipelineRun, RunStatus
from app.services.validation import ValidationOutcome, validate_records
from app.source_config import SourceConfig, load_source_config
from app.warehouse.connection import connect
from app.warehouse.repository import InspectionRepository

logger = logging.getLogger(__name__)


@dataclass
class PipelineDeps:
    """Seams for tests; production uses the defaults."""

    load_source: Callable[[Path], SourceConfig] = load_source_config
    build_collector: Callable[[Settings, SourceConfig, Diagnostics], InspectionCollector] = build_collector
    connect: Callable[[Settings, str], Any] = connect
    repository: Callable[..., InspectionRepository] = InspectionRepository


def _error_text(exc: BaseException) -> str:
    return redact(f"{type(exc).__name__}: {exc}")[:4000]


def _decide_status(run: PipelineRun, min_expected: int, warnings: list[str]) -> None:
    problems = list(warnings)
    if run.records_found < min_expected:
        run.status = RunStatus.FAILED
        problems.insert(0, f"expected at least {min_expected} records, found {run.records_found}")
    elif run.records_found > 0 and run.records_valid == 0:
        run.status = RunStatus.FAILED
        problems.insert(0, "no record passed validation")
    elif run.records_failed > 0 or warnings:
        run.status = RunStatus.PARTIAL_SUCCESS
    else:
        run.status = RunStatus.SUCCESS
    if run.records_failed:
        problems.append(f"{run.records_failed} record(s) failed validation (see VALIDATION_ERRORS)")
    run.error_message = "; ".join(problems) or None


def _write_dry_run(path: Path, outcome: ValidationOutcome) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for inspection in outcome.valid:
            fh.write(json.dumps({"type": "inspection", **inspection.model_dump(mode="json"),
                                 "record_hash": inspection.record_hash}, ensure_ascii=False) + "\n")
        for failure in outcome.failures:
            fh.write(json.dumps({"type": "failure", **failure.model_dump(mode="json")}, ensure_ascii=False) + "\n")
    logger.info("Dry-run output written to %s", path)


async def run_pipeline(
    settings: Settings,
    *,
    serial_number: str | None = None,
    inspection_number: str | None = None,
    skip_if_succeeded_today: bool = False,
    dry_run: bool = False,
    output_path: Path | None = None,
    deps: PipelineDeps | None = None,
) -> PipelineRun | None:
    deps = deps or PipelineDeps()
    filters = {k: v for k, v in (("serial_number", serial_number), ("inspection_number", inspection_number)) if v}
    run = PipelineRun(source=settings.source_name, collector_type=settings.collector_type, filters=filters)
    run_id = str(run.run_id)
    set_run_id(run_id)
    logger.info("Starting inspection collection (source=%s, collector=%s, filters=%s, dry_run=%s)",
                run.source, run.collector_type, filters or "none", dry_run)

    repo: InspectionRepository | None = None
    conn: Any = None
    started = False
    try:
        if not dry_run:
            conn = deps.connect(settings, run_id)
            repo = deps.repository(conn, batch_size=settings.snowflake_insert_batch_size)
            if skip_if_succeeded_today and repo.has_successful_run_on(run.source, datetime.now(timezone.utc)):
                logger.info("A successful run already exists today for source %s; skipping", run.source)
                return None
            repo.start_run(run)
            started = True

        source_cfg = deps.load_source(settings.source_config_path)
        collector = deps.build_collector(settings, source_cfg, Diagnostics(settings.diagnostics_dir, run_id))
        collected_at = datetime.now(timezone.utc)
        result = await collector.collect(serial_number=serial_number, inspection_number=inspection_number)
        run.records_found = len(result.records)
        run.expected_total = result.expected_total
        logger.info("Retrieved %d records in %d page(s)", run.records_found, result.pages_fetched)

        warnings = list(result.warnings)
        if result.expected_total is not None and result.expected_total != run.records_found:
            warnings.append(f"source reported {result.expected_total} records but {run.records_found} were collected")

        outcome = validate_records(
            result.records,
            mapping=collector.field_mapping,
            mode=collector.lookup_mode,
            run_id=run.run_id,
            source=run.source,
            collector_type=run.collector_type,
            collected_at=collected_at,
            date_formats=source_cfg.date_formats,
            tz=ZoneInfo(settings.source_timezone),
            allowed_statuses=source_cfg.allowed_statuses,
        )
        run.records_valid = len(outcome.valid)
        run.records_failed = len(outcome.failures)
        run.records_duplicate = outcome.duplicates

        if repo is not None:
            repo.record_failures(outcome.failures)
            stats = repo.load(run.run_id, outcome.valid)
            run.records_loaded = stats.loaded
            run.records_inserted, run.records_updated, run.records_unchanged = (
                stats.inserted, stats.updated, stats.unchanged,
            )
        elif output_path is not None:
            _write_dry_run(output_path, outcome)

        _decide_status(run, settings.min_expected_records, warnings)
    except Exception as exc:  # noqa: BLE001 - every failure must be recorded on the run
        run.status = RunStatus.FAILED
        run.error_message = _error_text(exc)
        logger.error("Pipeline failed: %s", run.error_message, exc_info=logger.isEnabledFor(logging.DEBUG))
    finally:
        run.end_time = datetime.now(timezone.utc)
        if repo is not None and started:
            try:
                repo.finish_run(run)
            except Exception as exc:  # noqa: BLE001
                logger.error("Could not record run outcome in PIPELINE_RUNS: %s", _error_text(exc))
                run.status = RunStatus.FAILED
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    level = logging.INFO if run.status == RunStatus.SUCCESS else logging.WARNING
    if run.status == RunStatus.FAILED:
        level = logging.ERROR
    logger.log(level, "Pipeline finished with status %s: %s", run.status.value, json.dumps(run.summary()))
    return run
