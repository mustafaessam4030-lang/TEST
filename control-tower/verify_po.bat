@echo off
title PO Automation on the real eHub
cd /d "%~dp0"
echo.
echo   Runs the PO pipeline on ONE real eHub record (A-J) and reports what it saw.
echo   Nothing is written to eHub. The business recipient is never emailed from here;
echo   step J sends the generated document only to the address you give.
echo.
echo      verify_po.bat                                  (next Under Clearance record)
echo      verify_po.bat 176-88452310 9116093 you@mantracgroup.com
echo                    BOL/AWB      invoice  test address
echo.
set ARGS=
if not "%~1"=="" set ARGS=--reference %1
if not "%~2"=="" set ARGS=%ARGS% --invoice-no %2
if not "%~3"=="" set ARGS=%ARGS% --email-to %3
where python >nul 2>&1 && (python -m worker.verify po %ARGS% & goto :end)
where py >nul 2>&1 && (py -m worker.verify po %ARGS% & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
pause
