# Install \Zoltar\ZoltarLead-Courier -- order t-20260908-courier-zoltarlead.
#
#   .\scripts\install_courier.ps1
#
# The ONLY thing this installs is the scheduled task. No packages, no remotes, no
# credential changes. The task runs scripts\fleet_courier.py, which runs git and
# scripts\fleet_sync.py and never a model.
#
# Measured, not assumed, on this node (PowerShell 5.1.26100 Desktop):
#
#  * The interpreter is an ABSOLUTE path. `python3` does not exist on ZoltarLead
#    (Microsoft Store alias stub); bare `python` is 2.7 on ZoltarGenesis. A task
#    naming either silently never runs -- indistinguishable from a dead node.
#
#  * The repetition trigger is `-Once` with an interval and NO duration. The fleet's
#    2026-09-01 outage was a `-Once` trigger with a BOUNDED 14h30m duration, which
#    expired after one day while the task still read `Ready`. An EMPTY duration is
#    indefinite: ZoltarFleetHeartbeat uses exactly this shape and had fired hourly
#    for 2.5 days with 0 missed runs when this was written. The script checks the
#    registered duration is empty and fails loudly if it is not.
#
#  * S4U: runs whether or not anyone is logged on, with no stored password.

$ErrorActionPreference = 'Stop'

$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = Join-Path $repo 'scripts\fleet_courier.py'
$path   = '\Zoltar\'
$name   = 'ZoltarLead-Courier'

foreach ($p in @($python, $script)) { if (-not (Test-Path $p)) { throw "missing: $p" } }

$action = New-ScheduledTaskAction -Execute $python `
            -Argument "`"$script`"" -WorkingDirectory $repo

$every10 = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
             -RepetitionInterval (New-TimeSpan -Minutes 10)
$boot = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = 'PT2M'

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
               -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
              -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 9) `
              -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskPath $path -TaskName $name -Action $action `
    -Trigger @($every10, $boot) -Principal $principal -Settings $settings `
    -Description 'ZoltarLead courier. Lands orders/, fleet/msgs, fleet/receipts, data/fleet/heartbeat and data/review on origin/fleet/dispatch via a dedicated worktree, then runs fleet_sync.py. Git and a python script only -- never a model, never main.' `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskPath $path -TaskName $name
$rep = ($t.Triggers | Where-Object { $_.Repetition.Interval })[0].Repetition
if ($rep.Interval -ne 'PT10M') { throw "repetition interval is $($rep.Interval), expected PT10M" }
if ($rep.Duration) { throw "repetition DURATION is '$($rep.Duration)' -- a bounded -Once trigger expires (2026-09-01 outage). Must be empty." }
if ($t.Principal.LogonType -ne 'S4U') { throw "logon type is $($t.Principal.LogonType), expected S4U" }
if (-not $t.Settings.StartWhenAvailable) { throw 'StartWhenAvailable is not set' }

Write-Host "installed $path$name  (interval PT10M, duration indefinite, S4U, StartWhenAvailable, at-boot +2m)"
Get-ScheduledTaskInfo -TaskPath $path -TaskName $name |
    Format-List LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
