@echo off
REM ===================================================================
REM  Connect Maia to Snowflake. Double-click after filling in snowflake.txt.
REM  It checks the connection, creates the tables if they are missing,
REM  and copies the lookups already saved in logs\sis-results\ into Snowflake.
REM  Nothing it prints contains a password or token.
REM ===================================================================
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Run START-MAIA.bat once first - it prepares Python for you.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -c "import snowflake.connector" 2>nul
if errorlevel 1 (
  echo Installing the Snowflake driver - once...
  ".venv\Scripts\python.exe" -m pip install "snowflake-connector-python[secure-local-storage]" --quiet
)
".venv\Scripts\python.exe" scripts\snowflake\snowflake_setup.py %*
echo.
pause
