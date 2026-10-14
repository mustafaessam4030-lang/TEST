#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "Run ./setup.sh first."; exit 1; }
if [ -d wheels ]; then .venv/bin/python -m pip install -q --no-index --find-links wheels -r requirements-dev.txt
else .venv/bin/python -m pip install -q -r requirements-dev.txt; fi
EKA_TEST_MODEL_DIR="$(pwd)/models" .venv/bin/python -m pytest "$@"
