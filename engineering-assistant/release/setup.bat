@echo off
setlocal
cd /d "%~dp0"
title Engineering Knowledge Assistant - setup
echo.
echo  Engineering Knowledge Assistant - one-time setup
echo  ================================================
echo.
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython
%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 goto nopython

%PY% -m eka.assemble models
if errorlevel 1 goto modelparts

if not exist ".venv\Scripts\python.exe" (
  echo Creating the Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto failed
)
echo Installing packages ...
if exist "wheels\" (
  echo   using the offline packages in the wheels folder
  ".venv\Scripts\python.exe" -m pip install --no-index --find-links wheels -r requirements.txt -r requirements-optional.txt
) else (
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -r requirements-optional.txt
)
if errorlevel 1 goto failed
echo.
echo Checking the installation ...
".venv\Scripts\python.exe" -m eka.check
if errorlevel 1 goto failed
echo.
echo  Setup finished. Double-click start.bat to run the assistant.
echo.
pause
exit /b 0

:nopython
echo  Python 3.10 or newer was not found.
echo  Install it from https://www.python.org/downloads/ and tick
echo  "Add python.exe to PATH" during installation, then run setup.bat again.
echo.
pause
exit /b 1

:modelparts
echo.
echo  The search model is incomplete. Extract EVERY file named
echo  EngineeringKnowledgeAssistant-...-model-partXofY.zip into the same
echo  place as the app zip, then run setup.bat again.
echo.
pause
exit /b 1

:failed
echo.
echo  Setup did not finish. Common causes:
echo   - no internet / company proxy: set HTTPS_PROXY, or use the offline
echo     packages zip (extract its "wheels" folder here) and run setup.bat again
echo   - antivirus blocking .venv: allow this folder and run setup.bat again
echo.
pause
exit /b 1
