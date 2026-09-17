@echo off
title Mantrac Control Tower
cd /d "%~dp0"

rem ---- self-elevate so the firewall port can be opened without extra steps ----
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo   Asking Windows for permission to open the dashboard port...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo   Opening inbound TCP 8787 for Domain and Private networks...
netsh advfirewall firewall delete rule name="Mantrac Control Tower" >nul 2>&1
netsh advfirewall firewall add rule name="Mantrac Control Tower" dir=in action=allow protocol=TCP localport=8787 profile=domain,private >nul
if %errorLevel% equ 0 (echo   Done.) else (echo   Could not add the rule - ask IT to allow inbound TCP 8787.)
echo.
echo   Starting the automation. Copy the link marked SEND THIS TO COLLEAGUES.
echo.

where python >nul 2>&1 && (python update_eta.py & goto :end)
where py >nul 2>&1 && (py update_eta.py & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
pause
