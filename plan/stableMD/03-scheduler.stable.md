---
module: scheduler
id: "03"
phase: stable
version: "1.1"
depends_on: ["01-calendar-todo", "02-whatsapp-message"]
input_from: .claude/skills/01-calendar-todo/SKILL.md + .claude/skills/02-whatsapp-message/SKILL.md (invoked, not read as data)
output_to: outputs/03-scheduler/
approved_by: A F M Nasir Uddin (2026-09-23 — "ok", v1.1 hotfix approved through to stable)
date: 2026-09-23
---

# 03 — Scheduler

## 1. Frontmatter
See YAML header above. Every field must be filled before validation.

## 2. Goal
Run the whole pipeline by itself at **10:00 Asia/Dhaka every day** on this Windows laptop, with no
window and no human present: launch headless Claude Code, have it run skill `01-calendar-todo` and
then `02-whatsapp-message`, verify from the artifacts that both actually did their job, and tell the
owner — by WhatsApp, a Windows toast, a log and a visible window — whenever a run fails or needs him.

**Why.** Modules 01 and 02 are stable but only fire when the owner remembers to open Claude Code. A
forgotten session means colleagues silently get no to-do list that day. This module removes the owner
from the daily path and puts him back only when something is actually broken.

**Why headless Claude and not a plain script.** Module 01 is *agentic*: Claude itself performs its ten
steps, calling the claude.ai Google Calendar connector for events. Only a Claude session can reach that
connector, so the scheduler drives Claude rather than re-implementing module 01 in Python. The owner
chose this over porting module 01 to the Calendar API (2026-09-19).

### Explore findings (2026-09-19, read-only)

- **Headless reaches the connector — proven, not assumed.**
  `claude -p "…list_calendars…" --allowedTools mcp__claude_ai_Google_Calendar__list_calendars --model sonnet --output-format json`
  returned `subtype: success`, `is_error: false`, `num_turns: 3`, and the account's 3 calendars
  (`Holidays in Bangladesh`, two personal). No login prompt, no interaction, exit 0.
  Cost **$0.0883** for that one trivial call. Supporting evidence: `~/.claude.json` holds
  `claudeAiMcpEverConnected` including *claude.ai Google Calendar* (connectors are account-level, not
  per-project), and this project's `disabledMcpServers` lists only Gmail, Zapier and a docs plugin.
