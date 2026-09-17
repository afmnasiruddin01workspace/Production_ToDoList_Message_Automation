---
module: calendar-todo
id: "01"
phase: stable
version: "0.1"
depends_on: []
input_from: src/calendarLinks/lists.txt + src/messageListNDetails/WhatsappContacts.txt (Person column only)
output_to: outputs/01-calendar-todo/
approved_by: A F M Nasir Uddin (2026-09-17 — "are all the testing validations passes? then go")
date: 2026-09-17
---

# 01 — Calendar ToDo by Person

## 1. Frontmatter
See YAML header above. Every field must be filled before validation.

## 2. Goal
Collect every Google Calendar event and Google Task for today through today + 30 days from the calendars in `lists.txt`, and group them by the responsible person named in the title, so module 02 can send each person a numbered to-do list.

## 3. Inputs
| Name | Path / Source | Format | Schema / Columns | Private? |
|---|---|---|---|---|
| Calendar IDs | `src/calendarLinks/lists.txt` | txt | One calendar ID per line; lines are trimmed; blank lines and lines starting with `#` are skipped | yes |
| Known persons | `src/messageListNDetails/WhatsappContacts.txt` | tsv (header row) | Only column `Person` is read; `WhatsApp contact name` and `Number` are never read or copied | yes |
| Calendar items | MCP `list_events` (Google Calendar connector) | json | `summary`, `eventType`, `description` (checked for the task-link marker only), `start`, `end`, `status`, `recurringEventId` | yes |

> Explore findings (2026-09-17): calendar #1 had 21 items (20 Google Tasks shown as `eventType: FOCUS_TIME` with a `tasks.google.com/task/` link in `description`; 1 recurring `DEFAULT` meeting already split into single dates by the connector). Calendar #2 had 0 items. Every line in `lists.txt` starts with a space.

## 4. Outputs (contract for next module)
| Name | Path | Format | Schema |
|---|---|---|---|
| ToDo by person | `outputs/01-calendar-todo/todo_by_name.json` | json | Object: key = group name (canonical `Person` value or `"No Name"`); value = array of items `{type, title, start, end, calendar}` sorted by `start` then `title` |
| Human view | `outputs/01-calendar-todo/todo_by_name.md` | md | One `## <group>` heading per group, numbered list `N. [type] title — start → end` |

Field rules:
- `type`: `"task"` if `description` contains `tasks.google.com/task/`, otherwise `"event"`.
- `title`: the full `summary`, unchanged.
- `start` / `end`: ISO 8601 with `+06:00` for timed items; `YYYY-MM-DD` for all-day items.
- `calendar`: the calendar `summary` (display name) returned by `list_calendars`.
- Owner fallback: this module cannot tell which Google Tasks list an item belongs to (see the limitation below), so it cannot detect "your own" tasks by list. Instead, any `type: "task"` item with no known name in the title is assumed to be yours: the group is `"A F M Nasir Uddin"` instead of `"No Name"`.
- Groups are sorted A→Z, with `"No Name"` last. No items in the window → `{}` (this is not an error).

```json
{
  "Alice": [
    {"type": "task", "title": "Machine condition check (Alice)", "start": "2026-09-17T13:00:00+06:00", "end": "2026-09-17T13:30:00+06:00", "calendar": "calendar #1"},
    {"type": "task", "title": "Gate design for workshop (Bob,Alice)", "start": "2026-09-21T11:00:00+06:00", "end": "2026-09-21T11:30:00+06:00", "calendar": "calendar #1"}
  ],
  "Bob": [
    {"type": "task", "title": "Gate design for workshop (Bob,Alice)", "start": "2026-09-21T11:00:00+06:00", "end": "2026-09-21T11:30:00+06:00", "calendar": "calendar #1"}
  ],
  "No Name": [
    {"type": "task", "title": "Land Tax works", "start": "2026-09-23T11:00:00+06:00", "end": "2026-09-23T11:30:00+06:00", "calendar": "calendar #1"}
  ]
}
```

