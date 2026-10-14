#!/usr/bin/env sh
# Start, stop or check the assistant in the background.  Usage: scripts/eka.sh start|stop|status
set -eu
cd "$(dirname "$0")/.."
DATA="${EKA_DATA_DIR:-data}"
PIDFILE="$DATA/server.pid"
PORT="${EKA_PORT:-8765}"
mkdir -p "$DATA"

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  start)
    if running; then echo "already running (pid $(cat "$PIDFILE"))"; exit 0; fi
    nohup python3 -m eka >>"$DATA/server.log" 2>&1 &
    echo $! >"$PIDFILE"
    i=0
    until curl -sf "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; do
      i=$((i + 1))
      if [ "$i" -gt 60 ] || ! running; then echo "failed to start; see $DATA/server.log"; exit 1; fi
      sleep 0.5
    done
    echo "running on http://127.0.0.1:$PORT (pid $(cat "$PIDFILE"))"
    ;;
  stop)
    if running; then kill "$(cat "$PIDFILE")"; rm -f "$PIDFILE"; echo "stopped"; else rm -f "$PIDFILE"; echo "not running"; fi
    ;;
  status)
    if running; then echo "running (pid $(cat "$PIDFILE"))"; curl -s "http://127.0.0.1:$PORT/api/health"; echo; else echo "not running"; exit 1; fi
    ;;
  *)
    echo "usage: $0 start|stop|status"; exit 2
    ;;
esac
