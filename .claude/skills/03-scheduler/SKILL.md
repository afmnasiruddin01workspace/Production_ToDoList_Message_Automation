---
name: 03-scheduler
description: Runs the daily pipeline automatically at 10:00 Asia/Dhaka via a hidden Windows Task Scheduler task - module 01 (calendar-todo) then two independent delivery channels, WhatsApp (module 02, through a headless Claude session) and email (module 04, run directly) - so it no longer depends on the owner remembering to open Claude Code. Verifies from the artifacts that each channel really delivered, retries on failure, and alerts the owner (WhatsApp, toast, log, visible window) only when a run still fails after retrying.
---

# 03 — Scheduler

Source of truth: `plan/stableMD/03-scheduler.stable.md` (v2.0). This skill is a summary of its §7 Steps — if they ever disagree, the stable plan wins and this file needs updating.

## When to use
This skill is not invoked directly — it *invokes* `01-calendar-todo` and `02-whatsapp-message` from inside a headless `claude -p` session launched by Windows Task Scheduler, and runs `04-email-message` directly. You interact with it only through `src/scheduler/install_task.ps1` (install/status/uninstall), `src/scheduler/run_daily.ps1` (manual runs) and by reading `outputs/03-scheduler/run_log.json`.

**Why headless Claude for modules 01/02 but not for 04.** Module 01 is agentic — it calls the claude.ai Google Calendar connector, which only a Claude session can reach — so the scheduler drives Claude for it. Module 04 is deterministic (a Python script that drives Outlook), so the wrapper runs it directly: no tokens, an exit code, and an email failure can never stop WhatsApp.

## One-time setup
- `cd src/scheduler && pwsh -File install_task.ps1` registers (or updates, idempotently) the `ToDo-WhatsApp-Daily` scheduled task: daily at 10:00 local (= Asia/Dhaka), fully hidden, `StartWhenAvailable`, `MultipleInstances IgnoreNew`, `ExecutionTimeLimit` 3h.
- Copy `src/scheduler/scheduler.env.sample` to `scheduler.env` (git-ignored) and adjust if needed. **`SCHED_EMAIL`** switches the email channel (`1` on, `0` off). The owner's file keeps it at `0` until the first real emails are approved — the wrapper forces `EMAIL_MODE=live`, so with `1` the next email day mails real colleagues.
- Modules 01, 02 and 04 must already be set up and working by hand at least once (Google sign-in completed, WhatsApp session linked, classic Outlook with `.secrets/email.env` and `EmailContacts.txt`) — this scheduler never performs a sign-in itself.
- `pwsh -File src/scheduler/install_task.ps1 -Status` shows the task's state; `-Uninstall` switches the automation off (logs and outputs untouched).

