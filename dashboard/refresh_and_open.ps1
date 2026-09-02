# Refresh the research console's data files, then open it.
#
# This is the one-click path. Double-click "Open Dashboard.bat" in the repo
# root; it calls this. Nothing here harvests, hits the network or writes to
# the database - it only re-derives the two files the page reads:
#
#   data/results/dashboard_data.json (+ .js)   sections 01-06
#   data/results/build_status.json   (+ .js)   sections 08-11
#
# The .js sidecars are what make file:// work. Without them Explorer opens a
# page that silently falls back to the embedded example scaffold, which looks
# like real data and is not. Regenerating before opening is the whole point.
#
#   .\dashboard\refresh_and_open.ps1            refresh, then open in browser
#   .\dashboard\refresh_and_open.ps1 -Serve     refresh, then start serve.ps1
#   .\dashboard\refresh_and_open.ps1 -NoOpen    refresh only (for scripts)
#   .\dashboard\refresh_and_open.ps1 -Quiet     no pause at the end
param(
    [switch]$Serve,
    [switch]$NoOpen,
    [switch]$Quiet,
    [int]$Port = 8787
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$results = Join-Path $repo "data\results"
$warnings = @()

function Write-Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Age($path) {
    if (-not (Test-Path $path)) { return "missing" }
    $m = (Get-Item $path).LastWriteTime
    $mins = [int]((Get-Date) - $m).TotalMinutes
    if ($mins -lt 1) { return "just now" }
    if ($mins -lt 90) { return "$mins min ago" }
    return "$($m.ToString('MMM d, h:mm tt')) ($([int]($mins / 60)) h ago)"
}

Write-Host "Zoltar research console - refreshing before open" -ForegroundColor White
Write-Host "repo:   $repo"
Write-Host "python: $python"

# ---------------------------------------------------------------- 01-06
# Reads DuckDB. Can legitimately fail if a harvest holds the write lock, or
# if the DB has not been built yet. That is not fatal: the previous .js is
# still on disk and the page stamps its own generated_at, so a stale research
# half is visible as stale rather than wrong. We warn and carry on.
Write-Step "Research data (sections 01-06)"
& $python -m zoltar_ranks.analysis.export_dashboard_data
if ($LASTEXITCODE -ne 0) {
    $warnings += "Research export failed (exit $LASTEXITCODE). Sections 01-06 show the previous snapshot: $(Age (Join-Path $results 'dashboard_data.js'))."
    Write-Host "  -> kept the previous dashboard_data.js" -ForegroundColor Yellow
}

# ---------------------------------------------------------------- 08-11
# Pure filesystem + git + JSON. Should never fail; if it does, the monitor
# half is the half that tells you whether anything is running, so a failure
# here is worth stopping for.
Write-Step "Build monitor (sections 08-11)"
& $python "dashboard\emit_build_status.py"
$emitFailed = ($LASTEXITCODE -ne 0)
if ($emitFailed) {
    $warnings += "Build monitor emit FAILED (exit $LASTEXITCODE). Sections 08-11 are stale: $(Age (Join-Path $results 'build_status.js'))."
}

# ---------------------------------------------------------------- audit
# --check exits non-zero when something the monitor depends on is missing or
# mismatched. That is a real finding about the build, not an error in this
# script - the page should still open and show it as red.
Write-Step "Input audit (emit_build_status.py --check)"
& $python "dashboard\emit_build_status.py" --check
$blocking = ($LASTEXITCODE -ne 0)

# ---------------------------------------------------------------- summary
Write-Step "Data the page will read"
foreach ($f in @("dashboard_data.js", "build_status.js")) {
    $p = Join-Path $results $f
    $ok = (Test-Path $p) -and ((Get-Date) - (Get-Item $p).LastWriteTime).TotalMinutes -lt 5
    $color = if ($ok) { "Green" } else { "Yellow" }
    Write-Host ("  {0,-20} {1}" -f $f, (Age $p)) -ForegroundColor $color
}

if ($warnings.Count -gt 0) {
    Write-Host ""
    foreach ($w in $warnings) { Write-Host "WARNING: $w" -ForegroundColor Yellow }
}
if ($blocking) {
    Write-Host "`n--check reports blocking rows. The gate above is red on purpose - read it." -ForegroundColor Yellow
}

# ---------------------------------------------------------------- open
if ($NoOpen) {
    Write-Host "`n-NoOpen: not launching." -ForegroundColor DarkGray
    exit ([int]$emitFailed)
}

if ($Serve) {
    Write-Step "Serving at http://127.0.0.1:$Port/dashboard/index.html"
    Write-Host "Ctrl-C to stop. The SS12 review board can post replies over HTTP." -ForegroundColor DarkGray
    Start-Process "http://127.0.0.1:$Port/dashboard/index.html"
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\serve.ps1" -Port $Port
    exit $LASTEXITCODE
}

$page = Join-Path $repo "dashboard\index.html"
Write-Step "Opening $page"
Start-Process $page

if (-not $Quiet) {
    Write-Host "`nDone. This window can be closed." -ForegroundColor DarkGray
    Start-Sleep -Seconds 4
}
exit ([int]$emitFailed)
