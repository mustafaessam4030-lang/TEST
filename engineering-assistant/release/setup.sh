#!/usr/bin/env sh
# One-time setup (Linux / macOS): creates .venv and installs the packages.
set -eu
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required (set PYTHON=/path/to/python3 to choose one)."; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
if [ -d wheels ]; then
  echo "Installing from the offline packages in ./wheels"
  .venv/bin/python -m pip install --no-index --find-links wheels -r requirements.txt -r requirements-optional.txt
else
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt -r requirements-optional.txt
fi
.venv/bin/python -m eka.check
echo
echo "Setup finished. Run ./start.sh"
