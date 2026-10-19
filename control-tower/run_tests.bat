@echo off
title Control Tower - tests
cd /d "%~dp0"
where python >nul 2>&1 && (python run_tests.py & goto :end)
where py >nul 2>&1 && (py run_tests.py & goto :end)
echo   Python was not found on PATH.
:end
echo.
pause
