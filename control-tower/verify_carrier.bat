@echo off
title Carrier access diagnostic
cd /d "%~dp0"
echo.
echo   Why does a carrier restrict this worker? Records VPN, proxy, public IP,
echo   network type and Edge; opens the carrier in a normal Edge (you say what it
echo   shows) and in the automation browser; compares. Nothing is written to eHub.
echo.
echo      verify_carrier.bat CMA_CGM CMAU1234567
echo.
if "%~1"=="" (echo   Give the carrier, e.g. CMA_CGM, and a reference. & goto :end)
set REF=
if not "%~2"=="" set REF=--reference %2
where python >nul 2>&1 && (python -m worker.verify carrier --carrier %1 %REF% & goto :end)
where py >nul 2>&1 && (py -m worker.verify carrier --carrier %1 %REF% & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
pause
