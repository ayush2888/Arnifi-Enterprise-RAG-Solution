$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& .\venv\Scripts\python.exe -m app.cli website-crawl
& .\venv\Scripts\python.exe -m app.cli website-ingest @args
