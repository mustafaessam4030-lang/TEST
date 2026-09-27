@echo off
REM Maia analysis of stored SIS results - no browser, no AI model.
REM Double-click, then type e.g.:  Analyze JAZ01865
cd /d "%~dp0"
set PYTHONUTF8=1
set "Q=%*"
if "%Q%"=="" set /p Q=Ask Maia (e.g. Analyze JAZ01865): 
if "%Q%"=="" set "Q=Analyze JAZ01865"
".venv\Scripts\python.exe" scripts\analysis\maia_analyze.py %Q%
echo.
pause
