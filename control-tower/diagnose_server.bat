@echo off
title What the automation costs this server
cd /d "%~dp0"
echo.
echo   Measures THIS server, not the carrier's page.
echo   Run this four times, in order:
echo.
echo      diagnose_server.bat A     with the automation OFF
echo      diagnose_server.bat B     automation RUNNING, no AFKL lookup yet
echo      diagnose_server.bat C     automation RUNNING, after an AFKL lookup
echo      diagnose_server.bat D     automation STOPPED
echo      diagnose_server.bat report
echo.
echo   B is the one that decides it. A visible Edge window opens for the
echo   probe - leave it alone, it closes itself.
echo.
set PHASE=%1
if "%PHASE%"=="" set PHASE=A
where python >nul 2>&1 && (python diagnose_server.py %PHASE% %2 & goto :end)
where py >nul 2>&1 && (py diagnose_server.py %PHASE% %2 & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
echo   Snapshots are written to logs\server_probe - send me that folder.
echo.
pause
