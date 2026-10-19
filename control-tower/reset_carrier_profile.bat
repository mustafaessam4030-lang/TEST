@echo off
title Reset the carriers' browser profile
cd /d "%~dp0"
echo.
echo   Deletes the browser profile the carrier sites open in, so every carrier
echo   "are you human?" answer is forgotten. The next run starts it again empty.
echo   Nothing else is touched: not the Hub, not the logs, not the results.
echo.
set PROFILE=%ATA_CARRIER_PROFILE_DIR%
if "%PROFILE%"=="" set PROFILE=C:\Automation\carrier_profile
if not exist "%PROFILE%" (echo   There is no profile at %PROFILE% - nothing to do. & goto :end)
rmdir /s /q "%PROFILE%" && echo   Deleted %PROFILE%.
:end
echo.
pause
