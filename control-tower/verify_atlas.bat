@echo off
title ATLAS - verify this machine
cd /d "%~dp0"
where python >nul 2>&1 && (python verify_atlas.py & goto :end)
where py >nul 2>&1 && (py verify_atlas.py & goto :end)
echo   Python was not found on PATH.
:end
echo.
pause
