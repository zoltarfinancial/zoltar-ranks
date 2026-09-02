# Install the repo's git hooks. Run once after a fresh clone.
#
# `.git/hooks/` is not versioned, so the post-commit hook does not survive a
# clone. Without it the console's repo header and the SS12 review board only
# refresh on a harvest run, which is every 30 minutes at best and never on a
# machine that is not running the scheduled task.
#
#   .\scripts\install_hooks.ps1

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$hookDir = Join-Path $repo '.git\hooks'

if (-not (Test-Path $hookDir)) {
    throw "no .git/hooks at $hookDir - run this from inside the repo"
}

$hook = Join-Path $hookDir 'post-commit'

# PYTHONIOENCODING is required, not cosmetic. Python on Windows defaults stdout
# to cp1252 and review.py prints the charter, whose critical path contains a
# U+2192 arrow, so it exits 1 with UnicodeEncodeError without this.
$body = @'
#!/bin/sh
# Refresh the console so the repo header, the SS11 activity log and the SS12
# review board stay current between harvests. `|| true` on each is deliberate:
# a monitor that can fail a commit is worse than a stale monitor.
#
# Installed by scripts/install_hooks.ps1. Edit there, not here.
export PYTHONIOENCODING=utf-8
python dashboard/emit_build_status.py >/dev/null 2>&1 || true
python dashboard/review.py emit >/dev/null 2>&1 || true
'@

# LF endings and no BOM: git runs this through sh, which rejects CRLF.
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($hook, ($body -replace "`r`n", "`n"), $utf8NoBom)

Write-Host "installed $hook"
Write-Host "verify with: git commit --allow-empty -m 'hook test'"
