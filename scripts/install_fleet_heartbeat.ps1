# Install ZoltarLead's fleet heartbeat task: hourly + at boot, S4U.
#
#   .\scripts\install_fleet_heartbeat.ps1
#
# Shape matched to ZoltarOne's and ZoltarGenesis's per docs/identity-kit/README.md.
#
# THREE THINGS HERE ARE NODE-SPECIFIC AND WERE MEASURED, NOT ASSUMED:
#
#  1. The interpreter is an ABSOLUTE path to the venv python. On this node
#     `python3` does not exist -- it resolves to the Microsoft Store alias stub --
#     and ZoltarGenesis has the mirror-image problem where bare `python` is 2.7.
#     A scheduled task naming either silently never runs, which is indistinguishable
#     from a dead node. That is the failure this heartbeat exists to detect, so it
#     must not be the failure that disables it.
#
#  2. The boot trigger is what turns a reboot into a liveness test rather than an
#     outage.
#
#  3. S4U: runs whether or not a user is logged on, with no stored password.
#
# PowerShell on this node is 5.1 (Desktop). Everything below is 5.1-compatible;
# docs/identity-kit/verify-guard.ps1 is NOT, because it uses
# ProcessStartInfo.ArgumentList (.NET Core only) and fails silently here.

$ErrorActionPreference = 'Stop'

$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$script = Join-Path $repo 'scripts\fleet_heartbeat.py'
$name   = 'ZoltarFleetHeartbeat'

foreach ($p in @($python, $script)) {
    if (-not (Test-Path $p)) { throw "missing: $p" }
}

$action = New-ScheduledTaskAction -Execute $python `
            -Argument "`"$script`" --kind scheduled" -WorkingDirectory $repo

$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
            -RepetitionInterval (New-TimeSpan -Hours 1)
$boot   = New-ScheduledTaskTrigger -AtStartup
$boot.Delay = 'PT1M'

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
               -LogonType S4U -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
              -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
              -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
              -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $name -Action $action `
    -Trigger @($hourly, $boot) -Principal $principal -Settings $settings `
    -Description 'ZoltarLead fleet heartbeat. Stamps data/fleet/heartbeat/zoltarlead.json on SUCCESS and on FAILURE - a node that only heartbeats when healthy is indistinguishable from one that is off.' `
    -Force | Out-Null

Write-Host "installed $name"
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 12
Get-ScheduledTask -TaskName $name | Get-ScheduledTaskInfo |
    Format-List TaskName, LastRunTime, LastTaskResult, NextRunTime, NumberOfMissedRuns
Write-Host "NOTE: LastTaskResult 0 means the task RAN. The liveness signal is the"
Write-Host "      stamp in data/fleet/heartbeat/zoltarlead.json, not this exit code."
