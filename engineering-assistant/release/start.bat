@echo off
cd /d "%~dp0"
title Engineering Knowledge Assistant
if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  First start in this folder: installing Luma once ^(3-5 minutes^) ...
  set "EKA_SETUP_NO_PAUSE=1"
  call setup.bat
)
if not exist ".venv\Scripts\python.exe" (
  echo  The installation did not finish. Run setup.bat and read its message.
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
