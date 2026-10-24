# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Production ToDo → WhatsApp Automation

Modular chain of Claude Code skills. Each module's output is the next module's input.

| ID | Module | Input | Output |
|---|---|---|---|
| 01 | calendar-todo | Google Tasks (all lists, Tasks API) + Google Calendars in `src/calendarLinks/lists.txt` | `outputs/01-calendar-todo/todo_by_name.json` (+ `.md`) |
| 02 | whatsapp-message | 01 output + `src/messageListNDetails/WhatsappContacts.txt` | `outputs/02-whatsapp-message/send_log.json` |
| 03 | scheduler | 01 + 02 skills (WhatsApp, 10:00) and 04 (email, run directly, 13:30) | `outputs/03-scheduler/run_log.json` (+ per-run transcripts in `logs/`) |
| 04 | email-message | 01 output + `src/EmailContent/EmailContacts.txt` | `outputs/04-email-message/send_log.json` + shared `outputs/delivery/delivery_log.json` |
| 05 | manual-ui | 01 output + both contact files + 02/04/ledger logs | local page on 127.0.0.1 (`src/ui/`) that runs 02 and 04 by hand; `outputs/05-manual-ui/` |

## Phase pipeline (mandatory for every module and every change)

```
explore → plan → draftMD → validate ⟲ → testMD → test ⟲ → stableMD → execute → SKILL → output → next module
```

1. **Explore** — read upstream `outputs/`, source files, MCP capabilities. Write nothing.
2. **Plan → draftMD** — copy `plan/templates/prd_todo_template.md` to `plan/draftMD/NN-<module>.draft.md`, fill all 12 sections, `phase: draft`.
3. **Validate** — run §8 checklist, report results. Fail → fix draft. Pass → **ask user to approve** → copy to `plan/testMD/NN-<module>.test.md`, `phase: test`.
4. **Test** — run §9 test cases, record Result + Evidence. Fail → fix (return to draft if design changes). Pass → **ask user to approve** → copy to `plan/stableMD/NN-<module>.stable.md`, `phase: stable`.
5. **Execute** — real run from stable §7, write to `outputs/NN-<module>/`.
6. **Skill** — create `.claude/skills/NN-<module>/SKILL.md` from stable §7; end it with an "If it fails" block linking `docs/TROUBLESHOOTING.md`.
7. **Publish** — add CHANGELOG line, commit, tag, push (see Git policy). **Ask before pushing.**

Never skip a gate. Never promote without explicit user approval.

## Fixed decisions
- Person = name inside `()` in event title, but only if it matches the `Person` column in `WhatsappContacts.txt` (case-insensitive; a leading `From `/`By ` is stripped). No match → `"No Name"`. `(A, B)` → listed under each matched name.
- Tasks come from the Google Tasks API (all lists, open tasks only, due today…today+30). Calendar items with a `tasks.google.com/task/` link are task copies and are dropped. Events come from the Calendar connector.
- A task with no matched name on the default "My Tasks" list → group `"A F M Nasir Uddin"`. Unnamed tasks on other lists and unnamed events → `"No Name"`.
- If the Tasks API read fails, the run stops without writing outputs.
- Google OAuth client + token live only in `.secrets/` (gitignored). Never print or commit them.
- Holiday calendars (ID contains `#holiday@group.v.calendar.google.com`) are never read, even if added to `lists.txt`.
- Known limitation: a recurring task shows only its current open instance (Tasks API behavior). Accepted for v3.0.
- Output contains only group name + list of items (type, title). No date, time, calendar or task-list name is output; they are used internally only (ordering, owner rule, run summary).
- Window: **start of today** (00:00:00) → today + 30 days (23:59:59), timezone `Asia/Dhaka`.
- WhatsApp: **v1.0 — linked-device automation** (`whatsapp-web.js` driving a real Chrome browser), not the Meta Cloud API — the owner's Facebook account is restricted, so no developer token can ever be issued. No token exists in this design. Settings live in `.secrets/whatsapp.env` (`WA_MODE` (`dry-run`\|`self`\|`live`), `WA_SENDER_NUMBER`, `WA_TEST_NUMBER`, `WA_MIN_DELAY`/`WA_MAX_DELAY`, `WA_MAX_PER_RUN`, optional `WA_CHROME_PATH`); the linked session lives in `.secrets/wweb-auth/` (gitignored), created by a one-time QR scan. This is not an officially supported interface — WhatsApp's terms permit only the Business API for automated sending — so volume is kept small (one message per person per scheduled day, capped per run) and the risk was accepted by the user on 2026-09-18.
- Module 02 test ladder: `dry-run` → `self` (own number only) → `live`.
- Messages in English, numbered list per person.

## Commands
There is no build, lint or test suite. Verification is the plan's §9 test table, run by hand. All commands run from the repo root.

```
python .claude/skills/02-whatsapp-message/send_whatsapp.py --check          # show config + session state, send nothing
python .claude/skills/02-whatsapp-message/send_whatsapp.py --today YYYY-MM-DD   # override the date (tests)
python .claude/skills/01-calendar-todo/fetch_tasks.py --today YYYY-MM-DD    # Tasks API read (module 01 step)
python src/scheduler/preflight.py                                           # the scheduler's 10 health checks
pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run        # safe full-pipeline test (never sends)
pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run -Channel email   # the 13:30 email run alone
src/ui/start_ui.cmd [--stop]                                                 # manual-send web page (loopback only)
pwsh -File src/scheduler/install_task.ps1 [-Status|-Uninstall]              # (re)register / inspect / remove both daily tasks
```
Module 02 deps: `cd .claude/skills/02-whatsapp-message && npm install`. Config: `.secrets/whatsapp.env`, `src/scheduler/scheduler.env` (copy from the `.sample`).

