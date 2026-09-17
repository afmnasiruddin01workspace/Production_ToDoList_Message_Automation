---
name: 01-calendar-todo
description: Pull open Google Tasks (all task lists, via the Tasks API) and Google Calendar events (calendars in src/calendarLinks/lists.txt) for today through today+30 days, group them by the person named in the title, and write outputs/01-calendar-todo/todo_by_name.json (+ .md) for module 02.
---

# 01 — Calendar ToDo by Person

Source of truth: `plan/stableMD/01-calendar-todo.stable.md` (v3.0). This skill is a summary of its §7 Steps — if they ever disagree, the stable plan wins and this file needs updating.

## When to use
Run this at the start of the pipeline, before module 02 (whatsapp-message). Re-run any time you need a fresh to-do snapshot; it always overwrites the previous output.

## One-time setup
- Python packages: `python -m pip install --user google-api-python-client google-auth-oauthlib`
- Google Cloud: enable **Google Tasks API**, create an **OAuth client ID (Desktop app)**, download the JSON into `.secrets/downloads/` (the script copies it to `.secrets/google/credentials.json`).
- First run opens a browser sign-in (read-only Tasks scope) and saves `.secrets/google/token.json`.
- `.secrets/` is gitignored. **Never print, copy or commit anything from it.**

## Steps

1. **Read calendar IDs** — read `src/calendarLinks/lists.txt` line by line. Trim each line. Skip blank lines and lines starting with `#`. Drop duplicates. Drop any ID containing `#holiday@group.v.calendar.google.com` (note it as "skipped (holiday)").

2. **Read known persons** — read `src/messageListNDetails/WhatsappContacts.txt` (tab-separated, header row). Find the `Person` column by its header and keep only that column, trimmed. **Never read or print the `WhatsApp contact name` or `Number` columns.** Build a lowercase → canonical lookup. If the file is missing, warn and continue with an empty list.

3. **Compute the window** — today = current date in Asia/Dhaka. Events: `startTime` = today `00:00:00+06:00`, `endTime` = today+30 `23:59:59+06:00`. Tasks: due date today … today+30 inclusive.

4. **Fetch tasks** — run:
   ```
   python .claude/skills/01-calendar-todo/fetch_tasks.py --today <YYYY-MM-DD>
   ```
   It prints `{"tasks": [{"list", "is_default", "title", "due"}, ...]}` — open tasks only, all lists, due in the window. `is_default` is true for the default ("My Tasks") list, detected by ID so a rename doesn't break it.
   **If it exits non-zero, STOP the whole run. Do not write or overwrite any output file.** Show the stderr message and use the troubleshooting table.

5. **Resolve calendar names** — call `list_calendars` and map each ID to its display name. An ID from step 1 that isn't in the list is a failed calendar (report it, keep going).

6. **Fetch calendar events** — for each calendar, call `list_events` with `calendarId`, `startTime`, `endTime`, `timeZone: "Asia/Dhaka"`, `orderBy: "startTime"`, `pageSize: 250`, `eventType: ["DEFAULT", "FOCUS_TIME"]`. Follow `nextPageToken`. Skip `status: "cancelled"`. **Drop every item whose `description` contains `tasks.google.com/task/`** — it is a Calendar copy of a task already fetched in step 4. If a calendar errors, record it and continue.

7. **Map items** — tasks → `type: "task"`, `title`, internal sort = due date at 00:00 Asia/Dhaka, internal `is_default`. Events → `type: "event"`, `title` = `summary`, internal sort = start (`dateTime` or `date`). List names, calendar names, dates and times are internal only.

8. **Find the group(s)** for each item —
   - Find every `(...)` group in the title. For each: trim, strip a leading `From `/`By ` (case-insensitive), split on `,`, trim each part, look it up (case-insensitive) in the persons map.
   - Collect unique canonical names in order of appearance → the item belongs to each.
   - None matched **and** it's a task on the default list → `"A F M Nasir Uddin"`.
   - Otherwise none matched → `"No Name"`.

9. **Group and sort** — sort each group's items by (internal sort, `title`). Sort group keys A→Z (case-insensitive), `"No Name"` always last.

10. **Write outputs** — keep only `{type, title}` per item.
    - `outputs/01-calendar-todo/todo_by_name.json`: `{ "<group>": [ {type, title}, ... ] }`, UTF-8, 2-space indent, non-ASCII kept (don't escape Bengali). No items → `{}` (not an error).
    - `outputs/01-calendar-todo/todo_by_name.md`: `# ToDo by person`, then one `## <group>` per group in the same order, numbered `N. [type] title`.
    - Both overwrite the previous run.

11. **Print a run summary** — events per calendar, open tasks per list, items per group, failed calendars, skipped holiday calendars. No secrets.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **Recurring tasks** show only their current open instance; future repeats appear on later runs once Google creates them.
- Completed tasks, tasks with no due date, and overdue items (before today) are not included.
- Tasks API gives no due time, so tasks sort at 00:00 of their due day (before same-day events). No times are output anyway.
- An unnamed task on any list other than "My Tasks" goes to `"No Name"`.
- A name is only recognized if it matches (case-insensitive) a `Person` value in `WhatsappContacts.txt`.
- If the OAuth consent screen is External + Testing, the Google sign-in expires every 7 days.

## If it fails
See `docs/TROUBLESHOOTING.md#01-calendar-todo` for symptom → cause → fix. Common ones: missing `credentials.json`, `invalid_grant` (delete `.secrets/google/token.json` and sign in again), Tasks API not enabled (403), calendar connector disconnected, a known person still in "No Name" (spelling mismatch), a task appearing twice (task-copy marker changed).
