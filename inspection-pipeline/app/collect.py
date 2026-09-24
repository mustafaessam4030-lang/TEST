"""Command-line entry point for one pipeline run.

    python -m app.collect                       # daily run (what the scheduler calls)
    python -m app.collect --serial-number SN123 # ad-hoc filtered run
    python -m app.collect --dry-run --output out/records.jsonl
    python -m app.collect --init-schema         # create/upgrade Snowflake objects

Exit codes: 0 SUCCESS (or skipped), 2 PARTIAL_SUCCESS, 1 FAILED / error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config import Settings
from app.errors import PipelineError
from app.logger import configure_logging, redact
from app.models import RunStatus
from app.pipeline import run_pipeline

EXIT_CODES = {RunStatus.SUCCESS: 0, RunStatus.PARTIAL_SUCCESS: 2, RunStatus.FAILED: 1}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m app.collect", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial-number", help="Only collect inspections for this serial number")
    parser.add_argument("--inspection-number", help="Only collect this inspection number")
    parser.add_argument("--init-schema", action="store_true", help="Create Snowflake tables/views and exit")
    parser.add_argument("--skip-if-succeeded-today", action="store_true",
                        help="Exit 0 without collecting if a SUCCESS run already exists today (UTC)")
    parser.add_argument("--dry-run", action="store_true", help="Collect and validate only; no Snowflake")
    parser.add_argument("--output", type=Path, help="With --dry-run: write records/failures as JSON lines")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Invalid configuration: {redact(str(exc))}", file=sys.stderr)
        return 1
    configure_logging(settings.log_level, settings.log_format)
    log = logging.getLogger("app.collect")

    if args.init_schema:
        from app.warehouse.connection import connect
        from app.warehouse.repository import InspectionRepository

        try:
            conn = connect(settings, "init-schema")
            try:
                InspectionRepository(conn).ensure_schema()
            finally:
                conn.close()
        except PipelineError as exc:
            log.error("Schema initialisation failed: %s", exc)
            return 1
        return 0

    run = asyncio.run(run_pipeline(
        settings,
        serial_number=args.serial_number,
        inspection_number=args.inspection_number,
        skip_if_succeeded_today=args.skip_if_succeeded_today,
        dry_run=args.dry_run,
        output_path=args.output,
    ))
    return 0 if run is None else EXIT_CODES[run.status]


if __name__ == "__main__":
    sys.exit(main())
