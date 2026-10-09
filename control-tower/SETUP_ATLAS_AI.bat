@echo off
rem ATLAS AI on this machine: natural answers from a local model (free, offline).
rem   1. Install Ollama first: https://ollama.com/download  (Windows installer)
rem   2. Double-click this file. It downloads the model once (about 3.3 GB).
rem   3. Start the automation as usual (update_eta.py or START_TOWER.bat).
rem      The console then shows "ATLAS conversation: ON".
rem Nothing in the automation changes. To switch ATLAS AI off: set ATLAS_AI=0
cd /d "%~dp0"
where ollama >nul 2>&1
if %errorLevel% neq 0 (
  echo.
  echo   Ollama is not installed. Install it from https://ollama.com/download
  echo   then run this file again.
  echo.
  pause
  exit /b 1
)
echo.
echo   Downloading the ATLAS model (qwen3.5:4b, about 3.3 GB, once only)...
ollama pull qwen3.5:4b
echo.
echo   What ATLAS can use on this machine:
where python >nul 2>&1 && (python -m intelligence.autoconfig) || (py -m intelligence.autoconfig)
echo.
pause
