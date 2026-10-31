@echo off
rem ATLAS web search on this PC: a private SearXNG (open source), no WSL or Docker.
rem   Double-click once. It downloads SearXNG (a few MB) and its packages into
rem   the "searxng" folder here, starts it and checks a real search.
rem   After that the tower starts it by itself (update_eta.py / START_TOWER.bat).
rem It listens on this PC only (127.0.0.1:8888). Delete the searxng folder to remove it.
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
echo.
echo   Installing ATLAS web search (SearXNG)...
echo.
%PY% -m intelligence.searxng_local install
if %errorLevel% neq 0 (
  echo.
  echo   Web search did not start. Send a photo of this window, and the file
  echo   searxng\searxng.log if it exists.
  echo.
  pause
  exit /b 1
)
echo.
echo   Testing ATLAS with web search (a few minutes)...
echo.
%PY% -m intelligence.autoconfig --test
echo.
pause
