@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
if exist "wheels\" (
  ".venv\Scripts\python.exe" -m pip install -q --no-index --find-links wheels -r requirements-dev.txt
) else (
  ".venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt
)
set "EKA_TEST_MODEL_DIR=%~dp0models"
".venv\Scripts\python.exe" -m pytest
pause