## Steps
1. **Launch, fully hidden.** Task Scheduler runs `conhost.exe --headless <pwsh> -File src/scheduler/run_daily.ps1` (a plain `-WindowStyle Hidden` is ignored on Windows 11 and a visible window once killed a run with `0xC000013A`).
2. **Classify the launch** (v2.0). Task Scheduler's own log is asked what it did: the **most recent** of event **107** (time trigger) → `schedule`, **114** (missed schedule, e.g. the laptop was asleep) → `catch-up`, **110** (a user's "Run") → `manual-gui`. No events at all → the old clock rule decides (a launch far from `SCHED_TIME` is `manual-gui`). A self-retry carries its label with `-ContinueFrom`, because it leaves no event. `schedule` and `catch-up` retry and alert only at the last attempt; `manual-gui` alerts at once and never retries.
3. **Lock, atomically** (`outputs/03-scheduler/run.lock`) — an overlapping run logs `skipped_locked`, toasts, and exits without touching WhatsApp's session.
4. **Pre-flight** (`preflight.py`, ten checks, each with a **scope**): `shared` failures (Google token, `lists`, tools, disk, `network`) stop everything; a `whatsapp` failure (`contacts`, `wa_session`, `wa_config`, `wa_network`) skips module 02 but module 01 and email still run; an `email` failure (`email_config`) skips email but WhatsApp still runs. It never starts Outlook and never triggers an interactive sign-in.
5. **WhatsApp channel** — a headless `claude -p`, `WA_MODE` and `EMAIL_MODE` forced to the run's mode (`live` for the schedule), follows a per-run copy of `src/scheduler/prompt.txt` (trigger label filled in) to invoke `01-calendar-todo` then, unless its pre-flight scope failed, `02-whatsapp-message`.
6. **Email channel** — whenever today's list exists (even if Claude failed after module 01), run `send_email.py --trigger <schedule|manual>` directly, bounded by `SCHED_EMAIL_TIMEOUT_MIN`.
7. **Verify each channel from its artifacts, not exit codes** — `todo_by_name.json` dated today, module 02's and module 04's `send_log.json` rows for today and the run's mode (aggregated across retries). `ok_nothing_to_send` when nobody on a channel was due.
8. **Status and warnings** — the status follows a fixed precedence (`skipped_locked` → `preflight_failed` → `timeout` → `claude_failed` → `verify_failed` → `whatsapp_preflight_failed` → `email_preflight_failed` → `email_failed` → `ok_nothing_to_send` → `ok`); `channels.whatsapp` and `channels.email` always record both outcomes. Delivery *warnings* (messages not confirmed delivered, emails bounced / queued / unconfirmed / with an invalid address) go in the row and fire **one toast**, never a retry or a window.
9. **Retry inside the wrapper** — a failure of either channel waits `SCHED_RETRY_WAIT_SEC` (15 min) and re-invokes the script with `-ContinueFrom`, up to `SCHED_MAX_ATTEMPTS` (3). Modules 02 and 04 are idempotent, so nobody is messaged or mailed twice. (Task Scheduler's own `RestartCount` cannot do this: the headless launcher hides the exit code.)
10. **Log and alert** — every attempt is appended to `outputs/03-scheduler/run_log.json` (newest first, 60-day retention) with `trigger`, `launch_event`, `channels`, `warnings`, `verify` (WhatsApp figures plus `verify.email`). The final failed attempt alerts on all four channels (WhatsApp to the owner's own number, toast, log, visible window) and names both channels' outcomes. If the wrapper had to kill a step, it also deletes the job files modules 02/04 left in `%TEMP%` (they hold real numbers/addresses). **Unlock, always**, in a `finally`.

For manual testing prefer a terminal over the Task Scheduler "Run" button: `pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run` runs **both** channels in dry-run and never sends.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **No real colleague has yet been emailed by the scheduler.** The first real unattended run with the email channel is scheduled for Wednesday 2026-10-21 (test T39); until the owner sets `SCHED_EMAIL=1` the channel is off. A genuine catch-up run after the laptop sleeps through 10:00 has still not happened live — its classification is proven against real events, the end-to-end retry behaviour on a stub.
- **The pre-10:00 WhatsApp worker hang is unexplained.** Module 02's v1.2 timeouts bound it at 120 s and the retry recovers from it automatically, but the root cause is undiagnosed.
- **When Outlook is closed at 10:00, module 04 starts it** (minimised, with an explicit profile) and leaves it open.
- **The Task Scheduler Operational log is a 10 MB circular log** (≈6 days here): launch classification reads it once, at launch time. With it disabled or rolled over the clock rule decides.
- **This automates a linked WhatsApp Web session against WhatsApp's terms** (module 02's accepted risk), and forces `live` every day, so any WhatsApp-side warning would first surface from an unattended run.
- Bash is intentionally left un-narrowed in `scheduler.settings.json` because module 01 is agentic and issues compound shell commands whose shape varies — see the file's own `_comment` block before tightening it.

## If it fails
See `docs/TROUBLESHOOTING.md#03-scheduler` for symptom → cause → fix. Common ones: a blank console window and `0xC000013A` (launcher regressed off `conhost --headless`), `preflight_failed` (read the `fix` in the alert — usually an expired Google token), `whatsapp_preflight_failed` / `email_preflight_failed` (only that channel is down — the alert names the check and its fix), `email_failed` (check Sent Items before re-running), `claude_failed` with `startup-timeout` (module 02's own timeout — nothing was sent, the next attempt should recover), a catch-up run showing `manual-gui` (`launch_event: none` — the Operational log held no event for it).
