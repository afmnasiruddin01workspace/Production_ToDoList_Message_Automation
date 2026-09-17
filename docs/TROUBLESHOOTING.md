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
| Symptom | Likely cause | How to check | Fix |
|---|---|---|---|
| Error `190` / HTTP 401 | Access token expired or invalid | Error body `error.code` | New token (System User permanent token recommended); update `WA_TOKEN` |
| Error `131047` | Outside 24-hour customer window — free text not allowed | Error body | Send approved template message instead |
| Error `132001` | Template name / language wrong or not approved | WhatsApp Manager → Templates | Use exact approved name + language code |
| Error `131026` | Number not on WhatsApp or wrong format | Contacts row | Use E.164 digits only: `8801678xxxxxx` (no `+`, spaces, dashes) |
| Error `130429` / `131056` | Rate limit | Error body | Delay between sends; retry with backoff; unsent rows stay in `send_log.json` |
| Duplicate messages | Re-run after partial failure | `send_log.json` for today | Skill must skip persons with `status: sent` today |
| Person not messaged | Group name not in contacts, or group is "No Name" | Report section of send log | Expected behavior — add contact or fix event title |
| Business app stopped receiving | Coexistence onboarding issue | Meta Business Manager → phone number status | Resolve in Meta; set `WA_MODE=dry-run` meanwhile |
| Sent to everyone during testing | `WA_MODE` was `live` | Env var | Always test with `self`; incident + hotfix |

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
