@echo off
title Ocean carrier check
cd /d "%~dp0"
echo.
echo   Checks one ocean carrier against its real website. Nothing is written.
echo.
echo      diagnose_ocean.bat MAERSK 231045678
echo      diagnose_ocean.bat MSC MEDUAB123456
echo      diagnose_ocean.bat            (lists the carriers)
echo.
where python >nul 2>&1 && (python diagnose_ocean.py %1 %2 & goto :end)
where py >nul 2>&1 && (py diagnose_ocean.py %1 %2 & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
pause
