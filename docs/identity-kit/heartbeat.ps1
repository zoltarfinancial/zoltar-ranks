<#
    Fleet shell-side heartbeat  --  IDENTITY KIT TEMPLATE
    -----------------------------------------------------
    Writes <Workspace>\fleet\heartbeat\<NodeId>.json every fire.

    Origin     : zoltargenesis/claude-code, 2026-09-07. Proven on ZoltarGenesis;
                 packaged as the fleet template so the next node copies rather
                 than re-derives. Change the param block and nothing else.
    Schema     : claude/fleet-registry.md §3

    Four contracts this script keeps, each one because the fleet has been bitten:

    1. IT ALWAYS WRITES. The write is in a finally block. A fire that throws still
       stamps, with outcome:"error" and the exception text. A node that only
       heartbeats when healthy is indistinguishable from a node that is off.

    2. EVERY METRIC FAILS INDEPENDENTLY. Each probe is in its own try. One dead
       counter cannot null out the other five, and each failure is named in
       `errors` rather than silently becoming a null.

    3. NULL IS NOT ZERO. A metric that could not be read is null and appears in
       `errors`. Nothing here ever emits 0 to mean "unknown".

    4. IT ATTESTS ITSELF. It re-reads fleet/identity.json and compares
       attested_hostname to the live hostname every fire, and stamps
       by.verified true / false / null accordingly. If the workspace is ever
       copied to another machine, the heartbeats from it say so rather than
       impersonating this node.

    NOTE ON WMI: this node's root\cimv2 has no Win32_* classes (F-ZG-4), so this
    script deliberately uses NO WMI. CPU comes from a perf counter, RAM from a
    perf counter, power from a kernel32 P/Invoke, disk from the Storage
    namespace. All four were verified working on this node on 2026-09-07.

    NOTE ON PYTHON: not used. Bare `python` here is 2.7.18 (F-ZG-5); keeping the
    heartbeat in PowerShell removes that failure mode from the scheduled task.
#>

# ===========================================================================
#  NEW NODE: change ONLY this param block. Nothing below it is node-specific.
# ===========================================================================
[CmdletBinding()]
param(
    # The node's id. Must match node_id in fleet/identity.json on this machine.
    [string]$NodeId    = 'CHANGEME',
    # The lane running this heartbeat, used for the agent_id and the by block.
    [string]$Lane      = 'claude-code',
    [string]$Workspace = 'C:\Shared\ZoltarUnlimited',
    [string]$RepoRoot  = 'C:\Shared\ZoltarUnlimited\zoltar-ranks',
    [string]$RemoteUrl = 'https://github.com/zoltarfinancial/zoltar-ranks',
    [string]$FireKind  = 'scheduled'
)

$AgentId = "$NodeId/$Lane"

$ErrorActionPreference = 'Continue'

# --------------------------------------------------------------------------- #
# state the finally block will write no matter what happens above it
# --------------------------------------------------------------------------- #
$outcome   = 'ok'
$errors    = New-Object System.Collections.ArrayList
# notices are things a reader should ACT on but which are not failures. Keeping
# them out of `errors` matters: any entry in `errors` degrades `outcome`, and a
# successfully observed rename is a match, not a degradation. Routing an
# informational message through the error channel would make "something worked
# and needs follow-up" and "something broke" render identically — the same
# conflation this script exists to avoid.
$notices   = New-Object System.Collections.ArrayList
$startedAt = Get-Date

