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
**v1.0: WhatsApp Web linked-device automation** (`whatsapp-web.js`), not the Meta Cloud API — see `plan/stableMD/02-whatsapp-message.stable.md` §12 for full detail and the real incidents this table summarizes.

| Symptom | Likely cause | How to check | Fix |
|---|---|---|---|
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
_To be filled during its explore/plan phase._

## Git / Repository
| Symptom | Likely cause | Fix |
|---|---|---|
| Push rejected (non-fast-forward) | Remote has newer commits | `git pull --rebase origin main`, re-check ignored files, push |
| Private file shows in `git status` | Missing/incorrect ignore rule | `git check-ignore -v <file>`; fix `.gitignore` before committing |
| Secret or real number committed (not pushed) | Ignore rule missed | `git reset --soft HEAD~1`, unstage file, fix `.gitignore`, recommit |
| Secret or real number **pushed** | Ignore rule missed | **Rotate token immediately**, then ask before rewriting history (`git filter-repo`) and force-pushing |
| Draft/test plan pushed | Wrong `git add` | `git rm --cached <file>`, commit, push |
