---
module: calendar-todo
id: "01"
phase: stable
version: "3.0"
depends_on: []
input_from: src/calendarLinks/lists.txt + Google Tasks API (all task lists) + src/messageListNDetails/WhatsappContacts.txt (Person column only)
output_to: outputs/01-calendar-todo/
approved_by: A F M Nasir Uddin (2026-09-17 — "Yes please" to promote to stable)
date: 2026-09-17
---

# 01 — Calendar ToDo by Person

## 1. Frontmatter
See YAML header above. Every field must be filled before validation.

## 2. Goal
Collect every open Google Task (from all task lists) and every Google Calendar event (from the calendars in `lists.txt`) for today through today + 30 days, and group them by the responsible person named in the title, so module 02 can send each person a numbered to-do list.

## 3. Inputs
| Name | Path / Source | Format | Schema / Columns | Private? |
|---|---|---|---|---|
| Calendar IDs | `src/calendarLinks/lists.txt` | txt | One calendar ID per line; lines are trimmed; blank lines and lines starting with `#` are skipped | yes |
| Known persons | `src/messageListNDetails/WhatsappContacts.txt` | tsv (header row) | Only column `Person` is read; `WhatsApp contact name` and `Number` are never read or copied | yes |
| Calendar events | MCP `list_events` (Google Calendar connector) | json | `summary`, `eventType`, `description` (checked for the task-link marker only), `start`, `status` | yes |
| Google Tasks | Google Tasks API v1 via `.claude/skills/01-calendar-todo/fetch_tasks.py` (scope `tasks.readonly`) | json (script stdout) | Per task: `list` (list title), `is_default` (true for the account's default list), `title`, `due` (date only, `YYYY-MM-DD`), `status` | yes |
| Google OAuth client + token | `.secrets/google/credentials.json`, `.secrets/google/token.json` (raw download dropped in `.secrets/downloads/`) | json | Google Desktop OAuth client; token created on first sign-in | yes — secret, gitignored, never printed |

> Explore findings v0.1 (2026-09-17): calendar #1 had 21 items (20 Google Tasks shown as `eventType: FOCUS_TIME` with a `tasks.google.com/task/` link in `description`; 1 recurring `DEFAULT` meeting). Calendar #2 had 0 items. Every line in `lists.txt` starts with a space.
>
> Explore findings v3.0 (2026-09-17, read-only probe against the Tasks API): 10 task lists (`My Tasks`, `Prd-OrderMgt`, `Prd-SOP`, `Prd-SFC`, `Prod-Costing`, `Prd-Development`, `Prd-Die`, `Prd-Audit`, `Meeting`, `Inventory-RM`); 27 tasks due in the window, 4 of them `completed`. All tasks the Calendar API never returned are present (e.g. "Electroplating Unloading Basin Trolly (Rabbani)", "Electroplating door Modifications (Rabbani)", "Monthly Production Report Preparation (Rabbani)", "Sealent in BOM … (Rashikul)"). `due` has no time part (always `T00:00:00.000Z`). The recurring task "Friday Shift change message" comes back once (current instance only), not once per future Friday.

## 4. Outputs (contract for next module)
| Name | Path | Format | Schema |
|---|---|---|---|
| ToDo by person | `outputs/01-calendar-todo/todo_by_name.json` | json | Object: key = group name (canonical `Person` value, `"A F M Nasir Uddin"`, or `"No Name"`); value = array of items `{type, title}`, ordered by the item's internal date/time (never output) then by title |
| Human view | `outputs/01-calendar-todo/todo_by_name.md` | md | One `## <group>` heading per group, numbered list `N. [type] title` |

Field rules:
- `type`: `"task"` for every item from the Google Tasks API; `"event"` for every item from the Calendar API. Calendar items that are copies of tasks (`description` contains `tasks.google.com/task/`) are dropped, so each task appears once.
- `title`: the full task/event title, unchanged.
- **No date or time is output**, and **no calendar or task-list name is output** (user decisions, 2026-09-17). They are used internally only (sorting, owner rule, run summary).
- Only **open** tasks are output (`status: needsAction`); completed tasks are excluded (user decision, 2026-09-17).
- Owner rule (changed in v3.0): a task with no known name in the title goes to `"A F M Nasir Uddin"` **only if it is on the default task list ("My Tasks")**. An unnamed task on any other list, and an unnamed event, goes to `"No Name"` (user decision, 2026-09-17).
- Groups are sorted A→Z, with `"No Name"` last. No items in the window → `{}` (this is not an error).

```json
{
  "A F M Nasir Uddin": [
    {"type": "task", "title": "Land Tax works"}
  ],
  "Alice": [
    {"type": "task", "title": "Machine condition check (Alice)"},
    {"type": "event", "title": "Monthly meeting by (Alice)"},
    {"type": "task", "title": "Gate design for workshop (Bob,Alice)"}
  ],
  "Bob": [
    {"type": "task", "title": "Gate design for workshop (Bob,Alice)"}
  ],
  "No Name": [
    {"type": "task", "title": "Reducing inventory aging (Emailed on 22Aug26)"}
  ]
}
```

## 5. Scope
**In scope**
- Reading calendar IDs and the `Person` column.
- Listing calendar events for 00:00 today through 23:59:59 on today + 30 days (Asia/Dhaka), handling pagination.
- Listing open Google Tasks from **all** task lists with a due date from today through today + 30 days, handling pagination.
- One-time Google sign-in (read-only Tasks scope) with the client and token kept in the gitignored `.secrets/` folder.
- Finding person names in titles, grouping, sorting, and writing the json + md files.
- A short run summary in the terminal (events per calendar, open tasks per list, items per group, failures, skipped holiday calendars).

**Out of scope**
- Sending messages (module 02) and scheduling (module 03).
- Overdue items (due or starting before today) and tasks with no due date.
- Completed tasks.
- Changing any calendar event or task (read-only scopes/tools only).
- Event types `OUT_OF_OFFICE`, `WORKING_LOCATION`, `BIRTHDAY` (only `DEFAULT` and `FOCUS_TIME` are collected).
- Holidays: public-holiday calendars are never read, even if their ID is added to `lists.txt`.
- **Future repeats of recurring tasks.** The Tasks API only returns the current instance of a recurring task; later repeats appear on later runs once Google creates them. Known limitation, accepted by user 2026-09-17.
- The v2.0 limitation "some Google Tasks are not returned by the Calendar API" no longer applies: tasks now come from the Tasks API directly.

## 6. Requirements
| ID | Requirement (testable) |
|---|---|
| R1 | Calendar IDs are read from `lists.txt` with each line trimmed; blank and `#` lines are skipped; duplicate IDs are processed once. |
| R2 | The window is today to today + 30 days, Asia/Dhaka. Calendar events: start from 00:00:00 today to 23:59:59 on today + 30. Tasks: `due` date from today to today + 30 inclusive. Items outside it are excluded; items earlier today are included. |
| R3 | A name is taken from a `(...)` group in the title only if, after trimming, removing a leading `From ` / `By ` (case-insensitive), and splitting on `,`, the part matches a `Person` value (case-insensitive, trimmed). The output key is the `Person` value exactly as written in the contacts file. |
| R4 | A title with several matching names (in one or more `(...)` groups) is listed under each matched person, once per person. |
| R5 | A title with no matching name goes to `"No Name"`, unless R11 applies. |
| R6 | Each item has exactly these fields: `type`, `title`. No IDs, notes, descriptions, links, attendees, emails, phone numbers, dates, times, calendar names or task-list names are output. |
| R7 | If one calendar fails (404, permission error), the error is shown in the run summary and the other calendars are still processed; the files are still written. |
| R8 | Recurring calendar events show up as one item per date in the window. A recurring task shows up once (its current open instance). |
| R9 | The output is the same on repeated runs over the same data: groups A→Z with `"No Name"` last, and items ordered by internal sort time (event start; task due date at 00:00 Asia/Dhaka) then by title. |
| R10 | `.md` and `.json` contain the same groups and item counts. |
| R11 | A task with no known name in the title goes to `"A F M Nasir Uddin"` only if it is on the default task list (identified by the API's `@default` list ID, so renaming "My Tasks" doesn't break it). Unnamed tasks on other lists and unnamed events go to `"No Name"`. If a known name is found, the item goes only to the names found. |
| R12 | Holiday calendars are skipped: any calendar whose ID contains `#holiday@group.v.calendar.google.com` is dropped in step 1 and reported as "skipped (holiday)" in the run summary. No holiday item appears in the output. |
| R13 | Tasks come from all task lists via the Tasks API. Calendar items whose `description` contains `tasks.google.com/task/` are dropped, so no task appears twice. |
| R14 | Only open tasks (`status: needsAction`) are output; completed tasks are excluded. |
| R15 | If the Tasks API step fails (missing credentials, sign-in refused, API disabled, network/auth error), the run stops with a clear message and **does not write or overwrite** the output files — a half-complete list must never reach module 02. |
| R16 | OAuth client and token live only under `.secrets/` (gitignored); their contents never appear in the output files, the run summary, or any committed file. |

## 7. Steps / Design
1. **Read calendar IDs** (R1, R12): file read of `src/calendarLinks/lists.txt` → trim each line → drop blanks and `#` lines → remove duplicates → drop holiday calendar IDs containing `#holiday@group.v.calendar.google.com` and note them as "skipped (holiday)".
2. **Read known persons** (R3): file read of `src/messageListNDetails/WhatsappContacts.txt` → split lines on tab → find the `Person` column by its header → keep only that column, trimmed and non-empty. Build a lookup `lowercase → canonical`. Do not keep, print or log the other columns. If the file is missing, warn and continue with an empty list.
3. **Compute the window** (R2): today = current date in Asia/Dhaka. Events: `startTime = <today>T00:00:00+06:00`, `endTime = <today+30>T23:59:59+06:00`. Tasks: due dates `<today>` … `<today+30>` inclusive.
4. **Fetch tasks** (R2, R11, R13, R14, R15, R16): run `python .claude/skills/01-calendar-todo/fetch_tasks.py --today <today>`. The script:
   - Loads `.secrets/google/credentials.json`; if missing, copies the newest `.secrets/downloads/client_secret*.json` there; if none, exits non-zero.
   - Loads/refreshes `.secrets/google/token.json`; if missing or refresh fails, opens the browser sign-in (scope `https://www.googleapis.com/auth/tasks.readonly`) and saves the new token.
   - Gets the default list ID with `tasklists.get("@default")`, then for every list from `tasklists.list` (following `nextPageToken`) calls `tasks.list` with `showCompleted=false`, `showHidden=false`, `showDeleted=false`, `dueMin=<today>T00:00:00Z`, `dueMax=<today+30>T23:59:59Z`, `maxResults=100`, following `nextPageToken`.
   - Keeps a task only if `status == "needsAction"` and `due[:10]` is within the window (double-check of the API filter).
   - Prints JSON to stdout (UTF-8): `{"tasks": [{"list", "is_default", "title", "due"}, ...]}`. Never prints credential or token contents.
   - Any failure → message to stderr and non-zero exit. **On non-zero exit, stop the whole run here without writing outputs (R15).**
5. **Resolve calendar names** — MCP `list_calendars` → map `id → summary`. An ID not in the list is marked as failed (R7).
6. **Fetch calendar events** (R2, R7, R8): for each calendar, call MCP `list_events` with `calendarId`, `startTime`, `endTime`, `timeZone: "Asia/Dhaka"`, `orderBy: "startTime"`, `pageSize: 250`, `eventType: ["DEFAULT","FOCUS_TIME"]`. Follow `nextPageToken`. Skip `status: "cancelled"`. **Drop items whose `description` contains `tasks.google.com/task/`** (task copies — R13). If one calendar errors, record it and continue.
7. **Map items** (R6, R9): events → `{type: "event", title: summary, sort: start (dateTime or date, as Asia/Dhaka time), source: calendar name}`; tasks → `{type: "task", title, sort: due date at 00:00 Asia/Dhaka, source: list title, is_default}`. `sort`, `source` and `is_default` are internal only.
8. **Find names** (R3, R4, R5, R11): regex `\(([^()]*)\)` over the title → for each group: trim, remove a leading `^(from|by)\s+` (case-insensitive), split on `,`, trim each part, look up in the persons map. Collect the unique canonical names in order of appearance. If none match → `["A F M Nasir Uddin"]` if `type == "task"` and `is_default` is true; otherwise `["No Name"]`.
9. **Group and sort** (R4, R9): add the item to each group found → sort items by (`sort`, `title`) → sort group keys A→Z (case-insensitive) with `"No Name"` last.
10. **Write outputs** (R6, R10): keep only `{type, title}` per item → write `outputs/01-calendar-todo/todo_by_name.json` (UTF-8, 2-space indent, non-ASCII kept) and `todo_by_name.md` from the same data. Overwrite the previous run.
11. **Print summary** (R7, R12, R16): events per calendar, open tasks per list, items per group, failed calendars, skipped holiday calendars. No secrets.

Configuration / environment:
| Name | Purpose | Where set |
|---|---|---|
| Calendar IDs | Which calendars to read | `src/calendarLinks/lists.txt` |
| Person list | Names that are recognized | `Person` column of `src/messageListNDetails/WhatsappContacts.txt` |
| Google OAuth client | Tasks API access (Desktop app client, Tasks API enabled in Google Cloud) | `.secrets/google/credentials.json` (gitignored) |
| Google token | Saved sign-in, read-only Tasks scope | `.secrets/google/token.json` (gitignored, created on first run) |
| Python packages | `google-api-python-client`, `google-auth-oauthlib` | `pip install --user` |
| Timezone | `Asia/Dhaka` (fixed) | this document §7 step 3 |

## 8. Validation Checklist
- [x] All frontmatter fields filled
- [x] No unfilled placeholders left
- [x] §3 Inputs match upstream §4 Outputs (or source file actually exists)
- [x] Every R# in §6 is implemented by at least one step in §7
- [x] Every R# in §6 is covered by at least one test in §9
- [x] No secrets or real phone numbers written in this file
- [x] §12 Troubleshooting filled for known failure points

Validation result: pass (v3.0 — 16 requirements, each in §7 and §9; no emails, phone numbers or secrets; frontmatter complete) — checked by Claude on 2026-09-17 — approved by user: yes (2026-09-17 — "yes, promote to testMD and run tests")

## 9. Test Cases
| ID | Covers | Input / Setup | Expected | Result | Evidence |
|---|---|---|---|---|---|
| T1 | R1 | Real `lists.txt` (lines start with a space); scratch copy with blank line, `#` comment, duplicate | 2 unique IDs from the real file; duplicates/comments/blanks dropped in the scratch copy | pass | Real lists.txt (leading spaces) → 2 IDs, both fetched. Scratch list (spaces, blank, `#` comment, duplicate, bad ID, holiday) → cal1 processed once, cal2 once, 1 failed, 1 holiday skipped |
| T2 | R2 | Real run; compare with the Google Calendar Schedule view and Tasks board for today → today+30 | Every open task and event in the UI is in the output, including "Electroplating Unloading Basin Trolly (Rabbani)" and the other 5 tasks missing in v2.0; nothing due/starting before today or after today+30 | pass | Real run 2026-09-17: Tasks API across all 10 lists → 27 due in window, 23 open (4 completed excluded) + 1 calendar event = 24 unique items. All 4 tasks missing in v2.0 now present (Electroplating Unloading Basin Trolly, Electroplating door Modifications, Monthly Production Report Preparation → Rabbani; Sealent in BOM → Rashikul). Checked against the user's Calendar Schedule screenshot for Sep 19–21: 17/17 open items present; "Office" working-location banners excluded by design. Synthetic: event 23:59 yesterday, event 00:00 on today+31, cancelled event, task due yesterday, task due today+31 all excluded; task due today and task due today+30 included |
| T3 | R3 | Titles `(From Alice)`, `-(Alice da)` with `Person` = `Alice da`, `(alice)` | Grouped under the canonical `Person` spelling | pass | `(From Alice)` → Alice, `-(Alice da)` → `Alice da`, `(bob)` and `(By BOB )` → Bob. Real: `(From Rashikul)` → Rashikul |
| T4 | R4 | Title `(Bob,Alice)` and title `(Alice) (SOP, HT)` | First under both Bob and Alice; second only under Alice | pass | `(Bob,Alice)` under both; `(Alice) (SOP, HT)` only Alice; `(Alice)(alice)` listed once. Real: `(Sajib,Rabbani)` → Rabbani only (Sajib not a Person); `(Mamun) (SOP, HT)` → Mamun |
| T5 | R5 | Unnamed `event`; unnamed task on a non-default list (e.g. `(Emailed on 22Aug26)`); Bengali word in parentheses on a non-default list | All in `"No Name"` | pass | Synthetic No Name = [unnamed event, `(Emailed on 22Aug26)` task on non-default list, Bengali-parentheses task on non-default list], `No Name` last. Real No Name: Megisto (Sanjoy), Cost center (Sowpon da), Reducing Inventory Aging — all non-default lists, names not in Person column |
| T6 | R6 | Real run | Every item has exactly the keys `type`, `title`; no date/time/calendar/list name/notes/links; no `@` or phone digits in either file | pass | Real (24 items) and synthetic: every item keys == [`title`,`type`]; real json+md contain no `@`, no 10+ digit number, no `YYYY-MM-DD` date, no calendar name, no task-list name |
| T7 | R7 | Scratch `lists.txt` with one invalid calendar ID | Invalid ID shown as failed; tasks and valid calendar events still written | pass | `bad-cal` → `FAILED calendar bad-cal: not in list_calendars`; tasks and cal1 events still written |
| T8 | R8 | Real run | Monthly recurring meeting appears once per date in the window; "Friday Shift change message" (recurring task) appears at most once | pass | Real: monthly meeting appears once (only date in window, 2026-10-07); recurring task "Friday Shift change message" appears 0 times (current instance completed → excluded), i.e. at most once. Synthetic: recurring event on 2 dates → 2 items |
| T9 | R9 | Two runs with no data changes | Byte-identical json; `"No Name"` last; tasks and events interleaved by date then title | pass | Two real runs → identical json sha256 `08fb249d5a31fbc0…`. Group order A F M Nasir Uddin, Mamun, Rabbani, Rashikul, Robiul, No Name. Synthetic Alice order: task due Sep 17 (00:00) before event Sep 17 08:00, then Sep 18 items by title, then Sep 24 event. Note: first synthetic assertion expected the 08:00 event before the same-day task — that expectation contradicted R9 (tasks sort at 00:00); the test was corrected, the code was not changed |
| T10 | R10 | Real run | Same groups and counts in md and json; md lines are `N. [type] title` | pass | Real and synthetic: md headings == json keys in order; numbered `N. [type] title` line count per group == json item count |
| T11 | R2 | Synthetic: no events and no open tasks in the window | `{}` written, no error | pass | No events + no open tasks → `{}
` written, exit 0 |
| T12 | R11 | Real: "Land Tax works" (My Tasks, no name). Real: "Reducing 1-Year+ Inventory Aging (Emailed on 22Aug26)" (Inventory-RM, no name). Synthetic: named task on the default list; default list with a renamed title | Land Tax → `A F M Nasir Uddin`; Inventory Aging → `No Name`; named default-list task → only that name; renamed default list still → owner | pass | Real: "Land Tax works" (My Tasks, default) → A F M Nasir Uddin; "Reducing 1-Year+ Inventory Aging (Emailed on 22Aug26)" (Inventory-RM) → No Name. Real default detection: fetch_tasks marked only `My Tasks` as `is_default: true`. Synthetic: `Call (bob)` on default list → Bob only; unnamed task on default list titled `Renamed` → owner |
| T13 | R12 | Scratch `lists.txt` with the Bangladesh holiday calendar ID | "skipped (holiday)"; no holiday items; others processed | pass | Holiday ID in scratch lists.txt → `skipped (holiday): en.bd#holiday@group.v.calendar.google.com`, never fetched |
| T14 | R13 | Real run: "Electroplating gate Design for workshop (Sajib,Rabbani)" exists in both the Calendar API and the Tasks API | Appears exactly once in `Rabbani`, as `type: task` | pass | Real: the Calendar API returned 20 task copies (incl. "Electroplating gate Design for workshop (Sajib,Rabbani)"); all dropped. That title appears exactly once in the output (Rabbani, `type: task`). Synthetic task-copy event with `(Bob,Alice)` absent from output |
| T15 | R14 | Real run: "CH(4) parts breaking Investigation (Robiul)" is completed | Not in the output | pass | Real: completed "CH(4) parts breaking Investigation (Robiul)", "Short leave apply…", "Rolling 66 Machine condition check (Robiul)", "Friday Shift change message…" all absent (the Rolling 66 Calendar copy was also dropped by R13) |
| T16 | R15 | Scratch run with the credentials path pointing to an empty folder (no credentials, no downloads) and an existing output file | Non-zero exit with a clear message; existing output files unchanged (same hash) | pass | fetch_tasks.py with an empty secrets folder → exit 1, empty stdout, stderr `fetch_tasks: missing …credentials.json and no client_secret*.json in …downloads`; build not run; existing output json sha256 unchanged |
| T17 | R16 | `git check-ignore -v` on `.secrets/google/credentials.json`, `token.json`, `.secrets/downloads/client_secret_x.json`; grep outputs + run summary for `client_secret`, `refresh_token`, `access_token` | All three ignored; no matches | pass | `git check-ignore` → all 3 `.secrets/` paths ignored (rule `.secrets/`). Real json, md and run summary contain no `client_secret`, `client_id`, `refresh_token`, `access_token` |

Test data rule: prefer real items that match each pattern. If a pattern has no real example, use synthetic fixtures, or create a temporary test task/event and delete it afterwards — **only with user approval**.

Test result: pass, 17/17 (T1–T17) — real run (live Tasks API: 23 open tasks from 10 lists; Calendar connector: 21 items, 20 task copies dropped, 1 event) + synthetic fixtures (13 tasks, 7 events, bad/empty/holiday calendars, missing credentials). One test expectation (T9 synthetic) was wrong and corrected; no code changes were needed — run on 2026-09-17 — approved by user: yes (2026-09-17)

## 10. Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| A person isn't in the `Person` column yet | Their items end up in `"No Name"` | Run summary shows the `"No Name"` count; add the person to contacts |
| Google refresh token expires (OAuth app left in "Testing" publishing status → tokens expire after 7 days) | Unattended runs (module 03) fail at step 4 | R15 stops the run safely; set the OAuth consent screen to Internal (Workspace) or publish it; otherwise re-sign-in weekly |
| Tasks API fails mid-run | Output would lack all tasks | R15: abort before writing, previous output kept and error shown |
| Stale output after an aborted run | Module 02 could send yesterday's list | Run summary makes the failure obvious; module 02/03 plans must check the run succeeded before sending |
| Secrets committed | Google client/token leak | `.secrets/` gitignored (T17); pre-commit `git diff --cached --name-only` check |
| Recurring tasks show only the current instance | Future repeats missing from this snapshot | Documented limitation (§5); they appear on later runs |
| Task due time not available from the API | Tasks sort by date only | No effect on output (no times are output) |
| Calendar task-copy marker changes | Tasks appear twice | T14 checks it; troubleshooting row |
| Tasks with no due date are ignored | Undated to-dos never messaged | Out of scope (§5); give tasks a due date |
| More than 100 tasks per list / 250 events per calendar | Items missing | Follow `nextPageToken` (steps 4, 6) |
| Module reads the private contacts file | Numbers could leak | Only the `Person` column is read; T6 greps for phone digits |
| CLAUDE.md fixed decisions describe v2.0 (owner rule, Tasks limitation) | Docs disagree with behavior | Update CLAUDE.md "Fixed decisions" in the same commit as the stable release |

## 11. Change Log
| Version | Date | Phase | Change | Reason |
|---|---|---|---|---|
| 0.1 | 2026-09-17 | draft | Initial draft | Explore showed titles with non-name `(...)` groups; user chose the known-names rule and a start-of-today window; added task-owner fallback (R11), holiday exclusion (R12); documented the Calendar-API task coverage gap, accepted for v0.1 |
| 1.0 | 2026-09-17 | draft | Removed `start`/`end` from the output contract (MAJOR) | User: "Do not use start and end (date and time). So output file does not have Date and time" |
| 2.0 | 2026-09-17 | draft | Removed `calendar` from the output contract (MAJOR) | User: "Do not use \"calendar\". So output file does not have calendar info" |
| 3.0 | 2026-09-17 | draft | Tasks now read from the Google Tasks API (all lists) instead of the Calendar API; calendar task copies dropped (R13); completed tasks excluded (R14); owner rule now "unnamed task on the default My Tasks list" instead of "any unnamed task" (R11 — §4 field rule changed, MAJOR); Tasks failure aborts without writing (R15); secrets kept in gitignored `.secrets/` (R16). R2, R5, R6, R8, R9 reworded; steps rewritten; T14–T17 added, all tests reset to pending | User: "why 'Electroplating Unloading Basin Trolly (Rabbani)' is missing in output" → chose option 1 (Google Tasks connector), Python + Tasks API, "My Tasks list only", "Exclude completed", "Accept" recurring-task limitation |

## 12. Troubleshooting & Rollback
| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| No calendars found / 404 | Wrong ID in `lists.txt`, or the calendar isn't shared with the connected account | `list_calendars` output vs IDs | Fix the ID or share the calendar |
| Calendar auth / permission error | Google Calendar connector disconnected | Try `list_calendars` | Reconnect the connector |
| `Missing …credentials.json` | OAuth client not downloaded | Look in `.secrets/downloads/` and `.secrets/google/` | Create a Desktop OAuth client in Google Cloud Console, save the JSON into `.secrets/downloads/` |
| `invalid_grant` / `Token has been expired or revoked` | Refresh token expired (Testing-status app: 7 days) or access revoked | Error text from step 4 | Delete `.secrets/google/token.json`, run again, sign in; consider Internal/published consent screen |
| `403 accessNotConfigured` / "Tasks API has not been used" | Tasks API not enabled in the Cloud project | Error text | Enable Google Tasks API in APIs & Services → Library |
| `access_denied` in the browser sign-in | Account not added as a test user (External, Testing) | Browser error page | Add the account under OAuth consent screen → Test users |
| `ModuleNotFoundError: googleapiclient` | Python packages not installed | Error text | `python -m pip install --user google-api-python-client google-auth-oauthlib` |
| Everything in "No Name" | Contacts file missing, `Person` header renamed, or titles lack `(Name)` | Run summary warning; header row | Restore the file/header; fix titles |
| A known person's item in "No Name" | Title spelling differs from the `Person` value, or an unhandled prefix | Compare the title with the `Person` column | Fix the title or contact spelling; add the prefix to step 8 (MINOR) |
| Owner's unnamed task in "No Name" | Task is on a list other than the default list (R11) | Check the list in Google Tasks | Expected — move it to My Tasks, or add a name in the title |
| A task appears twice | Calendar task-copy marker changed, so step 6 no longer drops copies | Look at a raw event `description` | Update the marker in step 6 (MINOR) |
| A task is missing | Completed, no due date, due before today / after today+30, or it's a future repeat of a recurring task | Check the task in Google Tasks | Expected per §5; otherwise check the step 4 date filter |
| Events missing | Window/timezone wrong, pagination not followed, or event type filtered | Check `startTime`/`endTime` and `nextPageToken` | Use Asia/Dhaka start-of-today; follow pages |
| Holiday items in output | A holiday calendar ID in a different format was added | Run summary for "skipped (holiday)" | Remove the ID, or extend the holiday match in step 1 (MINOR) |
| Empty output `{}` | No open tasks or events in the window | Calendar UI / Tasks board | Expected |

**Rollback:** `git checkout 01-calendar-todo/v2.0 -- .claude/skills/01-calendar-todo plan/stableMD/01-calendar-todo.stable.md` (v2.0 needs no `.secrets/`; tasks come from the Calendar API with its known gap).
See also `docs/TROUBLESHOOTING.md#01-calendar-todo`.