$hb = [ordered]@{
    schema_version = 1
    node_id        = $NodeId
    at             = $null
    fired_by       = $AgentId
    fire_kind      = $FireKind
    outcome        = $null
    load           = [ordered]@{ cpu_pct = $null; ram_free_gb = $null }
    power          = [ordered]@{ has_battery = $null; on_ac = $null; charge_pct = $null }
    repo           = [ordered]@{ branch = $null; head_sha = $null; dirty = $null }
    disk           = [ordered]@{ c_free_gb = $null }
    # ZoltarOne's convention (docs/courier-protocol.md): stamped every fire so a
    # reader can tell a post-reboot stamp from a pre-reboot one without guessing
    # from timestamps. Adopted here unchanged.
    boot_at            = $null
    since_boot_seconds = $null
    boot_storm         = $null
    boot_storm_note    = $null
    duration_ms        = $null
    agents         = @($AgentId)
    reachability   = [ordered]@{
        device_files              = $null
        device_shell              = $true
        artifact_db               = $false
        onedrive_fleet_transport  = $false
        git_remote_read           = $null
        git_remote_write          = $null
    }
    by             = [ordered]@{
        agent_id        = $AgentId
        node_id         = $NodeId
        hostname        = $null
        lane            = $Lane
        verified        = $null
        at              = $null
        # which identity.json was actually loaded, and what schema it was.
        # Added 2026-09-07 after handoff-02 could not tell which file a failing
        # guard had read. A stamp that names its own source settles that.
        identity_file   = $null
        identity_schema = $null
        rename_observed = $false
    }
    errors         = @()
    notices        = @()
    note           = $null
}

function Add-Notice([string]$text) { [void]$notices.Add($text) }

function Add-Err([string]$where, $ex) {
    $msg = if ($ex -is [System.Management.Automation.ErrorRecord]) { $ex.Exception.Message } else { "$ex" }
    [void]$errors.Add("${where}: $msg")
}

