#!/usr/bin/env bash
# VELA AI local - run:  bash local/start_vela_local.sh
set -e
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
  echo "First start: installing Python packages..."
  python3 -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r local/requirements.txt
fi
export VELA_OLLAMA_MODEL="${VELA_OLLAMA_MODEL:-qwen2.5:14b}"
export VELA_VISION_MODEL="${VELA_VISION_MODEL-qwen2.5vl:7b}"
exec .venv/bin/python -m streamlit run local/vela_local.py
