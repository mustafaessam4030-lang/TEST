#!/usr/bin/env sh
cd "$(dirname "$0")"
if [ -f data/server.pid ] && kill "$(cat data/server.pid)" 2>/dev/null; then
  i=0; while [ -f data/server.pid ] && [ "$i" -lt 40 ]; do i=$((i + 1)); sleep 0.25; done
  rm -f data/server.pid; echo "Stopped."
else
  rm -f data/server.pid; echo "The assistant is not running."
fi
