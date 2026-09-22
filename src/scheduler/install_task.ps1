<#
  03-scheduler: register / update / remove the daily scheduled task (R1, R14).

  Idempotent - running it twice leaves exactly one task.

      pwsh -File src/scheduler/install_task.ps1              # install or update
      pwsh -File src/scheduler/install_task.ps1 -Uninstall    # switch the automation off
      pwsh -File src/scheduler/install_task.ps1 -Status       # show what is registered

  The machine timezone is Asia/Dhaka (+06:00), so the task's local time IS the project's
  timezone and no conversion happens anywhere.
#>
[CmdletBinding()]
param(
    [string]$Time = '',
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = 'Stop'
$TaskName  = 'ToDo-WhatsApp-Daily'
$SchedDir  = $PSScriptRoot
$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Runner    = Join-Path $SchedDir 'run_daily.ps1'

if ($Status) {
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) { Write-Host "not installed"; exit 0 }
    $info = $t | Get-ScheduledTaskInfo
    Write-Host "task      : $TaskName ($($t.State))"
    Write-Host "trigger   : $(($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ', ')"
    Write-Host "last run  : $($info.LastRunTime)  result 0x$('{0:X}' -f $info.LastTaskResult)"
    Write-Host "next run  : $($info.NextRunTime)"
    exit 0
}

if ($Uninstall) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed scheduled task '$TaskName'. Logs and outputs are untouched."
    } else {
        Write-Host "'$TaskName' was not installed - nothing to do."
    }
    exit 0
}

# ---- time comes from scheduler.env unless overridden on the command line
if (-not $Time) {
    $envFile = Join-Path $SchedDir 'scheduler.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*SCHED_TIME\s*=\s*(.+?)\s*$') { $Time = $Matches[1].Trim('"').Trim("'") }
        }
    }
}
if (-not $Time) { $Time = '10:00' }

if (-not (Test-Path $Runner)) { throw "runner not found: $Runner" }
# Resolving pwsh needs care. On a Store/winget install, `Get-Command pwsh` returns the
# VERSION-STAMPED path (…\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__…\pwsh.exe), which stops
# existing the moment PowerShell updates - the task would then fail silently every morning.
# Prefer the stable app-execution alias, which survives updates.
$pwsh = $null
$alias = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\pwsh.exe'
if (Test-Path $alias) { $pwsh = $alias }
if (-not $pwsh) {
    $candidate = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
    # Only take a versioned WindowsApps path as a last resort, and say so.
    if ($candidate -and $candidate -match 'WindowsApps\\Microsoft\.PowerShell_[\d.]+') {
        Write-Warning "using a version-stamped pwsh path; re-run this installer after a PowerShell update:`n  $candidate"
    }
    $pwsh = $candidate
}
if (-not $pwsh) { $pwsh = (Get-Command powershell).Source }
Write-Host "launcher   : $pwsh"

# `-WindowStyle Hidden` is NOT enough on Windows 11: the default-terminal handoff opens a visible
# Windows Terminal window anyway (seen 2026-09-20 in T18), and closing it kills the run with
# 0xC000013A. `conhost.exe --headless` never creates a window at all.
$conhost = Join-Path $env:SystemRoot 'System32\conhost.exe'
$action = New-ScheduledTaskAction -Execute $conhost `
    -Argument "--headless `"$pwsh`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`" -Trigger schedule" `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($Time, 'HH:mm', $null))

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -DontStopOnIdleEnd `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal | Out-Null
    Write-Host "updated scheduled task '$TaskName' - daily at $Time"
} else {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description 'Runs the calendar-todo -> WhatsApp pipeline via headless Claude Code. See plan/stableMD/03-scheduler.stable.md' | Out-Null
    Write-Host "installed scheduled task '$TaskName' - daily at $Time"
}

Write-Host ""
Write-Host "  runs hidden, as $env:USERNAME, catching up after a missed start, retrying twice 15 min apart."
Write-Host "  check it:  pwsh -File src/scheduler/install_task.ps1 -Status"
Write-Host "  switch off: pwsh -File src/scheduler/install_task.ps1 -Uninstall"