## Architecture notes (need several files to see)
- **Module 01 is agentic, 02 is deterministic.** 01 is Claude following its SKILL.md (Calendar connector for events + `fetch_tasks.py` for Tasks). 02 only runs `send_whatsapp.py`, which shells out to `wa_send.js` (`whatsapp-web.js`). That is why the scheduler must drive a headless `claude -p` session rather than call scripts.
- **Module 03 wrapper** (`src/scheduler/run_daily.ps1`): atomic lock (`run.lock`) → `preflight.py` → headless `claude -p` with `prompt.txt`, forced `WA_MODE=live`, `--settings scheduler.settings.json` → verify **from artifacts** (`todo_by_name.json` dated today, `send_log.json` has a `live` row for today), never from exit codes → append `run_log.json` → alert (`notify.py`) only once retries are exhausted.
- **Two tasks, two run kinds (03 v3.0).** `ToDo-WhatsApp-Daily` (10:00, `SCHED_TIME`) runs modules 01+02 only (`run_kind: whatsapp`); `ToDo-Email-Daily` (13:30, `SCHED_EMAIL_TIME`) runs `run_daily.ps1 -Channel email` (`run_kind: email`), which rebuilds a missing list with a module-01-only Claude session (`WA_MODE=dry-run` forced) and waits for a held lock instead of skipping. Each kind has its own attempt ladder (rows without `run_kind` count as `all`). `SCHED_EMAIL_TIME=off` = v2.0's single run; re-run `install_task.ps1` after changing either time.
- **Retries live inside `run_daily.ps1`** (sleep 15 min, re-invoke itself), not in Task Scheduler's `RestartCount`: the task launches via `conhost.exe --headless`, which swallows the child's exit code. Do not "simplify" this to `-WindowStyle Hidden` (ignored on Windows 11; a visible window killed a real run).
- **`trigger` label.** A Task Scheduler launch outside the `SCHED_TIME` window is relabelled `manual-gui`: it runs, alerts immediately, never self-retries. A missed-run catch-up is recognised from Task Scheduler's event 114 and labelled `catch-up` (a scheduled run: keeps its retries); the launch is classified from the Operational log (107 `schedule`, 114 `catch-up`, 110 `manual-gui`), with the clock window only as a fallback.
- **`send_log.json` holds several runs per date**; always select by `date` **and** `mode`. The idempotency guard counts only `live` rows. Statuses `sent`, `already_sent`, `unconfirmed` all block re-sending — an ambiguous timeout is never auto-retried, so nobody is messaged twice.
- **Module 05 (manual UI) sends nothing itself**: it runs `send_whatsapp.py` / `send_email.py` with `--people … --trigger manual-ui`. Its WhatsApp sends and Rebuild take `outputs/03-scheduler/run.lock` and are refused from 6 min before to 3 min after `SCHED_TIME` — a scheduled run that finds the lock held logs `skipped_locked` and is not retried. Module 02 stores `sent_at` in UTC (`Z`), module 04 with `+06:00`; convert before displaying.
- **One Chrome profile** (`.secrets/wweb-auth/session`) is shared by every send; two concurrent runs corrupt it. Never start a send while the scheduled task may be running.
- Module 02 enforces its own deadlines (`WA_READY_TIMEOUT`, `WA_SEND_TIMEOUT`, `WA_KILL_MARGIN`); the ~10:01 worker-startup hang behind them is still undiagnosed.
- Each module's behaviour is specified in `plan/stableMD/NN-*.stable.md` (§7 Steps, §9 tests, §12 troubleshooting); the SKILL.md is a summary and the stable plan wins on disagreement.

## Naming
- Plans: `plan/{draftMD,testMD,stableMD}/NN-<module>.{draft,test,stable}.md`
- Skills: `.claude/skills/NN-<module>/SKILL.md`
- Tags: `NN-<module>/vMAJOR.MINOR`

## Git policy
- Remote: https://github.com/afmnasiruddin01workspace/Production_ToDoList_Message_Automation (branch `main`).
- Only stable work is committed: stableMD, skills, docs, samples, CLAUDE.md, CHANGELOG.md, `.gitignore`, `.claude/settings.json`.
- Never commit: draftMD/testMD, real `lists.txt`, real `WhatsappContacts.txt`, `outputs/` data, `.env`, `.secrets/`, `settings.local.json`.
- Before each commit run `git diff --cached --name-only` and check for private files.
- Commit: `feat(NN-module): stable vX.Y`, `fix(NN-module): vX.Y — …`, `revert(NN-module): back to vX.Y`.

## Changes, upgrades, failures
- Upgrade / hotfix / rollback process: `docs/UPGRADE.md`
- Symptom → fix runbook: `docs/TROUBLESHOOTING.md`
- Real failures: `docs/incidents/YYYY-MM-DD-NN-module-slug.md`
- Stable files are never edited in place — every change starts a new draft with a version bump.
