<#
  03-scheduler: the daily wrapper. The only thing Task Scheduler knows about.

  Lock -> pre-flight -> headless Claude (skills 01 then 02) -> verify artifacts -> log -> alert.

  Runnable by hand with exactly the same behaviour as the scheduled run (R13):
      pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run -Visible

  Every path is derived from $PSScriptRoot, never from the current directory, so it behaves
  identically whichever way it is launched.
#>
[CmdletBinding()]
param(
    [ValidateSet('schedule', 'manual')][string]$Trigger = 'schedule',
    [ValidateSet('dry-run', 'self', 'live')][string]$Mode = '',
    [switch]$Visible,
    [switch]$SkipPreflight
)

$ErrorActionPreference = 'Stop'

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

$cfg             = Read-EnvFile (Join-Path $SchedDir 'scheduler.env')
$Model           = if ($cfg['SCHED_MODEL'])       { $cfg['SCHED_MODEL'] }       else { 'sonnet' }
$TimeoutMin      = if ($cfg['SCHED_TIMEOUT_MIN']) { [int]$cfg['SCHED_TIMEOUT_MIN'] } else { 25 }
$RetentionDays   = if ($cfg['SCHED_RETENTION_DAYS']) { [int]$cfg['SCHED_RETENTION_DAYS'] } else { 60 }
$MaxAttempts     = if ($cfg['SCHED_MAX_ATTEMPTS']) { [int]$cfg['SCHED_MAX_ATTEMPTS'] } else { 3 }
$ShowWindow      = $Visible -or ($cfg['SCHED_VISIBLE'] -eq '1')
if (-not $Mode) { $Mode = 'live' }   # R4: the scheduled run always forces live

$channels = @()
if ($cfg['SCHED_ALERT_TOAST']    -ne '0') { $channels += 'toast' }
if ($cfg['SCHED_ALERT_WHATSAPP'] -ne '0') { $channels += 'whatsapp' }
if ($cfg['SCHED_ALERT_WINDOW']   -ne '0') { $channels += 'window' }

$PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
$NodeExe   = (Get-Command node   -ErrorAction SilentlyContinue).Source
# SCHED_CLAUDE_EXE lets the test ladder swap in a stub, the way module 02's tests stub its Node
# worker. It is never set in normal operation.
$ClaudeExe = if ($env:SCHED_CLAUDE_EXE) { $env:SCHED_CLAUDE_EXE }
             else { (Get-Command claude -ErrorAction SilentlyContinue).Source }

$Started = Get-Date
$Stamp   = $Started.ToString('yyyy-MM-ddTHHmm')
$Today   = $Started.ToString('yyyy-MM-dd')   # machine TZ is already Asia/Dhaka (+06:00)

