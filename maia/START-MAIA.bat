@echo off
REM ===================================================================
REM  Maia - double-click this file. It does everything.
REM ===================================================================
cd /d "%~dp0"

REM Windows marks files that came from the internet; clear that so the
REM scripts run without the "do you trust this?" prompt.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-ChildItem -Path '%~dp0' -Recurse -Include *.ps1 -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\one-click.ps1"
set EXITCODE=%ERRORLEVEL%

echo.
if not "%EXITCODE%"=="0" (
  echo Maia exited with code %EXITCODE%.
  echo The reason is above, and in: %~dp0logs\maia-start.log
)
REM Always hold the window open: a window that closes takes the error with it.
pause
