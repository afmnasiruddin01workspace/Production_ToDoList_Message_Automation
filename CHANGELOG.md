# Changelog

One line per stable release. Format: `YYYY-MM-DD | NN-module | vX.Y | summary`

| Date | Module | Version | Summary |
|---|---|---|---|
| 2026-09-17 | project | 0.0 | Scaffolding: folder structure, phase workflow, template, upgrade & troubleshooting docs |
| 2026-09-17 | 01-calendar-todo | 0.1 | First stable release: pulls events/tasks from `lists.txt` calendars (today→+30d, Asia/Dhaka), groups by name found in `WhatsappContacts.txt`, owner fallback for unnamed tasks, holiday calendars excluded. Known limitation: some Google Tasks are not returned by the Calendar API regardless of list/date/time — accepted for v0.1. |
| 2026-09-17 | 01-calendar-todo | 1.0 | Output contract change (MAJOR): removed `start`/`end` (date/time) from `todo_by_name.json` and `.md` per user request. Items are now `{type, title, calendar}` only; start time is still used internally to keep ordering chronological but is never written. |
| 2026-09-17 | 01-calendar-todo | 2.0 | Output contract change (MAJOR): removed `calendar` from `todo_by_name.json` and `.md` per user request. Items are now `{type, title}` only; calendar name is still used internally in the run summary but is never written. |
| 2026-09-17 | 01-calendar-todo | 3.0 | Tasks now read from the Google Tasks API (all lists) via `fetch_tasks.py`, fixing tasks the Calendar API never returned; Calendar task copies dropped; completed tasks excluded; owner rule narrowed to unnamed tasks on "My Tasks" (MAJOR); run aborts without writing if the Tasks read fails; OAuth secrets kept in gitignored `.secrets/`. Known limitation: recurring tasks show only the current instance. |
| 2026-09-18 | 02-whatsapp-message | 1.0 | First stable release. Sends each person in `WhatsappContacts.txt` (new `Send days` column) their module 01 to-do list on WhatsApp via a **linked-device WhatsApp Web session** (`whatsapp-web.js`), not the Meta Cloud API — the owner's Facebook account is restricted, so no developer token can ever be issued. No token in this design; session lives in gitignored `.secrets/wweb-auth/` after a one-time QR scan. Test ladder: `dry-run` → `self` → `live`, capped and paced (`WA_MAX_PER_RUN`, randomized delay) since this is not an officially-supported channel. Known limitation: `sendMessage()` can succeed without returning a usable id (logged as `"unconfirmed"`, not a failure). |
