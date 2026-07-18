$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not $args.Count) {
  Write-Error "Usage: .\scripts\run_query.ps1 `"Your question here`""
}
& .\venv\Scripts\python.exe -m app.cli query @args
