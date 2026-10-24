<#
  03-scheduler: the daily wrapper. The only thing Task Scheduler knows about.

  Lock -> classify launch -> pre-flight -> WhatsApp channel (headless Claude: skills 01 then 02)
       -> email channel (module 04, run directly) -> verify each channel's artifacts -> log -> alert.

  v3.0 (R27-R32): with SCHED_EMAIL_TIME set (default 13:30) email has its own run. The 10:00 task runs
  WhatsApp only (-Channel auto -> run_kind whatsapp); the second task ToDo-Email-Daily runs
  -Channel email: lock (waiting if held) -> pre-flight -> today's list, rebuilt by a module-01-only
  Claude session if missing -> email step -> verify. SCHED_EMAIL_TIME=off keeps the v2.0 single run.

  Runnable by hand with exactly the same behaviour as the scheduled run (R13):
      pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run -Visible
      pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run -Channel email

  Every path is derived from $PSScriptRoot, never from the current directory, so it behaves
  identically whichever way it is launched - and a copy of this folder inside a scratch repo is a
  fully isolated test bed (that is how v2.0 is tested without touching real logs).

  Test-only hooks (never set in normal operation):
      -ClassifyOnly                     print the R17 launch classification as JSON and exit
      SCHED_LAUNCH_TASK / _REF / _LOG   aim the classifier at another task name, reference time or log
      SCHED_PREFLIGHT_SKIP              comma-separated pre-flight check ids to skip
      SCHED_CLAUDE_EXE                  swap in a stub for `claude`
      SCHED_EMAIL_TASK                  aim the R32 "email task installed?" check at another task name
  Every SCHED_* setting may be given as an environment variable; it overrides scheduler.env.
#>
[CmdletBinding()]
param(
    [ValidateSet('schedule', 'manual')][string]$Trigger = 'schedule',
    [ValidateSet('dry-run', 'self', 'live')][string]$Mode = '',
    # v2.0 (R18): a self-retry continues the label it ended with; no Task Scheduler event announces it.
    [ValidateSet('schedule', 'catch-up')][string]$ContinueFrom = '',
    # v3.0 (R29): which channels this run delivers. auto = whatsapp for a scheduled launch when email has
    # its own time, otherwise all.
    [ValidateSet('auto', 'all', 'whatsapp', 'email')][string]$Channel = 'auto',
    [switch]$ClassifyOnly,
    [switch]$Visible,
    [switch]$SkipPreflight
)

$ErrorActionPreference = 'Stop'
if ($ContinueFrom -and $Trigger -ne 'schedule') {
    Write-Host '-ContinueFrom is only valid together with -Trigger schedule'
    exit 2
}

# ---------------------------------------------------------------- paths & config
$RepoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$SchedDir  = $PSScriptRoot
$OutDir    = Join-Path $RepoRoot 'outputs\03-scheduler'
$LogsDir   = Join-Path $OutDir 'logs'
$RunLog    = Join-Path $OutDir 'run_log.json'
$LockFile  = Join-Path $OutDir 'run.lock'
$Secrets   = Join-Path $RepoRoot '.secrets'
$TodoJson  = Join-Path $RepoRoot 'outputs\01-calendar-todo\todo_by_name.json'
$SendLog   = Join-Path $RepoRoot 'outputs\02-whatsapp-message\send_log.json'
$EmailLog  = Join-Path $RepoRoot 'outputs\04-email-message\send_log.json'
$EmailPy   = Join-Path $RepoRoot '.claude\skills\04-email-message\send_email.py'

New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null

function Read-EnvFile([string]$Path) {
    $cfg = @{}
    if (Test-Path $Path) {
        foreach ($line in Get-Content $Path) {
            $t = $line.Trim()
            if (-not $t -or $t.StartsWith('#') -or $t -notmatch '=') { continue }
            $k, $v = $t -split '=', 2
            $cfg[$k.Trim()] = $v.Trim().Trim('"').Trim("'")
        }
    }
    return $cfg
}

$cfg = Read-EnvFile (Join-Path $SchedDir 'scheduler.env')
function Get-Setting([string]$Name, [string]$Default) {
    # v2.0 (R25): an environment variable beats the file, so tests never have to edit the owner's file.
    $fromEnv = [Environment]::GetEnvironmentVariable($Name)
    if ($fromEnv) { return $fromEnv }
    if ($cfg[$Name]) { return $cfg[$Name] }
    return $Default
}

$Model           = Get-Setting 'SCHED_MODEL' 'sonnet'
$TimeoutMin      = [double](Get-Setting 'SCHED_TIMEOUT_MIN' '25')
$RetentionDays   = [int](Get-Setting 'SCHED_RETENTION_DAYS' '60')
$MaxAttempts     = [int](Get-Setting 'SCHED_MAX_ATTEMPTS' '3')
$SchedEmail      = (Get-Setting 'SCHED_EMAIL' '1') -ne '0'
$EmailTimeoutMin = [double](Get-Setting 'SCHED_EMAIL_TIMEOUT_MIN' '10')
$RetryWaitSec    = [int](Get-Setting 'SCHED_RETRY_WAIT_SEC' '900')
$ShowWindow      = $Visible -or ((Get-Setting 'SCHED_VISIBLE' '0') -eq '1')
$EmailLockWaitMin = [double](Get-Setting 'SCHED_EMAIL_LOCK_WAIT_MIN' '30')
if (-not $Mode) { $Mode = 'live' }   # R4: the scheduled run always forces live

