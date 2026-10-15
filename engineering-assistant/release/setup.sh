#!/usr/bin/env sh
# One-time setup (Linux / macOS): creates .venv and installs the packages.
set -eu
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required (set PYTHON=/path/to/python3 to choose one)."; exit 1; }
"$PY" -m eka.assemble models || {
  echo "The search model is incomplete: extract every ...-model-partXofY.zip into this folder and run setup again."; exit 1; }
[ -x .venv/bin/python ] || "$PY" -m venv .venv
SRC=""
if [ -d wheels ]; then
  echo "Installing from the offline packages in ./wheels"
  SRC="--no-index --find-links wheels"
else
  .venv/bin/python -m pip install --upgrade pip
fi
# the required packages first: the assistant runs with these alone
.venv/bin/python -m pip install $SRC -r requirements.txt
# semantic search is optional: without it, search is keyword-only
if .venv/bin/python -m pip install $SRC -r requirements-optional.txt; then
  .venv/bin/python -m eka.fetch_model || true
else
  echo "NOTE: semantic search could not be installed for this Python; keyword search is used."
  echo "For better search use Python 3.10-3.14: PYTHON=python3.13 ./setup.sh (after deleting .venv)."
fi
.venv/bin/python -m eka.check
echo
echo "Setup finished. Run ./start.sh"
