---
name: 03-scheduler
description: Runs modules 01 (calendar-todo) then 02 (whatsapp-message) automatically at 10:00 Asia/Dhaka every day via a hidden Windows Task Scheduler task, so the pipeline no longer depends on the owner remembering to open Claude Code. Verifies from the artifacts that both modules actually did their job, retries on failure, and alerts the owner (WhatsApp, toast, log, visible window) only when a run still fails after retrying.
---

# 03 — Scheduler

Source of truth: `plan/stableMD/03-scheduler.stable.md` (v1.1). This skill is a summary of its §7 Steps — if they ever disagree, the stable plan wins and this file needs updating.

## When to use
This skill is not invoked directly — it *invokes* `01-calendar-todo` and `02-whatsapp-message` from inside a headless `claude -p` session launched by Windows Task Scheduler. You interact with it only through `src/scheduler/install_task.ps1` (install/status/uninstall) and by reading `outputs/03-scheduler/run_log.json`.

**Why headless Claude, not a plain script.** Module 01 is agentic — it calls the claude.ai Google Calendar connector, which only a Claude session can reach — so the scheduler drives Claude itself rather than re-implementing module 01 in Python.

## One-time setup
- `cd src/scheduler && pwsh -File install_task.ps1` registers (or updates, idempotently) the `ToDo-WhatsApp-Daily` scheduled task: daily at 10:00 local (= Asia/Dhaka, machine timezone), fully hidden, `StartWhenAvailable`, `MultipleInstances IgnoreNew`, `ExecutionTimeLimit` 3h.
- Copy `src/scheduler/scheduler.env.sample` to `scheduler.env` (git-ignored) and adjust if needed — sensible defaults ship as-is.
- Modules 01 and 02 must already be set up and working by hand at least once (Google sign-in completed, WhatsApp session linked) — this scheduler never performs either sign-in itself; an expired token or missing session is a pre-flight failure, not something it recovers from.
- `pwsh -File src/scheduler/install_task.ps1 -Status` shows the task's state, last/next run.
- `pwsh -File src/scheduler/install_task.ps1 -Uninstall` switches the automation off; logs and outputs are untouched.

## Steps

1. **Launch, fully hidden.** Task Scheduler runs `conhost.exe --headless <pwsh> -File src/scheduler/run_daily.ps1`. `conhost --headless` is required — a plain `-WindowStyle Hidden` pwsh is silently shown anyway under Windows 11's default-terminal handoff, and closing that window kills the whole run (`0xC000013A`, found and fixed 2026-09-20).
2. **Lock, atomically** (`outputs/03-scheduler/run.lock`) — a second overlapping run (the previous one still going, or a manual fire) sees the lock, logs `skipped_locked`, alerts on the toast channel only, and exits without touching WhatsApp's session profile. **(v1.1)** Acquisition uses an atomic create, not check-then-write, so two launches within milliseconds of each other can't both slip through — this is exactly what happened on 2026-09-23 (two Task-Scheduler-launched instances collided on the shared Chrome profile) and is now fixed.
3. **Pre-flight** (`preflight.py`) — 8 checks: Google token refresh (never triggers an interactive sign-in, so it can't hang), `lists.txt`, contacts file, WhatsApp linked session present, WhatsApp config usable, `claude`/`node`/`python` all resolve, disk space, network reachability. Any failure stops the run before it starts, with a specific fix in the alert.
4. **Run the pipeline** — a headless `claude -p` session, forced `WA_MODE=live`, `--settings src/scheduler/scheduler.settings.json`, follows `src/scheduler/prompt.txt` to invoke skill `01-calendar-todo` then, only if that produced today's `todo_by_name.json`, skill `02-whatsapp-message`.
5. **Verify from artifacts, not exit codes** — `claude` exiting 0 does not by itself prove anything happened. The wrapper checks `todo_by_name.json` is dated today and `send_log.json` gained a `live` row for today before calling it `ok`.
6. **Retry, in the wrapper itself, not Task Scheduler.** `conhost --headless` does not propagate the child's exit code, so Task Scheduler's own `RestartCount` can never see a failure to retry (found 2026-09-21). `run_daily.ps1` instead retries itself: on failure it waits 15 minutes and re-invokes itself, up to `SCHED_MAX_ATTEMPTS` (default 3). Confirmed live 2026-09-22: attempt 1 hit module 02's worker-startup timeout, nothing was sent, and attempt 2 fifteen minutes later delivered both messages with no one at the laptop.
7. **Log** every attempt to `outputs/03-scheduler/run_log.json` (newest first, 60-day retention) — status, preflight results, the `claude` invocation's cost/turns, the verify block, which alert channels fired.
8. **Alert only once it matters.** No alert on attempt 1 or 2 of a scheduled run; the final failed attempt alerts on all four channels (WhatsApp to the owner's own number, a Windows toast, the log — always written — and a visible console window with the failure and its fix). A `skipped_locked` alerts only the toast, since it is usually benign.
9. **Unlock, always**, in a `finally`, whatever the outcome.

**(v1.1) A Task Scheduler launch is not automatically trusted as the real 10:00 fire.** Clicking "Run" in Task Scheduler's GUI launches the exact same registered command as the real trigger, so before this fix a manual click was indistinguishable from the genuine schedule — including inheriting its 15-minute self-retry loop, which is exactly what happened on 2026-09-23 (a stray click near 9:01 started a retry chain still running when the real 10:00 trigger fired). Every `-Trigger schedule` launch is now checked against the configured `SCHED_TIME` window: inside it, treated as `schedule` as before; well outside it, relabeled `trigger: manual-gui` — it still runs (useful for a deliberate test), but alerts on failure immediately instead of waiting for the attempt ladder, and never self-retries. **If you want to test by hand, prefer a terminal** (`pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run`) over the Task Scheduler "Run" button — it's unambiguous and defaults to a mode that never sends.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **The pre-10:00 WhatsApp worker hang is unexplained.** On three real mornings (2026-09-20, -21, -22) module 02's worker stalled at start-up around 10:01; it runs normally by hand minutes later. Module 02 v1.2's worker timeout bounds this at 120s and this scheduler's retry recovers from it automatically, but the root cause is still undiagnosed — worth its own investigation if it keeps costing a retry every morning.
- **Missed-run catch-up (`StartWhenAvailable`) is configured but not yet observed for real** — it needs the laptop to actually be asleep or off across 10:00, which hasn't happened in three days of operation. If a day is ever missed, check `run_log.json` for a run shortly after wake and record the outcome.
- **This automates a linked WhatsApp Web session against WhatsApp's terms** (module 02's accepted risk, not new here) — the scheduler adds no new risk on top of that, but forces `WA_MODE=live` every day, so any WhatsApp-side warning would first surface from an unattended run rather than one the owner is watching.
- Bash is intentionally left un-narrowed in `scheduler.settings.json` because module 01 is agentic and issues compound shell commands whose exact shape varies run to run — see the file's own `_comment` block for the measured allow/deny behaviour before tightening it.

## If it fails
See `docs/TROUBLESHOOTING.md#03-scheduler` for symptom → cause → fix. Common ones: a blank console window appearing and the run dying with `0xC000013A` (launcher regressed off `conhost --headless` — check the task's registered action), `preflight_failed` (read the `fix` field in the alert — usually an expired Google token or a dropped WhatsApp session), `claude_failed` with the WhatsApp worker's `startup-timeout`/`send-timeout` reasons (module 02's own timeout fired — nothing was sent, the next attempt should recover), a permission denial naming a compound Bash command (a `deny` rule matched one component of it — see `scheduler.settings.json`'s `_comment`).
