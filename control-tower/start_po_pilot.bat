@echo off
title PO Automation - real eHub pilot (NO EMAIL)
cd /d "%~dp0"
echo.
echo   PO Automation pilot on the REAL eHub. NO EMAIL is prepared or sent.
echo   The worker opens eHub, reads the Shipments list, decides every row,
echo   and processes the Under Clearance record(s): Manage - Documents -
echo   Bill Entry - PDF - extraction - validation - template - saved output.
echo   Every job stops at SAVED for your field-by-field review.
echo.
echo      start_po_pilot.bat        (first eligible record only)
echo      start_po_pilot.bat 5      (up to 5 eligible records)
echo.
set LIMIT=1
if not "%~1"=="" set LIMIT=%1
where python >nul 2>&1 && (set PY=python) || (set PY=py)
%PY% -m po sweep --no-email --limit %LIMIT%
echo.
echo   Evidence: the list decisions are in ml\data\po\sweeps\, each job's full
echo   record with:  %PY% -m po explain ^<po_id^>   and the outputs in ml\data\po\output\
%PY% -m po audit-verify
echo.
pause
