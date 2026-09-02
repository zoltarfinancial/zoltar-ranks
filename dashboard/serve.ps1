# Serve the dashboard locally so it can read data/results/dashboard_data.json.
#
# Opening index.html directly with file:// works, but the browser blocks the
# fetch of the JSON, so the page falls back to its embedded seed data. Serving
# over http fixes that.
#
#   .\dashboard\serve.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
Write-Host "Dashboard: http://localhost:8787/dashboard/" -ForegroundColor Green
Write-Host "Ctrl+C to stop."
& $python -m http.server 8787