# v3.0 (R27): the separate email time. 'off' keeps v2.0's single run. A value that does not parse, or
# equals SCHED_TIME, is treated as off with a warning, so a typo cannot silently lose the email channel.
$SchedTimeStr = Get-Setting 'SCHED_TIME' '10:00'
$EmailTimeStr = Get-Setting 'SCHED_EMAIL_TIME' '13:30'
$EmailTimeBad = $false
$SplitEmail   = $false
if ($EmailTimeStr -ne 'off') {
    try {
        $et = [datetime]::ParseExact($EmailTimeStr, 'HH:mm', $null)
        $st = [datetime]::ParseExact($SchedTimeStr, 'HH:mm', $null)
        if ($et.TimeOfDay -eq $st.TimeOfDay) { $EmailTimeBad = $true } else { $SplitEmail = $true }
    } catch { $EmailTimeBad = $true }
}

# v3.0 (R29): resolve the run kind once; it is stored as run_kind and passed on to a self-retry.
$RunKind = $Channel
if ($RunKind -eq 'auto') {
    $RunKind = if ($Trigger -eq 'schedule' -and $SplitEmail) { 'whatsapp' } else { 'all' }
}

$channels = @()
if ((Get-Setting 'SCHED_ALERT_TOAST'    '1') -ne '0') { $channels += 'toast' }
if ((Get-Setting 'SCHED_ALERT_WHATSAPP' '1') -ne '0') { $channels += 'whatsapp' }
if ((Get-Setting 'SCHED_ALERT_WINDOW'   '1') -ne '0') { $channels += 'window' }

$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
$NodeExe   = (Get-Command node   -ErrorAction SilentlyContinue).Source
# SCHED_CLAUDE_EXE lets the test ladder swap in a stub, the way module 02's tests stub its Node
# worker. It is never set in normal operation.
$ClaudeExe = if ($env:SCHED_CLAUDE_EXE) { $env:SCHED_CLAUDE_EXE }
             else { (Get-Command claude -ErrorAction SilentlyContinue).Source }

$Started = Get-Date
$Stamp   = $Started.ToString('yyyy-MM-ddTHHmm')
$Today   = $Started.ToString('yyyy-MM-dd')   # machine TZ is already Asia/Dhaka (+06:00)

# ---------------------------------------------------------------- launch classification (R16, R17, R18)
# Task Scheduler's own "Run" action launches the exact same registered command as the real 10:00
# trigger (found 2026-09-23), and a missed-run catch-up looks like a stray launch by the clock (found
# 2026-10-02, when a laptop-asleep catch-up at 16:21 was relabelled manual-gui and would have lost its
# retries). So the clock is the FALLBACK: first ask Task Scheduler what it logged about this launch.
#   114  "could not launch ... as scheduled ... started now ... if schedule is missed" -> catch-up
#   107  "launched ... instance ... due to a time trigger condition"                    -> schedule
#   110  "launched ... instance of task ... for user" (a user's Run)                     -> manual-gui
#   The MOST RECENT of 107/114/110 in the 2 minutes before start decides. Launch events
#   (100/129/200) with none of those -> manual-gui.
#   nothing at all (log disabled, rolled over, unreadable)                             -> window rule
# The log is a 10 MB circular log (about 6 days on this machine), so it is read once, at launch time.
# Event 114 is also raised hourly by OTHER machine tasks, so events are matched by task name.
function Get-LaunchReason([string]$TaskName, [datetime]$Ref) {
    $logName = if ($env:SCHED_LAUNCH_LOG) { $env:SCHED_LAUNCH_LOG } else { 'Microsoft-Windows-TaskScheduler/Operational' }
    $found = @()
    try {
        $found = @(Get-WinEvent -FilterHashtable @{
                LogName = $logName; Id = @(107, 114, 110, 100, 129, 200)
                StartTime = $Ref.AddMinutes(-2); EndTime = $Ref.AddSeconds(10)
            } -ErrorAction Stop)
    } catch { return @{ label = $null; event = 'none' } }   # no events, or the log is unreadable
    $needle = '"' + $TaskName + '"'
    $mine = @($found | Where-Object { $_.Message.Contains($needle) })
    # The MOST RECENT trigger event decides. Looking for "any 107 in the window" is wrong: a manual Run
    # a few seconds after a real time-triggered launch has both a 107 (the earlier launch) and a 110 (its
    # own) in the window and was classified `schedule` (found by the live test T23, 2026-10-19: a manual
    # launch 8 s after a time-triggered one). The launch that just started always owns the newest one.
    $trigger = @($mine | Where-Object { $_.Id -in @(107, 114, 110) } | Sort-Object TimeCreated, RecordId -Descending | Select-Object -First 1)
    if ($trigger.Count -gt 0) {
        $map = @{ 107 = 'schedule'; 114 = 'catch-up'; 110 = 'manual-gui' }
        return @{ label = $map[[int]$trigger[0].Id]; event = [int]$trigger[0].Id }
    }
    # Launch events only (no trigger event survived in the log): a non-time launch.
    if ($mine.Count -gt 0) { return @{ label = 'manual-gui'; event = ($mine | Select-Object -First 1).Id } }
    return @{ label = $null; event = 'none' }
}

# v3.0 (R31): an email run is classified against its own task and its own time.
$WindowTimeStr = if ($RunKind -eq 'email' -and $SplitEmail) { $EmailTimeStr } else { $SchedTimeStr }
$SchedToday   = $Started.Date.Add(([datetime]::ParseExact($WindowTimeStr, 'HH:mm', $null)).TimeOfDay)
$WindowStart  = $SchedToday.AddMinutes(-5)
$WindowEnd    = $SchedToday.AddMinutes(15 * ($MaxAttempts - 1) + 20)   # covers every self-retry plus slack