## 5. Scope
**In scope**
- Reading calendar IDs and the `Person` column.
- Listing events and tasks for 00:00 today through 23:59:59 on today + 30 days (Asia/Dhaka), handling pagination.
- Finding person names in titles, grouping, sorting, and writing the json + md files.
- A short run summary in the terminal (item count per calendar, count per group, calendars that failed).

**Out of scope**
- Sending messages (module 02) and scheduling (module 03).
- Overdue items from before today.
- Changing any calendar event or task.
- Event types `OUT_OF_OFFICE`, `WORKING_LOCATION`, `BIRTHDAY` (only `DEFAULT` and `FOCUS_TIME` are collected).
- Holidays: public-holiday calendars (e.g. "Holidays in Bangladesh") are never read, even if their ID is added to `lists.txt`.
- Some Google Tasks are not returned by the Calendar API at all, regardless of which Tasks list they're on. Verified 2026-09-17: four real tasks ("Short leave apply", "Electroplating Unloading Basin Trolly", "Sealent in BOM", "Monthly Production Report Preparation") returned zero results from `list_events` under every query tried (full window, full-text search, no event-type filter), while other tasks in the very same lists (Prd-SOP, Prod-Costing) *were* returned. No consistent rule (list, due date, due time, recurrence) explains the difference with the tools available here. This is an unpredictable coverage gap in the Calendar API itself, not a bug in this module's logic, and it cannot be fixed without a separate Google Tasks API connector — out of scope for v0.1. **Known limitation: some real to-do items will not appear in this module's output; spot-check against the Google Tasks/Calendar UI occasionally.**

