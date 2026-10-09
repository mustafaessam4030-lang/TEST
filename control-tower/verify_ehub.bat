@echo off
title Real eHub check (read-only)
cd /d "%~dp0"
echo.
echo   Opens the REAL eHub on this worker, the way a run does, and reads:
echo   the page, the shipment list, one shipment and its status. Nothing is changed.
echo.
echo      verify_ehub.bat                      (first shipment Under Clearance)
echo      verify_ehub.bat MEDUAHP69377         (that shipment)
echo.
set REF=
if not "%~1"=="" set REF=--reference %1
where python >/dev/null 2>&1 && (python -m worker.verify ehub %REF% & goto :end)
where py >/dev/null 2>&1 && (py -m worker.verify ehub %REF% & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
pause
