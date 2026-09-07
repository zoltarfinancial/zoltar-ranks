<#
    probe-claude-headless.ps1
    -------------------------
    Answers ONE question, the load-bearing unknown of away-mode:

        Does Claude Code headless (`claude -p`) run under a Windows scheduled
        task, non-interactively, as an S4U principal, with inherited auth?

    Written by zoltargenesis/claude-code, 2026-09-07.

    The prompt used deliberately requires NO TOOLS — it asks for a single word.
    That isolates the three things actually in question:
      1. can the binary start in session 0 under an S4U token
      2. does it find and decrypt stored credentials there
      3. does it reach the API and return
    A prompt that used tools would add permission-prompt behaviour as a fourth
    variable and muddy the result. Tool use is tested separately, by the
    executor itself, once this says yes.

    Writes a JSON result and NEVER throws: a probe that dies without a record
    is indistinguishable from a probe that was never run.
#>
[CmdletBinding()]
param(
    [string]$OutDir  = 'C:\Shared\ZoltarUnlimited\fleet\executor\probe-results',
    [string]$Tag     = 'manual',
    [int]$TimeoutSec = 180
)

$ErrorActionPreference = 'Continue'
$started = Get-Date

$r = [ordered]@{
    probe            = 'claude-headless'
    tag              = $Tag
    at               = $started.ToString('yyyy-MM-ddTHH:mm:ssK')
    # -- context: the whole point is WHICH context this ran in --
    context          = [ordered]@{
        whoami            = $null
        session_id        = $null
        is_interactive    = $null
        user_profile      = $env:USERPROFILE
        interactive_users = $null
        computername      = $env:COMPUTERNAME
    }
    claude           = [ordered]@{
        on_path        = $null
        path           = $null
        version        = $null
        creds_file     = $null
        creds_readable = $null
    }
    run              = [ordered]@{
        exit_code   = $null
        stdout      = $null
        stderr      = $null
        duration_ms = $null
        timed_out   = $false
    }
    verdict          = $null
    verdict_reason   = $null
}

