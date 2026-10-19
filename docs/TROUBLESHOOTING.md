# Troubleshooting Runbook

Format: **Symptom → Likely cause → How to check → Fix**.
When a new failure type appears: write an incident (`docs/incidents/`), add a row here, and add a test case in the module's next version.

## General checks (run first)
1. Is the right mode set? `echo $WA_MODE` (`dry-run` | `self` | `live`).
2. Are connectors connected? Google Calendar connector in Claude settings.
3. Did the upstream module produce output? Check `outputs/NN-<module>/` timestamp.
4. Which version is running? Check `version` in `plan/stableMD/NN-<module>.stable.md` and `git tag`.

## 01-calendar-todo
| Symptom | Likely cause | How to check | Fix |
|---|---|---|---|
| No calendars found / 404 | Wrong calendar ID in `lists.txt`, or calendar not shared with the connected Google account | Run `list_calendars` and compare IDs | Correct ID; share calendar with the account |
| Auth / permission error | Google Calendar connector disconnected or expired | Try `list_calendars` | Reconnect the connector |
| Everything in "No Name" | Titles lack `(Name)`, or use other brackets like `（）` `[]` | Look at raw titles in the MCP response | Fix titles, or add bracket variant to parsing rule (MINOR upgrade) |
| Name group split in two (e.g. `Rabbani` vs `rabbani `) | Case / extra spaces | Compare group names in output | Normalize: trim + match `Person` column case-insensitively (MINOR) |
| Missing or extra items | Timezone (UTC vs Asia/Dhaka) or window boundary; recurring events not expanded | Check `timeMin`/`timeMax` used | Use Asia/Dhaka; expand recurring instances |
| Empty output file | No open tasks or events in window | Check Calendar UI / Tasks board for next 30 days | Expected — output `{}`, not an error |
| `fetch_tasks: missing …credentials.json` | OAuth client not downloaded | Look in `.secrets/downloads/` and `.secrets/google/` | Create Desktop OAuth client in Google Cloud Console; save JSON into `.secrets/downloads/` |
| `invalid_grant` / token expired or revoked | Refresh token expired (External + Testing app: 7 days) or access revoked | stderr of `fetch_tasks.py` | Delete `.secrets/google/token.json`, run again and sign in; use Internal/published consent screen |
| HTTP 403 `accessNotConfigured` | Google Tasks API not enabled | stderr | APIs & Services → Library → enable Google Tasks API |
| `access_denied` in browser sign-in | Account not a test user (External + Testing) | Browser error page | OAuth consent screen → Test users → add the account |
| `ModuleNotFoundError: googleapiclient` | Python packages missing | stderr | `python -m pip install --user google-api-python-client google-auth-oauthlib` |
| A task appears twice | Calendar task-copy marker changed | Raw event `description` | Update the marker in skill step 6 (MINOR) |
| Owner's unnamed task in "No Name" | Task is on a list other than "My Tasks" | Tasks board | Expected — move to My Tasks or add a name |
| A task is missing | Completed, no due date, overdue, beyond +30 days, or future repeat of a recurring task | Tasks board | Expected per plan §5 |

## 02-whatsapp-message
**v2.0: WhatsApp Web linked-device automation** (`whatsapp-web.js`), not the Meta Cloud API, with bounded worker deadlines (R18–R22) and delivery receipts (R23–R26) — see `plan/stableMD/02-whatsapp-message.stable.md` §12 for full detail and the real incidents this table summarizes.

