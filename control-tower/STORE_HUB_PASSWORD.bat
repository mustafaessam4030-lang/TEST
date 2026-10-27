@echo off
title Hub sign-in - Windows Credential Manager
cd /d "%~dp0"
echo.
echo   Moves the Logistics Hub sign-in into Windows Credential Manager
echo   (encrypted for this Windows account) and deletes credentials.txt.
echo   Run it signed in as the Windows account that runs the tower.
echo.
set CT_PY=
py -c "import sys" >nul 2>&1 && set CT_PY=py
if not defined CT_PY set CT_PY=python
if exist "C:\Automation\credentials.txt" (
  %CT_PY% -m hub_credentials move
) else (
  %CT_PY% -m hub_credentials set
)
echo.
%CT_PY% -m hub_credentials status
echo.
pause
