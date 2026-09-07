<#
    push-receipt.ps1 — put an executor receipt into git.
    ---------------------------------------------------
    ADDITION to ZoltarGenesis's executor kit, by zoltarone/claude-code,
    2026-09-07. Kept in its own file so the change against the kit is a visible
    diff rather than a fork of run-order.ps1.

    WHY THIS EXISTS ON THIS NODE
    ----------------------------
    ZoltarOne's OneDrive has no service. It starts only from HKCU\...\Run and a
    scheduled task with a LOGON trigger, and AutoAdminLogon is 0. So after any
    unattended reboot — Windows Update, a power blip — nobody logs in, OneDrive
    never starts, and this node goes on working while looking dead in the
    mirror. Git is the only transport that survives that, so receipts go to git
    and the mirror is a convenience.

    A SEPARATE WORKTREE, deliberately: committing receipts must never touch the
    order branch, the working tree, or HEAD. The executor may be mid-order.

    BEST EFFORT BY DESIGN. Every failure is caught and reported in the return
    value. A receipt that cannot be pushed must never turn a good cycle into an
    error — the local receipt is still written either way.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RepoRoot,
    [Parameter(Mandatory)][string]$ReceiptPath,
    [string]$NodeId = 'zoltarone'
)

$out = [ordered]@{ pushed = $false; branch = "fleet/receipts-$NodeId"; sha = $null; error = $null }

try {
    if (-not (Test-Path $ReceiptPath)) { $out.error = 'receipt file missing'; return }

    $env:GIT_TERMINAL_PROMPT = '0'
    $wt     = Join-Path $PSScriptRoot 'receipts-wt'
    $branch = $out.branch

    & git -C $RepoRoot fetch origin --prune 2>&1 | Out-Null
    $remoteHas = (& git -C $RepoRoot ls-remote --heads origin $branch 2>$null | Out-String).Trim()

    if (-not (Test-Path (Join-Path $wt '.git'))) {
        # stale directory from a killed run would block worktree add
        if (Test-Path $wt) { Remove-Item $wt -Recurse -Force -EA SilentlyContinue }
        & git -C $RepoRoot worktree prune 2>&1 | Out-Null
        if ($remoteHas) {
            & git -C $RepoRoot worktree add -B $branch $wt "origin/$branch" 2>&1 | Out-Null
        } else {
            & git -C $RepoRoot worktree add -b $branch $wt origin/main 2>&1 | Out-Null
        }
    } elseif ($remoteHas) {
        # fast-forward the worktree to whatever else has landed on the branch
        & git -C $wt fetch origin $branch 2>&1 | Out-Null
        & git -C $wt reset --hard "origin/$branch" 2>&1 | Out-Null
    }

    if (-not (Test-Path (Join-Path $wt '.git'))) { $out.error = 'worktree could not be created'; return }

    $destDir = Join-Path $wt "data\fleet\receipts\$NodeId"
    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }
    Copy-Item -LiteralPath $ReceiptPath -Destination (Join-Path $destDir (Split-Path $ReceiptPath -Leaf)) -Force

    & git -C $wt add -A 2>&1 | Out-Null
    $dirty = (& git -C $wt status --porcelain 2>$null | Out-String).Trim()
    if (-not $dirty) { $out.pushed = $true; $out.error = 'no change (receipt identical)'; return }

    $leaf = Split-Path $ReceiptPath -Leaf
    $msg  = "receipt($NodeId): $leaf`n`nWritten by the ZoltarOne order executor. Receipts go to git because`nthis node's OneDrive mirror stops syncing after an unattended reboot.`n`nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>`nClaude-Session: https://claude.ai/code/session_01PeUnnBW3xtmtDyxyVvaRXM"
    $mf = Join-Path $env:TEMP "receipt-msg-$PID.txt"
    $msg | Set-Content $mf -Encoding utf8
    & git -C $wt commit -F $mf 2>&1 | Out-Null
    Remove-Item $mf -Force -EA SilentlyContinue

    $out.sha = (& git -C $wt rev-parse HEAD 2>$null | Out-String).Trim()
    & git -C $wt push -u origin $branch 2>&1 | Out-Null

    $remoteNow = (& git -C $RepoRoot ls-remote --heads origin $branch 2>$null | Out-String).Trim()
    $out.pushed = [bool]($out.sha -and $remoteNow -match [regex]::Escape($out.sha.Substring(0, 7)))
    if (-not $out.pushed) { $out.error = 'commit made but remote does not show the sha' }
}
catch {
    $out.error = $_.Exception.Message
}
finally {
    [pscustomobject]$out
}
