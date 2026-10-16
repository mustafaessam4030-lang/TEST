@echo off
rem ATLAS demonstration: the dashboard with ATLAS in conversation.
rem   START_ATLAS_DEMO.bat              the last real run from C:\Automation
rem   START_ATLAS_DEMO.bat --test-data  a run labelled TEST DATA in every answer
rem   START_ATLAS_DEMO.bat --check      what is ready, what is not
rem It never starts the automation, signs in anywhere or sends email.
cd /d "%~dp0"
set ARGS=%*
if "%ARGS%"=="" set ARGS=--replay
python atlas_demo.py %ARGS%
pause
