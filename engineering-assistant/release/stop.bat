@echo off
cd /d "%~dp0"
if not exist "data\server.pid" (
  echo The assistant is not running.
  timeout /t 3 >nul
  exit /b 0
)
set /p PID=<"data\server.pid"
taskkill /PID %PID% /T /F >nul 2>nul
del "data\server.pid" >nul 2>nul
echo The assistant was stopped.
timeout /t 3 >nul
