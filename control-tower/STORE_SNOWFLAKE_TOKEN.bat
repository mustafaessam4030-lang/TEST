@echo off
title ATLAS - Snowflake Cortex token
cd /d "%~dp0"
echo.
echo   Connects ATLAS to Snowflake Cortex (Mantrac's Snowflake account).
echo   The token is kept in Windows Credential Manager for this Windows account,
echo   never in a file. Run it signed in as the account that runs the tower.
echo.
set CT_PY=
py -c "import sys" >nul 2>&1 && set CT_PY=py
if not defined CT_PY set CT_PY=python
%CT_PY% -m intelligence.cortex set
if %errorLevel% equ 0 %CT_PY% -m intelligence.cortex test
echo.
%CT_PY% -m intelligence.cortex status
echo.
echo   Then restart the tower so ATLAS uses it, in an Administrator PowerShell:
echo     Stop-ScheduledTask "Mantrac Tower"; Start-ScheduledTask "Mantrac Tower"
pause
