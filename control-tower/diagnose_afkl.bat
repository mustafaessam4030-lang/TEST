@echo off
title AFKL navigation diagnostic
cd /d "%~dp0"
echo.
echo   Testing which browser can reach the AFKL shipment page from THIS machine.
echo   A visible Edge window will open for test 1 - that is expected.
echo.
set AWB=%1
if "%AWB%"=="" set AWB=057-05765454
where python >nul 2>&1 && (python diagnose_afkl.py %AWB% & goto :end)
where py >nul 2>&1 && (py diagnose_afkl.py %AWB% & goto :end)
echo   Python was not found on PATH. Run check_dashboard.bat for help.
:end
echo.
echo   The report was also saved to afkl_diagnostic.txt - send me that file.
echo.
pause
