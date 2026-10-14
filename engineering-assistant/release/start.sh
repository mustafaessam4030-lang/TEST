#!/usr/bin/env sh
# Run the assistant (Linux / macOS). Foreground by default; --background to detach.
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if [ "${1:-}" = "--background" ]; then
  mkdir -p data
  nohup .venv/bin/python -m eka >>data/server.log 2>&1 &
  i=0
  until [ -f data/server.pid ] && curl -sf "http://127.0.0.1:${EKA_PORT:-8765}/api/health" >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -gt 120 ] && { echo "Did not start; see data/server.log"; exit 1; }; sleep 0.5
  done
  echo "Running at http://127.0.0.1:${EKA_PORT:-8765} (log: data/server.log). Stop with ./stop.sh"
else
  echo "Engineering Knowledge Assistant: http://127.0.0.1:${EKA_PORT:-8765}  (Ctrl+C to stop)"
  exec .venv/bin/python -m eka
fi
