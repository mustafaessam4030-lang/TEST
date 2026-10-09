@echo off
rem ATLAS AI on this PC: natural answers from a local model (free, runs offline).
rem   1. Install Ollama first: https://ollama.com/download  (Windows installer)
rem   2. Double-click this file. It downloads the model once (about 3.3 GB),
rem      then tests ATLAS for real and prints PASS or FAIL.
rem   3. Start the automation as usual (update_eta.py or START_TOWER.bat).
rem Nothing in the automation changes. To switch ATLAS AI off: set ATLAS_AI=0
cd /d "%~dp0"

rem Ollama on PATH, or where its installer puts it (PATH may not be refreshed yet).
set "OLLAMA=ollama"
where ollama >nul 2>&1
if %errorLevel% neq 0 (
  if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
  ) else (
    echo.
    echo   Ollama is not installed. Install it from https://ollama.com/download
    echo   then run this file again.
    echo.
    pause
    exit /b 1
  )
)

rem Make sure Ollama is running (it normally starts with Windows).
"%OLLAMA%" list >nul 2>&1
if %errorLevel% neq 0 (
  echo   Starting Ollama...
  start "" /min "%OLLAMA%" serve
  timeout /t 8 /nobreak >nul
)

echo.
echo   Downloading the ATLAS model (qwen3.5:4b, about 3.3 GB, once only)...
"%OLLAMA%" pull qwen3.5:4b
if %errorLevel% neq 0 (
  echo.
  echo   The download failed. Check the internet connection, then run this again.
  pause
  exit /b 1
)

echo.
echo   Testing ATLAS for real (a few minutes the first time)...
echo.
where python >nul 2>&1
if %errorLevel% equ 0 (python -m intelligence.autoconfig --test) else (py -m intelligence.autoconfig --test)
echo.
pause