try {
    # ----------------------------------------------------------------------- #
    # 0. identity — attest before claiming to be this node
    # ----------------------------------------------------------------------- #
    $liveHost = $null
    try {
        $liveHost = $env:COMPUTERNAME
        if ([string]::IsNullOrWhiteSpace($liveHost)) { $liveHost = [System.Net.Dns]::GetHostName() }
    } catch { Add-Err 'hostname' $_ }

    $hb.by.hostname = $liveHost

    # Understands identity.json v2 (attested_hostnames array + rename_pending)
    # AND v1 (singular attested_hostname). The v1 fallback is deliberate: this
    # script is the template the other nodes copy, and ZoltarOne and ZoltarLead
    # are still v1. A guard that only understood the newest schema would fail
    # closed on every node that has not migrated yet.
    #
    # verified stays THREE-STATE and the states never collapse:
    #   $true  matched an attested name
    #   $false read the hostname, matched nothing
    #   $null  could not read the hostname at all -> unattested
    try {
        $idPath = Join-Path $Workspace 'fleet\identity.json'
        # record which file was actually loaded. Handoff-02 suspected two
        # identity files being read by different callers; stamping the resolved
        # path means a future session reads the answer instead of re-deriving it.
        $hb.by.identity_file = $idPath
        $ident  = Get-Content $idPath -Raw -ErrorAction Stop | ConvertFrom-Json
        $hb.by.identity_schema = $(if ($null -ne $ident.schema_version) { [int]$ident.schema_version } else { $null })

        $live = if ($liveHost) { $liveHost.Trim().ToLowerInvariant() } else { $null }

        # candidate names, newest schema first, v1 singular as fallback
        $names = New-Object System.Collections.ArrayList
        foreach ($n in @($ident.attested_hostnames)) {
            if (-not [string]::IsNullOrWhiteSpace($n)) { [void]$names.Add($n.Trim().ToLowerInvariant()) }
        }
        if ($names.Count -eq 0 -and -not [string]::IsNullOrWhiteSpace($ident.attested_hostname)) {
            [void]$names.Add($ident.attested_hostname.Trim().ToLowerInvariant())
        }

        $rp        = $ident.rename_pending
        $rpTo      = if ($rp -and -not [string]::IsNullOrWhiteSpace($rp.to))   { $rp.to.Trim().ToLowerInvariant() }   else { $null }
        $rpFrom    = if ($rp -and -not [string]::IsNullOrWhiteSpace($rp.from)) { $rp.from.Trim().ToLowerInvariant() } else { $null }
        $rpExpired = $false
        if ($rp -and -not [string]::IsNullOrWhiteSpace($rp.expires_at)) {
            try { $rpExpired = ([datetimeoffset]::Parse($rp.expires_at) -lt [datetimeoffset]::Now) }
            catch { Add-Err 'identity' "rename_pending.expires_at unparseable: '$($rp.expires_at)'" }
        }

        if ($null -eq $live) {
            $hb.by.verified = $null
            Add-Err 'identity' 'hostname unreadable; heartbeat is unattested'
        }
        elseif ($names.Count -eq 0 -and $null -eq $rpTo) {
            # neither schema supplied anything to compare against
            $hb.by.verified = $false
            $outcome = 'identity_mismatch'
            Add-Err 'identity' ("identity.json at '{0}' carries no attested_hostnames and no attested_hostname; nothing to verify live '{1}' against" -f $idPath, $liveHost)
        }
        elseif ($names -contains $live) {
            $hb.by.verified = $true
        }
        elseif ($rpTo -and $rpTo -eq $live -and -not $rpExpired) {
            # an authorised rename, observed for the first time. A MATCH.
            $hb.by.verified = $true
            $hb.by.rename_observed = $true
            Add-Notice ("rename_pending satisfied: live '{0}' is the authorised new name (from '{1}'). ACTION: complete the rename per identity.json — prune attested_hostnames to the new name, move the old name to _former_hostnames, set renamed_at, clear rename_pending." -f $liveHost, $rp.from)
        }
        elseif ($rpFrom -and $rpFrom -eq $live -and $rpExpired) {
            $hb.by.verified = $false
            $outcome = 'identity_mismatch'
            Add-Err 'identity' ("rename to '{0}' EXPIRED at {1} and this machine is still '{2}' — the authorised transition window closed without the rename happening" -f $rp.to, $rp.expires_at, $liveHost)
        }
        else {
            $hb.by.verified = $false
            $outcome = 'identity_mismatch'
            $expected = if ($names.Count) { ($names -join "', '") } else { '<none>' }
            Add-Err 'identity' ("attested '{0}' != live '{1}' — this workspace is not on the node it claims (identity.json: {2}, schema v{3})" -f $expected, $liveHost, $idPath, $hb.by.identity_schema)
        }
    } catch {
        $hb.by.verified = $null
        Add-Err 'identity' $_
    }

    # ----------------------------------------------------------------------- #
    # 1. CPU — perf counter, no WMI
    # ----------------------------------------------------------------------- #
    try {
        $c = (Get-Counter '\Processor(_Total)\% Processor Time' -ErrorAction Stop).CounterSamples[0].CookedValue
        $hb.load.cpu_pct = [math]::Round($c, 1)
    } catch { Add-Err 'cpu_pct' $_ }

    # ----------------------------------------------------------------------- #
    # 2. RAM free — perf counter, no WMI
    # ----------------------------------------------------------------------- #
    try {
        $m = (Get-Counter '\Memory\Available MBytes' -ErrorAction Stop).CounterSamples[0].CookedValue
        $hb.load.ram_free_gb = [math]::Round($m / 1024, 2)
    } catch { Add-Err 'ram_free_gb' $_ }

    # ----------------------------------------------------------------------- #
    # 3. Power — kernel32 GetSystemPowerStatus, no WMI, no WinForms
    #    (Win32_Battery does not exist on this node; and reporting has_battery
    #     false on a failed read is exactly the bug filed as F-ZG-10.)
    # ----------------------------------------------------------------------- #
    try {
        if (-not ('ZgPower' -as [type])) {
            Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[StructLayout(LayoutKind.Sequential)]
public struct SYSTEM_POWER_STATUS {
    public byte ACLineStatus;
    public byte BatteryFlag;
    public byte BatteryLifePercent;
    public byte SystemStatusFlag;
    public int  BatteryLifeTime;
    public int  BatteryFullLifeTime;
}
public static class ZgPower {
    [DllImport("kernel32.dll", SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool GetSystemPowerStatus(out SYSTEM_POWER_STATUS s);
}
'@
        }
        $st = New-Object SYSTEM_POWER_STATUS
        if ([ZgPower]::GetSystemPowerStatus([ref]$st)) {
            # ACLineStatus: 0 offline, 1 online, 255 unknown
            $hb.power.on_ac = switch ($st.ACLineStatus) { 0 { $false } 1 { $true } default { $null } }
            # BatteryFlag bit 7 (128) = no system battery; 255 = unknown
            if     ($st.BatteryFlag -eq 255) { $hb.power.has_battery = $null; Add-Err 'battery' 'BatteryFlag=255 (unknown)' }
            elseif (($st.BatteryFlag -band 128) -ne 0) { $hb.power.has_battery = $false }
            else   { $hb.power.has_battery = $true }
            # BatteryLifePercent: 0-100, or 255 = unknown
            $hb.power.charge_pct = if ($st.BatteryLifePercent -le 100) { [int]$st.BatteryLifePercent } else { $null }
        } else {
            Add-Err 'power' 'GetSystemPowerStatus returned false'
        }
    } catch { Add-Err 'power' $_ }

    # ----------------------------------------------------------------------- #
    # 4. Repo — branch, head sha, dirty
    # ----------------------------------------------------------------------- #
    try {
        if (Test-Path (Join-Path $RepoRoot '.git')) {
            $b = & git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null
            $s = & git -C $RepoRoot rev-parse --short HEAD     2>$null
            $d = & git -C $RepoRoot status --porcelain          2>$null
            if ($LASTEXITCODE -eq 0 -or $b) {
                $hb.repo.branch   = if ($b) { "$b".Trim() } else { $null }
                $hb.repo.head_sha = if ($s) { "$s".Trim() } else { $null }
                $hb.repo.dirty    = [bool]($d)
            } else { Add-Err 'repo' 'git returned no output' }
        } else {
            Add-Err 'repo' "no .git at $RepoRoot"
        }
    } catch { Add-Err 'repo' $_ }

    # ----------------------------------------------------------------------- #
    # 4b. boot_at — TickCount64, not Win32_OperatingSystem.LastBootUpTime,
    #     which does not exist on this node (F-ZG-4). Approximate by ~ms drift.
    # ----------------------------------------------------------------------- #
    try {
        $uptimeMs   = [Environment]::TickCount64
        $hb.boot_at = (Get-Date).AddMilliseconds(-$uptimeMs).ToString('yyyy-MM-ddTHH:mm:ssK')
        $hb.since_boot_seconds = [math]::Round($uptimeMs / 1000)

        # BOOT STORM LABELLING — measured, not suppressed.
        #
        # The 04:31 post-reboot fire recorded cpu_pct 100.0 and took 97.7 s,
        # against 13.7 s on the previous fire. Both numbers are TRUE: the machine
        # really was pinned three minutes into a cold boot. The hazard is not the
        # measurement, it is a consumer reading cpu_pct 100 as "this node is
        # overloaded" when it means "this node is still starting up".
        #
        # So: do NOT sleep the fire, and do NOT drop the sample. Delaying the
        # boot-triggered run is the one thing that would damage what it is for —
        # it exists to turn a reboot into a liveness test, and every second of
        # sleep is a second the node looks dead. Discarding the sample would hide
        # a real condition. Labelling it keeps the data and tells the reader how
        # to weigh it, which is the same rule this file applies everywhere else.
        if ($hb.since_boot_seconds -lt 600) {
            $hb.boot_storm = $true
            $hb.boot_storm_note = "Fired $($hb.since_boot_seconds)s after boot. cpu_pct and any duration in this stamp reflect boot contention, not steady-state load. Do not read them as node health, and do not average them into a load trend."
        } else {
            $hb.boot_storm = $false
        }
    } catch { Add-Err 'boot_at' $_ }

    # ----------------------------------------------------------------------- #
    # 5. Disk free on C: — Storage namespace, unaffected by F-ZG-4
    # ----------------------------------------------------------------------- #
    try {
        $v = Get-Volume -DriveLetter C -ErrorAction Stop
        $hb.disk.c_free_gb = [math]::Round($v.SizeRemaining / 1GB, 1)
    } catch { Add-Err 'disk' $_ }

    # ----------------------------------------------------------------------- #
    # 6. Reachability
    # ----------------------------------------------------------------------- #
    try {
        # create the directory FIRST. Probing a path whose parent does not exist
        # yet would report device_files:false on a healthy but fresh node — the
        # same absence-from-failure bug this script exists to avoid, and the one
        # filed against fleet_probe.py as F-ZG-10. Caught by the mismatch test
        # on 2026-09-07, which ran against a workspace that had no heartbeat dir.
        $hbDir = Join-Path $Workspace 'fleet\heartbeat'
        if (-not (Test-Path $hbDir)) { New-Item -ItemType Directory -Force -Path $hbDir -ErrorAction Stop | Out-Null }
        $probe = Join-Path $hbDir ('.write-probe-{0}' -f $PID)
        Set-Content -Path $probe -Value 'x' -ErrorAction Stop
        Remove-Item $probe -Force -ErrorAction SilentlyContinue
        $hb.reachability.device_files = $true
    } catch { $hb.reachability.device_files = $false; Add-Err 'device_files' $_ }

    try {
        # BOUNDED. The 04:31 post-reboot fire took 97.7 s against 13.7 s steady
        # state, and this is the only call in the script that touches the
        # network — three minutes into a cold boot the stack is often not up, so
        # an unbounded ls-remote sits there. Two guards, neither of which
        # suppresses a result:
        #   GIT_TERMINAL_PROMPT=0 — never block waiting for credentials.
        #   lowSpeedLimit/lowSpeedTime — abort if the transfer stalls for 10 s.
        # A bounded failure is recorded as git_remote_read:false with the reason,
        # which is a measurement. An unbounded hang produces nothing at all.
        $prevPrompt = $env:GIT_TERMINAL_PROMPT
        $env:GIT_TERMINAL_PROMPT = '0'
        try {
            $null = & git -c http.lowSpeedLimit=1000 -c http.lowSpeedTime=10 `
                          ls-remote --heads $RemoteUrl 2>$null
            $hb.reachability.git_remote_read = ($LASTEXITCODE -eq 0)
            if ($LASTEXITCODE -ne 0) { Add-Err 'git_remote_read' "git ls-remote exited $LASTEXITCODE" }
        } finally {
            $env:GIT_TERMINAL_PROMPT = $prevPrompt
        }
    } catch { Add-Err 'git_remote_read' $_ }

    # git_remote_write is deliberately NOT probed here. Proving push auth means
    # pushing, and a heartbeat must never create refs as a side effect. It stays
    # null until a session proves it. See F-ZG-11.
    $hb.reachability.git_remote_write = $null

    if ($errors.Count -gt 0 -and $outcome -eq 'ok') { $outcome = 'degraded' }
}
catch {
    $outcome = 'error'
    Add-Err 'fatal' $_
}
finally {
    # ----------------------------------------------------------------------- #
    # THE WRITE. Unconditional. This block is the whole point of the script.
    # ----------------------------------------------------------------------- #
    try {
        $now          = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ssK')
        $hb.at        = $now
        $hb.by.at     = $now
        $hb.outcome   = $outcome
        $hb.errors    = @($errors)
        $hb.notices   = @($notices)
        $hb.duration_ms = [int]((Get-Date) - $startedAt).TotalMilliseconds
        $hb.note      = "Shell-side heartbeat. Wrote in a finally block, so this stamp exists whether the fire succeeded or failed. outcome='$outcome'. Nulls mean unmeasured and are listed in errors; they never mean zero. Duration $($hb.duration_ms) ms."

        $dir = Join-Path $Workspace 'fleet\heartbeat'
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
        $target = Join-Path $dir "$NodeId.json"

        $json = $hb | ConvertTo-Json -Depth 6
        # write via a temp file + move so a reader never sees a half-written stamp
        $tmp = "$target.tmp"
        Set-Content -Path $tmp -Value $json -Encoding utf8 -ErrorAction Stop
        Move-Item -Path $tmp -Destination $target -Force -ErrorAction Stop
    }
    catch {
        # last resort: if even the heartbeat write failed, leave a breadcrumb
        try {
            $fallback = Join-Path $Workspace "fleet\heartbeat\$NodeId.WRITE-FAILED.txt"
            "$(Get-Date -Format o)  heartbeat write failed: $($_.Exception.Message)" |
                Add-Content -Path $fallback -ErrorAction SilentlyContinue
        } catch { }
    }
}

if ($outcome -eq 'error' -or $outcome -eq 'identity_mismatch') { exit 1 } else { exit 0 }


