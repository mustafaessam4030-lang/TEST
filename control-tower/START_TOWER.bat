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
rem The same Python that runs update_eta.py when it is double-clicked (the py
rem launcher) first: Start runs the automation with this Python.
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
%CT_PY% -m dashboard.supervisor --share
:end
pause
