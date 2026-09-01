# One-time setup. Run from the repo root in PowerShell:
#   .\scripts\setup.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example - fill it in before Phase 2." -ForegroundColor Yellow
}

Write-Host "== creating venv ==" -ForegroundColor Cyan
if (-not (Test-Path ".venv")) { python -m venv .venv }
& .\.venv\Scripts\Activate.ps1

Write-Host "== installing dependencies ==" -ForegroundColor Cyan
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .

Write-Host "== verifying git is available (required: the harvester shells out to it) ==" -ForegroundColor Cyan
git --version
if ($LASTEXITCODE -ne 0) { throw "git not found on PATH" }

Write-Host "== running offline contract tests ==" -ForegroundColor Cyan
pytest tests -q -m "not network"
if ($LASTEXITCODE -ne 0) { throw "offline tests failed - stop here" }

Write-Host "== running upstream contract tests (network) ==" -ForegroundColor Cyan
pytest tests -q
if ($LASTEXITCODE -ne 0) { throw "upstream contract tests failed - read docs/FINDINGS.md before continuing" }

Write-Host "== backfilling the archive (one time, ~200MB of blob fetches) ==" -ForegroundColor Cyan
python scripts\daily.py --backfill
if ($LASTEXITCODE -ne 0) { throw "backfill failed - see data\results\last_run_status.json" }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next: .\scripts\schedule_harvest.ps1  (elevated - stops the ongoing data loss)"
Write-Host "Then: read START_HERE.md and begin Phase 2."
