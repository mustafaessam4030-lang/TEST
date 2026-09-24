#!/usr/bin/env bash
# Daily entry point for cron / any Linux scheduler. Contains no schedule itself.
#
#   15 5 * * *  /opt/inspection-pipeline/scripts/run_daily.sh >> /var/log/inspection-pipeline.log 2>&1
#
# * flock prevents overlapping runs if a previous run is still going.
# * --skip-if-succeeded-today makes scheduler retries/double-fires harmless.
# * Exit code: 0 success/skipped, 2 partial success, 1 failed (alert on != 0).
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-.venv/bin/python}"
exec flock -n /tmp/inspection-pipeline.lock "$PYTHON" -m app.collect --skip-if-succeeded-today "$@"
