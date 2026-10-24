---
name: 05-manual-ui
description: A local web page (127.0.0.1 only) to send the to-do list by hand over WhatsApp (module 02) and/or email (module 04) in dry-run, self or live mode, to chosen people, and to see in one place whether each send really went - plus a Rebuild-list button and a delivery log with filters and CSV export. It adds no sending logic of its own: it runs the two modules exactly as published.
---

# 05 - Manual Send UI

Source of truth: `plan/stableMD/05-manual-ui.stable.md` (v1.1). This skill is a summary of its section 7 - if they ever disagree, the stable plan wins and this file needs updating.

## When to use
When the 10:00 run is not what you need: a late task someone must hear about now, a message that never arrived, a correction after fixing an address, or a test before trusting a change. The normal day is still module 03's scheduled run.

## Start it
- Start Menu -> **Production ToDo - Manual Send** (created by `src/ui/make_shortcut.ps1`; deliberately not on the Desktop, which is a Google-Drive-synced folder), or run `src/ui/start_ui.cmd`. It starts the server and opens the browser at a one-time link.
- `src/ui/start_ui.cmd --stop` stops it. A second start says where the running one is.
- Closing the console window also stops it. Every start makes a new access token, so an old link stops working (401): start it from the launcher again.

## Using it
1. **Send page.** One row per person; WhatsApp and Email each show whether the person is due today, the item count and today's state from the module's own log (`sent 10:03 - delivered`, `server`, `pending`, `bounced`, `queued`, `invalid address`, ...). Anyone due, with items and not yet sent live today is pre-ticked.
2. **Preview** shows exactly the text that would go out (module 02's own message builder), with no side effects.
3. **Mode** starts on `dry-run` on every page load. `self` sends to your own test number / mailbox. `LIVE` colours the page red and needs a confirm listing who gets it. A person already sent live today needs a separate "send again" confirm and is passed `--resend`.
4. **Send...** runs WhatsApp then email (one failing never stops the other). The Job box shows progress, then the per-person outcome read from the modules' logs.
5. **Rebuild list** (if the list is not from today, send is disabled) runs a headless Claude session for module 01 only; the confirm states the cost (about $0.2-0.3, 1-2 minutes).
6. **Refresh email receipts** runs `send_email.py --reconcile` to find bounces. There is no WhatsApp equivalent: module 02 only sees a receipt while its browser is connected.
7. **Log page** merges the shared delivery ledger with older WhatsApp rows from module 02's own log (`legacy`, no delivery receipt), with filters, colours and CSV export. The default hides `skipped_*` rows.

## What v1.1 changed (display only, R23-R25)
- A new job starts with an empty result area (no previous job's rows while it runs).
- The status strip refreshes when a job ends and every 60 s while idle; after a successful Rebuild list the page reloads itself (mode back to `dry-run`).
- The strip shows both scheduled runs (`WhatsApp ... 10:00 - Email ... 13:30`, or `Email with the 10:00 run` / `Email off`) and the heading is "Last run" with the run kind. The UI's 10:00 guard is not extended to 13:30: module 03's email run waits for the lock.

## Safety rules the UI enforces (do not weaken them)
- Loopback only (`127.0.0.1`), forged `Host` -> 400, an in-memory token in an HttpOnly SameSite=Strict cookie, same-origin check on every POST, nothing happens on a GET. A page that can send real messages must be safe from other websites in your browser.
- The server re-validates every request: people must be in the contact file, a live send needs the confirm, a resend needs a prior live send today plus its confirm, the list must be from today.
- A WhatsApp send (self/live) or a Rebuild takes the scheduler's lock `outputs/03-scheduler/run.lock` (atomic create) and is **refused from 6 minutes before to 3 minutes after `SCHED_TIME`**: a scheduled run that finds the lock held logs `skipped_locked` and is not retried, so holding it at 10:00 would silently skip the day. Email-only jobs and dry-runs take no lock.
- No phone number, email address, contact name or token is ever shown, saved or exported; only Flask is a third-party dependency; no external resource is loaded.
- The UI never retries and never reports a send the modules' logs do not show.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **T24 was not run at release, by the owner's decision:** no real `live` send to a real colleague through the UI, and no confirmed resend, has been exercised yet. The live path is tested against stub workers and in `self` mode with the real systems; do the first real live send on a day when someone is due and unsent.
- A terminal run of module 02 still bypasses the scheduler lock (an existing gap the UI narrows but cannot close): use the UI or the scheduler for WhatsApp.
- Module 02's `delivered` is not "the person saw it", and WhatsApp receipts after the run cannot be captured.
- Local use only: no login accounts, no HTTPS, no LAN or mobile access.

## If it fails
See `docs/TROUBLESHOOTING.md#05-manual-ui` for symptom -> cause -> fix. Common ones: 401 (restart from the launcher), "already running", send buttons disabled by a held lock or the 10:00 guard, an old-list banner (Rebuild list), a person unticked because they were already sent.
