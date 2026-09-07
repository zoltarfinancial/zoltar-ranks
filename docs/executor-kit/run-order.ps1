<#
    run-order.ps1 — the ZoltarGenesis order executor
    ------------------------------------------------
    Written by zoltargenesis/claude-code, 2026-09-07, for away-mode.

    Runs as its OWN scheduled task, separate from ZoltarGenesis-FleetHeartbeat,
    so that a hung or refused order can never stop the pulse. The heartbeat says
    the node is alive; this says the node is working. Those must be able to fail
    independently or a stuck order looks like a dead machine.

    ONE ORDER PER CYCLE. Never two concurrently — enforced by a lock file AND by
    the task's own MultipleInstances=IgnoreNew.

    EVERY PATH PRODUCES A RECEIPT. Refusal, malformed input, gate failure, crash:
    all of them write to bridge/outbox/. A silent skip is indistinguishable from
    a dead node, and nobody is here to tell the difference.

    THE AGENT DOES NOT PUSH. claude works in the tree; this script inspects what
    changed and does the commit and push itself, only if the post-run gate
    passes. That is the difference between "we asked it not to" and "it cannot".

    Exit codes: 0 normal (including a clean refusal), 1 executor fault.
#>
[CmdletBinding()]
param(
    [string]$Workspace   = 'C:\Shared\ZoltarUnlimited',
    [string]$RepoRoot    = 'C:\Shared\ZoltarUnlimited\zoltar-ranks',
    [int]$TimeoutSec     = 900,          # wall-clock cap on one order
    [double]$MinFreeGb   = 0.5,          # this box has 7.92 GB and thrashes
    [double]$MaxBudgetUsd = 2.0,
    [switch]$DryRun                      # screen + gate, never launch claude
)

$ErrorActionPreference = 'Continue'
$startedAt = Get-Date

. (Join-Path $PSScriptRoot 'allowlist.ps1')

$OutboxDir = Join-Path $Workspace 'fleet\bridge\outbox'
$InboxDir  = Join-Path $Workspace 'fleet\bridge\inbox'
$HaltFile  = Join-Path $Workspace 'fleet\HALT'
$LockFile  = Join-Path $Workspace 'fleet\executor\.order.lock'
$DoneDir   = Join-Path $Workspace 'fleet\bridge\consumed'
$ScratchDir= Join-Path $Workspace 'fleet\executor\scratch'
$StateFile = Join-Path $Workspace 'fleet\executor\last-run.json'

$R = [ordered]@{
    at = $null; node_id = 'zoltargenesis'; fired_by = 'zoltargenesis/claude-code'
    order = [ordered]@{ id=$null; file=$null; channel=$null; provenance=$null; from=$null; title=$null }
    outcome = $null           # executed | refused | halted | no_orders | skipped | error
    refusal = [ordered]@{ rule_id=$null; rule_what=$null; evidence=$null }
    gate    = [ordered]@{ passed=$null; reason=$null; changed_paths=@(); protected_changed=@() }
    result  = [ordered]@{ branch=$null; sha=$null; pushed=$false; claude_exit=$null; claude_ms=$null; cost_usd=$null }
    machine = [ordered]@{ ram_free_gb=$null; halted=$null }
    by      = [ordered]@{ agent_id='zoltargenesis/claude-code'; node_id='zoltargenesis'; hostname=$null
                          lane='claude-code'; verified=$null; at=$null }
    notes   = @()
    errors  = @()
}
$notes  = New-Object System.Collections.ArrayList
$errors = New-Object System.Collections.ArrayList
function Note($t){ [void]$notes.Add($t) }
function Fail($w,$e){ $m = if($e -is [System.Management.Automation.ErrorRecord]){$e.Exception.Message}else{"$e"}; [void]$errors.Add("${w}: $m") }

$lockStream = $null
$orderFile  = $null
$consumed   = $false

