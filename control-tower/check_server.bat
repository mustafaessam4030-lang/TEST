@echo off
title Is this server ready for the tower?
cd /d "%~dp0"
where python >nul 2>&1 && (python check_server.py & goto :end)
where py >nul 2>&1 && (py check_server.py & goto :end)
echo   Python was not found on PATH.
:end
pause
