@echo off
REM VELA AI local - double-click to start. Change the models here if you use others.
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  echo First start: installing Python packages...
  py -3 -m venv .venv || python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r local\requirements.txt
)
set VELA_OLLAMA_MODEL=qwen2.5:14b
set VELA_VISION_MODEL=qwen2.5vl:7b
".venv\Scripts\python.exe" -m streamlit run local\vela_local.py --server.headless false
