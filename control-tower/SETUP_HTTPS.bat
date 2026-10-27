@echo off
title ATA Control Tower - https setup
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
  echo   Asking Windows for administrator rights...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

echo.
echo   https for the dashboard: IIS, the company certificate, the site.
echo   Put the certificate (.pfx) in C:\Automation\certificate first.
echo   You will be asked for its password here; it is not saved anywhere.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\iis\setup_https.ps1" %*
echo.
pause