try {
    # ---------------- context ----------------
    try { $r.context.whoami = (whoami.exe 2>&1 | Out-String).Trim() } catch {}
    try { $r.context.session_id = (Get-Process -Id $PID).SessionId } catch {}
    try { $r.context.is_interactive = [Environment]::UserInteractive } catch {}
    try {
        # who is actually logged on right now — this is what makes the
        # logged-in vs logged-out distinction measurable rather than assumed
        $q = (query.exe user 2>&1 | Out-String).Trim()
        $r.context.interactive_users = $q
    } catch { $r.context.interactive_users = "query user failed: $($_.Exception.Message)" }

    # ---------------- claude presence ----------------
    $cmd = Get-Command claude -ErrorAction SilentlyContinue
    if (-not $cmd) {
        # a scheduled task can have a narrower PATH than an interactive shell
        $fallback = Join-Path $env:USERPROFILE '.local\bin\claude.exe'
        if (Test-Path $fallback) { $cmd = Get-Item $fallback }
    }
    $r.claude.on_path = [bool](Get-Command claude -ErrorAction SilentlyContinue)
    if ($cmd) {
        $exe = if ($cmd.PSObject.Properties['Source'] -and $cmd.Source) { $cmd.Source } else { $cmd.FullName }
        $r.claude.path = $exe
        try { $r.claude.version = (& $exe --version 2>&1 | Out-String).Trim() } catch { $r.claude.version = "ERR: $($_.Exception.Message)" }
    }

    # credentials: EXISTENCE and READABILITY only. Never the contents.
    # Readability under S4U is the specific thing that can differ from an
    # interactive logon (DPAPI user master key availability).
    $credPath = Join-Path $env:USERPROFILE '.claude\.credentials.json'
    $r.claude.creds_file = (Test-Path $credPath)
    if ($r.claude.creds_file) {
        try {
            $fs = [System.IO.File]::Open($credPath,'Open','Read','ReadWrite')
            $len = $fs.Length; $fs.Close()
            $r.claude.creds_readable = $true
            $r.claude.creds_bytes = $len

            # STORAGE-FORM DISCRIMINATOR — this is the whole logged-out question.
            # If credentials are a DPAPI-protected blob, decryption needs the
            # user's master key, which is unlocked at INTERACTIVE logon; an S4U
            # task with nobody logged on may not be able to open it. If they are
            # a plain token file, logon state is irrelevant to reading them.
            # We inspect ONLY the first byte and whether the text parses as JSON.
            # No key, no value, and no secret is read, logged, or transmitted.
            try {
                $firstByte = [System.IO.File]::ReadAllBytes($credPath)[0]
                $r.claude.creds_first_byte = ('0x{0:X2}' -f $firstByte)
                $r.claude.creds_looks_json = ($firstByte -eq 0x7B)   # '{'
                $r.claude.creds_storage_form = if ($firstByte -eq 0x7B) {
                    'plaintext-json — not DPAPI-wrapped, so readability does not depend on an interactive logon'
                } else {
                    'NOT json — possibly DPAPI/binary; readability under a logged-out S4U task is NOT established'
                }
            } catch { $r.claude.creds_storage_form = "could not determine: $($_.Exception.Message)" }
        } catch {
            $r.claude.creds_readable = $false
            $r.claude.creds_error = $_.Exception.Message
        }
    }

    # ---------------- the actual run ----------------
    if (-not $cmd) {
        $r.verdict = 'FAIL'
        $r.verdict_reason = 'claude executable not found on PATH nor at the known install location'
    }
    else {
        $exe = $r.claude.path
        $outF = Join-Path $env:TEMP "claudeprobe-$PID-out.txt"
        $errF = Join-Path $env:TEMP "claudeprobe-$PID-err.txt"
        $t0 = Get-Date

        # -p is headless. The prompt needs no tools, so no permission prompt.
        #
        # QUOTING: pass ONE pre-quoted argument string. Start-Process's
        # -ArgumentList array form joins elements with spaces and does NOT quote
        # them, so an array of @('-p','Reply with the single word...') reaches
        # the exe as `-p Reply with the single word ...` and the prompt is
        # shredded into separate argv entries. First run of this probe hit
        # exactly that and returned a generic greeting instead of the token —
        # which read as a capability failure and was a harness bug.
        $promptArg = '-p "Reply with the single word PONG and nothing else."'
        $p = Start-Process -FilePath $exe `
                -ArgumentList $promptArg `
                -WorkingDirectory 'C:\Shared\ZoltarUnlimited' `
                -RedirectStandardOutput $outF -RedirectStandardError $errF `
                -NoNewWindow -PassThru

        if (-not $p.WaitForExit($TimeoutSec * 1000)) {
            $r.run.timed_out = $true
            try { $p.Kill() } catch {}
            Start-Sleep -Milliseconds 500
        }
        $r.run.duration_ms = [int]((Get-Date) - $t0).TotalMilliseconds
        try { $r.run.exit_code = $p.ExitCode } catch {}
        if (Test-Path $outF) { $r.run.stdout = ((Get-Content $outF -Raw -EA SilentlyContinue) + '').Trim() }
        if (Test-Path $errF) { $r.run.stderr = ((Get-Content $errF -Raw -EA SilentlyContinue) + '').Trim() }
        Remove-Item $outF,$errF -Force -EA SilentlyContinue

        if ($r.run.timed_out) {
            $r.verdict = 'FAIL'; $r.verdict_reason = "no response within ${TimeoutSec}s — headless run hung (a hang under the scheduler usually means it is waiting on something interactive)"
        }
        elseif ($r.run.exit_code -ne 0) {
            $r.verdict = 'FAIL'; $r.verdict_reason = "exit code $($r.run.exit_code); see stderr"
        }
        elseif ($r.run.stdout -match 'PONG') {
            $r.verdict = 'PASS'; $r.verdict_reason = 'headless run returned the expected token — binary started, auth resolved, API reached'
        }
        else {
            $r.verdict = 'FAIL'; $r.verdict_reason = 'exit 0 but the expected token was not in stdout'
        }
    }
}
catch {
    $r.verdict = 'FAIL'
    $r.verdict_reason = "probe threw: $($_.Exception.Message)"
}
finally {
    try {
        if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }
        $r.total_ms = [int]((Get-Date) - $started).TotalMilliseconds
        $f = Join-Path $OutDir ("probe-{0}-{1}.json" -f $Tag, $started.ToString('yyyyMMdd-HHmmss'))
        ($r | ConvertTo-Json -Depth 6) | Set-Content -Path $f -Encoding utf8
        # stable pointer to the newest result for this tag
        ($r | ConvertTo-Json -Depth 6) | Set-Content -Path (Join-Path $OutDir "latest-$Tag.json") -Encoding utf8
    } catch { }
}
