@echo off
title Leave Remote Desktop, keep the automation running
rem Closing or minimising Remote Desktop can stop Windows drawing this
rem session, and Edge then freezes. This hands the session to the machine's
rem own console instead: you are disconnected, the desktop stays live, and
rem the automation keeps working. Sign in again with Remote Desktop as usual.

net session >nul 2>&1
if %errorLevel% neq 0 (
  powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

for /f "skip=1 tokens=3" %%s in ('query user %USERNAME%') do (
  %windir%\System32\tscon.exe %%s /dest:console
)
