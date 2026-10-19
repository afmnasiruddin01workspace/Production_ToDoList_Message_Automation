---
name: 04-email-message
description: Email each person in src/EmailContent/EmailContacts.txt their own to-do list from outputs/01-calendar-todo/todo_by_name.json (the same text module 02 sends on WhatsApp) through the owner's classic Outlook, on that person's own email days. Proves hand-over from Sent Items, detects bounces, and writes outputs/04-email-message/send_log.json plus rows in outputs/delivery/delivery_log.json.
---

# 04 — Email Message per Person

Source of truth: `plan/stableMD/04-email-message.stable.md` (v1.0). This skill is a summary of its §7 Steps — if they ever disagree, the stable plan wins and this file needs updating.

## When to use
Run after module 01 (`01-calendar-todo`) has produced a **fresh, same-day** `todo_by_name.json`. Re-run any time; it is idempotent per real (`live`) send per day. Normally module 03 (scheduler) runs it; the manual-send UI (module 05) uses the same options.

**Why Outlook itself, not SMTP.** The owner already sends this mail by hand from classic Outlook 16 on one Exchange mailbox. Sending through that same Outlook (COM) means no password or token anywhere, the mail leaves exactly like a hand-sent one, and Sent Items is the proof of hand-over.

## One-time setup
- **Classic Outlook** (not "New Outlook") with `pywin32` installed for the Python in use (`pip install pywin32`).
- `.secrets/email.env` (git-ignored, `KEY=VALUE`): `EMAIL_MODE` (`dry-run`\|`self`\|`live`, required, no default that sends), `EMAIL_TEST_ADDRESS` (for `self`: an address, or `me` = your own Outlook address), optionally `EMAIL_MAX_PER_RUN` (10), `EMAIL_MIN_DELAY`/`EMAIL_MAX_DELAY` (1/3 s), `EMAIL_SUBJECT`, `EMAIL_SIGNATURE` (`|` separates lines), `EMAIL_OUTLOOK_PROFILE` (default: the registry `DefaultProfile`), `EMAIL_STARTUP_TIMEOUT` (90), `EMAIL_SEND_TIMEOUT` (60), `EMAIL_VERIFY_TIMEOUT` (30), `EMAIL_BOUNCE_WAIT` (30), `EMAIL_KILL_MARGIN` (60). There is **no password** — none exists.
- `src/EmailContent/EmailContacts.txt` (git-ignored; copy the layout of `EmailContacts.sample.txt`): tab-separated, header row, columns `Person`, `WhatsApp contact name`, `Number`, then **one tab-field holding the address and the Send days separated by spaces** (`name@host  Sat,Wed`). The WhatsApp name and number are never read.
- `.secrets/` is gitignored. **Never print, copy or commit anything from it, and never print an address or number.**

## Steps
1. **Load config**; refuse unless `EMAIL_MODE` is exactly `dry-run`, `self` or `live`. Parse options: `--people A,B` (mail only these, ignoring days), `--resend` (only with `--people`; logged `resend: true`), `--trigger schedule|manual|manual-ui`, `--reconcile` (look for bounces of the last 3 days, send nothing), `--check` (show settings and Outlook state, send nothing, never start Outlook), `--today` (tests).
2. **Take the lock** (`outputs/04-email-message/run.lock`, atomic create; stale if its process is gone or it is over 3 h old) — except for `dry-run`. Held → print `skipped_locked`, exit 0.
3. **Read module 01 output**; missing or not dated today (Asia/Dhaka) → stop without sending, naming the file and date.
4. **Read contacts**; decide per group: no contact → `skipped_no_contact`; bad address → `invalid_address`; already sent live today → `already_sent`; day not today → `skipped_day`; no items → `skipped_no_items`; else build the message.
5. **Build the message** — module 02's `build_message` text, then two blank lines and the signature (`Regards` / `A F M Nasir Uddin` / `HOD of Fastener Production` / `ID 9781`), as **HTML in Aptos 12 pt**; subject `Pending Task List, Please communicate if any obstacle and close the issue if finished.` In `self` mode every mail goes to `EMAIL_TEST_ADDRESS` with the subject prefixed `[SELF-TEST for <Person>] `.
6. **Cap and send** — at most `EMAIL_MAX_PER_RUN`; `dry-run` never touches Outlook. Otherwise hand a job to `email_worker.py`, a **child process** under a hard ceiling (it is killed, with its tree, if exceeded). The worker never calls `Dispatch` unless `OUTLOOK.EXE` is already running (a bare `Dispatch` on a closed Outlook blocks on a "Choose Profile" dialog nobody can click); if Outlook is closed it starts `OUTLOOK.EXE /profile <name>` minimised and waits for it. Per recipient: create the mail, set subject and HTML body, add the one recipient and resolve it, stamp a `TodoRunId` marker, `Send()`, then look for the marker in **Sent Items** (`delivery: accepted`) or the **Outbox** (`queued`).
7. **Check for bounces** — wait `EMAIL_BOUNCE_WAIT`, then scan the Inbox for non-delivery reports naming a recipient (`delivery: bounced`; status stays `sent`, so a wrong address is never auto-resent). The scan prints `bounce scan: N inbox item(s), N report(s), N unreadable, N matched` — **a scan with unreadable items is not proof of "no bounce".**
8. **Write the logs** — `outputs/04-email-message/send_log.json` (every run of the last 30 days; select by `date` **and** `mode`) and one row per entry in the shared `outputs/delivery/delivery_log.json`. No address or 8+ digit number is ever written. Exit non-zero on any `failed`/`unconfirmed` or an early stop; `bounced`, `queued` and `invalid_address` print `! ` lines on stderr but exit 0.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **No delivery or read receipts.** `accepted` means Outlook handed the mail to the server (a Sent Items copy exists); it does not prove the person opened it. Receipts were left out on purpose (none has ever reached this mailbox, and they would not prove reading).
- **Untested at release:** the Outbox lookup when Outlook is genuinely offline (`delivery: queued`, test T16) and any real `live` send to a colleague (T24). The first real mail goes out through module 03.
- **Classic Outlook only.** New Outlook and Outlook on the web have no COM automation.
- An Outlook this module starts is left running (closing it could strand queued mail).
- The email text is module 02's text by construction; a change to the WhatsApp message changes the email.
- A bounce is detected only while it sits in the Inbox; reports another rule moves elsewhere would be missed.

## If it fails
See `docs/TROUBLESHOOTING.md#04-email-message` for symptom → cause → fix. Common ones: `outlook-unavailable` (Outlook did not become ready — open it once by hand), a "Choose Profile" window (something started Outlook without `/profile`), `delivery: queued` (Outlook offline — do not re-run), `unconfirmed` (check Sent Items by hand first), `delivery: bounced` (wrong address in `EmailContacts.txt`), `invalid_address` (the contact row needs exactly one address then the days), stale module 01 output (run `01-calendar-todo` first).