try {
    # ---------------------------------------------------------------- identity
    $live = $env:COMPUTERNAME
    if ([string]::IsNullOrWhiteSpace($live)) { $live = [System.Net.Dns]::GetHostName() }
    $R.by.hostname = $live
    try {
        $ident = Get-Content (Join-Path $Workspace 'fleet\identity.json') -Raw -EA Stop | ConvertFrom-Json
        $names = @()
        foreach ($n in @($ident.attested_hostnames)) { if($n){ $names += $n.Trim().ToLowerInvariant() } }
        if (-not $names -and $ident.attested_hostname) { $names = @($ident.attested_hostname.Trim().ToLowerInvariant()) }
        $R.by.verified = if ([string]::IsNullOrWhiteSpace($live)) { $null } else { $names -contains $live.Trim().ToLowerInvariant() }
    } catch { $R.by.verified = $null; Fail 'identity' $_ }

    # An unattested executor must not act. Verified false means this workspace
    # was copied somewhere it does not belong; null means we could not tell.
    # Neither is a licence to run orders on someone else's machine.
    if ($R.by.verified -ne $true) {
        $R.outcome = 'refused'
        $R.refusal.rule_id = 'DENY-UNATTESTED'
        $R.refusal.rule_what = 'executor could not attest this node; refusing to run any order'
        Note "identity verified=$($R.by.verified); no order was read or run"
        return
    }

    # ------------------------------------------------------------ kill switch
    # Checked FIRST, before locks, RAM, git or orders. A stop button that only
    # works when everything else is healthy is not a stop button.
    if (Test-Path $HaltFile) {
        $R.outcome = 'halted'; $R.machine.halted = $true
        $reason = ''
        try { $reason = (Get-Content $HaltFile -Raw -EA SilentlyContinue).Trim() } catch {}
        Note "fleet\HALT present - executor stopped before touching anything. No order read, none consumed. HALT says: $(if($reason){$reason}else{'(empty file)'})"
        return
    }
    $R.machine.halted = $false

    # ------------------------------------------------------- single instance
    try {
        $lockDir = Split-Path $LockFile -Parent
        if (-not (Test-Path $lockDir)) { New-Item -ItemType Directory -Force -Path $lockDir | Out-Null }
        $lockStream = [System.IO.File]::Open($LockFile,'OpenOrCreate','ReadWrite','None')
    } catch {
        $R.outcome = 'skipped'
        Note 'another executor instance holds the lock; exiting without touching anything'
        return
    }

    # --------------------------------------------------------------- RAM gate
    try {
        $freeMb = (Get-Counter '\Memory\Available MBytes' -EA Stop).CounterSamples[0].CookedValue
        $R.machine.ram_free_gb = [math]::Round($freeMb/1024,2)
    } catch { Fail 'ram' $_ }
    if ($null -ne $R.machine.ram_free_gb -and $R.machine.ram_free_gb -lt $MinFreeGb) {
        $R.outcome = 'skipped'
        Note "free RAM $($R.machine.ram_free_gb) GB is under the $MinFreeGb GB floor - refusing to start rather than thrash a 7.92 GB machine into swap. No order consumed; it will be retried next cycle."
        return
    }

    # ------------------------------------------------------------- git pull
    try {
        $env:GIT_TERMINAL_PROMPT = '0'
        & git -C $RepoRoot fetch origin --prune 2>&1 | Out-Null
        $cur = (& git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
        if ($cur -eq 'main') { & git -C $RepoRoot pull --ff-only origin main 2>&1 | Out-Null }
        else { Note "clone is on branch '$cur', not main; fetched but did not pull" }
    } catch { Fail 'git_pull' $_ }

    # -------------------------------------------------------- collect orders
    # Two channels, deliberately unequal in provenance:
    #   git   -> AUTHORISED (someone with push rights committed it)
    #   inbox -> REQUEST    (the bound Cowork brain placed it from the dashboard)
    # Both are held to the same allowlist. The difference is recorded, not enforced,
    # because the allowlist is what actually protects the machine.
    $candidates = @()
    $gitOrders = Join-Path $RepoRoot 'orders\zoltargenesis'
    if (Test-Path $gitOrders) {
        foreach ($f in (Get-ChildItem $gitOrders -Filter *.md -File -EA SilentlyContinue | Sort-Object Name)) {
            if ($f.Name -eq 'README.md') { continue }
            $candidates += [pscustomobject]@{ file=$f; channel='git'; provenance='authorised' }
        }
    }
    foreach ($f in (Get-ChildItem $InboxDir -Filter *.md -File -EA SilentlyContinue | Sort-Object Name)) {
        if ($f.Name -eq 'README.md') { continue }
        $candidates += [pscustomobject]@{ file=$f; channel='inbox'; provenance='request' }
    }

    if (-not $candidates -or $candidates.Count -eq 0) {
        $R.outcome = 'no_orders'
        Note 'no orders in orders/zoltargenesis/ or fleet/bridge/inbox/'
        return
    }

    # git channel first: authorised beats request when both are waiting
    $pick = ($candidates | Sort-Object @{e={if($_.channel -eq 'git'){0}else{1}}}, @{e={$_.file.Name}})[0]
    $orderFile = $pick.file
    $R.order.file = $orderFile.FullName
    $R.order.channel = $pick.channel
    $R.order.provenance = $pick.provenance
    $R.order.id = [System.IO.Path]::GetFileNameWithoutExtension($orderFile.Name)

    # -------------------------------------------------------------- parse it
    $raw = $null
    try { $raw = Get-Content $orderFile.FullName -Raw -EA Stop } catch { Fail 'read_order' $_ }

    if ([string]::IsNullOrWhiteSpace($raw)) {
        $R.outcome = 'refused'
        $R.refusal.rule_id = 'MALFORMED-EMPTY'
        $R.refusal.rule_what = 'order file is empty or unreadable'
        Note 'refused as malformed; consumed so it cannot block the queue'
        $consumed = $true
        return
    }

    # Optional ```json metadata block. Its ABSENCE is not an error - an order is
    # allowed to be plain prose. Malformed JSON inside it is also not fatal: it
    # degrades provenance to unknown and is noted, rather than killing the loop.
    # A bad file must never take the executor down; that is how a node goes dark
    # on the first typo someone makes from a phone.
    try {
        $m = [regex]::Match($raw, '(?s)```json\s*(\{.*?\})\s*```')
        if ($m.Success) {
            $meta = $m.Groups[1].Value | ConvertFrom-Json -EA Stop
            if ($meta.from)  { $R.order.from  = "$($meta.from)" }
            if ($meta.title) { $R.order.title = "$($meta.title)" }
            if ($meta.id)    { $R.order.id    = "$($meta.id)" }
        } else { Note 'no json metadata block; treating the whole file as the instruction' }
    } catch { Note "json metadata block present but unparseable - continuing with provenance 'unknown': $($_.Exception.Message)" }

    if (-not $R.order.title) {
        $h = [regex]::Match($raw,'(?m)^\s*#\s+(.+)$')
        $R.order.title = if ($h.Success) { $h.Groups[1].Value.Trim() } else { $orderFile.Name }
    }

    # -------------------------------------------- L1: static allowlist screen
    $verdict = Test-OrderAllowed -Text $raw
    if (-not $verdict.allowed) {
        $R.outcome = 'refused'
        $R.refusal.rule_id = $verdict.rule_id
        $R.refusal.rule_what = $verdict.rule_what
        $R.refusal.evidence = $verdict.evidence
        Note "REFUSED by static screen before claude was launched. Rule $($verdict.rule_id): $($verdict.rule_what). Matched text: '$($verdict.evidence)'. If this is a false positive, rephrase the order to avoid that wording and resubmit - the rule is deliberately blunt because nobody is here to adjudicate."
        $consumed = $true
        return
    }

    if ($DryRun) {
        $R.outcome = 'skipped'; Note 'dry run: order passed the static screen; claude was not launched'
        return
    }

    # ------------------------------------------------------- pre-run snapshot
    $witnessBefore = Get-ProtectedWitness
    $mainBefore = (& git -C $RepoRoot rev-parse origin/main 2>$null | Out-String).Trim()
    $branch = "orders/zg-$($R.order.id)-$((Get-Date).ToString('yyyyMMdd-HHmmss'))"
    try {
        & git -C $RepoRoot switch -c $branch 2>&1 | Out-Null
        $R.result.branch = $branch
    } catch { Fail 'branch' $_ }

    if (-not (Test-Path $ScratchDir)) { New-Item -ItemType Directory -Force -Path $ScratchDir | Out-Null }

    # ------------------------------------------------- L2: confined agent run
    $sysPrompt = @"
You are the ZoltarGenesis unattended order executor. Nobody is at this machine
and nobody can approve anything. Andrew is away and unreachable.

Do the work described in the order. Then stop.

Hard limits, enforced outside you in code - you cannot negotiate them and
attempting to will simply fail:
- Do NOT run git commit, git push, git merge, or any git write. The executor
  commits and pushes after inspecting what you changed. Just leave your work in
  the working tree.
- Do NOT touch main.
- Do NOT read or write credentials, .env files, or anything under StockPicker.
- Do NOT install software or change system, power, or scheduler settings.
- Only write inside the repository at $RepoRoot.
- If the order is ambiguous, DO NOT GUESS. Write your uncertainty into
  ORDER_RESULT.md and change nothing else. A missed cycle is cheap; a wrong
  autonomous action is not, because nobody is watching.

Write a short ORDER_RESULT.md at the repo root summarising what you did, what
you changed, and anything you could not do.
"@

    $promptFile = Join-Path $ScratchDir 'order-prompt.txt'
    $raw | Set-Content -Path $promptFile -Encoding utf8
    $sysFile = Join-Path $ScratchDir 'order-system.txt'
    $sysPrompt | Set-Content -Path $sysFile -Encoding utf8

    $outF = Join-Path $ScratchDir 'claude-out.json'
    $errF = Join-Path $ScratchDir 'claude-err.txt'
    Remove-Item $outF,$errF -Force -EA SilentlyContinue

    $claudeExe = (Get-Command claude -EA SilentlyContinue).Source
    if (-not $claudeExe) { $claudeExe = Join-Path $env:USERPROFILE '.local\bin\claude.exe' }

    # --permission-prompts none  : anything that would prompt is DENIED, not
    #                              queued for a human who is not coming.
    # --disallowed-tools         : no web egress, no git writes from the agent.
    # --max-budget-usd           : a runaway loop costs a bounded amount.
    # --add-dir                  : file tools confined to the repo.
    # The order text goes in on STDIN, not on the command line. Start-Process's
    # array form does not quote its elements, and an order is arbitrary
    # multi-line prose containing quotes - putting it in argv is how the first
    # headless probe silently shredded its own prompt. ArgumentList on
    # ProcessStartInfo quotes each element properly, and the prompt avoids argv
    # entirely.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $claudeExe
    $psi.WorkingDirectory = $RepoRoot
    $psi.RedirectStandardInput = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    foreach ($a in @(
        '-p',
        '--output-format','json',
        '--permission-mode','acceptEdits',
        '--permission-prompts','none',
        '--disallowed-tools','WebFetch WebSearch Bash(git commit:*) Bash(git push:*) Bash(git merge:*) Bash(git reset:*) Bash(git clean:*)',
        '--add-dir',$RepoRoot,
        '--max-budget-usd',"$MaxBudgetUsd",
        '--append-system-prompt',$sysPrompt
    )) { [void]$psi.ArgumentList.Add($a) }

    $t0 = Get-Date
    $proc = [System.Diagnostics.Process]::Start($psi)
    $proc.StandardInput.Write($raw)
    $proc.StandardInput.Close()
    $stdout = $proc.StandardOutput.ReadToEndAsync()
    $stderr = $proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($TimeoutSec * 1000)) {
        try { $proc.Kill($true) } catch {}
        Note "claude exceeded the ${TimeoutSec}s wall-clock cap and was killed"
        Fail 'claude' "timeout after ${TimeoutSec}s"
    }
    $R.result.claude_ms = [int]((Get-Date)-$t0).TotalMilliseconds
    try { $R.result.claude_exit = $proc.ExitCode } catch {}
    $so = ''; $se = ''
    try { $so = $stdout.Result } catch {}
    try { $se = $stderr.Result } catch {}
    if ($so) { $so | Set-Content $outF -Encoding utf8 }
    if ($se) { $se | Set-Content $errF -Encoding utf8; Note "claude stderr: $($se.Substring(0,[Math]::Min(400,$se.Length)))" }
    try {
        $j = $so | ConvertFrom-Json -EA Stop
        if ($null -ne $j.total_cost_usd) { $R.result.cost_usd = $j.total_cost_usd }
    } catch { }

    # ------------------------------------------------------ L3: post-run gate
    $gateReasons = @()
    $witnessAfter = Get-ProtectedWitness
    $protChanged = Compare-ProtectedWitness -Before $witnessBefore -After $witnessAfter
    $R.gate.protected_changed = @($protChanged)
    if ($protChanged.Count) { $gateReasons += "protected files changed: $($protChanged -join ', ')" }

    $nowBranch = (& git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
    if ($nowBranch -eq 'main') { $gateReasons += 'HEAD is on main after the run' }

    $mainAfter = (& git -C $RepoRoot rev-parse origin/main 2>$null | Out-String).Trim()
    if ($mainBefore -and $mainAfter -and ($mainBefore -ne $mainAfter)) { $gateReasons += 'origin/main moved during the run' }

    $changed = @()
    try {
        $st = & git -C $RepoRoot status --porcelain 2>$null
        foreach ($line in @($st)) {
            if ("$line".Trim()) { $changed += ("$line".Substring(3)).Trim().Trim('"') }
        }
    } catch { Fail 'status' $_ }
    $R.gate.changed_paths = @($changed)

    foreach ($p in $changed) {
        $ok = $false
        foreach ($pre in $script:ZG_WRITABLE_PREFIXES) { if ($p -like "$pre*") { $ok = $true; break } }
        if ($p -eq 'ORDER_RESULT.md') { $ok = $true }
        if (-not $ok) { $gateReasons += "changed path outside the writable set: $p" }
    }

    if ($gateReasons.Count) {
        $R.gate.passed = $false
        $R.gate.reason = ($gateReasons -join ' | ')
        $R.outcome = 'refused'
        $R.refusal.rule_id = 'GATE-FAILED'
        $R.refusal.rule_what = 'post-run verification gate rejected the result; nothing was pushed'
        Note "GATE FAILED - $($R.gate.reason). The working tree was left as-is for inspection and NOTHING was committed or pushed."
        $consumed = $true
        return
    }
    $R.gate.passed = $true

    # --------------------------------------------- commit + push (executor's)
    if ($changed.Count -eq 0) {
        Note 'order ran and produced no file changes; nothing to commit'
        $R.outcome = 'executed'
    } else {
        try {
            & git -C $RepoRoot add -A 2>&1 | Out-Null
            $msg = "order($($R.order.channel)): $($R.order.title)`n`nOrder id: $($R.order.id)`nChannel: $($R.order.channel) (provenance: $($R.order.provenance))`nExecuted unattended by the ZoltarGenesis order executor.`nPost-run gate: passed.`n`nCo-Authored-By: Claude Opus 5 <noreply@anthropic.com>`nClaude-Session: https://claude.ai/code/session_01J1RNAb38e4mU2ZA2c5Mr8P"
            $mf = Join-Path $ScratchDir 'commitmsg.txt'
            $msg | Set-Content $mf -Encoding utf8
            & git -C $RepoRoot commit -F $mf 2>&1 | Out-Null
            $R.result.sha = (& git -C $RepoRoot rev-parse HEAD 2>$null | Out-String).Trim()
            & git -C $RepoRoot push -u origin $branch 2>&1 | Out-Null
            $remote = (& git -C $RepoRoot ls-remote --heads origin $branch 2>$null | Out-String).Trim()
            $R.result.pushed = [bool]($remote -match [regex]::Escape($R.result.sha.Substring(0,7)))
            if (-not $R.result.pushed -and $remote) { $R.result.pushed = $true }
            $R.outcome = 'executed'
        } catch { Fail 'commit_push' $_; $R.outcome = 'error' }
    }
    $consumed = $true
}
catch {
    $R.outcome = 'error'
    Fail 'fatal' $_
}
finally {
    # ------------------------------------------------------------- the receipt
    try {
        $now = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
        $R.at = $now; $R.by.at = $now
        $R.notes = @($notes); $R.errors = @($errors)
        $R.duration_ms = [int]((Get-Date)-$startedAt).TotalMilliseconds

        # move the consumed order aside so it is not re-run
        if ($consumed -and $orderFile -and (Test-Path $orderFile.FullName)) {
            try {
                if (-not (Test-Path $DoneDir)) { New-Item -ItemType Directory -Force -Path $DoneDir | Out-Null }
                $suffix = if ($R.result.sha) { $R.result.sha.Substring(0,7) } else { $R.outcome }
                $dest = Join-Path $DoneDir ("{0}--{1}--{2}{3}" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $orderFile.BaseName, $suffix, $orderFile.Extension)
                Move-Item -LiteralPath $orderFile.FullName -Destination $dest -Force
                $R.order.moved_to = $dest
            } catch { Fail 'move_order' $_ }
        }

        if (-not (Test-Path $OutboxDir)) { New-Item -ItemType Directory -Force -Path $OutboxDir | Out-Null }

        # Receipts are written for EVERY outcome, including no_orders, so that a
        # reader can tell "nothing to do" from "executor never ran". Quiet cycles
        # go to a rolling file rather than accumulating hundreds of files.
        $stamp = (Get-Date).ToString('yyyyMMdd-HHmm')
        if ($R.outcome -in @('no_orders','skipped','halted')) {
            $f = Join-Path $OutboxDir 'executor-status.json'
        } else {
            $f = Join-Path $OutboxDir ("{0}-receipt-{1}-{2}.json" -f $stamp, $R.order.id, $R.outcome)
        }
        ($R | ConvertTo-Json -Depth 8) | Set-Content -Path $f -Encoding utf8
        ($R | ConvertTo-Json -Depth 8) | Set-Content -Path $StateFile -Encoding utf8

        # return the clone to main so the next cycle starts clean
        try {
            $b = (& git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null | Out-String).Trim()
            if ($b -ne 'main' -and $R.gate.passed -ne $false) { & git -C $RepoRoot switch main 2>&1 | Out-Null }
        } catch {}
    } catch {
        try { "$(Get-Date -Format o) receipt write failed: $($_.Exception.Message)" |
              Add-Content (Join-Path $OutboxDir 'RECEIPT-WRITE-FAILED.txt') -EA SilentlyContinue } catch {}
    }
    if ($lockStream) { try { $lockStream.Close(); $lockStream.Dispose() } catch {} }
}

if ($R.outcome -eq 'error') { exit 1 } else { exit 0 }
