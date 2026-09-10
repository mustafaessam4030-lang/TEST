@echo off
title Mantrac Control Tower
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
  echo   Asking Windows for permission to open the dashboard port...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

netsh advfirewall firewall delete rule name="Mantrac Control Tower" >nul 2>&1
netsh advfirewall firewall add rule name="Mantrac Control Tower" dir=in action=allow protocol=TCP localport=8787 profile=domain,private >nul

echo.
echo   The dashboard stays open whether or not a run is going.
echo   Use Start and Stop in the dashboard header.
echo.
where python >nul 2>&1 && (python -m dashboard.supervisor --share --key mantrac2026 & goto :end)
where py >nul 2>&1 && (py -m dashboard.supervisor --share --key mantrac2026 & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
pause