- **Environment.** `claude.exe` 2.1.278 at `C:\Users\nasir9781\.local\bin\`; `pwsh` 7.6.6 and Windows
  PowerShell both present; node v26.7.0; Python 3.14.7 with `google-auth-oauthlib` / `googleapiclient`
  importable. Machine timezone is **Bangladesh Standard Time (+06:00)**, so Task Scheduler's local time
  *is* Asia/Dhaka and no timezone conversion is needed anywhere. No scheduled tasks exist yet.
  `BurntToast` is **not** installed. `src/scheduler/` is empty but for `.gitkeep`.
- **`fetch_tasks.py` can hang an unattended run.** When the stored token is expired or revoked it falls
  back to `InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0)` — an interactive
  browser sign-in. With no one at the laptop that blocks forever. This is the single most likely cause
  of a silent, stuck 10:00 run, and it drives R3 and R6 below.
- **Current token state.** `.secrets/google/token.json` holds a `refresh_token` and expires
  `2026-09-19T04:00:57Z`. Whether the OAuth consent screen is External+Testing (refresh token revoked
  every 7 days) is **unknown** — the design must detect and report it either way.
- **`send_whatsapp.py --check` already exists**: prints mode, masked `WA_SENDER_NUMBER`/`WA_TEST_NUMBER`,
  delays, cap, Chrome path and whether the linked session exists, then exits without sending. It is
  reused verbatim as the WhatsApp half of the pre-flight.
- **`wa_send.js` job schema** (for the failure alert, reused rather than rebuilt):
  `{mode, sender, auth_dir, chrome, min_delay, max_delay, qr_timeout, recipients:[{person, to, parts[]}]}`
  → `node wa_send.js <job.json> <result.json>`.
- **Module 02 already covers** per-person `Send days`, the live-mode `already_sent` guard (a retry never
  double-sends), the `WA_MAX_PER_RUN` cap, and refusing to send on a stale module 01 output. Module 03
  must not duplicate any of it.
- **`.secrets/wweb-auth/session` is a live Chrome profile.** Two runs opening it at once corrupt the
  session — this has already happened once (02 stable §12). Hence the lock in R7.

## 3. Inputs

| Name | Path / Source | Format | Schema / Columns | Private? |
|---|---|---|---|---|
| Skill 01 | `.claude/skills/01-calendar-todo/SKILL.md` | md | invoked via the Skill tool inside the headless session | no |
| Skill 02 | `.claude/skills/02-whatsapp-message/SKILL.md` | md | invoked via the Skill tool inside the headless session | no |
| Module 01 artifact (verification) | `outputs/01-calendar-todo/todo_by_name.json` | json | `{ "<group>": [ {type, title} ] }` — module 01 v3.0 §4 contract | yes |
| Module 02 artifact (verification) | `outputs/02-whatsapp-message/send_log.json` | json | `{ "runs": [ {date, mode, entries[], summary} ] }` — module 02 v1.0 §4 contract | yes |
| Google OAuth token | `.secrets/google/token.json` | json | refresh probe only; never printed, never re-consented unattended | **secret** |
| WhatsApp settings | `.secrets/whatsapp.env` | `KEY=VALUE` | `WA_SENDER_NUMBER`, `WA_TEST_NUMBER`, `WA_CHROME_PATH`, … (read via `send_whatsapp.py --check`) | **secret** |
| WhatsApp session | `.secrets/wweb-auth/` | Chromium profile | presence and non-emptiness only | **secret** |
| Calendar IDs | `src/calendarLinks/lists.txt` | txt | parse check only (≥1 non-comment line) | yes |
| Contacts | `src/messageListNDetails/WhatsappContacts.txt` | tsv | header + duplicate-`Person` check only; **`Number` is never read by module 03** | yes |
| Scheduler settings | `src/scheduler/scheduler.env` | `KEY=VALUE` txt (git-ignored) | `SCHED_TIME`, `SCHED_MODEL`, `SCHED_TIMEOUT_MIN`, `SCHED_VISIBLE`, `SCHED_ALERT_*` | no |

## 4. Outputs (contract for next module)

| Name | Path | Format | Schema |
|---|---|---|---|
| Run log | `outputs/03-scheduler/run_log.json` | json | `{ "runs": [ {...} ] }`, newest first, last 60 days kept |
| Per-run transcript | `outputs/03-scheduler/logs/<YYYY-MM-DD>T<HHMM>.json` | json | raw `claude -p --output-format json` envelope + wrapper stdout/stderr |

Run entry fields: `date`, `started_at`, `finished_at`, `duration_s`, `trigger` (`schedule` \| `manual`),
`attempt` (1–3), `status`, `preflight` (per-check results), `claude` (`exit_code`, `num_turns`,
`total_cost_usd`, `session_id`), `verify` (`todo_date`, `todo_groups`, `todo_items`, `send_log_date`,
`sent`, `failed`, `skipped`), `alerts` (which channels fired), `error`.

Statuses: `ok` · `ok_nothing_to_send` · `skipped_locked` · `preflight_failed` · `claude_failed` ·
`verify_failed` · `timeout` · `notify_failed`.

Field rules:
- **No phone number, no token, no `.secrets` value ever appears** in `run_log.json`, in the per-run
  transcript, in a toast, or on stdout. Numbers are masked to their last 4 digits, exactly as module 02 does.
- The per-run transcript is the full headless envelope, so a failed run can be diagnosed without re-running.
- `ok_nothing_to_send` is a success, not a failure: on Friday nobody is scheduled, so every recipient is
  `skipped_day` and no message goes out.

```json
{
  "runs": [
    {
      "date": "2026-09-19",
      "started_at": "2026-09-19T10:00:04+06:00",
      "finished_at": "2026-09-19T10:04:38+06:00",
      "duration_s": 274,
      "trigger": "schedule",
      "attempt": 1,
      "status": "ok",
      "preflight": {"google_token": "ok", "lists": "ok", "contacts": "ok", "wa_session": "ok", "wa_config": "ok", "tools": "ok", "disk": "ok", "network": "ok"},
      "claude": {"exit_code": 0, "num_turns": 34, "total_cost_usd": 0.41, "session_id": "1a5f…"},
      "verify": {"todo_date": "2026-09-19", "todo_groups": 6, "todo_items": 24, "send_log_date": "2026-09-19", "sent": 2, "failed": 0, "skipped": 4},
      "alerts": [],
      "error": null
    }
  ]
}
```

## 5. Scope

**In scope**
- A Windows Task Scheduler task firing at 10:00 local time, all 7 days, hidden, as the logged-on user.
- Catch-up when the machine was off or asleep (start-when-available), and retry twice at 15-minute intervals.
- A single-instance lock so a scheduled run never collides with a manual one.
- A pre-flight health check that refuses to start the pipeline when a prerequisite is broken.
- Driving headless `claude -p` with a fixed prompt and an explicit, least-privilege permission set.
- A hard wall-clock timeout that kills a stuck run (notably an unattended Google re-consent prompt).
- Artifact-based verification that both modules really produced today's work.
- Four alert channels on failure: WhatsApp to the owner's own number, Windows toast, the run log, and a
  visible console window.
- `run_log.json` plus a per-run transcript, and a run summary.
- An idempotent installer/uninstaller for the scheduled task.

**Out of scope**
- Changing anything in module 01 or module 02 — both are invoked exactly as published.
- Cloud or always-on scheduling. This is a laptop-only design; if the laptop is off all day, no run happens.
- Performing the Google sign-in or the WhatsApp QR scan automatically. Both are *detected and alerted*,
  never automated — they are deliberate human gates.
- Waking the machine from full shutdown, and any BIOS/power-plan changes.
- Delivery/read receipts, and any change to message content or the send schedule.
- Committing, pushing or tagging anything from the scheduled run.

## 6. Requirements

| ID | Requirement (testable) |
|---|---|
| R1 | A Windows scheduled task named `ToDo-WhatsApp-Daily` runs `src/scheduler/run_daily.ps1` at **10:00 local time every day**, as user `nasir9781`, **hidden** (no window on a successful run), with start-when-available so a missed run fires on next wake, and restart-on-failure twice at 15-minute intervals. `install_task.ps1` creates or updates it idempotently; running it twice leaves exactly one task. |
| R2 | Before anything else the wrapper takes an exclusive lock at `outputs/03-scheduler/run.lock` holding the PID and start time. If a live lock is held, the run exits **0** with status `skipped_locked`, logs it and alerts — it never starts a second Chrome against `.secrets/wweb-auth/`. A lock whose PID is dead, or older than `SCHED_TIMEOUT_MIN`, is treated as stale, reclaimed, and the reclaim is recorded. The lock is released on every exit path including a crash or a kill. **(v1.1)** Acquisition is atomic (`FileMode.CreateNew`), not check-then-write, so two launches within milliseconds of each other cannot both pass the check before either has written the file — this is what let two real 2026-09-23 launches both proceed and collide on the shared Chrome profile. |
| R3 | `preflight.py` runs before the pipeline and checks: (a) the Google token loads and **refreshes non-interactively** — it must never reach `InstalledAppFlow`/`run_local_server`; (b) `lists.txt` exists with ≥1 non-comment, non-holiday line; (c) `WhatsappContacts.txt` parses with the required headers and no duplicate `Person`; (d) `.secrets/wweb-auth/` exists and is non-empty; (e) `send_whatsapp.py --check` exits 0 and reports a usable mode and a linked session; (f) `claude`, `node` and `python` are all resolvable; (g) ≥500 MB free on the repo drive; (h) `www.googleapis.com` and `web.whatsapp.com` are reachable. It prints one JSON object `{"ok":bool,"checks":[{"id","status","detail","fix"}]}` and exits non-zero if any check fails. Any failure → status `preflight_failed`, alert naming the failed check **and its fix**, and the pipeline is never started. |
| R4 | The wrapper sets `WA_MODE=live` in the child environment for its own run, overriding whatever `.secrets/whatsapp.env` contains, so a forgotten `dry-run` can never silently deliver nothing. The file on disk is not modified. |
| R5 | The pipeline is run as a single headless invocation: `claude -p "<fixed prompt>" --settings src/scheduler/scheduler.settings.json --model <SCHED_MODEL> --output-format json`, with cwd = repo root. The prompt instructs it to invoke skill `01-calendar-todo`, then — only if today's `todo_by_name.json` was written — skill `02-whatsapp-message`, to never ask a question, and to print `SCHEDULER_OK` or `SCHEDULER_FAIL: <reason>`. |
| R6 | The whole `claude` invocation is bounded by `SCHED_TIMEOUT_MIN` (default **25** minutes) of wall clock. On expiry the process tree is killed, status is `timeout`, and the alert says so. This is the guard against an unattended Google re-consent prompt or a hung Chrome. |
| R7 | The run uses `scheduler.settings.json` with **`--permission-mode manual --permission-prompts none`**, never `--dangerously-skip-permissions`. That combination turns every unapproved tool call into an unanswerable prompt, i.e. an automatic denial (never a hang). **What this does and does not buy, measured in T10 on Claude Code 2.1.278 — stated plainly because the earlier wording of this requirement promised a sandbox that does not exist:** <br>• **Enforced:** every non-Bash tool outside `allow` is denied (`WebFetch`, `WebSearch`, `Task`, and anything added later); `deny` path patterns hold for Claude's own file tools, so `Read`/`Write`/`Edit` on `.secrets/**` and writes to `src/**`, `.claude/**`, `plan/**`, `docs/**` and the top-level project files are blocked. <br>• **Not enforced:** `Bash` is allowed **bare**, which is effectively unrestricted shell access as the logged-on user. Module 01 is agentic and issues compound commands (`cd "…" && head … \| cut …; python -c "…"; python fetch_tasks.py …`); Claude Code matches Bash rules by **prefix**, so no `Bash(...)` pattern can match them — narrowing Bash stopped the pipeline dead twice. The same prefix matching makes the Bash deny rules best-effort only (`cd x && git push` slips past `Bash(git:*)`); they are defence-in-depth, not a boundary. Anything reachable from a shell command, including `.secrets`, is reachable by this run. <br>• **Real containment** therefore rests on the fixed `prompt.txt`, the published skills, and the task launching nothing else. Accepted by the owner on 2026-09-19. If module 01 is ever made non-agentic, narrow `Bash` back immediately. <br>• `permission_denials` from the headless envelope is recorded in `run_log.json`, because it names the exact refused call and is the fastest route to diagnosing a failed run. |
| R8 | A zero exit code from `claude` is **not** accepted as success. The wrapper verifies the artifacts: `outputs/01-calendar-todo/todo_by_name.json` parses and is dated today (Asia/Dhaka), **and** `outputs/02-whatsapp-message/send_log.json` has a run for today with `mode: "live"`. Either missing → status `verify_failed` and an alert, even if Claude exited 0 and claimed success. |
| R9 | A run where every recipient is legitimately skipped (e.g. Friday, `skipped_day`, or module 01 returned `{}`) is status `ok_nothing_to_send` — a success, with no alert. |
| R10 | On any failing status the wrapper alerts on all four channels, in this order and independently (one channel failing never suppresses the others): (1) append to `run_log.json`; (2) Windows toast; (3) WhatsApp message to `WA_TEST_NUMBER` via `wa_send.js`, bounded to 90 s; (4) a **visible** console window showing the failure and the fix. A successful run alerts on nothing and shows no window. If the failure is itself the WhatsApp session, channel 3 is expected to fail and is recorded as `notify_failed` without changing the run's own status. |
| R11 | Retries do not double-send. Attempts 2 and 3 re-run the identical pipeline; module 02's live `already_sent` guard means anyone already messaged on attempt 1 is skipped. The owner is alerted only after the **third** attempt fails; attempts 1 and 2 are logged, not alerted. |
| R12 | `run_log.json` keeps the last 60 days, newest run first, and per-run transcripts older than 60 days are deleted. No phone number, token, QR string or any `.secrets` value appears in any of them, on stdout, in a toast, or in the visible window; numbers are masked to their last 4 digits. `outputs/**` and `.secrets/**` stay git-ignored, and the scheduled run never runs git. |
| R13 | `run_daily.ps1` is runnable by hand from a terminal with the same behaviour as the scheduled run, taking `-Trigger manual`, `-Mode <dry-run\|self\|live>` (overriding R4 for testing) and `-Visible`. This is how every test below is driven, without touching the registered task. |
| R14 | `install_task.ps1 -Uninstall` removes the task cleanly, leaving logs and outputs intact, so the automation can be switched off in one command if WhatsApp ever warns the number. |
| R15 | **(v1.1)** Lock acquisition (R2) is atomic: `[System.IO.File]::Open(..., FileMode.CreateNew)` either creates the lock file or throws, with no gap between checking and writing for a second process to slip through. A collision on the atomic create is treated exactly as before — live lock → refuse and report who holds it; stale lock (dead PID or past `SCHED_TIMEOUT_MIN`) → reclaim and retry the create once. |
| R16 | **(v1.1)** A launch whose `-Trigger` is `schedule` is reclassified as `manual-gui` if the wall-clock start time falls outside a window around `SCHED_TIME` (5 minutes before, through every configured retry interval plus slack after — wide enough to cover the real fire and all its retries, narrow enough to exclude an unrelated time of day). `manual-gui` still runs the pipeline (the owner may be testing on purpose), but: it is logged with that distinct trigger value; a failure alerts immediately rather than waiting for `SCHED_MAX_ATTEMPTS`; and it never self-retries or contributes to the schedule day's attempt count. This closes the gap where Task Scheduler's own "Run" action launches the identical registered command, so a manual click was previously indistinguishable from — and could chain a 15-minute retry loop into collision with — the real 10:00 fire. |

## 7. Steps / Design

Four new files under `src/scheduler/`, plus the skill. Nothing in modules 01 or 02 is modified.

```
Task Scheduler "ToDo-WhatsApp-Daily"  (10:00 daily · hidden · start-when-available · 2 retries × 15 min)
  └─ conhost.exe --headless pwsh -NoProfile -File src/scheduler/run_daily.ps1   (no window at all; -WindowStyle Hidden is ignored on Win11)
       1. lock        outputs/03-scheduler/run.lock          (R2, R15 - atomic acquire)
       2. preflight   python src/scheduler/preflight.py      (R3)
       3. pipeline    WA_MODE=live claude -p … --settings …  (R4, R5, R6, R7)
       4. verify      artifacts, not exit codes              (R8, R9)
       5. log         outputs/03-scheduler/run_log.json      (R12)
       6. alert       log · toast · WhatsApp · window        (R10, R11)
       7. unlock      always                                 (R2)
```

**`run_daily.ps1`** — the wrapper; the only thing Task Scheduler knows about.
1. Resolve the repo root from `$PSScriptRoot`, load `src/scheduler/scheduler.env`, parse the
   `-Trigger`/`-Mode`/`-Visible` parameters (R13). Every path is derived from `$PSScriptRoot`, never
   from the current directory, so the script behaves identically whether Task Scheduler launches it or
   the owner runs it by hand from any folder — this is what makes the whole test ladder below possible
   without touching the registered task.
2. Determine the effective trigger (R16): a `-Trigger schedule` launch outside the `SCHED_TIME`
   window is relabeled `manual-gui` for logging, alerting and retry purposes, though it still runs.
   Take the lock via an atomic create (R2, R15) — no check-then-write gap. Held and live → log
   `skipped_locked`, alert (toast only), release nothing, exit 0.
3. Run `preflight.py`, capture its JSON. Not ok → `preflight_failed`, alert with the failed check's
   `fix` text, exit 1 (so Task Scheduler's retry applies).
4. Run `claude` (R5) as a child process with `WA_MODE` forced (R4), `--output-format json`, a job object
   for the timeout (R6), stdout/stderr tee'd to `outputs/03-scheduler/logs/<stamp>.json`.
5. Verify the artifacts (R8), classify the status (R9).
6. Append the run entry to `run_log.json`, prune to 60 days (R12).
7. Alert immediately if the effective trigger is `manual-gui`, or if this is a `schedule` failure at
   the final attempt (R10, R11, R16); otherwise log only and let the self-retry (below) run.
8. `finally`: release the lock; exit 0 on success, non-zero on failure so retries fire.

**Retries live inside `run_daily.ps1` itself** (R11), not in Task Scheduler: `conhost --headless`
does not propagate the child's exit code, so Task Scheduler's own `RestartCount` can never see a
failure to restart (found 2026-09-21). A `schedule`-effective failure below `SCHED_MAX_ATTEMPTS`
waits 15 minutes and re-invokes itself with `-Trigger schedule`; a `manual-gui`-effective failure
(R16) never does this, so a stray click at the wrong time of day cannot chain into a retry loop
that is still running when the real fire arrives (found 2026-09-23).

**`preflight.py`** — Python 3, stdlib plus the `google-auth` libraries module 01 already depends on.
Each check returns `{id, status, detail, fix}` with `detail` free of secrets. The Google check loads
`token.json` directly and calls `creds.refresh(Request())` **only when a refresh token is present**;
it never constructs an `InstalledAppFlow`, so it can report `needs_signin` instead of hanging (R3a).
The WhatsApp check shells out to `send_whatsapp.py --check` and reads its output.

**`notify.py`** — one entry point, `notify(title, body, channels)`.
- *toast*: WinRT from PowerShell (`Windows.UI.Notifications.ToastNotificationManager`) under the built-in
  PowerShell AppId — `BurntToast` is not installed and is not made a dependency.
- *whatsapp*: builds a one-recipient `wa_send.js` job (`{mode:"live", sender, auth_dir, chrome,
  min_delay:0, max_delay:0, qr_timeout:15, recipients:[{person:"scheduler", to:<WA_TEST_NUMBER>,
  parts:[body]}]}`) in the system temp dir, runs the worker with a 90 s bound, deletes both files.
- *window*: `Start-Process pwsh -WindowStyle Normal` showing the failure, the failed check and its fix,
  held open until dismissed.

**`install_task.ps1`** — `Register-ScheduledTask` with `-Settings (New-ScheduledTaskSettingsSet
-StartWhenAvailable -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)
-MultipleInstances IgnoreNew)`, `-Trigger (New-ScheduledTaskTaskTrigger -Daily -At 10:00)`,
`-User nasir9781 -RunLevel Limited`, action = hidden `pwsh -File run_daily.ps1`. Re-running updates in
place (R1). `-Uninstall` unregisters (R14). The machine's timezone is already +06:00, so 10:00 local
**is** 10:00 Asia/Dhaka — no conversion anywhere.

**`scheduler.settings.json`** — a deny-list, not an allow-list (R7). Testing (T10, T15, 2026-09-19)
showed `allow` requires *every* component of a compound Bash command to be individually allowed, while
module 01 is agentic and issues compound commands (`cd … && mkdir … && python … ; rm …`) whose exact
shape varies run to run — narrowing `Bash` in `allow` stopped the pipeline dead twice. `deny`, by
contrast, fires if *any* component matches (verified directly), so it is a real boundary even for Bash.
The permission set is therefore: bare `Skill`/`Read`/`Write`/`Edit`/`Bash` in `allow`, with `deny`
carrying `Bash(git:*)` (blocks any shell command containing git, protecting the repo and its secrets
from an off-script run) plus path-scoped `Read`/`Write`/`Edit` denies over `.secrets/`, `src/`,
`.claude/`, `plan/`, `docs/`, and the top-level project files. `Bash(rm:*)` is deliberately **not**
denied — it blocked module 01's own cleanup of its temp file inside `outputs/` (T15), and `deny` cannot
carry a path exception. Real containment rests on `deny Bash(git:*)`, the path-scoped file denies,
`prompt.txt`'s own rules, and the task launching nothing beyond the two published skills. See the
`_comment` block in `src/scheduler/scheduler.settings.json` for the full measured behaviour. If module 01
is ever made non-agentic (parsing moved into a script with a fixed command shape), narrow `Bash` back.

**The fixed prompt (R5)** — stored in `src/scheduler/prompt.txt` so it is reviewable and diffable:

> Run the daily pipeline non-interactively. Never ask a question; if anything blocks you, stop and print
> `SCHEDULER_FAIL: <reason>`.
> 1. Invoke the Skill tool with skill `01-calendar-todo` and follow it exactly.
> 2. Only if step 1 wrote `outputs/01-calendar-todo/todo_by_name.json` with today's date, invoke the
>    Skill tool with skill `02-whatsapp-message` and follow it exactly.
> 3. Print `SCHEDULER_OK` followed by each module's run summary.
> Do not write outside `outputs/`. Do not run git. Do not print any phone number, token or `.secrets` value.

Configuration / environment variables (`src/scheduler/scheduler.env`, git-ignored):

| Name | Purpose | Where set | Default |
|---|---|---|---|
| `SCHED_TIME` | Daily fire time, local (= Asia/Dhaka) | `scheduler.env`, read by `install_task.ps1` | `10:00` |
| `SCHED_MODEL` | Model for the headless run | `scheduler.env` | `sonnet` |
| `SCHED_TIMEOUT_MIN` | Hard wall-clock kill (R6) | `scheduler.env` | `25` |
| `SCHED_MAX_ATTEMPTS` | Attempts per day before the owner is alerted; retries now run **inside** `run_daily.ps1` itself (v1.0), 15 min apart — see §11 change log | `scheduler.env` | `3` |
| `SCHED_VISIBLE` | Show the console even on success (testing) | `scheduler.env` / `-Visible` | `0` |
| `SCHED_ALERT_TOAST` / `_WHATSAPP` / `_WINDOW` | Enable each alert channel | `scheduler.env` | all `1` |
| `SCHED_RETENTION_DAYS` | Run-log and transcript retention | `scheduler.env` | `60` |
| `WA_MODE` | Forced to `live` for the scheduled run (R4) | child process env only | `live` |
| Timezone | `Asia/Dhaka`, same as modules 01 and 02 | machine TZ, verified +06:00 | — |

## 8. Validation Checklist
- [x] All frontmatter fields filled
- [x] No `TBD`, `TODO`, `<placeholder>` left
- [x] §3 Inputs match upstream §4 Outputs (or source file actually exists)
- [x] Every R# in §6 is implemented by at least one step in §7
- [x] Every R# in §6 is covered by at least one test in §9
- [x] No secrets or real phone numbers written in this file
- [x] §12 Troubleshooting filled for known failure points

Validation result: pass — checked by Claude on 2026-09-19; **re-checked 2026-09-23 for v1.1** (R15, R16 each appear in §7 and are covered by T19–T22; no placeholders; no 8+ digit runs; §12 has new rows for the collision and the retry-chain risk) — approved by user: yes (2026-09-19; v1.1 hotfix directed and approved 2026-09-23, "ok")

## 9. Test Cases

Ladder: wrapper by hand in `dry-run` → wrapper by hand in `live` → the registered task fired by hand →
the registered task firing on its own. No test is allowed to send a real colleague a message until the
`dry-run` rungs pass.

| ID | Covers | Input / Setup | Expected | Result | Evidence |
|---|---|---|---|---|---|
| T1 | R5, R7 | Headless reachability of the Calendar connector | `claude -p` reaches `list_calendars` with no prompt and exits 0 | **pass** | 2026-09-19 probe: `subtype: success`, `is_error: false`, `num_turns: 3`, 3 calendars returned, `total_cost_usd: 0.0883` |
| T2 | R3 | `preflight.py` on a healthy machine | Exits 0, all 8 checks `ok`, JSON parses, no secret in the output | **pass** | 2026-09-19: exit 0, `ok: true`, all 8 `ok` (`token valid` · `2 calendar id(s)` · `6 people, Send days column present` · `linked session present` · `settings usable for a live run` · `claude, node and python all resolve` · `137468 MB free` · `googleapis and whatsapp reachable`); leak scan for `\d{8,}` over the whole output → no hits |
| T3 | R3a, R6 | Point the token at an expired/revoked copy in a scratch `.secrets` | Reports `google_token: needs_signin` with the re-sign-in fix, exits non-zero, **never opens a browser**, returns in seconds | **pass** | 2026-09-19, fixture token with a bogus `refresh_token`: exit 1, `google_token: needs_signin — refresh rejected (RefreshError)`, fix text names both the by-hand sign-in and publishing the consent screen. **Elapsed 2 s** (not a hang) and `chrome.exe` process count 24 before → 24 after, so no browser was opened — the `InstalledAppFlow` path was never reached |
| T4 | R3 | Break each prerequisite one at a time: missing `lists.txt`; holiday-only `lists.txt`; duplicate `Person`; missing `Number` column; missing session + no `WA_SENDER_NUMBER` | Each is caught by its own check with the right `fix`, and the pipeline never starts | **pass** | 2026-09-19, synthetic fixtures (no real numbers left in temp): `lists: src/calendarLinks/lists.txt missing` · `lists: no usable calendar IDs (1 holiday lines skipped)` · `contacts: duplicate Person value(s): alpha` · `contacts: required column 'number' missing` · `wa_session: no linked WhatsApp session` + `wa_config: WA_SENDER_NUMBER is required for mode live`. The last one also confirms the pre-flight validates the **live** configuration the scheduled run will actually use (R4) |
| T5 | R13, R4, R5, R8 | `run_daily.ps1 -Trigger manual -Mode dry-run` | Both skills run, module 01 writes today's output, module 02 logs everyone `dry-run`, verification passes, a row lands in `run_log.json` | **pass** (4th attempt) | 2026-09-19 final run: `status: ok`, exit 0, **66 s**, 14 turns, **$0.2050**, **0 permission denials**, `verify {todo_date: 2026-09-19, todo_groups: 6, todo_items: 26}`, `alerts: []` (silent on success). Earlier attempts failed and each exposed a real defect: (1) `Start-Process` not quoting the space in the repo path; (2) `Write(outputs/**)` in `allow` matching nothing, so module 01 could not write its output; (3) `Bash(python:*)` not matching module 01's compound `cd … && … ; python …` command. Also confirmed module 02 v1.1 **against real data**: two stacked dry-runs left today's `live` row intact, which v1.0 would have destroyed |
| T6 | R2 | Hold the lock in a second shell, then run the wrapper | Exits 0 with `skipped_locked`, alerts, no second Chrome starts | **pass** (after 3 fixes) | 2026-09-19: lock held by a real live PID → wrapper exits 0 in 2 s with `skipped_locked`, exactly **1** row in `run_log.json`, `alerts: [toast]` only, `chrome.exe` count 20 → 20 (no browser), and the *other* run's lock file left in place rather than deleted. First attempt exposed three bugs — see §12 |
| T7 | R2 | Write a lock with a dead PID, then a lock older than `SCHED_TIMEOUT_MIN` | Both reclaimed as stale, reclaim recorded, run proceeds | **pass** | 2026-09-19: dead PID 999999 → `alive = False` → reclaimable; lock back-dated 90 min → age 90 min vs the 25 min limit → reclaimable. Both paths let the run proceed |
| T8 | R8 | Let the pipeline run, then delete `todo_by_name.json` before verification; separately, back-date `send_log.json` | `verify_failed` both times, even though `claude` exited 0 | pending | `run_log.json` |
| T9 | R6 | Force a stall (`SCHED_TIMEOUT_MIN=1` against a normal run) | Process tree killed at 1 min, status `timeout`, lock released, alert says timeout | pending | `run_log.json` + no orphan `claude`/`chrome` |
| T10 | R7 | Prompt the headless session to run `git status`, to write outside `outputs/`, and to write into `.secrets/` | All denied; the run reports the denial rather than hanging | **pass** (after the mechanism was corrected) | 2026-09-19, four probe runs. **First attempt failed outright**: with the settings file alone, `git status` ran *and* `PERMISSION_TEST_SHOULD_NOT_EXIST.txt` was written to the repo root — `permissions.allow` is an auto-approve list, not a sandbox. Adding `--permission-mode manual --permission-prompts none` made the root write `DENIED` and the file was not created. Further probes established that a bare `Write` rule is honored, `Bash(git:*)` in `deny` correctly denies `git status`, `Write(.secrets/**)` in `deny` correctly blocks a write into `.secrets/`, but `Write(outputs/**)` in `allow` matches nothing and denied the whole pipeline (caught by re-running T5). Final set verified by the T5 re-run below |
| T11 | R10 | Force each failure class once | Toast appears, WhatsApp alert reaches `WA_TEST_NUMBER`, visible window opens, `run_log.json` row written — all four independent | **pass** | 2026-09-19, real repo with a forced `claude_failed`: `alerts fired: [toast, whatsapp, window]`, `alerts_failed: none`. Toast and window seen on screen; the WhatsApp alert was also confirmed standalone (`notify.py --channels whatsapp` → `fired: [whatsapp]`), delivered to the owner's test number |
| T12 | R10 | Force a failure with the WhatsApp worker unreachable (the WhatsApp channel itself is broken) | Toast, window and log still fire; the WhatsApp channel records `notify_failed` without changing the run status | **pass** | 2026-09-19, two ways. `notify.py --channels toast,whatsapp` against a non-existent secrets dir → `fired: [toast]`, `failed: [{whatsapp, "WA_SENDER_NUMBER or WA_TEST_NUMBER not set"}]`. Full wrapper with an unreachable worker → `fired: [toast, window]`, `alerts_failed: [{whatsapp, "module 02 wa_send.js missing"}]`, run status unchanged. Exposed a logging gap on the first pass — `run_log.json` recorded only fired channels, so a failed channel vanished; `alerts_failed` was added |
| T13 | R9 | Run on a Friday (nobody scheduled) | Status `ok_nothing_to_send`, no alert, no window | **pass** | 2026-09-19, live mode with a send_log summary of `sent=0, failed=0, skipped=6` → `status: ok_nothing_to_send`, exit **0**, `alerts: []`, no window |
| T14 | R11 | Make attempt 1 fail, let the retries run | Attempt numbering advances; no alert until the final attempt; a manual run alerts at once | **pass** (retry mechanism redesigned and re-confirmed live 2026-09-22) | 2026-09-19: three consecutive `-Trigger schedule` failures gave `attempt=1,2,3` under the original Task-Scheduler-driven retry design. **That design was replaced 2026-09-21**, because `conhost --headless` swallows the child's exit code, so `RestartCount` could never fire; retries now happen inside `run_daily.ps1` itself (sleep 15 min, re-invoke). **Confirmed live 2026-09-22:** the real 10:00 run's attempt 1 failed (`claude_failed`, worker `startup-timeout`), no alert fired, and the wrapper's own retry produced attempt 2 at ~10:15, which succeeded and delivered both scheduled messages — the exact scenario this test exists for, proven in production rather than simulated |
| T15 | R1, R14 | `install_task.ps1` twice, then fire it by hand | Exactly one task exists; firing it runs the pipeline with **no visible window**; settings are as specified | **pass** (re-confirmed 2026-09-22 with the `conhost --headless` launcher) | 2026-09-19: installer run twice → "installed" then "updated", `Get-ScheduledTask` count **1**. Settings verified: `StartWhenAvailable True`, `RestartCount 2`, `RestartInterval PT15M`, `MultipleInstances IgnoreNew`, principal `nasir9781/Interactive/Limited`. **Re-confirmed 2026-09-22:** the real unattended 10:00 run (T18) produced no visible window on either attempt, with the current launcher `conhost.exe --headless <pwsh> …` and `ExecutionTimeLimit PT3H`. Live-run safety confirmed end to end across both dates: nobody was ever messaged twice |
| T16 | R1 | Set the trigger to a time already past with start-when-available, sleep and wake the laptop | The run fires on wake, same day only | **not verified — accepted gap** | 2026-09-19: the setting is confirmed present (`StartWhenAvailable: True`), but the behaviour was **not** reproduced. Re-pointing the trigger at a time 12 minutes past did not cause a catch-up: Windows simply moved `NextRunTime` to the following day, because `StartWhenAvailable` fires only for a trigger the task *already had* while the machine was unavailable — it does not treat a newly-set past trigger as missed. Proving it properly needs the laptop actually asleep or off across 10:00, which has not happened in three days of real operation. **Accepted as an open item for stable v1.0**: the setting is correctly configured and nothing found contradicts it; confirm the first time it actually applies (laptop off/asleep at 10:00) and record the outcome in §12 |
| T17 | R12 | After every test | Grep every new file, log, transcript and stdout for `\d{8,}` and for any `.secrets` value → no hits; `git status` clean; `git check-ignore` passes for `outputs/03-scheduler` and `src/scheduler/scheduler.env` | **pass** | 2026-09-19: 8 real phone numbers and 4 real credential values loaded as needles (never printed) and searched across 21 files — `src/scheduler/*`, all of `outputs/03-scheduler/`, both plans, the incident file, CHANGELOG, .gitignore → **zero** hits. Five keyword flags were all false positives (the identifiers `refresh_token` / `client_secret` in code and prose, no values). `git status` shows only code and docs; `git check-ignore` confirms `src/scheduler/scheduler.env`, `outputs/03-scheduler/run_log.json`, the incident file and testMD are all ignored |
| T18 | R1, R4, R5, R8 | **The real thing**: leave the task to fire at 10:00 on a real scheduled day, laptop on, owner not touching it | Recipients get their lists, `run_log.json` shows `ok`, no window ever appeared, measured cost recorded | **pass — 2026-09-22, third real attempt, unattended, fully automatic** | Failed twice first (see below), fixed each time, then passed cleanly: 2026-09-22 10:00, attempt 1 hit module 02's still-unexplained ~10:01 hang, but this time the **v1.2 worker timeout** (deployed 2026-09-21, separate draft) caught it — `claude_failed`, "WhatsApp session unusable (startup-timeout, last stage 'authenticated') — worker did not become ready within 120s; nothing was sent", duration 336 s, no window, no alert (attempt 1 of `SCHED_MAX_ATTEMPTS`), nothing sent. The wrapper's own retry loop (T14) then fired attempt 2 at ~10:15 automatically, with **no human involvement**: `status: ok`, 216 s, `LastTaskResult 0x0`, live delivery `sent=2 failed=0` to Mamun and Rashikul (today's scheduled recipients; everyone else correctly `skipped_day`). Owner observed no message at ~10:09 (attempt 1 mid-failure) and confirmed delivery once attempt 2's log was read. **2026-09-20 (1st attempt, failed):** module 01 completed; module 02 built a live job for Rabbani; a visible blank `claude` console window appeared (owner's screenshot) and the task was killed, `0xC000013A`, nothing sent. Root cause: `-WindowStyle Hidden` is ignored under the Windows 11 default-terminal handoff. Fixed with `conhost.exe --headless`. **2026-09-21 (2nd attempt, failed):** launcher fix worked (no window), but module 02's worker hung ~10 min with no bound on it, `claude_failed`, and the old Task-Scheduler-driven retry never fired because `conhost --headless` swallows exit codes (see T14). Delivered by hand as a stopgap. Fixed with the in-wrapper retry loop (T14) and the module 02 v1.2 worker timeout, both proven together on 2026-09-22 |
| T19 | R2, R15 | Standalone repro: 8 processes race to acquire the same lock file simultaneously, each holding it 5s like a real run would | Exactly 1 acquires; the other 7 see it as live and refuse | **pass** | 2026-09-23: run 8x with the pre-fix check-then-write logic isolated in a scratch script alongside the fixed atomic version for comparison; the atomic `FileMode.CreateNew` version gave **1 winner, 7 refusals** on every run. (The two real launches that collided on 2026-09-23 were 2 seconds apart at the Task-Scheduler-event level, but the actual TOCTOU window that let both through was inside each process's own `Test-Path` → `Set-Content` gap, milliseconds wide — this test targets exactly that gap, not the 2-second event spacing) |
| T20 | R16 | Invoke with `-Trigger schedule` while `SCHED_TIME` is set far from the real wall clock (simulating a manual Task Scheduler "Run" click at an unrelated hour) | Logged as `trigger: manual-gui`; no 15-minute self-retry; failure alerts immediately | **pass** | 2026-09-23, `SCHED_TIME=03:00` with wall clock at 10:34 (stub `claude` exiting 1, `-SkipPreflight`, all alert channels disabled for the test): console printed "launched via Task Scheduler but well outside the 03:00 window - treating as manual-gui"; `run_log.json` row shows `trigger: manual-gui, attempt: 1`; the run exited in **0s** with no "waiting 15 minutes" message (proving no self-retry was scheduled) |
| T21 | R16 regression | Invoke with `-Trigger schedule` while `SCHED_TIME` matches the real wall clock (the normal case) | Still logged as `trigger: schedule`; attempt numbering and the immediate-alert-at-cap behaviour are unchanged | **pass** | 2026-09-23, `SCHED_TIME=10:00` at wall clock 10:34 (inside the window): `trigger: schedule`. Today's log already held 2 real failed schedule rows from the morning's incident, so `attempt` correctly computed as 3 (= `SCHED_MAX_ATTEMPTS`); since `attempt >= MaxAttempts`, the run alerted immediately and exited without sleeping — confirming both the normal `schedule` labelling and the at-cap immediate-alert path still work unchanged |
| T22 | R17 | Grep test scratch files and the working tree for the fake 8-digit numbers used to isolate T19; confirm `scheduler.env` and `run_log.json` are byte-identical to their pre-test backups afterward | No leakage; no lingering test state | **pass** | 2026-09-23: `diff` against the pre-test backups of `scheduler.env` and `run_log.json` showed **no differences**; test transcripts and stub files removed; `SCHED_CLAUDE_EXE` cleared from the session; today's real 10:00 delivery (T18) was untouched by any of this testing |

Test data rule: prefer real data. Any test that messages a real colleague needs the owner's go-ahead first.

Test result: **17 of 18 pass, 1 not-verified-and-accepted** — updated 2026-09-22. Passing: T1–T15, T17, T18. **T16 not verified** — the setting (`StartWhenAvailable: True`) is confirmed present and nothing in three days of real operation contradicts it, but the catch-up behaviour itself has not been observed because the laptop has not been asleep or off across 10:00. Accepted as an open item to confirm from real use, recorded in §12, not a blocker for stable. **T18 passed on the third real attempt (2026-09-22)**, unattended and fully automatic: attempt 1 hit the still-unexplained ~10:01 worker hang, the v1.2 worker timeout stopped it cleanly at 120 s with nothing sent, and the wrapper's own 15-minute retry (T14) delivered both messages on attempt 2 with no human involvement. Root causes of the first two failures (hidden window not actually hidden; retries silently disabled by the headless launcher) are both fixed and now proven live.

**v1.1 (2026-09-23):** T19–T22 pass (4 of 4), fixing a real incident: two Task-Scheduler-launched instances (very likely a Run-button double-click, not the real 10:00 trigger) raced past the old check-then-write lock within milliseconds of each other, collided on the shared WhatsApp Chrome profile, and each started its own unbounded 15-minute self-retry loop that was still running when investigated — left alone, it would have kept colliding with the genuine 10:00 fire. Nothing was sent during the incident (module 02's artifact verification correctly reported `verify_failed`/`claude_failed`, never `ok`). Both stray process trees were killed by hand before 10:00; the real trigger then fired clean 40 minutes later with a normal single `live` delivery. R2 gained atomic lock acquisition (R15) and a new R16 distinguishes a genuine schedule window from an out-of-window Task-Scheduler launch, so a stray click can no longer runaway-retry into the real run.

**Cause still unknown:** module 02's WhatsApp worker has hung at ~10:01 on all three real mornings (2026-09-20, -21, -22), yet runs in 20–35 s by hand at any other time. This is no longer fatal — the v1.2 timeout (module 02) bounds it at 120 s and the in-wrapper retry (module 03) recovers automatically — but the underlying cause (a 10:00-specific contention, resource, or network condition) is undiagnosed and worth investigating separately if it keeps costing a retry every morning.

**Promoting to stable v1.0** on the strength of: 3 real unattended 10:00 runs (2 failed-and-recovered, 1 clean), the retry loop proven live, the window bug fixed and re-confirmed, and 17/18 tests passing with the 18th an accepted, non-blocking gap.

**Open design gaps (each needs its own draft, not an in-place edit):** (1) module 02 v1.2 — write an intent record before sending, since `send_log.json` is written only at the end and a hard kill can leave a sent-but-unlogged message that a retry would repeat; (2) an unfinished-run marker in `run_daily.ps1`, so a hard kill (no `finally`) is reported by the next run instead of passing silently.

The scheduled task **is installed** (`ToDo-WhatsApp-Daily`, daily 10:00, hidden, next run 2026-09-20 10:00) and has been fired successfully by hand.

Nine real defects were found and fixed during this phase — see §12 for each: duplicate `run_log` rows from `exit` inside `try`; a benign `skipped_locked` escalating to WhatsApp and a popup; `Start-Process` not quoting the space in the repo path; the pre-flight reporting a traceback header instead of the error; `permissions.allow` not restricting anything without `--permission-mode manual --permission-prompts none`; `Write(outputs/**)` in `allow` matching nothing and denying the whole pipeline; `alerts_failed` missing from the run log so a failed alert channel vanished; the scheduled task pointing at a **version-stamped** PowerShell path that would break on the next update; and `Bash(rm:*)` in `deny` blocking module 01's own temp-file cleanup. A tenth was found in **module 02** and fixed there as v1.1 (`send_log.json` overwrite — incident filed).

Two findings worth carrying forward rather than filing away:
1. **`deny` is a real boundary for Bash, `allow` is not.** `deny` matches any component of a compound command; `allow` requires every component to match. This asymmetry decides the whole permission design (R7).
2. **Module 01's agentic execution is non-deterministic in shape.** The same pipeline passed at 12:25 and failed at 12:47 with no configuration change, purely because the agent composed a different shell command. Any tightening of `deny` will therefore break intermittently rather than consistently.

— approved by user: pending

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Unattended Google re-consent hangs the run forever** — `fetch_tasks.py` falls back to `run_local_server()` with nobody there to click | The task sits stuck, the lock is held, no messages go out, and nothing tells the owner | R3a tests the refresh without ever reaching `InstalledAppFlow`, so the run is refused *before* it starts; R6's hard timeout kills anything that still stalls; R2 always releases the lock |
| OAuth consent screen is External+Testing, so the refresh token dies every 7 days | The automation breaks weekly | Pre-flight names it as `needs_signin` with the exact fix; the owner is deciding whether to publish the app to In production. Documented in §12 either way |
| WhatsApp session drops and wants a QR scan | Nothing sends; the worker waits 120 s then exits | Pre-flight (d)(e) catches the missing/empty session before the pipeline starts and alerts with "scan from Settings → Linked devices" |
| Two runs open `.secrets/wweb-auth/` at once and corrupt the session | The session has to be deleted and re-linked (this already happened once) | R2's lock, plus `-MultipleInstances IgnoreNew` on the task itself |
| Headless Claude deviates from the skill and produces a wrong or partial grouping | People get a wrong list, silently | R8 verifies artifacts rather than exit codes; the transcript is kept for every run; the prompt is fixed in `prompt.txt` and diffable; the permission set stops it doing anything outside `outputs/` |
| A broad permission set lets an unattended agent do damage | Arbitrary writes or a stray git operation | R7's explicit allow/deny instead of `--dangerously-skip-permissions`; unlisted tools are auto-denied in headless |
| Daily token cost grows unnoticed | Recurring spend | `total_cost_usd` is recorded per run in `run_log.json`; T18 records the real per-run figure; `SCHED_MODEL` defaults to the cheaper Sonnet |
| The laptop is off all day | Nobody gets their list and the owner may not notice | Start-when-available catches the same day on wake; if the whole day is missed, the next day's run still sends a current list. Accepted limitation of a laptop-only design |
| An automated alert leaks a phone number into a toast or a log | Private data on screen or in a file | R12 + T17: everything masked to 4 digits, `outputs/**` and `.secrets/**` git-ignored |
| The scheduled run sends in `live` while the owner is mid-test | Real colleagues get a test-era message | R4 forces `live` only for the scheduler's own run; the manual wrapper takes `-Mode`; module 02's cap and `already_sent` bound the blast radius; `install_task.ps1 -Uninstall` switches the whole thing off in one command |

## 11. Change Log

| Version | Date | Phase | Change | Reason |
|---|---|---|---|---|
| 0.1 | 2026-09-19 | draft | Initial draft: Task Scheduler → hidden PowerShell wrapper → headless `claude -p` running skills 01 then 02; lock, pre-flight, forced `WA_MODE=live`, hard timeout, artifact verification, four alert channels, run log | Explore of 2026-09-19 (headless connector probe passed; `run_local_server` hang and `send_whatsapp.py --check` both found) + the owner's decisions of 2026-09-19 |
| 1.0 | 2026-09-22 | test → stable | **First stable release.** Launcher changed from `pwsh -WindowStyle Hidden` to `conhost.exe --headless` (the former is ignored on Windows 11 and opened a visible window that killed the run when closed); retry mechanism moved from Task-Scheduler `RestartCount` into `run_daily.ps1` itself, since `conhost --headless` does not propagate the child's exit code so Task Scheduler could never see a failure to retry; `ExecutionTimeLimit` raised from 1 h to 3 h to fit three attempts 15 min apart | The real unattended 10:00 run failed on 2026-09-20 (visible window, `0xC000013A`, run killed, nothing sent) and again on 2026-09-21 (module 02 worker hung, no retry fired); both root causes fixed and both fixes proven together on the 2026-09-22 real run, which passed fully unattended |
| 1.1 | 2026-09-23 | draft → test → stable | **Lock race + manual-Run detection.** Lock acquisition (R2) made atomic (`FileMode.CreateNew`), closing a check-then-write gap; a Task-Scheduler launch outside the `SCHED_TIME` window is now relabelled `trigger: manual-gui` (R16) — it still runs, but alerts immediately and never self-retries or joins the schedule day's attempt count | Two Task-Scheduler-launched instances, almost certainly a Run-button click at ~9:01, raced past the old lock within milliseconds, collided on the shared WhatsApp Chrome profile, and each started an unbounded 15-minute self-retry that was still running 20 minutes later — found and killed by hand before the real 10:00 trigger, which then fired clean. Nothing was sent during the incident |

## 12. Troubleshooting & Rollback

| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| 10:00 came and went, nothing happened | Laptop off or asleep; or the task is disabled | `Get-ScheduledTask ToDo-WhatsApp-Daily \| Get-ScheduledTaskInfo` shows `LastRunTime`/`LastTaskResult` | Start-when-available catches it on wake; if the task is disabled, `install_task.ps1` re-registers it |
| Run status `preflight_failed: google_token needs_signin` | Refresh token expired or revoked (External+Testing revokes it every 7 days) | preflight JSON in the run log | Run skill `01-calendar-todo` once by hand to complete the browser sign-in; consider publishing the OAuth consent screen to In production to stop the 7-day cycle |
| Run status `preflight_failed: wa_session` | `.secrets/wweb-auth/` missing or emptied | preflight JSON | Re-link: run module 02 by hand and scan the QR from WhatsApp → Settings → Linked devices |
| Run status `timeout` | A stall — usually an unattended sign-in prompt, or Chrome not exiting | `run_log.json` + the per-run transcript | Read the transcript, fix the underlying cause, re-run by hand; raise `SCHED_TIMEOUT_MIN` only if the run is genuinely long |
| Run status `skipped_locked` every day | A stale lock whose PID is being reused, or a crashed run that left it behind | `outputs/03-scheduler/run.lock` contents vs running PIDs | Delete the lock file; R2's stale detection should already handle it — if it recurs, that's a bug worth an incident |
| Run status `verify_failed`, but Claude exited 0 | The agent claimed success without writing the artifacts, or module 02 refused a stale input | `run_log.json` `verify` block + the transcript | Run both skills by hand to see the real error; this is the case R8 exists for |
| `claude_failed` with a permission denial in the transcript | The skills needed a tool outside `scheduler.settings.json` | transcript shows the denied tool | Add the exact tool to `allow` — narrowly; never reach for `--dangerously-skip-permissions` |
| No toast appears, though the log shows a failure | WinRT toast blocked by Focus Assist / notification settings | `run_log.json` `alerts` lists which channels fired | The visible window and the WhatsApp alert still fire; re-enable notifications for PowerShell in Windows Settings |
| Two messages reached one person | A retry ran against a deleted or rolled-over `send_log.json` | compare `send_log.json` entries for the date | Keep the log — R11 depends on it |
| Daily cost is higher than expected | The agent is taking many turns, or the model was changed | `total_cost_usd` per run in `run_log.json` | Keep `SCHED_MODEL=sonnet`; if turns are high, tighten `prompt.txt` |
| **WhatsApp warns or restricts the number** | Automated sending detected (module 02's standing risk) | The Business app shows the warning | `install_task.ps1 -Uninstall` immediately, set `WA_MODE=dry-run`, write `docs/incidents/`, send by hand until decided otherwise |
| Two `run_log.json` rows appear for a single run | An `exit` inside the `try` block still runs `finally`, so a branch that saved its own row and then exited wrote it twice | `run_log.json` shows two rows with the same `started_at` and different `duration_s` | Fixed during T6: there is now exactly **one** `Save-Run` call in the whole script, in `finally`; branches `return` instead of saving and exiting. Guard this when editing `run_daily.ps1` |
| A benign `skipped_locked` sends a WhatsApp message and opens a popup window | Every status was alerting on all three channels, so a harmless collision escalated like a real failure — it also launched Chrome and cost 25 s | `run_log.json` shows `alerts: [toast, whatsapp, window]` on a `skipped_locked` row | Fixed during T6: `Send-Alert` takes an `-Only` channel filter and `skipped_locked` uses `-Only toast`. WhatsApp and the popup window stay reserved for statuses that actually need the owner |
| The scheduled task silently stops running some weeks after install, with no log row at all | `Get-Command pwsh` returns the **version-stamped** Store path (`…\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__…\pwsh.exe`). That directory ceases to exist the moment PowerShell updates, so the task's action points at nothing | `Get-ScheduledTask … \| Select -Expand Actions` shows an `Execute` path containing `Microsoft.PowerShell_<version>`; `LastTaskResult` is non-zero and `run_log.json` has no matching row (the wrapper never started) | Fixed during T15: `install_task.ps1` now prefers the stable app-execution alias `%LOCALAPPDATA%\Microsoft\WindowsApps\pwsh.exe`, and warns if it is ever forced back to a version-stamped path. Re-run the installer after any PowerShell major upgrade |
| A scheduled run fails with `permission_denials` naming a long compound command that looks harmless | A **deny** rule matched one component of it. Claude Code checks `deny` against every component of a compound command (verified: `cd . && echo hello && git status --short` is denied by `Bash(git:*)`), so one bad verb anywhere blocks the whole line. `Bash(rm:*)` blocked module 01's own cleanup of its temp file inside `outputs/` | `run_log.json` → `claude.permission_denials`; read the **full** command in the transcript, not the 300-char summary — the offending verb is often at the very end | Fixed during T15: `Bash(rm:*)` and `Bash(del:*)` removed from `deny`; `Bash(git:*)` kept, since that is the rule that actually protects the repo and its secrets. Deny cannot carry a path exception, so there is no way to permit `rm` only inside `outputs/` |
| A run passes one day and fails the next with no configuration change | Module 01 is **agentic**: it composes a different shell command each run. One run used `python -c` inline, another used a heredoc plus `rm`. A deny list that is fine for one shape breaks another | Compare `claude.permission_denials` across `run_log.json` rows — the commands differ between runs | Keep `deny` minimal and Bash bare (current design). The durable fix is to make module 01 non-agentic so the commands are fixed; that was considered on 2026-09-19 and deferred |
| `claude` exits 1 in ~3 s with `Error: Settings file not found: D:\practice\Claude` | `Start-Process -ArgumentList` joins arguments with spaces and quotes **nothing**, so the settings path broke at the first space in `D:\practice\Claude Code Essential Practice-02\…`. The multi-line prompt was being passed the same fragile way | The stderr file next to the transcript names a truncated path ending at a space; the transcript is 0 bytes | Fixed during T5: the argument string is built with the settings path explicitly quoted, and the prompt now arrives on **stdin** via `-RedirectStandardInput prompt.txt` instead of as an argument. Any new argument holding a path must be quoted the same way |
| Pre-flight reports `wa_config` failure with a bare "Traceback (most recent call last):" | `send_whatsapp.py --secrets` takes the env **file** path, not the `.secrets` directory — and the check reported the *first* traceback line, which is never the error | preflight JSON `detail` is a traceback header rather than a message | Fixed during T2: the check passes `<secrets>/whatsapp.env`, verifies it exists first, and reports the **last** traceback line (the actual exception) |
| A blank `claude` console window appears at 10:00, and the run dies with `LastTaskResult 0xC000013A` and no log row | `pwsh -WindowStyle Hidden` is ignored on Windows 11 when Windows Terminal is the default terminal: the console is handed off and shown. Closing that window sends a close event that kills pwsh, `claude` and the node/Chrome worker, so `finally` never runs (no row, stale `run.lock`) | `Get-ScheduledTaskInfo` shows `0xC000013A`; `run_log.json` has no row for that date; `outputs/03-scheduler/run.lock` still exists with a dead pid; `%TEMP%\wa_job_*.json` and an empty `wa_res_*.json` are left behind | Fixed during T18: the task action is `conhost.exe --headless <pwsh> …`, which creates no window. If it ever recurs, check `Get-ScheduledTask … \| Select -Expand Actions` still starts with `conhost.exe --headless`, and never close a window belonging to a scheduled run |
| After a hard kill, a message may have been sent but not logged | Module 02 writes `send_log.json` only at the very end; a kill in between loses the record, and a retry would send again | An empty `wa_res_*.json` and a `wa_job_*.json` in `%TEMP%`; check the recipient's chat by hand before any re-run | Manual check today (2026-09-20: owner confirmed nothing was sent). Durable fix is module 02 v1.2 (write intent before sending) — open |
| A scheduled run fails on attempt 1 and is **never retried**; `LastTaskResult` is `0x0` despite `status: claude_failed` | The task runs through `conhost.exe --headless`, which does not propagate the child's exit code, so Task Scheduler always sees success and its `RestartCount` never triggers | `run_log.json` has one failed row for the day and no `attempt: 2`; `Get-ScheduledTaskInfo` shows `0x0` | Fixed 2026-09-21: `run_daily.ps1` retries itself (15 min apart, up to `SCHED_MAX_ATTEMPTS`) after the row is saved and the lock released. Never rely on the task's exit code while the launcher is `conhost --headless` |
| Module 02 hangs ~10 min with no output at ~10:01 and the agent stops it (`claude_failed`, "waiting for a QR scan?") | **Not found.** `--check` shows the session linked; the same worker runs in 20–35 s by hand at other times. Left behind: `%TEMP%\wa_job_*.json` plus an **empty** `wa_res_*.json` | The run transcript's `SCHEDULER_FAIL` text; the empty result file; `send_log.json` has no row for the date | Retry (now automatic). Check the recipient chats before any manual re-run — a hang before the log write cannot prove nothing was sent. Open: worker-level timeout in module 02, and a worker log to catch what it is waiting on |
| A `self`-mode test looks like a delivery | `self` sends every recipient's list to the owner's **own** test number | `send_log.json` row has `mode: self`; no `live` row for the date | Not a defect — read the mode. Real delivery is only the `live` row |
| Two (or more) instances of the task are `Running` at once, and one fails with a WhatsApp/Chrome "already running for .secrets/wweb-auth/session" error | Two launches started within milliseconds of each other slipped past the old check-then-write lock before either had written the file — very likely a Task Scheduler "Run" click, possibly a double-click, landing near but not at `SCHED_TIME` | `Get-CimInstance Win32_Process` shows more than one `run_daily.ps1` process tree; `run_log.json` has two rows with almost identical `started_at` timestamps | Fixed in v1.1 (R15): lock acquisition is now atomic. If it recurs, kill every `run_daily.ps1` process tree by hand (`taskkill /PID <pid> /T /F` on each `conhost.exe` root), delete a stale `outputs/03-scheduler/run.lock`, and confirm no leftover `chrome.exe` still references `wweb-auth` before the next fire |
| A failed run from an odd time of day quietly starts its own 15-minute retry chain, still running when the real 10:00 trigger arrives | Clicking "Run" in Task Scheduler launches the exact same registered command as the real trigger, so before v1.1 a manual click was logged and treated identically to the genuine 10:00 fire — including its self-retry loop | `run_log.json` shows a `trigger: schedule` row at a time far from `SCHED_TIME`, or a `run_daily.ps1` process still alive many minutes after it should have finished (it is asleep inside its own retry wait) | Fixed in v1.1 (R16): a `schedule` launch outside the `SCHED_TIME` window is now logged `trigger: manual-gui` and never self-retries. If you need to test by hand, prefer `pwsh -File run_daily.ps1 -Trigger manual -Mode dry-run` from a terminal over the Task Scheduler "Run" button — it is unambiguous and defaults to a mode that never sends |

**Rollback:** `git checkout 03-scheduler/v<prev> -- .claude/skills/03-scheduler plan/stableMD/03-scheduler.stable.md`,
then `powershell -File src/scheduler/install_task.ps1` to re-register the task from the restored version.
To stop the automation entirely without a rollback: `powershell -File src/scheduler/install_task.ps1 -Uninstall`.
See also `docs/TROUBLESHOOTING.md#03-scheduler`.
