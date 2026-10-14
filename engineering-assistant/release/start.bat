@echo off
cd /d "%~dp0"
title Engineering Knowledge Assistant
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo.
echo  Engineering Knowledge Assistant
echo  -------------------------------
echo  The browser opens at http://127.0.0.1:8765 (see config.env to change it).
echo  Put PDFs in the "pdfs" folder; they are indexed automatically.
echo  Keep this window open. Close it, or run stop.bat, to stop the assistant.
echo.
".venv\Scripts\python.exe" -m eka
echo.
echo  The assistant has stopped.
pause
