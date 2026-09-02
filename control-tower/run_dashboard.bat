@echo off
title Shipment Control Tower - review mode
cd /d "%~dp0"
where python >nul 2>&1 && (python -m dashboard.server --replay --base "C:\Automation" & goto :end)
where py >nul 2>&1 && (py -m dashboard.server --replay --base "C:\Automation" & goto :end)
echo Python was not found on PATH. Run check_dashboard.bat for help.
:end
pause
