# Changelog

One line per stable release. Format: `YYYY-MM-DD | NN-module | vX.Y | summary`

| Date | Module | Version | Summary |
|---|---|---|---|
| 2026-09-17 | project | 0.0 | Scaffolding: folder structure, phase workflow, template, upgrade & troubleshooting docs |
| 2026-09-17 | 01-calendar-todo | 0.1 | First stable release: pulls events/tasks from `lists.txt` calendars (today→+30d, Asia/Dhaka), groups by name found in `WhatsappContacts.txt`, owner fallback for unnamed tasks, holiday calendars excluded. Known limitation: some Google Tasks are not returned by the Calendar API regardless of list/date/time — accepted for v0.1. |
