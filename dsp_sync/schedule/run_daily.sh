#!/usr/bin/env bash
# Daily DSP -> Snowflake run. Example crontab entry (06:00 every day):
#   0 6 * * * /opt/dsp_sync/schedule/run_daily.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python main.py run
