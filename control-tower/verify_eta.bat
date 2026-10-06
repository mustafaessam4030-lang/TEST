@echo off
title Real ETA write proof
cd /d "%~dp0"
echo.
echo   ONE shipment through the real automation: carrier ETA, write to eHub,
echo   read back from eHub, compare. THIS WRITES TO eHub, as a normal run does.
echo.
echo      verify_eta.bat MEDUAHP69377
echo.
if "%~1"=="" (echo   Give the shipment reference. & goto :end)
where python >/dev/null 2>&1 && (python -m worker.verify eta --reference %1 & goto :end)
where py >/dev/null 2>&1 && (py -m worker.verify eta --reference %1 & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
pause
