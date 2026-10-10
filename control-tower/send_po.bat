@echo off
rem Authorize and send ONE PO email to controltower@mantracgroup.com, then verify it in
rem Sent Items. Run email_preview_po.bat first and check the preview.
cd /d "%~dp0"
set "PY=python"
where python >nul 2>&1
if %errorLevel% neq 0 set "PY=py"
set /p JOB=Job, Bill of Entry number or BOL/AWB: 
set /p BY=Your name (recorded as the authorizer): 
set /p OK=Type SEND to send this one email: 
%PY% -m po authorize-send "%JOB%" --to controltower@mantracgroup.com --by "%BY%" --confirm "%OK%"
echo.
pause
