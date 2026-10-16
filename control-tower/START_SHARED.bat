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

rem Use the Python that has the automation's libraries (Playwright). A PC can
rem have two Pythons: "python" may be one without them ("No module named
rem 'playwright'"), so each is checked before it is used.
set CT_PY=
py -c "import playwright" >nul 2>&1 && set CT_PY=py
if not defined CT_PY python -c "import playwright" >nul 2>&1 && set CT_PY=python
if not defined CT_PY (
  echo   No Python on this PC has Playwright installed.
  echo   Fix: open a normal Command Prompt in this folder and run:
  echo       py -m pip install -r requirements.txt
  echo   then start this file again. check_dashboard.bat shows which Python is used.
  goto :end
)
echo   Using: %CT_PY%
%CT_PY% update_eta.py
:end
pause
