@echo off
rem Why a PO job stopped: each blocking field and every value the Bill of Entry prints for it,
rem with its page. Read-only: nothing is changed, sent or written.
rem   trace_po.bat 41026844263      (or the BOL/AWB, e.g. K284375)
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
if "%~1"=="" (
  set /p JOB=Bill of Entry number or BOL/AWB: 
) else (
  set "JOB=%~1"
)
%PY% -m po trace "%JOB%"
echo.
pause
