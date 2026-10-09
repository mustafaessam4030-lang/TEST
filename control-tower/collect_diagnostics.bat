@echo off
title Collect diagnostics
cd /d "%~dp0"
echo.
echo   Packing the last runs' logs, results and failed Bill of Entry PDFs into one zip.
echo   No passwords, keys or cookies are included.
echo.
where python >nul 2>&1 && (python collect_diagnostics.py & goto :end)
where py >nul 2>&1 && (py collect_diagnostics.py & goto :end)
echo   Python was not found on PATH.
:end
pause