| Symptom | Likely cause | How to check | Fix |
|---|---|---|---|
| Your own WhatsApp desktop/web app shows "Waiting for this message. This may take a while." on an automated message | That app is a **different linked device** from the automation's Chrome session and cannot yet decrypt a message another of your devices sent. It says nothing about the recipient's phone | `send_log.json` → that entry's `delivery` | `delivered`/`read` → the person has it, nothing to do. `server`/`pending` → next row |
| An entry is `sent` but `delivery: server` or `pending` ("not confirmed delivered") | The recipient's phone was offline/slow within `WA_ACK_TIMEOUT`, or the run was killed before the receipt | summary line `! <person>: not confirmed delivered` | Check the chat on your primary phone later (two ticks = delivered). **Do not re-run to "fix" it** — the idempotency guard correctly stops a duplicate; use the manual UI's resend only if the chat still shows one tick hours later |
| Every entry in a run is `delivery: pending`, even to phones that are online | The library stopped emitting `message_ack` (WhatsApp Web changed) | all-`pending` across a whole run | Re-run the explore spike (`self` mode) to see which events still fire; until fixed, `delivery` is uninformative and `sent` means what it meant in v1.2 |
| Testing the "phone offline" case but receipts still reach `delivered` | Airplane mode alone does not disconnect Wi-Fi, so the phone stayed online | the phone can open a website while "offline" | Turn Wi-Fi **and** mobile data off, confirm no internet, then test |
| `ledger: not written (...)` on stderr | `outputs/delivery/` not writable | the message names the reason | Fix the path/permissions; the run itself and `send_log.json` are unaffected |
| Run stops: `startup-timeout` | The worker never reached `ready` within `WA_READY_TIMEOUT` (default 120s) — WhatsApp Web stalled loading, or the session needs attention. Observed for real at ~10:01 on module 03's scheduled runs (2026-09-20 through -22); cause still undiagnosed | stderr/transcript names the **last stage reached** (`launching`, `loading NN%`, `authenticated`, `ready`); nothing was sent | Safe to re-run at once — nothing was sent. If it repeats, run the worker headful (`WA_HEADFUL=1`) to see the page; raise `WA_READY_TIMEOUT` only if the last stage shows steady progress rather than a dead stop |
| Run stops: `send-timeout`, one person logged `unconfirmed` | `sendMessage()` did not return within `WA_SEND_TIMEOUT` (default 60s) for that recipient | `send_log.json` shows `unconfirmed` for that person; anyone after them is `failed` ("not attempted") | **Check that person's WhatsApp chat by hand before doing anything else.** Delivered → nothing to do. Not delivered → send by hand, or clear the entry and re-run. A re-run never auto-resends an `unconfirmed` person (idempotency treats it like `sent`) |
| Log shows `"worker killed after Ns — not attempted"` | The Python-side hard ceiling (R21) fired: the worker or Chrome was wedged past its own timers and the whole process tree was killed | stderr/transcript, plus the last stage reached before the kill | Handled recipients are still logged correctly from the worker's partial result (written after every recipient); re-run to reach the rest. If it recurs, treat it as `startup-timeout` above |
| A QR code appears every run | Session folder missing/deleted, or was never fully linked | `.secrets/wweb-auth/` missing or empty | Scan again from the phone with the business number; leave the folder alone afterward |
| Run stops: `auth-timeout` | Nobody scanned the QR within `WA_QR_TIMEOUT` | stderr reason | Re-run with the phone in hand: WhatsApp → Settings → Linked devices → Link a device |
| Run stops: `sender-mismatch` | The scan linked the wrong WhatsApp account, or `WA_SENDER_NUMBER` is wrong | stderr shows the masked linked number vs. expected | Unlink the wrong account from its phone, re-scan with the correct one, or fix `WA_SENDER_NUMBER` |
| `Execution context was destroyed`, at launch or mid-session, even on a fresh scan | Installed Chrome is much newer than Puppeteer/`whatsapp-web.js`'s pinned protocol version | Compare Chrome's version to the pinned `whatsapp-web.js`/Puppeteer versions in `package.json` | Already mitigated with `protocolTimeout` + extra launch args in `wa_send.js`; if it recurs, try `WA_HEADFUL=1` or an older Chrome build |
| Same error, but happens on *every* run including a brand-new session | `.secrets/wweb-auth/` is corrupted (usually from a scan that was interrupted partway) | A clean, empty session folder launches without error | Delete `.secrets/wweb-auth/` entirely and re-link with a fresh QR scan |
| A send is logged `failed` with `Cannot read properties of undefined ('id')`, but it actually arrived | `sendMessage()` can resolve without a usable id even on success (library/WhatsApp Web version drift) | Compare the log to the phone | Already fixed — a missing id is recorded as `"unconfirmed"`, not `failed` |
| The last person in a run never gets their message, though everyone before them does | No trailing pacing delay after the last send before the browser closes | Reproducible: always the alphabetically-last recipient | Already fixed — a 5 s settle delay runs before `client.destroy()` |
| A `self`-mode test run seems to block a same-day `live` send (`already_sent`) | Idempotency check used to ignore run `mode` | `send_log.json` shows a `mode: "self"` `sent` entry for today | Already fixed — idempotency only counts `mode: "live"` entries |
| Duplicate message arrives | Log file deleted or the date rolled over mid-run | Compare `send_log.json` entries for the date | Keep the log; re-runs rely on it |
| Person not messaged though items exist | `Send days` excludes today, no contact row, or the run hit `WA_MAX_PER_RUN` | Log status `skipped_day` / `skipped_no_contact` / `skipped_over_cap` | Edit `Send days`, add the contact row, or raise the cap |
| Refuses to start | `WA_MODE` missing, empty, or unrecognized | stderr | Set `WA_MODE` to `dry-run`, `self`, or `live` in `.secrets/whatsapp.env` |
| Stale module 01 output | Module 01 wasn't run today | stderr shows the file's date | Run skill `01-calendar-todo` first |
| **WhatsApp warns or blocks the number** | Automated sending detected (this is against WhatsApp's terms, a known and accepted risk) | Business app shows a warning | Stop immediately (`WA_MODE=dry-run`), unlink the device, write an incident under `docs/incidents/`, send by hand until decided otherwise |

## 03-scheduler
**v1.1: Task Scheduler → `conhost.exe --headless` PowerShell wrapper → headless `claude -p` running skills 01 then 02, now with an atomic lock and manual-Run detection** — see `plan/stableMD/03-scheduler.stable.md` §12 for full detail and the real incidents this table summarizes.

| Symptom | Likely cause | How to check | Fix |
|---|---|---|---|
| A blank console window appears at 10:00, and the run dies with `LastTaskResult 0xC000013A` and no log row | The task's action reverted to plain `-WindowStyle Hidden`, which Windows 11 ignores under the default-terminal handoff — the window is shown, and closing it kills the whole tree | `Get-ScheduledTask ToDo-WhatsApp-Daily \| Select -Expand Actions` — the `Execute` should be `conhost.exe` with `--headless` as the first argument | Re-run `install_task.ps1` to re-register the correct action. Never close a window belonging to a scheduled run — check `run_log.json` instead |
| A run fails and there was **no retry** — `LastTaskResult` is `0x0` despite a `claude_failed` row | Task Scheduler's own `RestartCount` never fires, because `conhost --headless` does not propagate the child's exit code back to it | `run_log.json` has one failed row for the day with no `attempt: 2` | This is expected — retries run **inside** `run_daily.ps1` itself (wait 15 min, re-invoke), not via Task Scheduler. Confirm `SCHED_MAX_ATTEMPTS` in `scheduler.env`; a second/third row should appear ~15 min after the first |
| `preflight_failed: google_token needs_signin` | Refresh token expired or revoked (External+Testing consent screen revokes it every 7 days) | preflight JSON in the failed run's `run_log.json` entry | Run skill `01-calendar-todo` once by hand to complete the browser sign-in; consider publishing the OAuth consent screen to *In production* to stop the 7-day cycle |
| `preflight_failed: wa_session` | `.secrets/wweb-auth/` missing or emptied | preflight JSON | Re-link: run module 02 by hand and scan the QR from WhatsApp → Settings → Linked devices |
| `claude_failed` with reason `startup-timeout` or `send-timeout` in the error text | Module 02's own worker timeout fired (v1.2) — WhatsApp Web stalled reaching `ready`, or one send never returned | The transcript names the **last stage reached**; nothing (`startup-timeout`) or one `unconfirmed` person (`send-timeout`) was sent | Nothing to fix by hand — the next scheduled attempt (15 min later) should recover on its own. If it recurs every morning around 10:00–10:01, that hang's root cause is still undiagnosed; worth its own investigation if it keeps costing a retry |
| `skipped_locked` every day | A stale lock whose PID is being reused, or a crashed run that left it behind | `outputs/03-scheduler/run.lock` contents vs. running PIDs | Delete the lock file; stale-lock detection should already reclaim it on the next run — if it recurs, that's a bug worth an incident |
| `verify_failed`, but `claude` exited 0 | The agent claimed success without writing the artifacts, or module 02 refused a stale module 01 output | `run_log.json`'s `verify` block + the per-run transcript | Run both skills by hand to see the real error |
| A permission denial names a long compound Bash command that looks harmless | A `deny` rule matched **one component** of it — Claude Code checks `deny` against every component of a compound command, so one bad verb anywhere blocks the whole line | `run_log.json` → `claude.permission_denials`; read the **full** command in the transcript, not the truncated summary | Add the exact tool/verb narrowly to `allow`, or remove it from `deny` if it is something module 01 legitimately needs (e.g. `rm` for its own temp-file cleanup) — never reach for `--dangerously-skip-permissions` |
| No toast appears, though the log shows a failure | WinRT toast blocked by Focus Assist or notification settings | `run_log.json`'s `alerts` list for that run | The visible window and the WhatsApp alert still fire on the final attempt; re-enable notifications for PowerShell in Windows Settings |
| 10:00 came and went, nothing in `run_log.json` | Laptop was off or asleep; or the task got disabled | `Get-ScheduledTask ToDo-WhatsApp-Daily \| Get-ScheduledTaskInfo` | `StartWhenAvailable` should catch it on wake (not yet observed live — see the skill's Known limitations); if the task is disabled, re-run `install_task.ps1` |
| Daily token cost creeping up | The agent is taking unusually many turns, or the model was changed | `total_cost_usd` per run in `run_log.json` | Keep `SCHED_MODEL=sonnet`; if turns are consistently high, tighten `prompt.txt` |
| Two (or more) `run_daily.ps1` instances running at once; one fails with a WhatsApp/Chrome "already running for .secrets/wweb-auth/session" error | Two launches started within milliseconds of each other slipped past the old check-then-write lock before either had written the file — most likely a Task Scheduler "Run" click, possibly a double-click | `Get-CimInstance Win32_Process` shows more than one `run_daily.ps1` process tree; `run_log.json` has two rows with nearly identical `started_at` timestamps | Fixed in v1.1: lock acquisition is atomic. If it still happens, kill every `run_daily.ps1` process tree (`taskkill /PID <pid> /T /F` on each `conhost.exe` root), delete a stale `run.lock`, and confirm no `chrome.exe` still references `wweb-auth` before the next fire |
| A failed run at an odd time of day quietly starts a 15-minute retry loop still running when the real 10:00 trigger arrives | Task Scheduler's "Run" button launches the identical registered command as the real trigger, so a manual click used to be indistinguishable from the genuine 10:00 fire — including inheriting its self-retry | `run_log.json` shows `trigger: schedule` at a time far from `SCHED_TIME`, or a `run_daily.ps1` process still alive long after it should have finished | Fixed in v1.1: an out-of-window `schedule` launch is now logged `trigger: manual-gui` and never self-retries. To test by hand, use `pwsh -File src/scheduler/run_daily.ps1 -Trigger manual -Mode dry-run` from a terminal rather than the Task Scheduler "Run" button |

## 04-email-message
**v1.0: email through the owner's classic Outlook** (COM, no password) with Sent Items proof and bounce detection — see `plan/stableMD/04-email-message.stable.md` §12 for the full table.

| Symptom | Likely cause | How to check | Fix |
|---|---|---|---|
| Run stops: `outlook-unavailable` | Outlook did not become ready within `EMAIL_STARTUP_TIMEOUT` (profile problem, sign-in needed, classic Outlook missing) | stderr reason; `send_email.py --check` | Open Outlook by hand once and sign in; check `EMAIL_OUTLOOK_PROFILE`. Nothing was sent, safe to re-run |
| A "Choose Profile" window sits on screen | Outlook was started without `/profile` (by hand, or by another program's COM call) | the window | Pick the profile once; the module itself always passes `/profile` |
| Entry `sent` with `delivery: queued` | Outlook offline or not synchronised: the mail is in the Outbox | stderr `! ... still in the Outbox` | Go online — it will send. **Do not re-run** (it would not resend anyway) |
| Entry `unconfirmed` / `delivery: pending` | `Send()` returned but the mail is in neither Sent Items nor the Outbox | the log | **Check Sent Items by hand first.** Found → nothing to do; not found → send by hand or clear the entry and re-run |
| Entry `delivery: bounced` | The address is wrong or the mailbox is gone (a non-delivery report is in the Inbox) | stderr `! ... non-delivery report received` | Correct the address in `EmailContacts.txt`, then resend from the manual UI |
| A bounce is in the Inbox but the run says `bounced: 0` | The scan could not read it | the `bounce scan:` line shows `N unreadable` | Run `--reconcile`; never treat "0 bounced" as proof while `unreadable` is not 0 (fixed once already: a report item has no `ReceivedTime`) |
| `invalid_address` for someone | The contact row has no address, two addresses, or a typo | log status | Fix the row: one address, then the days, separated by spaces |
| Someone is not mailed though they have items | Email days exclude today, or they are only in the WhatsApp contacts | log status `skipped_day` / `skipped_no_contact` | Edit `EmailContacts.txt` |
| An Outlook security prompt appears on send | Object-model guard active (antivirus not recognised as current) | the dialog | Stop live sending and decide with the owner; never click through unattended |
| `skipped_locked` | Another run (scheduler or manual UI) is in progress | `outputs/04-email-message/run.lock` | Wait; a lock from a dead process is reclaimed automatically |
| Log shows `worker killed after Ns` | The hard ceiling fired: Outlook or the worker wedged | stderr, last stage | Handled recipients are logged from the partial result; check Sent Items for the in-flight one before re-running |
| Refuses to start | `EMAIL_MODE` missing or misspelled | stderr | Set it to `dry-run`, `self` or `live` in `.secrets/email.env` |

## Git / Repository
| Symptom | Likely cause | Fix |
|---|---|---|
| Push rejected (non-fast-forward) | Remote has newer commits | `git pull --rebase origin main`, re-check ignored files, push |
| Private file shows in `git status` | Missing/incorrect ignore rule | `git check-ignore -v <file>`; fix `.gitignore` before committing |
| Secret or real number committed (not pushed) | Ignore rule missed | `git reset --soft HEAD~1`, unstage file, fix `.gitignore`, recommit |
| Secret or real number **pushed** | Ignore rule missed | **Rotate token immediately**, then ask before rewriting history (`git filter-repo`) and force-pushing |
| Draft/test plan pushed | Wrong `git add` | `git rm --cached <file>`, commit, push |
