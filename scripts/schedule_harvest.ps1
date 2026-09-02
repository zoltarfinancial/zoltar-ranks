# Registers a Windows scheduled task that runs the harvester every 30 minutes
# between 07:00 and 21:30 CDT. This is the time-sensitive step: the upstream
# `all_*` intraday buffer is capped at 200 run timestamps and drops older ones
# permanently. See docs/FINDINGS.md F2.
#
# Run once, from an elevated PowerShell, at the repo root:
#   .\scripts\schedule_harvest.ps1
$ErrorActionPreference = "Stop"
$repo   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo ".venv\Scripts\python.exe"
$script = Join-Path $repo "scripts\daily.py"
$taskName = "ZoltarRanksHarvest"

if (-not (Test-Path $python)) { throw "venv not found - run .\scripts\setup.ps1 first" }

$action  = New-ScheduledTaskAction -Execute $python -Argument "`"$script`"" -WorkingDirectory $repo

# DAILY, not Once. A `-Once` trigger with a RepetitionDuration repeats for that
# duration on ONE day and then expires: NextRunTime goes empty, the task still
# reports State=Ready, and the harvester silently stops forever. That happened on
# 2026-09-01 -- it ran 07:00-21:30, then never scheduled again, and only
# NumberOfMissedRuns=1 with a blank NextRunTime gave it away.
# The daily trigger carries the 30-minute repetition via its Repetition property.
$trigger = New-ScheduledTaskTrigger -Daily -At 7:00AM
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At 7:00AM `
             -RepetitionInterval (New-TimeSpan -Minutes 30) `
             -RepetitionDuration (New-TimeSpan -Hours 14 -Minutes 30)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
             -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
             -ExecutionTimeLimit (New-TimeSpan -Minutes 20)

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "Harvest Zoltar ranks/ER/SHAP from upstream git into the local archive"

Write-Host "Registered '$taskName'. Verify with:" -ForegroundColor Green
Write-Host "  Get-ScheduledTask -TaskName $taskName | Get-ScheduledTaskInfo"
Write-Host "Run it now with:"
Write-Host "  Start-ScheduledTask -TaskName $taskName"
