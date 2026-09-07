<#
    verify-guard.ps1 — prove a node's identity guard before trusting it
    -------------------------------------------------------------------
    Origin: zoltargenesis/claude-code, 2026-09-07.

    Run this on a new node AFTER installing heartbeat.ps1 and BEFORE relying on
    any stamp it writes. It builds throwaway workspaces in a temp directory,
    points the real heartbeat script at each one, and checks what it stamped.

    Nothing here touches the node's actual identity.json, heartbeat, or repo.

    WHY A NEW NODE SHOULD BOTHER
    ----------------------------
    The guard is the only thing standing between "this workspace was copied to
    another machine" and "that machine writes stamps claiming to be you". On
    ZoltarGenesis the guard was correct and the DATA was stale, and the handoff
    that described the situation got it backwards - it called a correct refusal
    a false positive, which is the kind of misreading that gets a guard
    weakened. Seven fixtures take about a minute and settle it with evidence.

    The three-state rule is the part most likely to be broken by a well-meaning
    edit, so two of the seven fixtures exist only to defend it:
        true  = read the hostname, matched an attested name
        false = read the hostname, matched nothing
        null  = could not read the identity card at all  -> UNATTESTED
    null must never collapse into false. "I checked and you are an impostor"
    and "I could not check" are different facts and consumers colour them
    differently.

    Usage:
        pwsh -File verify-guard.ps1 -HeartbeatScript .\heartbeat.ps1 -NodeId <id>
    Exit code 0 if all fixtures pass, 1 otherwise.
#>
[CmdletBinding()]
param(
    [string]$HeartbeatScript = (Join-Path $PSScriptRoot 'heartbeat.ps1'),
    [string]$NodeId = 'testnode',
    [string]$RepoRoot = '',
    [string]$WorkDir = (Join-Path $env:TEMP ('identity-kit-verify-' + [guid]::NewGuid().ToString('N').Substring(0,8)))
)

$ErrorActionPreference = 'Continue'
if (-not (Test-Path $HeartbeatScript)) { Write-Host "heartbeat script not found: $HeartbeatScript" -ForegroundColor Red; exit 1 }

$live = $env:COMPUTERNAME
if ([string]::IsNullOrWhiteSpace($live)) { $live = [System.Net.Dns]::GetHostName() }

Write-Host ""
Write-Host "identity guard verification" -ForegroundColor Cyan
Write-Host "  script    : $HeartbeatScript"
Write-Host "  node_id   : $NodeId"
Write-Host "  live host : $live"
Write-Host ""

$pass = 0; $fail = 0; $results = @()

function Invoke-Fixture {
    param([string]$Name, [string]$Json, [string]$ExpectVerified, [string]$ExpectOutcome, [string]$Why)

    $w = Join-Path $WorkDir $Name
    New-Item -ItemType Directory -Force -Path (Join-Path $w 'fleet') | Out-Null
    $Json | Set-Content (Join-Path $w 'fleet\identity.json') -Encoding utf8

    $args = @('-NoProfile','-File',$HeartbeatScript,'-NodeId',$NodeId,'-Workspace',$w,'-FireKind',"verify_$Name")
    if ($RepoRoot) { $args += @('-RepoRoot',$RepoRoot) }
    & pwsh @args | Out-Null

    $stamp = Join-Path $w "fleet\heartbeat\$NodeId.json"
    if (-not (Test-Path $stamp)) {
        # A missing stamp is itself a failure: the script must write in a
        # finally block, so even a hard failure leaves a record.
        $script:fail++
        $script:results += [pscustomobject]@{ fixture=$Name; verified='NO STAMP'; outcome='NO STAMP'; expected="$ExpectVerified/$ExpectOutcome"; result='FAIL'; why=$Why }
        return
    }
    $j = Get-Content $stamp -Raw | ConvertFrom-Json
    $v = if ($null -eq $j.by.verified) { 'null' } else { "$($j.by.verified)".ToLower() }
    $ok = ($v -eq $ExpectVerified.ToLower()) -and ($j.outcome -eq $ExpectOutcome)
    if ($ok) { $script:pass++ } else { $script:fail++ }
    $script:results += [pscustomobject]@{
        fixture=$Name; verified=$v; outcome=$j.outcome
        expected="$ExpectVerified/$ExpectOutcome"; result=$(if($ok){'PASS'}else{'FAIL'}); why=$Why
    }
}

# 1 - v2 array, live name present among several
Invoke-Fixture 'v2-array-match' `
    "{`"schema_version`":2,`"attested_hostnames`":[`"OTHERBOX`",`"$live`"]}" `
    'true' 'ok' 'v2 array matches any entry, not just the first'

# 2 - v1 singular still verifies (the fleet is mid-migration)
Invoke-Fixture 'v1-singular-fallback' `
    "{`"schema_version`":1,`"attested_hostname`":`"$live`"}" `
    'true' 'ok' 'a guard that only knows v2 fails closed on every unmigrated node'

# 3 - the copied-workspace case, which is the whole point of the guard
Invoke-Fixture 'v2-no-match' `
    "{`"schema_version`":2,`"attested_hostnames`":[`"SOMEONEELSE`"]}" `
    'false' 'identity_mismatch' 'workspace copied to a machine it does not belong to'

# 4 - an authorised rename, observed for the first time: a MATCH, not a mismatch
Invoke-Fixture 'rename-pending-satisfied' `
    "{`"schema_version`":2,`"attested_hostnames`":[`"OLDNAME`"],`"rename_pending`":{`"from`":`"OLDNAME`",`"to`":`"$live`",`"expires_at`":`"2099-01-01T00:00:00-05:00`"}}" `
    'true' 'ok' 'and it must stay outcome=ok - the follow-up instruction is a notice, not an error'

# 5 - the transition window closed and the rename never happened
Invoke-Fixture 'rename-expired' `
    "{`"schema_version`":2,`"attested_hostnames`":[`"NOPE`"],`"rename_pending`":{`"from`":`"$live`",`"to`":`"NEWNAME`",`"expires_at`":`"2020-01-01T00:00:00-05:00`"}}" `
    'false' 'identity_mismatch' 'an expired authorisation is not an authorisation'

# 6 - a card with nothing to compare against must not silently pass
Invoke-Fixture 'empty-card' `
    "{`"schema_version`":2}" `
    'false' 'identity_mismatch' 'no attested names at all is a refusal, never a default-allow'

# 7 - THREE-STATE: unreadable card is null, NOT false
Invoke-Fixture 'unreadable-card' `
    '{ this is not valid json' `
    'null' 'degraded' 'null means unattested; collapsing it into false loses a distinction consumers colour differently'

Write-Host ""
$results | Format-Table fixture,verified,outcome,expected,result -AutoSize | Out-String | Write-Host
foreach ($r in $results) { if ($r.result -eq 'FAIL') { Write-Host ("  FAILED {0}: {1}" -f $r.fixture, $r.why) -ForegroundColor Red } }

Write-Host ""
if ($fail -eq 0) {
    Write-Host "ALL $pass FIXTURES PASSED - this node's guard may be trusted." -ForegroundColor Green
} else {
    Write-Host "$fail of $($pass+$fail) FIXTURES FAILED - do NOT trust this node's stamps until fixed." -ForegroundColor Red
}
try { Remove-Item $WorkDir -Recurse -Force -ErrorAction SilentlyContinue } catch {}
exit $(if ($fail -eq 0) { 0 } else { 1 })
