@echo off
rem Set the launch page's countdown: the date and time ATLAS opens, in Cairo time.
rem   Run as Administrator. Then sign out of Windows and in again (or restart).
rem   The UTC offset (+02:00, or +03:00 in summer time) is worked out by Windows.
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo.
  echo   Right-click SET_LAUNCH.bat and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)
echo.
echo   When does ATLAS open? Cairo time, e.g. 2026-11-01 09:00
echo   (type OFF to stop using the launch page)
echo.
set /p LAUNCH_WHEN=  Date and time: 
if /i "%LAUNCH_WHEN%"=="OFF" (
  reg delete "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v ATA_LAUNCH_AT /f >nul 2>&1
  echo   Launch page off. Sign out and in again.
  pause
  exit /b 0
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $d=[datetime]::ParseExact($env:LAUNCH_WHEN.Trim(),'yyyy-MM-dd HH:mm',[Globalization.CultureInfo]::InvariantCulture); $z=[TimeZoneInfo]::FindSystemTimeZoneById('Egypt Standard Time'); $o=$z.GetUtcOffset($d); $sign=if($o -lt [TimeSpan]::Zero){'-'}else{'+'}; $s=$d.ToString('yyyy-MM-ddTHH:mm:00')+$sign+$o.ToString('hh\:mm'); [Environment]::SetEnvironmentVariable('ATA_LAUNCH_AT',$s,'Machine'); [Environment]::SetEnvironmentVariable('ATA_LAUNCH_TZ','Africa/Cairo','Machine'); Write-Host ('   Launch set: '+$s+'  (Cairo time)') } catch { Write-Host '   Not set: write it like 2026-11-01 09:00'; exit 1 }"
if %errorLevel% neq 0 ( pause & exit /b 1 )
echo.
echo   Music: the song in dashboard\static\launch\audio\ plays by itself.
echo   Now sign out of Windows and in again, then open the dashboard address.
echo.
pause
