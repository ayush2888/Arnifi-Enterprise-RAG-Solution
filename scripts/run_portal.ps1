# Start the Arnifi RAG web portal (http://127.0.0.1:8000)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (Test-Path ".\venv\Scripts\Activate.ps1") {
    .\venv\Scripts\Activate.ps1
}

python -m app.api.server
