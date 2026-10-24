<#
  03-scheduler: register / update / remove the daily scheduled tasks (R1, R14, R28).

  Idempotent - running it twice leaves exactly one of each task:
      ToDo-WhatsApp-Daily  at SCHED_TIME        (module 01 + WhatsApp; + email when SCHED_EMAIL_TIME=off)
      ToDo-Email-Daily     at SCHED_EMAIL_TIME  (v3.0: email only; removed when SCHED_EMAIL_TIME=off)

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
# SCHED_INSTALL_SUFFIX is a test-only hook: it registers the tasks under other names, so the installer
# can be tested without touching the real ones. Never set in normal operation.
$TaskName      = 'ToDo-WhatsApp-Daily' + $env:SCHED_INSTALL_SUFFIX
$EmailTaskName = 'ToDo-Email-Daily' + $env:SCHED_INSTALL_SUFFIX
$SchedDir  = $PSScriptRoot
$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Runner    = Join-Path $SchedDir 'run_daily.ps1'

if ($Status) {
    foreach ($name in $TaskName, $EmailTaskName) {
        $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $t) { Write-Host "task      : $name (not installed)"; Write-Host ""; continue }
        $info = $t | Get-ScheduledTaskInfo
        Write-Host "task      : $name ($($t.State))"
        Write-Host "trigger   : $(($t.Triggers | ForEach-Object { $_.StartBoundary }) -join ', ')"
        Write-Host "last run  : $($info.LastRunTime)  result 0x$('{0:X}' -f $info.LastTaskResult)"
        Write-Host "next run  : $($info.NextRunTime)"
        Write-Host ""
    }
    exit 0
}

if ($Uninstall) {
    foreach ($name in $TaskName, $EmailTaskName) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $name -Confirm:$false
            Write-Host "removed scheduled task '$name'. Logs and outputs are untouched."
        } else {
            Write-Host "'$name' was not installed - nothing to do."
        }
    }
    exit 0
}

# ---- times come from scheduler.env (SCHED_TIME unless overridden on the command line)
$EmailTime = ''
$envFile = Join-Path $SchedDir 'scheduler.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if (-not $Time -and $line -match '^\s*SCHED_TIME\s*=\s*(.+?)\s*$') { $Time = $Matches[1].Trim('"').Trim("'") }
        if ($line -match '^\s*SCHED_EMAIL_TIME\s*=\s*(.+?)\s*$') { $EmailTime = $Matches[1].Trim('"').Trim("'") }
    }
}
if (-not $Time) { $Time = '10:00' }
if (-not $EmailTime) { $EmailTime = '13:30' }   # the wrapper's default too (R27)

# R27: refuse a bad email time before changing anything, so a typo cannot silently lose the email channel.
if ($EmailTime -ne 'off') {
    $parsed = [datetime]::MinValue
    if (-not [datetime]::TryParseExact($EmailTime, 'HH:mm', $null, 'None', [ref]$parsed)) {
        Write-Host "SCHED_EMAIL_TIME '$EmailTime' is not a time (HH:mm, e.g. 13:30) or 'off' - nothing changed"
        exit 1
    }
    if ($parsed.TimeOfDay -eq ([datetime]::ParseExact($Time, 'HH:mm', $null)).TimeOfDay) {
        Write-Host "SCHED_EMAIL_TIME equals SCHED_TIME ($Time) - use 'off' to send email in the $Time run; nothing changed"
        exit 1
    }
}

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
$runArgs = "--headless `"$pwsh`" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`" -Trigger schedule"
$action = New-ScheduledTaskAction -Execute $conhost -Argument $runArgs -WorkingDirectory $RepoRoot

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

# ---- v3.0 (R28): the separate email task, same settings, -Channel email
if ($EmailTime -ne 'off') {
    $emailAction  = New-ScheduledTaskAction -Execute $conhost -Argument "$runArgs -Channel email" -WorkingDirectory $RepoRoot
    $emailTrigger = New-ScheduledTaskTrigger -Daily -At ([datetime]::ParseExact($EmailTime, 'HH:mm', $null))
    if (Get-ScheduledTask -TaskName $EmailTaskName -ErrorAction SilentlyContinue) {
        Set-ScheduledTask -TaskName $EmailTaskName -Action $emailAction -Trigger $emailTrigger -Settings $settings -Principal $principal | Out-Null
        Write-Host "updated scheduled task '$EmailTaskName' - daily at $EmailTime"
    } else {
        Register-ScheduledTask -TaskName $EmailTaskName -Action $emailAction -Trigger $emailTrigger -Settings $settings -Principal $principal `
            -Description 'Sends the to-do list by email (module 04) at its own time. See plan/stableMD/03-scheduler.stable.md' | Out-Null
        Write-Host "installed scheduled task '$EmailTaskName' - daily at $EmailTime"
    }
}
elseif (Get-ScheduledTask -TaskName $EmailTaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $EmailTaskName -Confirm:$false
    Write-Host "removed scheduled task '$EmailTaskName' - SCHED_EMAIL_TIME=off, email goes out in the $Time run"
}

Write-Host ""
Write-Host "  runs hidden, as $env:USERNAME, catching up after a missed start, retrying twice 15 min apart."
Write-Host "  check it:  pwsh -File src/scheduler/install_task.ps1 -Status"
Write-Host "  switch off: pwsh -File src/scheduler/install_task.ps1 -Uninstall"
