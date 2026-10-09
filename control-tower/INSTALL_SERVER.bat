@echo off
title Install the Mantrac tower on this server
cd /d "%~dp0"

net session >nul 2>&1
if %errorLevel% neq 0 (
  echo   Asking Windows for administrator rights...
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_server.ps1"
echo.
pause
