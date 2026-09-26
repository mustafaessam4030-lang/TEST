@echo off
title Control Tower - why is it slow
cd /d "%~dp0"
where python >nul 2>&1 && (python why_slow.py & goto :end)
where py >nul 2>&1 && (py why_slow.py & goto :end)
echo Python was not found on PATH.
echo Reinstall Python and tick "Add Python to PATH", or run it with the full
echo path to python.exe, for example:
echo    "C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python312\python.exe" why_slow.py
:end
pause
