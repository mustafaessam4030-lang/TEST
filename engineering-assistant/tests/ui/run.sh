#!/usr/bin/env sh
# Run the browser end-to-end suite against fresh servers.
#
#   tests/ui/run.sh <output_dir>
#
# Starts (1) a real server on :8765 with the sample PDFs, (2) the MOCK slow
# model (tests/ui/slow_ollama.py) on :8799 and (3) a server on :8766 that uses
# it, runs tests/ui/e2e.mjs, then stops everything. Requires Node with the
# `playwright` package resolvable from tests/ui (or set PLAYWRIGHT_DIR to a
# directory whose node_modules contains it) and a Chromium.
set -eu
cd "$(dirname "$0")/../.."
OUT="${1:-/tmp/eka-ui}"
WORK="$(mktemp -d)"
trap 'EKA_DATA_DIR="$WORK/real" scripts/eka.sh stop >/dev/null 2>&1 || true;
      EKA_DATA_DIR="$WORK/slow" scripts/eka.sh stop >/dev/null 2>&1 || true;
      [ -n "${MOCK_PID:-}" ] && kill "$MOCK_PID" 2>/dev/null || true' EXIT

python3 tests/ui/make_ui_fixtures.py "$WORK/fixtures" >/dev/null
python3 tests/ui/slow_ollama.py 8799 6 & MOCK_PID=$!
mkdir -p "$WORK/pdfs"
EKA_CONFIG=/dev/null EKA_DATA_DIR="$WORK/real" EKA_PORT=8765 EKA_INBOX_DIR="$WORK/pdfs" EKA_INBOX_SETTLE_S=1 \
  scripts/eka.sh start
EKA_CONFIG=/dev/null EKA_DATA_DIR="$WORK/slow" EKA_PORT=8766 EKA_INBOX_DIR=off EKA_GENERATOR=ollama \
  EKA_OLLAMA_URL=http://127.0.0.1:8799 EKA_LLM_TIMEOUT_S=30 scripts/eka.sh start
for port in 8765 8766; do
  for f in samples/*.pdf; do
    curl -s -o /dev/null -X POST "http://127.0.0.1:$port/api/documents?filename=$(basename "$f")" --data-binary @"$f"
  done
done
for i in $(seq 1 120); do
  done_count=0
  for port in 8765 8766; do
    busy=$(curl -s "http://127.0.0.1:$port/api/documents" | python3 -c \
      "import json,sys; print(sum(d['status'] in ('queued','processing') for d in json.load(sys.stdin)['documents']))")
    [ "$busy" = 0 ] && done_count=$((done_count + 1))
  done
  [ "$done_count" = 2 ] && break
  sleep 1
done
SCRIPT="$(pwd)/tests/ui/e2e.mjs"
if [ -n "${PLAYWRIGHT_DIR:-}" ]; then cp "$SCRIPT" "$PLAYWRIGHT_DIR/e2e.mjs"; SCRIPT="$PLAYWRIGHT_DIR/e2e.mjs"; fi
BASE=http://127.0.0.1:8765 SLOW=http://127.0.0.1:8766 FIXTURES="$WORK/fixtures" INBOX="$WORK/pdfs" OUT="$OUT" node "$SCRIPT"
