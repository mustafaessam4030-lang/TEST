@echo off
cd /d "%~dp0"
title Luma - shared with the team
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo.
echo  Luma - share with the team
echo  --------------------------
echo  Colleagues on the company network open the link printed below in their browser.
echo  Put PDFs in the "pdfs" folder; they are indexed automatically.
echo  Keep this window open. Close it, or run stop.bat, to stop sharing.
".venv\Scripts\python.exe" -m eka.share
echo.
echo  Luma has stopped.
pause