## 6. Requirements
| ID | Requirement (testable) |
|---|---|
| R1 | Calendar IDs are read from `lists.txt` with each line trimmed; blank and `#` lines are skipped; duplicate IDs are processed once. |
| R2 | The window is 00:00:00 today to 23:59:59 on today + 30 days, Asia/Dhaka. Items outside it are excluded, and items earlier today are included. |
| R3 | A name is taken from a `(...)` group in the title only if, after trimming, removing a leading `From ` / `By ` (case-insensitive), and splitting on `,`, the part matches a `Person` value (case-insensitive, trimmed). The output key is the `Person` value exactly as written in the contacts file. |
| R4 | A title with several matching names (in one or more `(...)` groups) is listed under each matched person, once per person. |
| R5 | A title with no matching name (no parentheses, or only non-name parentheses such as dates, codes or Bengali words) goes to `"No Name"`, unless R11 applies. |
| R6 | Each item has exactly these fields: `type`, `title`, `start`, `end`, `calendar`, with `type` set as described in §4. No IDs, descriptions, attendees, emails or phone numbers are output. |
| R7 | If one calendar fails (404, permission error), the error is shown in the run summary and the other calendars are still processed; the files are still written. |
| R8 | Recurring events show up as one item per date in the window. |
| R9 | The output is the same on repeated runs over the same data: groups A→Z with `"No Name"` last, and items sorted by `start` then `title`. |
| R10 | `.md` and `.json` contain the same groups and item counts. |
| R11 | If a `type: "task"` item has no known name in the title, the item goes to the group `"A F M Nasir Uddin"` instead of `"No Name"` (this module cannot detect Tasks-list membership, so it assumes any unnamed task is the owner's). If a known name is found, the item goes only to the names found (task or not). |
| R12 | Holiday calendars are skipped: any calendar whose ID contains `#holiday@group.v.calendar.google.com` is dropped in step 1 and reported as "skipped (holiday)" in the run summary. No holiday item appears in the output. |

## 7. Steps / Design
1. **Read calendar IDs** (R1, R12): file read of `src/calendarLinks/lists.txt` → trim each line → drop blanks and `#` lines → remove duplicates → drop holiday calendar IDs containing `#holiday@group.v.calendar.google.com` and note them as "skipped (holiday)" (R12).
2. **Read known persons** (R3): file read of `src/messageListNDetails/WhatsappContacts.txt` → split lines on tab → find the `Person` column by its header → keep only that column, trimmed and non-empty. Build a lookup `lowercase → canonical`. Do not keep, print or log the other columns. If the file is missing, warn and continue with an empty list (everything goes to `"No Name"`).
3. **Resolve calendar names** — MCP `list_calendars` → map `id → summary`. An ID not in the list is marked as failed (R7), with its ID shown in the summary.
4. **Compute the window** (R2): `startTime = <today>T00:00:00+06:00`, `endTime = <today+30>T23:59:59+06:00`, where today is the current date in Asia/Dhaka.
5. **Fetch items** (R2, R7, R8): for each calendar, call MCP `list_events` with `calendarId`, `startTime`, `endTime`, `timeZone: "Asia/Dhaka"`, `orderBy: "startTime"`, `pageSize: 250`, `eventType: ["DEFAULT","FOCUS_TIME"]`. Follow `nextPageToken` until there are no more pages. Skip items with `status: "cancelled"`. If one calendar errors, record it and continue.
6. **Map items** (R6): `type` = `"task"` if `description` contains `tasks.google.com/task/`, otherwise `"event"`. `start`/`end` = `dateTime` if present, otherwise `date`. `calendar` = the name from step 3.
7. **Find names** (R3, R4, R5, R11): regex `\(([^()]*)\)` over the title → for each group: trim, remove a leading `^(from|by)\s+` (case-insensitive), split on `,`, trim each part, look up in the persons map. Collect the unique canonical names in order of appearance. If none match → `["A F M Nasir Uddin"]` if `type` is `"task"` (R11 — assume an unnamed task is the owner's); otherwise → `["No Name"]`.
8. **Group and sort** (R4, R9): add the item to each group found → sort items by (`start`, `title`) → sort group keys A→Z with `"No Name"` last.
9. **Write outputs** (R10): write `outputs/01-calendar-todo/todo_by_name.json` (UTF-8, 2-space indent, `ensure_ascii` off so Bengali text stays readable) and `todo_by_name.md` from the same data. Overwrite the previous run.
10. **Print summary** (R7, R12): items per calendar, items per group, failed calendars, skipped holiday calendars.

Configuration / environment variables:
| Name | Purpose | Where set |
|---|---|---|
| Calendar IDs | Which calendars to read | `src/calendarLinks/lists.txt` |
| Person list | Names that are recognized | `Person` column of `src/messageListNDetails/WhatsappContacts.txt` |
| Timezone | `Asia/Dhaka` (fixed) | this document §7 step 4 |

## 8. Validation Checklist
- [x] All frontmatter fields filled
- [x] No unfilled placeholders left
- [x] §3 Inputs match upstream §4 Outputs (or source file actually exists)
- [x] Every R# in §6 is implemented by at least one step in §7
- [x] Every R# in §6 is covered by at least one test in §9
- [x] No secrets or real phone numbers written in this file
- [x] §12 Troubleshooting filled for known failure points

Validation result: pass (re-checked after the "My Tasks only" theory was disproved and replaced with the verified coverage-gap finding) — checked by Claude on 2026-09-17 — approved by user: yes (2026-09-17)

## 9. Test Cases
| ID | Covers | Input / Setup | Expected | Result | Evidence |
|---|---|---|---|---|---|
| T1 | R1 | Current `lists.txt` (lines start with a space) plus an extra blank line in a scratch copy | 2 unique IDs parsed | pass | Real lists.txt (lines start with a space) → 2 IDs. Scratch list with spaces, blank line, `#` comment, duplicate → 3 unique IDs + 1 holiday skipped |
| T2 | R2 | Real run; compare with Google Calendar UI for 00:00 today → today+30 | Same item count as the UI; an item earlier today is included; nothing after today+30 | pass, with a documented gap | Window logic verified against fixtures (yesterday/today/today+30/UTC conversion all correct). The connector returns 21 DEFAULT/FOCUS_TIME items for calendar #1 in this window — matches the module's own count exactly. The user counted more (29+) in the Calendar UI; the difference is (a) "Office" WORKING_LOCATION banners and a holiday overlay, both excluded by design (§5, R12), and (b) an unpredictable Calendar-API coverage gap — some real tasks never come back from `list_events` under any query (confirmed with 4 real titles across 3 different Tasks lists), while other tasks in the very same lists are returned. No consistent rule was found; this is a Calendar-API limitation, not a bug in this module — see §5 Scope and §10 Risks. User accepted this gap for v0.1. |
| T3 | R3 | Titles like `(From Alice)`, `-(Alice da)` where the `Person` value is `Alice da`, `(alice)` | Grouped under the canonical `Person` spelling | pass | `(From Alice)`, `(alice)`, `(By BOB )` → canonical `Alice` / `Bob`; `-(Alice da)` → `Alice da` |
| T4 | R4 | Title `(Bob,Alice)` and title `(Alice) (SOP, HT)` | First under both Bob and Alice; second only under Alice | pass | `(Bob,Alice)` under both; `(Alice) (SOP, HT)` only under Alice; `(Alice)(alice)` listed once. Real: `(Sajib,Rabbani)` → Rabbani, `(Mamun) (SOP, HT)` → Mamun |
| T5 | R5 | Titles with no parentheses, `(Emailed on 22Aug26)`, a Bengali word in parentheses | All in `"No Name"` | pass | Fixture: no parens, `(Emailed on 22Aug26)`, Bengali word, unknown name → No Name. Real: no items left in No Name after R11 fix (all were tasks → owner) |
| T6 | R6 | Real run | Every item has exactly 5 keys; `type` = `task` for Tasks and `event` for the recurring meeting; no `@` or phone digits in the output | pass | Real run: every item has exactly 5 keys; Tasks = `task`, recurring meeting = `event`; no `@` in titles and no 10+ digit runs in the json |
| T7 | R7 | Scratch `lists.txt` with one invalid ID added | Invalid ID shown as failed; valid calendar items still written | pass | Bad ID → `FAILED calendar …: The requested event could not be found or has been deleted.` (real connector message); the other calendars were still written |
| T8 | R8 | Real run | The recurring meeting shows up once per date in the window | pass | Real run: monthly meeting appears once, on 2026-10-07 (only date in the window); connector already splits repeats |
| T9 | R9 | Run twice with no calendar changes | Both json files are byte-identical; `"No Name"` last | pass | Two runs → identical sha256; `No Name` last (when present) |
| T10 | R10 | Real run | Group and item counts in md = json | pass | Real run: md headings = json keys in the same order; 21 items in both |
| T11 | R1, R2 | Calendar with 0 items (calendar #2 only) | `{}` written, no error | pass | Real calendar #2 had 0 items; empty-only list → `{}`, no error |
| T12 | R11 | Real run: 5 no-name Tasks (e.g. "Land Tax works") now grouped as owner. Synthetic: (a) task, no name; (b) task, `(Alice)` in title; (c) task, no name, plain description with no special text | (a) → `A F M Nasir Uddin`; (b) → `Alice` only; (c) → `A F M Nasir Uddin` (type-based, not text-based) | pass | real/out3/todo_by_name.json (owner group has 5 items) + syn/out2/todo_by_name.json |
| T13 | R12 | Scratch `lists.txt` with the Bangladesh holiday calendar ID added | That calendar shows as "skipped (holiday)"; no holiday items in the output; other calendars processed normally | pass | Holiday ID in scratch lists.txt → `skipped (holiday)`, not fetched; no holiday items in output |

Test data rule: prefer real items that match each pattern. If a pattern has no real example, create a temporary test task and delete it afterwards — **only with user approval**.

Test result: pass, 13/13 (T1–T13) — run on 2026-09-17 — approved by user: yes (2026-09-17)

## 10. Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| A person isn't in the `Person` column yet | Their items end up in `"No Name"` | The run summary lists how many `"No Name"` items there are; add the person to contacts |
| Connector changes how Tasks are represented | `type` becomes wrong | T6 checks it; troubleshooting row below |
| Completed tasks may still be returned | Finished work gets messaged | Check during testing; if so, add a filter in v0.2 |
| More than 250 items | Items missing | Follow `nextPageToken` (step 5) |
| Module reads the private contacts file | Numbers could leak into output/logs | Only the `Person` column is read; T6 greps the output for phone digits |
| Changes two CLAUDE.md fixed decisions (name rule, window start) and adds the task-owner rule | Docs disagree with behavior | Update CLAUDE.md "Fixed decisions" in the same commit as the stable release |
| Some real Google Tasks never reach this module, and which ones is unpredictable | Real to-do items can be silently missing from the output, even though they exist in Google Tasks | Verified 2026-09-17 by direct connector search: 4 real tasks return zero results from `list_events` under every query tried, while other tasks in the same Tasks lists ARE returned; no list/date/time/recurrence pattern explains the split. No fix without a separate Google Tasks connector — out of scope for v0.1; user accepted this gap for v0.1 | 

## 11. Change Log
| Version | Date | Phase | Change | Reason |
|---|---|---|---|---|
| 0.1 | 2026-09-17 | draft | Initial draft | Explore showed titles with non-name `(...)` groups; user chose the known-names rule and a start-of-today window; user added the task-owner fallback for items with no name (R11, T12); user asked to exclude holidays (R12, T13); test run found the fallback text (`My Tasks`) is never in the data — replaced with a type-based rule (R11 rewritten). A first theory ("Calendar API only exposes the default My Tasks list") was tested against more real items and disproved (tasks from Prd-OrderMgt, Prd-SOP and Prod-Costing were all visible); the real, verified finding is an unpredictable Calendar-API coverage gap affecting some tasks regardless of list — documented in §5/§10, user accepted it for v0.1 (T2 re-evidenced) |

## 12. Troubleshooting & Rollback
| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| No calendars found / 404 | Wrong ID in `lists.txt`, or the calendar isn't shared with the connected account | `list_calendars` output vs IDs | Fix the ID or share the calendar |
| Auth / permission error | Google Calendar connector disconnected | Try `list_calendars` | Reconnect the connector |
| Everything in "No Name" | Contacts file missing, `Person` header renamed, or titles lack `(Name)` | Run summary warning; check the header row | Restore the file/header; fix titles |
| A known person's item in "No Name" | Spelling in the title differs from the `Person` value, or an unhandled prefix | Compare the title with the `Person` column | Fix the title or the contact spelling; add the prefix to step 7 (MINOR) |
| Items missing | Window/timezone wrong, pagination not followed, or event type filtered out | Check the `startTime`/`endTime` used and `nextPageToken` | Use Asia/Dhaka start-of-today; follow pages |
| All items `type: event` | Task description link format changed | Look at a raw `description` | Update the detection rule in step 6 (MINOR) |
| Owner's items in "No Name" | Item is `type: "event"`, not `"task"` (R11 only applies to tasks) | Check the item's `type` in the output | Expected — only tasks get the owner fallback |
| A real to-do never shows up anywhere in the output | It's one of the unpredictable Calendar-API coverage gaps (see §5, §10) — not tied to any Tasks list, date, or time pattern found so far | Search for it directly with `list_events fullText`; if it returns nothing, this is the known gap | No fix in this module; would need a Google Tasks connector (future upgrade). Accepted as a known limitation for v0.1 |
| Holiday items in output | A holiday calendar ID in a different format was added to `lists.txt` | Check the run summary for "skipped (holiday)" | Remove the ID from `lists.txt`, or extend the holiday ID match in step 1 (MINOR) |
| Empty output `{}` | No items in the window | Calendar UI | Expected |

**Rollback:** `git checkout 01-calendar-todo/v<prev> -- .claude/skills/01-calendar-todo plan/stableMD/01-calendar-todo.stable.md`
See also `docs/TROUBLESHOOTING.md#01-calendar-todo`.
