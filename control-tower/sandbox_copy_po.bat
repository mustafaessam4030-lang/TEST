@echo off
rem SANDBOX: copy a SAVED PO job (its record, PDF and generated PO) into sandbox\po.
rem The production job is only read. Nothing is sent.
rem   sandbox_copy_po.bat K284375
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
set "PO_EMAIL_MODE=sandbox"
if not defined PO_SANDBOX_RECIPIENTS set "PO_SANDBOX_RECIPIENTS=MEMabrouk@mantracgroup.com"
if "%~1"=="" (set /p JOB=Job, Bill of Entry number or BOL/AWB: ) else (set "JOB=%~1")
%PY% -m po sandbox-copy "%JOB%"
echo.
pause
