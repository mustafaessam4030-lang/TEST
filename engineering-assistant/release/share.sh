#!/usr/bin/env sh
# Share Luma with colleagues on the company network (Linux / macOS). Ctrl+C stops it.
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
echo "Luma - share with the team. Colleagues open the link printed below. Ctrl+C to stop."
exec .venv/bin/python -m eka.share
