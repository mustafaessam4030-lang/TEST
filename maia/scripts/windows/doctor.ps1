<# Check every precondition for a real SIS run on this PC. #>
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repo
if (Test-Path ".\.venv\Scripts\Activate.ps1") { & .\.venv\Scripts\Activate.ps1 }
$env:MAIA_SIS_SECRET_REF = "env://SIS"
python scripts\e2e\doctor.py
