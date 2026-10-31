@echo off
rem SANDBOX: authorize and send ONE PO email (the sandbox copy) to an allowlisted recipient,
rem then check it in Sent Items (not proof of delivery). Production PO email stays disabled.
rem Run sandbox_copy_po.bat and email_preview_po.bat first.
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
set "PO_EMAIL_MODE=sandbox"
if not defined PO_SANDBOX_RECIPIENTS set "PO_SANDBOX_RECIPIENTS=MEMabrouk@mantracgroup.com"
set /p JOB=Job, Bill of Entry number or BOL/AWB: 
set /p TO=Send to (must be on PO_SANDBOX_RECIPIENTS = %PO_SANDBOX_RECIPIENTS%): 
set /p BY=Your name (recorded as the authorizer): 
set /p OK=Type SEND to send this one email: 
%PY% -m po authorize-send "%JOB%" --to "%TO%" --by "%BY%" --confirm "%OK%"
echo.
pause
