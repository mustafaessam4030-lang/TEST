@echo off
REM REAL SIS check: Parts + Troubleshooting + 3D Model, then a PASS/FAIL table.
REM Double-click, press Enter for JAZ01865 or type another serial.
setlocal
set "SERIAL=%~1"
if "%SERIAL%"=="" set /p SERIAL=Serial number [JAZ01865]: 
if "%SERIAL%"=="" set "SERIAL=JAZ01865"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\windows\investigate.ps1" -Serial "%SERIAL%"
