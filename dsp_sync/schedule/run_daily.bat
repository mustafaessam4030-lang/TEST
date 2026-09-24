@echo off
REM Daily DSP to Snowflake run, for Windows Task Scheduler.
cd /d "%~dp0.."
.venv\Scripts\python.exe main.py run
exit /b %ERRORLEVEL%
