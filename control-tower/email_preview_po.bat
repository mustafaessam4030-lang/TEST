@echo off
rem SANDBOX: show exactly what the PO email would be (sender, recipient, subject, body,
rem attachment, validation). READ-ONLY: nothing is prepared, written or sent.
rem Run sandbox_copy_po.bat first.   email_preview_po.bat K284375
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
set "PO_EMAIL_MODE=sandbox"
if not defined PO_SANDBOX_RECIPIENTS set "PO_SANDBOX_RECIPIENTS=MEMabrouk@mantracgroup.com"
if "%~1"=="" (set /p JOB=Job, Bill of Entry number or BOL/AWB: ) else (set "JOB=%~1")
set /p TO=Send to (must be on PO_SANDBOX_RECIPIENTS = %PO_SANDBOX_RECIPIENTS%): 
%PY% -m po email-preview "%JOB%" --to "%TO%"
echo.
pause