$EffectiveTrigger = $Trigger
$LaunchEvent      = 'none'
if ($ContinueFrom) {
    $EffectiveTrigger = $ContinueFrom; $LaunchEvent = 'continuation'
}
elseif ($Trigger -eq 'schedule') {
    $taskName = if ($env:SCHED_LAUNCH_TASK) { $env:SCHED_LAUNCH_TASK }
                elseif ($RunKind -eq 'email') { '\ToDo-Email-Daily' } else { '\ToDo-WhatsApp-Daily' }
    $refTime  = if ($env:SCHED_LAUNCH_REF)  { [datetime]::Parse($env:SCHED_LAUNCH_REF) } else { $Started }
    $reason   = Get-LaunchReason $taskName $refTime
    if ($reason.label) {
        $EffectiveTrigger = $reason.label; $LaunchEvent = $reason.event
        if ($EffectiveTrigger -eq 'manual-gui') {
            Write-Host "note: Task Scheduler logged event $LaunchEvent for this launch, not a time trigger - treating as manual-gui, not schedule"
        }
    }
    elseif ($Started -lt $WindowStart -or $Started -gt $WindowEnd) {
        # v1.1 (R16) window rule, now only the fallback for when the event log has nothing to say.
        $EffectiveTrigger = 'manual-gui'
        Write-Host "note: launched via Task Scheduler but well outside the $WindowTimeStr window - treating as manual-gui, not schedule"
    }
}
# A catch-up run is a scheduled run in every respect: lock, attempt ladder, quiet until the last
# attempt, self-retry (R17). Only manual and manual-gui are the "alert at once, never retry" kind.
$IsScheduled = $EffectiveTrigger -in @('schedule', 'catch-up')
# What modules 02 and 04 record as the trigger of this run (their choices are schedule|manual|manual-ui).
$ModuleTrigger = if ($IsScheduled) { 'schedule' } else { 'manual' }

if ($ClassifyOnly) {
    @{ trigger = $EffectiveTrigger; launch_event = $LaunchEvent; run_kind = $RunKind } | ConvertTo-Json -Compress | Write-Output
    exit 0
}

