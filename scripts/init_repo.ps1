# One-time: turn this folder into the git repo and connect it to the remote.
# The remote https://github.com/zoltarfinancial/zoltar-ranks.git starts empty.
#
#   .\scripts\init_repo.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

if (Test-Path ".git") {
    Write-Host "Already a git repo. Current remote:" -ForegroundColor Yellow
    git remote -v
    exit 0
}

git init
git branch -M main
git add -A
git status --short

Write-Host ""
Write-Host "Check the list above: data/ and .env must NOT appear." -ForegroundColor Yellow
$reply = Read-Host "Commit and push? (y/n)"
if ($reply -ne "y") { Write-Host "Stopped. Nothing committed."; exit 0 }

git commit -m "Initial scaffold: point-in-time rank archive, plan, contract tests"
git remote add origin https://github.com/zoltarfinancial/zoltar-ranks.git
git push -u origin main

Write-Host "Pushed to origin/main." -ForegroundColor Green
