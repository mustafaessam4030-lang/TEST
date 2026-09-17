@echo off
title myCargo - what is really on the page
cd /d "%~dp0"
echo.
echo   Opens the real myCargo page and prints every input it carries, which
echo   box each matcher picks, and - if you pass a flight and a date - what
echo   the Check flight status card answers.
echo.
echo      diagnose_flight_status.bat
echo      diagnose_flight_status.bat AF0877 04/09/2026
echo.
where python >nul 2>&1 && (python diagnose_flight_status.py %1 %2 & goto :end)
where py >nul 2>&1 && (py diagnose_flight_status.py %1 %2 & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
pause