# ---------------------------------------------------------------- helpers
function Get-RunLog {
    if (Test-Path $RunLog) {
        try { return (Get-Content $RunLog -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { }
    }
    return [pscustomobject]@{ runs = @() }
}

function Save-Run([hashtable]$Entry) {
    $log  = Get-RunLog
    $runs = @($log.runs)
    $cut  = (Get-Date).AddDays(-$RetentionDays).ToString('yyyy-MM-dd')
    $kept = @($runs | Where-Object { $_.date -ge $cut })
    $all  = @([pscustomobject]$Entry) + $kept
    ([pscustomobject]@{ runs = $all } | ConvertTo-Json -Depth 12) |
        Set-Content -Path $RunLog -Encoding UTF8
    # prune old transcripts (json, email output, per-run prompt copies) alongside the log
    Get-ChildItem $LogsDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-AttemptNumber {
    # The attempt number is derived from today's already-failed scheduled runs (R11: alert only once
    # the last attempt has failed). A catch-up run counts like a schedule run (R17, R18); a manual-gui
    # launch never joins the schedule day's attempt ladder (v1.1, R16). v3.0 (R31): each run kind has its
    # own ladder; a row written before v3.0 has no run_kind and counts as `all`.
    if (-not $IsScheduled) { return 1 }
    $runs = @((Get-RunLog).runs)
    $failedToday = @($runs | Where-Object {
        $kind = if ($_.run_kind) { [string]$_.run_kind } else { 'all' }
        $_.date -eq $Today -and $_.trigger -in @('schedule', 'catch-up') -and $kind -eq $RunKind -and
        $_.status -notin @('ok', 'ok_nothing_to_send', 'skipped_locked')
    })
    return $failedToday.Count + 1
}

function Send-Alert([string]$Title, [string]$Body, [string[]]$Only) {
    $use = if ($Only) { @($channels | Where-Object { $_ -in $Only }) } else { $channels }
    if (-not $use) { return @{ fired = @(); failed = @() } }
    $notify = Join-Path $SchedDir 'notify.py'
    try {
        $raw = & $PythonExe $notify --title $Title --body $Body `
                    --channels ($use -join ',') --secrets $Secrets --node $NodeExe 2>&1
        return ($raw | Out-String | ConvertFrom-Json)
    } catch {
        return @{ fired = @(); failed = @(@{ channel = 'all'; error = "$_" }) }
    }
}

function Test-Lock {
    if (-not (Test-Path $LockFile)) { return $null }
    try { $lock = Get-Content $LockFile -Raw | ConvertFrom-Json } catch { return $null }
    $alive = $null -ne (Get-Process -Id $lock.pid -ErrorAction SilentlyContinue)
    $age   = ((Get-Date) - [datetime]$lock.started).TotalMinutes
    if (-not $alive -or $age -gt $TimeoutMin) { return $null }   # stale -> reclaimable
    return $lock
}

function Try-AcquireLock {
    # v1.1 (R15): FileMode.CreateNew is atomic at the OS level: it either creates the file or throws,
    # with no gap between checking and writing for a second near-simultaneous launch to slip through.
    for ($try = 0; $try -lt 2; $try++) {
        try {
            $fs = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
            try {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes((@{ pid = $PID; started = $Started.ToString('o') } | ConvertTo-Json))
                $fs.Write($bytes, 0, $bytes.Length)
            } finally { $fs.Close() }
            return $true
        } catch [System.IO.IOException] {
            if (Test-Lock) { return $false }
            Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
        }
    }
    return $false
}

function Remove-StaleJobFiles([datetime]$Since) {
    # R25 hygiene. Modules 02 and 04 hand their worker a job file in the system temp dir that holds REAL
    # numbers / addresses and delete it in a `finally`. When this wrapper has to kill a step, that
    # `finally` never runs (found by T31: the killed email step left its job file behind), so whatever
    # those modules created during the step is removed here.
    foreach ($pat in 'email_job_*', 'email_res_*', 'wa_job_*', 'wa_res_*') {
        Get-ChildItem $env:TEMP -Filter $pat -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $Since.AddSeconds(-2) } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Read-TodayRuns([string]$Path, [string]$RunMode) {
    # Module 02 and module 04 keep every run inside their window, so a date can hold several rows
    # (this run plus its retries). Select by date AND mode, newest first.
    if (-not (Test-Path $Path)) { return @() }
    try { $j = Get-Content $Path -Raw -Encoding UTF8 | ConvertFrom-Json } catch { return @() }
    return @($j.runs | Where-Object { $_.date -eq $Today -and $_.mode -eq $RunMode })
}

function Sum-Field($Runs, [string]$Field) {
    $n = 0; foreach ($r in $Runs) { $n += [int]$r.summary.$Field }; return $n
}

# ---------------------------------------------------------------- run
$status    = 'ok'
$errorText = $null
$preflight = $null
$claudeInfo = @{}
$verify    = @{}
$alerts    = @{ fired = @(); failed = @() }
$chan      = @{ whatsapp = @{ status = 'skipped'; detail = '' }; email = @{ status = 'skipped'; detail = '' } }
$warnings  = @()
$attempt   = Get-AttemptNumber
$lockTaken = $false

try {
    # 1 -------------------------------------------------------- lock (R2)
    # A collision is not a failure, so it is logged and toasted but never escalated to a
    # WhatsApp message or a popup window - those are reserved for things that need the owner.
    # Note there is exactly one Save-Run in this script, in the finally block: an `exit` here
    # would still run finally and write the row a second time.
    $gotLock = Try-AcquireLock
    if (-not $gotLock -and $RunKind -eq 'email') {
        # v3.0 (R30): an email run waits for the holder (a late 10:00 run, a module 05 job) instead of
        # skipping, because a skipped email run would lose the day. Still held afterwards -> a failure.
        $deadline = (Get-Date).AddMinutes($EmailLockWaitMin)
        Write-Host "lock held - the email run waits up to $EmailLockWaitMin minute(s)"
        while (-not $gotLock -and (Get-Date) -lt $deadline) {
            Start-Sleep -Seconds ([Math]::Max(1, [Math]::Min(30, ($deadline - (Get-Date)).TotalSeconds)))
            $gotLock = Try-AcquireLock
        }
        if (-not $gotLock) {
            $held = Test-Lock
            $status = 'email_failed'
            $errorText = if ($held) { "lock held by pid $($held.pid) (started $($held.started)) for all of $EmailLockWaitMin minute(s)" }
                         else { "the lock stayed held for all of $EmailLockWaitMin minute(s)" }
            $chan.email = @{ status = 'failed'; detail = $errorText }
            $chan.whatsapp = @{ status = 'skipped'; detail = "runs at $SchedTimeStr" }
            return
        }
    }
    if (-not $gotLock) {
        $status = 'skipped_locked'
        $held = Test-Lock
        $errorText = if ($held) { "another run (pid $($held.pid)) started $($held.started) is still going" }
                     else { "the lock is held by another run that started at nearly the same moment" }
        $alerts = Send-Alert 'ToDo scheduler: run skipped' $errorText -Only @('toast')
        Write-Host "skipped: $errorText"
        return
    }
    $lockTaken = $true

    $todoToday = (Test-Path $TodoJson) -and ((Get-Item $TodoJson).LastWriteTime.ToString('yyyy-MM-dd') -eq $Today)
    if ($RunKind -eq 'email') {
        $chan.whatsapp = @{ status = 'skipped'; detail = "runs at $SchedTimeStr" }
        $verify.list_rebuilt = $false
        if (-not $SchedEmail) {
            # R30: switched off - nothing is rebuilt (no money spent) and nothing is sent.
            $chan.email = @{ status = 'skipped'; detail = 'switched off (SCHED_EMAIL=0)' }
            $status = 'ok_nothing_to_send'
            return
        }
    }

    # 2 -------------------------------------------------------- pre-flight (R3, R20)
    # Each check has a scope. A `shared` failure means there can be no list, so nothing starts. A
    # `whatsapp` or `email` failure only switches that one channel off; the other still runs.
    $waPreflightBad = @(); $emailPreflightBad = @()
    if (-not $SkipPreflight) {
        $pfArgs = @('--repo', $RepoRoot, '--python', $PythonExe)
        # v3.0 (R30): an email run never uses WhatsApp, so its checks are not run at all.
        $skipIds = @($env:SCHED_PREFLIGHT_SKIP -split ',' | Where-Object { $_ })
        if ($RunKind -eq 'email') { $skipIds += @('contacts', 'wa_session', 'wa_config', 'wa_network') }
        if ($skipIds.Count -gt 0) { $pfArgs += @('--skip', ($skipIds -join ',')) }
        $pfRaw = & $PythonExe (Join-Path $SchedDir 'preflight.py') @pfArgs 2>&1
        $pfOk  = ($LASTEXITCODE -eq 0)
        try { $preflight = ($pfRaw | Out-String | ConvertFrom-Json) } catch { $preflight = $null }
        if (-not $pfOk) {
            $fmt = { param($c) "$($c.id): $($c.detail)`n  fix: $($c.fix)" }
            if (-not $preflight) {
                $status = 'preflight_failed'; $errorText = ($pfRaw | Out-String).Trim(); throw 'preflight'
            }
            $bad = @($preflight.checks | Where-Object { $_.status -notin @('ok', 'skipped') })
            $sharedBad = @($bad | Where-Object { -not $_.scope -or $_.scope -eq 'shared' })
            $waPreflightBad    = @($bad | Where-Object { $_.scope -eq 'whatsapp' } | ForEach-Object { & $fmt $_ })
            $emailPreflightBad = @($bad | Where-Object { $_.scope -eq 'email' }    | ForEach-Object { & $fmt $_ })
            if ($sharedBad.Count -gt 0 -and $RunKind -eq 'email' -and $todoToday) {
                # v3.0 (R30): the shared checks only matter for rebuilding the list, and today's exists.
                foreach ($c in $sharedBad) { $warnings += "pre-flight $($c.id) failed (not needed: today's list exists)" }
            }
            elseif ($sharedBad.Count -gt 0) {
                $status = 'preflight_failed'
                $errorText = ($sharedBad | ForEach-Object { & $fmt $_ }) -join "`n"
                throw 'preflight'
            }
        }
    }
    if ($RunKind -eq 'email' -and $emailPreflightBad.Count -gt 0) {
        # R30: nothing could be sent, so the list is not rebuilt either (no money spent).
        $status = 'email_preflight_failed'; $errorText = $emailPreflightBad -join "`n"
        $chan.email = @{ status = 'preflight_failed'; detail = $errorText }
        return
    }

    # 3 -------------------------------------------------------- WhatsApp channel: module 01 then 02 (R4-R7, R20, R24)
    # v3.0 (R30): in an email run this step only rebuilds a missing list - module 01 alone, never 02.
    $promptFile   = Join-Path $SchedDir 'prompt.txt'
    $settingsFile = Join-Path $SchedDir 'scheduler.settings.json'
    $transcript   = Join-Path $LogsDir "$Stamp.json"
    $errFile      = Join-Path $LogsDir "$Stamp.stderr.txt"
    $promptRun    = Join-Path $LogsDir "$Stamp.prompt.txt"

    $env:WA_MODE    = $Mode     # R4 - child environment only; the .env files on disk are never modified
    $env:EMAIL_MODE = $Mode
    # v3.0 (R30): a second guard for the email run's rebuild - even an off-script session cannot send WhatsApp.
    if ($RunKind -eq 'email') { $env:WA_MODE = 'dry-run' }

    # R24 + R20: a per-run copy of the prompt, with the trigger label filled in and - when the WhatsApp
    # pre-flight scope failed, or this is an email run - module 02 switched off (module 01 still runs:
    # email needs its list).
    $skipWhatsApp  = $waPreflightBad.Count -gt 0
    $moduleOneOnly = $skipWhatsApp -or $RunKind -eq 'email'
    $runClaude     = -not ($RunKind -eq 'email' -and $todoToday)
    $promptText = (Get-Content $promptFile -Raw -Encoding UTF8).Replace('{TRIGGER}', $ModuleTrigger)
    if ($moduleOneOnly) {
        $promptText = $promptText -replace '(?m)^3\. .*$', '3. Do NOT run module 02 this time: WhatsApp is unavailable and this is deliberate. Skip this step.'
    }
    Set-Content -Path $promptRun -Value $promptText -Encoding UTF8

    $waStatus = 'ok'; $waError = $null
    if (-not $runClaude) {
        # v3.0 (R30): the email run found today's list - no Claude session, no cost.
        $todo = Get-Content $TodoJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $groups = @($todo.PSObject.Properties)
        $itemCount = 0; foreach ($g in $groups) { $itemCount += @($g.Value).Count }
        $verify.todo_date = $Today; $verify.todo_groups = $groups.Count; $verify.todo_items = $itemCount
    }
    else {
        if ($RunKind -eq 'email') { $verify.list_rebuilt = $true; Write-Host "no list dated $Today - rebuilding it (module 01 only)" }
        try {
            # Two things matter here and both have bitten us:
            #  - Start-Process -ArgumentList joins with spaces and quotes NOTHING, so every path
            #    containing a space (this repo's does) must be quoted by hand.
            #  - The prompt is multi-line, so it goes in on stdin rather than as an argument;
            #    `claude -p` with no prompt argument reads it from there.
            # --permission-mode manual + --permission-prompts none is what actually RESTRICTS this run.
            # `permissions.allow` is only an auto-approve list: on its own it does not stop anything (T10).
            $argString = '-p --settings "{0}" --model {1} --permission-mode manual --permission-prompts none --output-format json' -f $settingsFile, $Model
            $claudeStartedAt = Get-Date
            $proc = Start-Process -FilePath $ClaudeExe -ArgumentList $argString `
                        -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
                        -RedirectStandardInput $promptRun `
                        -RedirectStandardOutput $transcript -RedirectStandardError $errFile

            if (-not $proc.WaitForExit([int]($TimeoutMin * 60 * 1000))) {          # R6
                try { & taskkill.exe /T /F /PID $proc.Id 2>&1 | Out-Null } catch { }
                Remove-StaleJobFiles $claudeStartedAt
                $waStatus = 'timeout'
                $waError = "the run passed its $TimeoutMin-minute limit and was killed. " +
                           "Most likely an unattended Google sign-in prompt or a hung Chrome - see $transcript"
                throw 'timeout'
            }

            $claudeExit = $proc.ExitCode
            $envelope = $null
            if (Test-Path $transcript) {
                try { $envelope = Get-Content $transcript -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
            }
            # The headless envelope carries `permission_denials`, the most useful clue when a run fails.
            # Record just the tool names and the commands, truncated - never the whole tool_input.
            $denials = @()
            if ($envelope -and $envelope.permission_denials) {
                foreach ($d in @($envelope.permission_denials)) {
                    $what = if ($d.tool_input.command) { [string]$d.tool_input.command }
                            elseif ($d.tool_input.file_path) { [string]$d.tool_input.file_path }
                            else { '' }
                    if ($what.Length -gt 300) { $what = $what.Substring(0, 300) + '...' }
                    $denials += @{ tool = [string]$d.tool_name; detail = $what }
                }
            }
            $claudeInfo = @{
                exit_code         = $claudeExit
                num_turns         = if ($envelope) { $envelope.num_turns } else { $null }
                total_cost_usd    = if ($envelope) { $envelope.total_cost_usd } else { $null }
                session_id        = if ($envelope) { $envelope.session_id } else { $null }
                transcript        = "logs/$Stamp.json"
                permission_denials = $denials
            }
            $resultText = if ($envelope) { [string]$envelope.result } else { '' }

            if ($claudeExit -ne 0 -or $resultText -match 'SCHEDULER_FAIL') {
                $waStatus = 'claude_failed'
                $reason = if ($resultText -match 'SCHEDULER_FAIL:\s*(.+)') { $Matches[1] } else { "claude exited $claudeExit" }
                $waError = "$reason`n  transcript: logs/$Stamp.json"
                throw 'claude'
            }

            # 4 ----------------------------------------------------- verify artifacts, not exit codes (R8)
            if (-not (Test-Path $TodoJson)) {
                $waStatus = 'verify_failed'; $waError = 'module 01 wrote no todo_by_name.json'; throw 'verify'
            }
            $todoDate = (Get-Item $TodoJson).LastWriteTime.ToString('yyyy-MM-dd')
            if ($todoDate -ne $Today) {
                $waStatus = 'verify_failed'
                $waError = "todo_by_name.json is from $todoDate, not today ($Today) - module 01 did not run"
                throw 'verify'
            }
            $todo = Get-Content $TodoJson -Raw -Encoding UTF8 | ConvertFrom-Json
            $groups = @($todo.PSObject.Properties)
            $itemCount = 0; foreach ($g in $groups) { $itemCount += @($g.Value).Count }
            $verify.todo_date = $todoDate; $verify.todo_groups = $groups.Count; $verify.todo_items = $itemCount

            $sentCount = 0; $failedCount = 0; $skippedCount = 0; $sendDate = $null
            if (-not $moduleOneOnly -and $Mode -eq 'live') {
                if (-not (Test-Path $SendLog)) {
                    $waStatus = 'verify_failed'; $waError = 'module 02 wrote no send_log.json'; throw 'verify'
                }
                $todayRuns = Read-TodayRuns $SendLog 'live'
                if ($todayRuns.Count -eq 0) {
                    $waStatus = 'verify_failed'
                    $waError = "send_log.json has no live run for $Today - module 02 did not send"
                    throw 'verify'
                }
                $sendDate = $Today
                # Aggregate across the day's rows: on a retry the newest row can legitimately show sent=0
                # because attempt 1 already delivered.
                $sentCount = Sum-Field $todayRuns 'sent'; $failedCount = Sum-Field $todayRuns 'failed'
                $skippedCount = Sum-Field $todayRuns 'skipped'
            }
            $verify.send_log_date = $sendDate; $verify.sent = $sentCount; $verify.failed = $failedCount; $verify.skipped = $skippedCount

            # R9 - a run where nobody was due is a success, not a failure
            if (-not $moduleOneOnly -and $Mode -eq 'live' -and $sentCount -eq 0 -and $failedCount -eq 0) { $waStatus = 'ok_nothing_to_send' }
            else { $waStatus = 'ok' }
        }
        catch {
            if ($waStatus -eq 'ok') { $waStatus = 'claude_failed'; $waError = "$_" }
        }
    }

    # R24: did module 02 record the trigger label we asked for? A label, never a delivery: a warning only.
    $waRowsMode = Read-TodayRuns $SendLog $Mode
    if (-not $moduleOneOnly -and $waStatus -in @('ok', 'ok_nothing_to_send') -and $waRowsMode.Count -gt 0) {
        if ([string]$waRowsMode[0].trigger -ne $ModuleTrigger) { $warnings += 'whatsapp trigger label missing' }
    }

    # 3b ------------------------------------------------------- email channel: module 04, run directly (R19, R21)
    $emailStatus = 'skipped'; $emailDetail = ''
    $todoToday = (Test-Path $TodoJson) -and ((Get-Item $TodoJson).LastWriteTime.ToString('yyyy-MM-dd') -eq $Today)
    if (-not $SchedEmail) {
        $emailDetail = 'switched off (SCHED_EMAIL=0)'
    }
    elseif ($RunKind -eq 'whatsapp') {
        # v3.0 (R27): email has its own run; this one never starts send_email.py.
        $emailDetail = if ($SplitEmail) { "runs separately at $EmailTimeStr" } else { 'not part of this run (-Channel whatsapp)' }
    }
    elseif ($RunKind -eq 'email' -and $waStatus -notin @('ok', 'ok_nothing_to_send')) {
        $emailStatus = 'failed'; $emailDetail = "today's list could not be rebuilt ($waStatus) - nothing was sent"
    }
    elseif ($emailPreflightBad.Count -gt 0) {
        $emailStatus = 'preflight_failed'; $emailDetail = $emailPreflightBad -join "`n"
    }
    elseif (-not $todoToday) {
        $emailDetail = "no list dated $Today to send (module 01 did not produce one)"
    }
    else {
        $emailOut = Join-Path $LogsDir "$Stamp.email.txt"
        $emailErr = Join-Path $LogsDir "$Stamp.email.err.txt"
        $env:PYTHONIOENCODING = 'utf-8'
        $eargs = '-u "{0}" --trigger {1}' -f $EmailPy, $ModuleTrigger
        $emailStartedAt = Get-Date
        try {
            $ep = Start-Process -FilePath $PythonExe -ArgumentList $eargs -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
                      -RedirectStandardOutput $emailOut -RedirectStandardError $emailErr
            if (-not $ep.WaitForExit([int]($EmailTimeoutMin * 60 * 1000))) {      # R19 backstop
                try { & taskkill.exe /T /F /PID $ep.Id 2>&1 | Out-Null } catch { }
                Remove-StaleJobFiles $emailStartedAt
                $emailStatus = 'failed'
                $emailDetail = "the email step passed its $EmailTimeoutMin-minute limit and was killed - see logs/$Stamp.email.txt"
            }
            else {
                $eExit = $ep.ExitCode
                $outText = if (Test-Path $emailOut) { Get-Content $emailOut -Raw -Encoding UTF8 } else { '' }
                $rows = Read-TodayRuns $EmailLog $Mode
                $verify.email = @{
                    sent = (Sum-Field $rows 'sent'); failed = (Sum-Field $rows 'failed'); unconfirmed = (Sum-Field $rows 'unconfirmed')
                    skipped = (Sum-Field $rows 'skipped'); invalid_address = (Sum-Field $rows 'invalid_address')
                    accepted = (Sum-Field $rows 'accepted'); queued = (Sum-Field $rows 'queued'); bounced = (Sum-Field $rows 'bounced')
                }
                if ($outText -match 'skipped_locked' -and $rows.Count -eq 0) {
                    $emailStatus = 'failed'; $emailDetail = 'module 04 was locked by another run'
                }
                elseif ($eExit -ne 0) {
                    $emailStatus = 'failed'; $emailDetail = "send_email.py exited $eExit - see logs/$Stamp.email.txt and .email.err.txt"
                }
                elseif ($rows.Count -eq 0) {
                    $emailStatus = 'failed'; $emailDetail = "module 04's send_log.json has no $Mode run for $Today - the email step did not record anything"
                }
                else {
                    $newest = $rows[0]
                    if ([int]$newest.summary.failed -gt 0 -or [int]$newest.summary.unconfirmed -gt 0) {
                        $emailStatus = 'failed'
                        $emailDetail = "failed $([int]$newest.summary.failed), unconfirmed $([int]$newest.summary.unconfirmed) - check Sent Items before re-running"
                    }
                    elseif ($verify.email.sent -eq 0) { $emailStatus = 'ok_nothing_to_send'; $emailDetail = 'nobody due' }
                    else { $emailStatus = 'ok'; $emailDetail = "sent $($verify.email.sent), accepted $($verify.email.accepted)" }
                }
            }
        } catch {
            $emailStatus = 'failed'; $emailDetail = "could not run the email step: $_"
        }
    }

    # 4b ------------------------------------------------------ channels, status precedence, warnings (R9, R22, R23)
    $waChanStatus = if ($RunKind -eq 'email') { 'skipped' }
                    elseif ($skipWhatsApp) { 'preflight_failed' }
                    elseif ($waStatus -in @('ok', 'ok_nothing_to_send')) { $waStatus }
                    else { 'failed' }
    $waDetail = if ($RunKind -eq 'email') { "runs at $SchedTimeStr" }
                elseif ($skipWhatsApp) { $waPreflightBad -join "`n" }
                elseif ($waChanStatus -eq 'failed') { "$waStatus" }
                elseif ($waChanStatus -eq 'ok') { "sent $($verify.sent)" } else { 'nobody due' }
    $chan.whatsapp = @{ status = $waChanStatus; detail = $waDetail }
    $chan.email    = @{ status = $emailStatus;  detail = $emailDetail }

    if ($waStatus -in @('timeout', 'claude_failed', 'verify_failed')) { $status = $waStatus; $errorText = $waError }
    elseif ($skipWhatsApp)                    { $status = 'whatsapp_preflight_failed'; $errorText = $waPreflightBad -join "`n" }
    elseif ($emailStatus -eq 'preflight_failed') { $status = 'email_preflight_failed'; $errorText = $emailDetail }
    elseif ($emailStatus -eq 'failed')        { $status = 'email_failed'; $errorText = $emailDetail }
    elseif ($waChanStatus -in @('ok_nothing_to_send', 'skipped') -and $emailStatus -in @('ok_nothing_to_send', 'skipped')) { $status = 'ok_nothing_to_send' }
    else { $status = 'ok' }

    # R23: delivery warnings, summed over today's rows for this mode. Reported, never failed. v3.0: each
    # run reports only the channel(s) it delivered, so the 13:30 run does not repeat the 10:00 warnings.
    $waRows = if ($RunKind -ne 'email') { Read-TodayRuns $SendLog $Mode } else { @() }
    $emRows = if ($RunKind -ne 'whatsapp') { Read-TodayRuns $EmailLog $Mode } else { @() }
    $n = Sum-Field $waRows 'not_delivered'; if ($n -gt 0) { $warnings += "$n WhatsApp message(s) not confirmed delivered" }
    $n = Sum-Field $waRows 'unconfirmed';   if ($n -gt 0) { $warnings += "$n WhatsApp message(s) unconfirmed - check the chat" }
    $n = Sum-Field $emRows 'unconfirmed';   if ($n -gt 0) { $warnings += "$n email(s) unconfirmed - check Sent Items" }
    $n = Sum-Field $emRows 'bounced';       if ($n -gt 0) { $warnings += "$n email(s) bounced - check the address" }
    $n = Sum-Field $emRows 'queued';        if ($n -gt 0) { $warnings += "$n email(s) still in the Outbox" }
    $n = Sum-Field $emRows 'invalid_address'; if ($n -gt 0) { $warnings += "$n email contact(s) with an invalid address" }
    # v3.0 (R32): the 10:00 run no longer mails, so a missing email task or a bad email time must be said.
    if ($SchedEmail -and $EmailTimeBad -and $RunKind -ne 'email') {
        $warnings += "SCHED_EMAIL_TIME '$EmailTimeStr' is not a usable time - email was sent in this run instead"
    }
    if ($SchedEmail -and $RunKind -eq 'whatsapp' -and $SplitEmail) {
        $emailTask = if ($env:SCHED_EMAIL_TASK) { $env:SCHED_EMAIL_TASK } else { 'ToDo-Email-Daily' }
        $emailTaskObj = Get-ScheduledTask -TaskName $emailTask -ErrorAction SilentlyContinue
        if (-not $emailTaskObj -or $emailTaskObj.State -eq 'Disabled') {
            $warnings += 'email task not installed - email will not be sent; run install_task.ps1'
        }
    }
    if ($warnings.Count -gt 0 -and $status -in @('ok', 'ok_nothing_to_send')) {
        $alerts = Send-Alert 'ToDo scheduler: delivery warnings' ($warnings -join "`n") -Only @('toast')
    }
}
catch {
    if ($status -eq 'ok') { $status = 'claude_failed'; $errorText = "$_" }
}
finally {
    $finished = Get-Date

    # 6 ------------------------------------------------------- alert (R10, R11)
    $isFailure = $status -notin @('ok', 'ok_nothing_to_send', 'skipped_locked')
    if ($isFailure -and (-not $IsScheduled -or $attempt -ge $MaxAttempts)) {
        $chanLine = "whatsapp: $($chan.whatsapp.status); email: $($chan.email.status)"
        $kindLabel = if ($RunKind -eq 'all') { '' } else { " ($RunKind run)" }
        $alerts = Send-Alert "ToDo scheduler$kindLabel failed: $status" `
                             "Attempt $attempt of $MaxAttempts on $Today ($EffectiveTrigger).`n$chanLine`n`n$errorText"
    }
    elseif ($isFailure) {
        Write-Host "attempt $attempt of $MaxAttempts failed ($status) - not alerting yet"
    }

    # 5 ------------------------------------------------------- log (R12)
    Save-Run @{
        date = $Today; started_at = $Started.ToString('o'); finished_at = $finished.ToString('o')
        duration_s = [int]($finished - $Started).TotalSeconds
        trigger = $EffectiveTrigger; run_kind = $RunKind; launch_event = $LaunchEvent; attempt = $attempt; status = $status
        preflight = $preflight; claude = $claudeInfo; verify = $verify
        channels = $chan; warnings = @($warnings)
        # Both halves are recorded: a channel that failed is as diagnostic as one that fired,
        # and R10 requires a failed channel to be visible as notify_failed rather than silently absent.
        alerts = $alerts.fired
        alerts_failed = @($alerts.failed | ForEach-Object { @{ channel = [string]$_.channel; error = [string]$_.error } })
        error = $errorText
    }

    # 7 ------------------------------------------------------- unlock, always (R2)
    if ($lockTaken) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }

    $line = "[$status] $Today $RunKind run, attempt $attempt ($EffectiveTrigger) in $([int]($finished - $Started).TotalSeconds)s  whatsapp=$($chan.whatsapp.status) email=$($chan.email.status)"
    if ($errorText) { $line += "`n$errorText" }
    Write-Host $line
    if ($ShowWindow -and -not $isFailure) { Write-Host 'run finished' }
}

if ($status -in @('ok', 'ok_nothing_to_send', 'skipped_locked')) { exit 0 }

# R11 retries live HERE, not in Task Scheduler. The task launches us through `conhost.exe --headless`
# (to get no window at all), and conhost does not pass our exit code back - Task Scheduler always
# sees 0x0, so its RestartCount can never fire (found 2026-09-21: attempt 1 failed, nothing retried).
# The lock is already released and this attempt's row already saved, so re-running ourselves is safe.
# A scheduled-kind launch (schedule or catch-up) retries; a manual-gui one never does, so a stray click
# can't chain into a retry loop still running when the real fire arrives (found 2026-09-23). The retry
# carries its label with -ContinueFrom (R18): 15 minutes after a 16:21 catch-up there is no Task
# Scheduler event for it and the clock says nothing, so it must be told what it is.
if ($IsScheduled -and $attempt -lt $MaxAttempts) {
    Write-Host "attempt $attempt failed - waiting $RetryWaitSec seconds, then attempt $($attempt + 1)"
    Start-Sleep -Seconds $RetryWaitSec
    & (Get-Process -Id $PID).Path -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $PSCommandPath -Trigger schedule -ContinueFrom $EffectiveTrigger -Channel $RunKind
    exit $LASTEXITCODE
}
exit 1
