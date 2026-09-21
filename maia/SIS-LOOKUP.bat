@echo off
REM One real Caterpillar SIS lookup, saved to logs\sis-results\.
REM Double-click, then type the serial number when asked.
setlocal
set "SERIAL=%~1"
if "%SERIAL%"=="" set /p SERIAL=Serial number (e.g. JAZ01865): 
if "%SERIAL%"=="" (
  echo No serial number given.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\sis-lookup.ps1" -Serial "%SERIAL%"
