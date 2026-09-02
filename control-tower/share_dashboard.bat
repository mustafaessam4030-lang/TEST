@echo off
title Shipment Control Tower - shared review mode
cd /d "%~dp0"
echo.
echo  Sharing the last finished run on this network.
echo  Colleagues open the link printed below.
echo.
echo  If nobody can connect, run this ONCE in an Administrator PowerShell:
echo    New-NetFirewallRule -DisplayName "Mantrac Control Tower" -Direction Inbound -Protocol TCP -LocalPort 8787 -Action Allow -Profile Domain,Private
echo.
set /p KEY=Access key to require (press ENTER for none): 
where python >nul 2>&1 && (
  if "%KEY%"=="" (python -m dashboard.server --share --replay --base "C:\Automation") else (python -m dashboard.server --share --key "%KEY%" --replay --base "C:\Automation")
  goto :end
)
where py >nul 2>&1 && (
  if "%KEY%"=="" (py -m dashboard.server --share --replay --base "C:\Automation") else (py -m dashboard.server --share --key "%KEY%" --replay --base "C:\Automation")
  goto :end
)
echo Python was not found on PATH. Run check_dashboard.bat for help.
:end
pause