# v1.1 (R16): Task Scheduler's "Run" action launches this exact same registered command, so a
# `-Trigger schedule` launch cannot by itself tell "the real 10:00 fire" apart from "someone
# clicked Run in the GUI" - both looked identical in the log until now (found 2026-09-23, when a
# manual Run near 9:00 was logged and alerted exactly like a real scheduled failure, then quietly
# started its own 15-minute self-retry chain that was still running when the real 10:00 trigger
# fired). $EffectiveTrigger relabels a `schedule` launch that falls well outside the configured
# SCHED_TIME window as `manual-gui`: it still runs (the owner may be testing on purpose), but it is
# logged distinctly, alerts immediately on failure instead of staying quiet, and never joins the
# schedule day's attempt ladder or self-retry loop - so a stray click can no longer chain into (or
# collide with) the real run.
$SchedTimeStr    = if ($cfg['SCHED_TIME']) { $cfg['SCHED_TIME'] } else { '10:00' }
$SchedToday      = $Started.Date.Add(([datetime]::ParseExact($SchedTimeStr, 'HH:mm', $null)).TimeOfDay)
$WindowStart     = $SchedToday.AddMinutes(-5)
$WindowEnd       = $SchedToday.AddMinutes(15 * ($MaxAttempts - 1) + 20)   # covers every self-retry plus slack
$EffectiveTrigger = $Trigger
if ($Trigger -eq 'schedule' -and ($Started -lt $WindowStart -or $Started -gt $WindowEnd)) {
    $EffectiveTrigger = 'manual-gui'
    Write-Host "note: launched via Task Scheduler but well outside the $SchedTimeStr window - treating as manual-gui, not schedule"
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
    # prune old transcripts alongside the log
    Get-ChildItem $LogsDir -Filter '*.json' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$RetentionDays) } |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Get-AttemptNumber {
    # Task Scheduler restarts the same task, so the attempt number is derived from today's
    # already-failed scheduled runs (R11: alert only once the last attempt has failed).
    # Uses $EffectiveTrigger (v1.1, R16): a manual-gui launch never joins the schedule day's
    # attempt ladder, so a stray click can't inflate or be confused with the real run's count.
    if ($EffectiveTrigger -ne 'schedule') { return 1 }
    $runs = @((Get-RunLog).runs)
    $failedToday = @($runs | Where-Object {
        $_.date -eq $Today -and $_.trigger -eq 'schedule' -and
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
    # v1.1 (R15): the old check-then-write (Test-Path, then Set-Content) has a race window
    # between the two calls. Two launches within milliseconds of each other (found 2026-09-23:
    # two Task Scheduler instances two seconds apart) could both see no lock and both write one,
    # the second silently overwriting the first - so both proceeded and collided on the shared
    # WhatsApp Chrome profile. FileMode.CreateNew is atomic at the OS level: it either creates the
    # file or throws, with nothing in between for a second process to slip through.
    for ($try = 0; $try -lt 2; $try++) {
        try {
            $fs = [System.IO.File]::Open($LockFile, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
            try {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes((@{ pid = $PID; started = $Started.ToString('o') } | ConvertTo-Json))
                $fs.Write($bytes, 0, $bytes.Length)
            } finally { $fs.Close() }
            return $true
        } catch [System.IO.IOException] {
            # Someone else's lock file already exists. If it's live, we lose fairly; if it's
            # stale, clear it and try the atomic create once more.
            if (Test-Lock) { return $false }
            Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
        }
    }
    return $false
}

# ---------------------------------------------------------------- run
$status    = 'ok'
$errorText = $null
$preflight = $null
$claudeInfo = @{}
$verify    = @{}
$alerts    = @{ fired = @(); failed = @() }
$attempt   = Get-AttemptNumber
$lockTaken = $false

try {
    # 1 -------------------------------------------------------- lock (R2)
    # A collision is not a failure, so it is logged and toasted but never escalated to a
    # WhatsApp message or a popup window - those are reserved for things that need the owner.
    # Note there is exactly one Save-Run in this script, in the finally block: an `exit` here
    # would still run finally and write the row a second time.
    if (-not (Try-AcquireLock)) {
        $status = 'skipped_locked'
        $held = Test-Lock
        $errorText = if ($held) { "another run (pid $($held.pid)) started $($held.started) is still going" }
                     else { "the lock is held by another run that started at nearly the same moment" }
        $alerts = Send-Alert 'ToDo scheduler: run skipped' $errorText -Only @('toast')
        Write-Host "skipped: $errorText"
        return
    }
    $lockTaken = $true

    # 2 -------------------------------------------------------- pre-flight (R3)
    if (-not $SkipPreflight) {
        $pfRaw = & $PythonExe (Join-Path $SchedDir 'preflight.py') --repo $RepoRoot --python $PythonExe 2>&1
        $pfOk  = ($LASTEXITCODE -eq 0)
        try { $preflight = ($pfRaw | Out-String | ConvertFrom-Json) } catch { $preflight = $null }
        if (-not $pfOk) {
            $status = 'preflight_failed'
            $bad = @()
            if ($preflight) {
                $bad = @($preflight.checks | Where-Object { $_.status -notin @('ok', 'skipped') } |
                         ForEach-Object { "$($_.id): $($_.detail)`n  fix: $($_.fix)" })
            }
            $errorText = if ($bad) { $bad -join "`n" } else { ($pfRaw | Out-String).Trim() }
            throw "preflight"
        }
    }

    # 3 -------------------------------------------------------- pipeline (R4-R7)
    $promptFile  = Join-Path $SchedDir 'prompt.txt'
    $settingsFile = Join-Path $SchedDir 'scheduler.settings.json'
    $transcript  = Join-Path $LogsDir "$Stamp.json"
    $errFile     = Join-Path $LogsDir "$Stamp.stderr.txt"

    $env:WA_MODE = $Mode     # R4 - child environment only; .secrets/whatsapp.env is never modified

    # Two things matter here and both have bitten us:
    #  - Start-Process -ArgumentList joins with spaces and quotes NOTHING, so every path
    #    containing a space (this repo's does) must be quoted by hand.
    #  - The prompt is multi-line, so it goes in on stdin rather than as an argument;
    #    `claude -p` with no prompt argument reads it from there.
    # --permission-mode manual + --permission-prompts none is what actually RESTRICTS this run.
    # `permissions.allow` in the settings file is only an auto-approve list: on its own it does not
    # stop anything, and T10 proved a write outside outputs/ went straight through without these
    # two flags. With them, anything not allowed becomes an unanswerable prompt, i.e. a denial.
    $argString = '-p --settings "{0}" --model {1} --permission-mode manual --permission-prompts none --output-format json' -f $settingsFile, $Model
    $proc = Start-Process -FilePath $ClaudeExe -ArgumentList $argString `
                -WorkingDirectory $RepoRoot -NoNewWindow -PassThru `
                -RedirectStandardInput $promptFile `
                -RedirectStandardOutput $transcript -RedirectStandardError $errFile

    if (-not $proc.WaitForExit($TimeoutMin * 60 * 1000)) {          # R6
        try { & taskkill.exe /T /F /PID $proc.Id 2>&1 | Out-Null } catch { }
        $status = 'timeout'
        $errorText = "the run passed its $TimeoutMin-minute limit and was killed. " +
                     "Most likely an unattended Google sign-in prompt or a hung Chrome - see $transcript"
        throw 'timeout'
    }

    $claudeExit = $proc.ExitCode
    $envelope = $null
    if (Test-Path $transcript) {
        try { $envelope = Get-Content $transcript -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
    }
    # The headless envelope carries `permission_denials`, which is by far the most useful clue when
    # a run fails: it names the exact tool call that was refused. Record just the tool names and the
    # commands, truncated - never the whole tool_input, which could echo file contents.
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
        $status = 'claude_failed'
        $reason = if ($resultText -match 'SCHEDULER_FAIL:\s*(.+)') { $Matches[1] } else { "claude exited $claudeExit" }
        $errorText = "$reason`n  transcript: logs/$Stamp.json"
        throw 'claude'
    }

    # 4 -------------------------------------------------------- verify artifacts, not exit codes (R8)
    if (-not (Test-Path $TodoJson)) {
        $status = 'verify_failed'; $errorText = 'module 01 wrote no todo_by_name.json'; throw 'verify'
    }
    $todoDate = (Get-Item $TodoJson).LastWriteTime.ToString('yyyy-MM-dd')
    if ($todoDate -ne $Today) {
        $status = 'verify_failed'
        $errorText = "todo_by_name.json is from $todoDate, not today ($Today) - module 01 did not run"
        throw 'verify'
    }
    $todo = Get-Content $TodoJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $groups = @($todo.PSObject.Properties)
    $itemCount = 0; foreach ($g in $groups) { $itemCount += @($g.Value).Count }

    $sentCount = 0; $failedCount = 0; $skippedCount = 0; $sendDate = $null
    if ($Mode -eq 'live') {
        if (-not (Test-Path $SendLog)) {
            $status = 'verify_failed'; $errorText = 'module 02 wrote no send_log.json'; throw 'verify'
        }
        $sl = Get-Content $SendLog -Raw -Encoding UTF8 | ConvertFrom-Json
        # Module 02 v1.1 keeps every run inside the window, so a date can hold several rows
        # (e.g. this run plus its retries). Select by date AND mode, and aggregate across them
        # rather than trusting the newest row alone - on a retry the newest row can legitimately
        # show sent=0 because attempt 1 already delivered.
        $todayRuns = @($sl.runs | Where-Object { $_.date -eq $Today -and $_.mode -eq 'live' })
        if ($todayRuns.Count -eq 0) {
            $status = 'verify_failed'
            $errorText = "send_log.json has no live run for $Today - module 02 did not send"
            throw 'verify'
        }
        $sendDate = $Today
        foreach ($r in $todayRuns) {
            $sentCount    += [int]$r.summary.sent
            $failedCount  += [int]$r.summary.failed
            $skippedCount += [int]$r.summary.skipped
        }
    }

    $verify = @{
        todo_date = $todoDate; todo_groups = $groups.Count; todo_items = $itemCount
        send_log_date = $sendDate; sent = $sentCount; failed = $failedCount; skipped = $skippedCount
    }

    # R9 - a run where nobody was due is a success, not a failure
    if ($Mode -eq 'live' -and $sentCount -eq 0 -and $failedCount -eq 0) { $status = 'ok_nothing_to_send' }
    else { $status = 'ok' }
}
catch {
    if ($status -eq 'ok') { $status = 'claude_failed'; $errorText = "$_" }
}
finally {
    $finished = Get-Date

    # 6 ------------------------------------------------------- alert (R10, R11)
    $isFailure = $status -notin @('ok', 'ok_nothing_to_send', 'skipped_locked')
    if ($isFailure -and ($EffectiveTrigger -ne 'schedule' -or $attempt -ge $MaxAttempts)) {
        $alerts = Send-Alert "ToDo scheduler failed: $status" `
                             "Attempt $attempt of $MaxAttempts on $Today.`n`n$errorText"
    }
    elseif ($isFailure) {
        Write-Host "attempt $attempt of $MaxAttempts failed ($status) - not alerting yet"
    }

    # 5 ------------------------------------------------------- log (R12)
    Save-Run @{
        date = $Today; started_at = $Started.ToString('o'); finished_at = $finished.ToString('o')
        duration_s = [int]($finished - $Started).TotalSeconds
        trigger = $EffectiveTrigger; attempt = $attempt; status = $status
        preflight = $preflight; claude = $claudeInfo; verify = $verify
        # Both halves are recorded: a channel that failed is as diagnostic as one that fired,
        # and R10 requires a failed channel to be visible as notify_failed rather than silently absent.
        alerts = $alerts.fired
        alerts_failed = @($alerts.failed | ForEach-Object { @{ channel = [string]$_.channel; error = [string]$_.error } })
        error = $errorText
    }

    # 7 ------------------------------------------------------- unlock, always (R2)
    if ($lockTaken) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }

    $line = "[$status] $Today attempt $attempt in $([int]($finished - $Started).TotalSeconds)s"
    if ($errorText) { $line += "`n$errorText" }
    Write-Host $line
    if ($ShowWindow -and -not $isFailure) { Write-Host 'run finished' }
}

if ($status -in @('ok', 'ok_nothing_to_send', 'skipped_locked')) { exit 0 }

# R11 retries live HERE, not in Task Scheduler. The task launches us through `conhost.exe --headless`
# (to get no window at all), and conhost does not pass our exit code back - Task Scheduler always
# sees 0x0, so its RestartCount can never fire (found 2026-09-21: attempt 1 failed, nothing retried).
# The lock is already released and this attempt's row already saved, so re-running ourselves is safe.
# Uses $EffectiveTrigger (v1.1, R16): a manual-gui launch never self-retries, so a stray click at
# some other time of day can't chain into a 15-minute retry loop that's still running when the
# real scheduled fire arrives (found 2026-09-23 - see the note where $EffectiveTrigger is set).
if ($EffectiveTrigger -eq 'schedule' -and $attempt -lt $MaxAttempts) {
    Write-Host "attempt $attempt failed - waiting 15 minutes, then attempt $($attempt + 1)"
    Start-Sleep -Seconds 900
    & (Get-Process -Id $PID).Path -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $PSCommandPath -Trigger schedule
    exit $LASTEXITCODE
}
exit 1
