# Daily entry point for Windows Task Scheduler. Contains no schedule itself.
#
# Register once (runs daily at 05:15 as a service account):
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#              -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\inspection-pipeline\scripts\run_daily.ps1"
#   $trigger = New-ScheduledTaskTrigger -Daily -At 05:15
#   $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
#   Register-ScheduledTask -TaskName "InspectionPipeline" -Action $action -Trigger $trigger -Settings $settings `
#              -User "DOMAIN\svc-inspections" -Password "<entered interactively>"
#
# Exit code: 0 success/skipped, 2 partial success, 1 failed.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
$python = if ($env:PYTHON) { $env:PYTHON } else { ".\.venv\Scripts\python.exe" }
& $python -m app.collect --skip-if-succeeded-today @args
exit $LASTEXITCODE
