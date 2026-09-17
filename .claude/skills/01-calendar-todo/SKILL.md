---
name: 01-calendar-todo
description: Pull Google Calendar events and tasks from the calendars in src/calendarLinks/lists.txt for today through today+30 days, group them by the person named in the title, and write outputs/01-calendar-todo/todo_by_name.json (+ .md) for module 02.
---

# 01 — Calendar ToDo by Person

Source of truth: `plan/stableMD/01-calendar-todo.stable.md` (v0.1). This skill is a summary of its §7 Steps — if they ever disagree, the stable plan wins and this file needs updating.

## When to use
Run this at the start of the pipeline, before module 02 (whatsapp-message). Re-run any time you need a fresh to-do snapshot; it always overwrites the previous output.

## Steps

1. **Read calendar IDs** — read `src/calendarLinks/lists.txt` line by line. Trim each line. Skip blank lines and lines starting with `#`. Drop duplicates. Drop any ID containing `#holiday@group.v.calendar.google.com` (note it as "skipped (holiday)" — holiday calendars are never read, even if added to this file).

2. **Read known persons** — read `src/messageListNDetails/WhatsappContacts.txt` (tab-separated, header row). Find the `Person` column by its header and keep only that column, trimmed. **Never read or print the `WhatsApp contact name` or `Number` columns.** Build a lowercase → canonical lookup. If the file is missing, warn and continue with an empty list.

3. **Resolve calendar names** — call `list_calendars` and map each ID to its display name (`summary`). An ID from step 1 that isn't in this list is a failed calendar (report it, keep processing the rest).

4. **Compute the window** — `startTime` = today at `00:00:00+06:00`, `endTime` = today+30 days at `23:59:59+06:00`, timezone `Asia/Dhaka`. "Today" is the current date in Asia/Dhaka.

5. **Fetch items** — for each remaining calendar ID, call `list_events` with `calendarId`, `startTime`, `endTime`, `timeZone: "Asia/Dhaka"`, `orderBy: "startTime"`, `pageSize: 250`, `eventType: ["DEFAULT", "FOCUS_TIME"]`. Follow `nextPageToken` if present. Skip items with `status: "cancelled"`. If a calendar's call errors, record the error and continue with the others.

6. **Map each item** —
   - `type`: `"task"` if `description` contains `tasks.google.com/task/`, else `"event"`.
   - `title`: the raw `summary`.
   - `start` / `end`: `dateTime` if present, else `date`.
   - `calendar`: the display name from step 3.

7. **Find the group(s)** for each item —
   - Find every `(...)` group in the title. For each: trim it, strip a leading `From `/`By ` (case-insensitive), split on `,`, trim each part, and look it up (case-insensitive) against the persons map from step 2.
   - Collect the unique canonical names found, in order of appearance.
   - If at least one matched → the item belongs to each of those groups.
   - If none matched **and** `type` is `"task"` → the item belongs to `"A F M Nasir Uddin"` (owner fallback — the module can't tell which Tasks list an item is from, so an unnamed task is assumed to be the user's own).
   - If none matched and it's an `"event"` → `"No Name"`.

8. **Group and sort** — sort each group's items by (`start`, `title`). Sort the group keys A→Z (case-insensitive), with `"No Name"` always last.

9. **Write outputs** —
   - `outputs/01-calendar-todo/todo_by_name.json`: `{ "<group>": [ {type, title, start, end, calendar}, ... ] }`, UTF-8, 2-space indent, non-ASCII characters kept as-is (don't escape Bengali text). No items anywhere → write `{}` (this is not an error).
   - `outputs/01-calendar-todo/todo_by_name.md`: one `## <group>` heading per group in the same order, numbered list `N. [type] title — start → end`.
   - Both files overwrite whatever was there before.

10. **Print a run summary** — items fetched per calendar, items per group, any failed calendars, any skipped holiday calendars.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **Google Tasks coverage gap:** some real Google Tasks never come back from `list_events`, regardless of which Tasks list they're on, their due date, due time, or recurrence — verified directly against the connector with no consistent rule found. This is a Calendar API limitation, not a bug in this skill. If a to-do the user knows about is missing, this is the most likely reason — see `docs/TROUBLESHOOTING.md#01-calendar-todo`.
- Only `DEFAULT` and `FOCUS_TIME` event types are collected (no `OUT_OF_OFFICE`, `WORKING_LOCATION`, `BIRTHDAY`).
- A name is only recognized if it exactly matches (case-insensitive) a `Person` value already in `WhatsappContacts.txt`. Add the person there first if their items should stop going to "No Name".

## If it fails
See `docs/TROUBLESHOOTING.md#01-calendar-todo` for symptom → cause → fix. Common ones: no calendars found (wrong ID or not shared), connector disconnected, everything in "No Name" (contacts file/header issue), a known person still in "No Name" (spelling mismatch), items missing (window/timezone or the Tasks coverage gap above).
