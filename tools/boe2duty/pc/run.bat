@echo off
cd /d "%~dp0"
python run_boe.py
if errorlevel 1 echo.
echo.
pause
