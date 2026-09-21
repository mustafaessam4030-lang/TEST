@echo off
REM ===================================================================
REM  Maya - double-click this file. It does everything.
REM ===================================================================
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\one-click.ps1"
if errorlevel 1 (
  echo.
  echo Something went wrong. The message above says what.
  pause
)
