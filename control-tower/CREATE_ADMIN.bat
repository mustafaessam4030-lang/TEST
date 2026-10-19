@echo off
rem Turn on personal sign-in for the Control Tower dashboard.
rem   Creates the first Admin account and prints a one-time link to set the password.
rem   After that, everyone signs in at http://<this PC>:8787/login and the
rem   Admin adds the team in Settings -> Access levels.
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
echo.
echo   Create the first Admin for the Control Tower
echo.
set /p ADMIN_NAME=  Your full name: 
set /p ADMIN_EMAIL=  Your work email: 
echo.
%PY% -m dashboard.accounts create-admin --email "%ADMIN_EMAIL%" --name "%ADMIN_NAME%"
echo.
echo   Restart the tower (START_TOWER.bat / START_SHARED.bat) so sign-in takes effect.
echo.
pause
