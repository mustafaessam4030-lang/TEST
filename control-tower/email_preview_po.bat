@echo off
rem Show exactly what the PO email would be (sender, recipient, subject, body, attachment,
rem validation). Prepares the email for a SAVED job; NOTHING IS SENT.
rem   email_preview_po.bat K284375
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
if "%~1"=="" (set /p JOB=Job, Bill of Entry number or BOL/AWB: ) else (set "JOB=%~1")
%PY% -m po email-preview "%JOB%" --to controltower@mantracgroup.com
echo.
pause
