---
name: 02-whatsapp-message
description: Send each person in src/messageListNDetails/WhatsappContacts.txt their own to-do list from outputs/01-calendar-todo/todo_by_name.json as a WhatsApp message, on their scheduled weekday, via a linked-device WhatsApp Web session (no Meta API, no token). Writes outputs/02-whatsapp-message/send_log.json.
---

# 02 — WhatsApp Message per Person

Source of truth: `plan/stableMD/02-whatsapp-message.stable.md` (v1.0). This skill is a summary of its §7 Steps — if they ever disagree, the stable plan wins and this file needs updating.

## When to use
Run this after module 01 (`01-calendar-todo`) has produced a **fresh, same-day** `todo_by_name.json`. Re-run any time; it's idempotent per real (`live`) send per day.

**Why not the Meta Cloud API?** The owner's Facebook account is restricted, so no Meta developer app or access token can ever be issued. This skill instead links the owner's WhatsApp Business account as a linked device (the same mechanism as WhatsApp Web in a browser) and sends through that session. This is **not an officially supported interface** — WhatsApp's terms permit only the Business API for automated sending — so this design keeps volume small (one message per person per scheduled day, capped per run) and paces sends to look human. See stable §10 for the accepted risk and §2 for why.

## One-time setup
- Node packages: `cd .claude/skills/02-whatsapp-message && npm install` (installs the pinned `whatsapp-web.js`, `qrcode-terminal`, `qrcode` into a git-ignored `node_modules/`).
- `.secrets/whatsapp.env` (git-ignored, `KEY=VALUE`): `WA_MODE` (`dry-run`\|`self`\|`live`, required, no default that sends), `WA_SENDER_NUMBER` (the owner's business number, digits only), `WA_TEST_NUMBER` (a second number the owner owns, required for `self`), optionally `WA_MIN_DELAY`/`WA_MAX_DELAY` (default 8/25s), `WA_MAX_PER_RUN` (default 10), `WA_CHROME_PATH` (defaults to Puppeteer's own Chromium if unset).
- `src/messageListNDetails/WhatsappContacts.txt` needs a 4th `Send days` column (comma-separated 3-letter days, or `Daily`/empty/missing = every day).
- **First real run** (`self` or `live`) prints a QR code (terminal + a `wa_link_qr.png` file, since terminal rendering can be unreliable) and waits up to `WA_QR_TIMEOUT` (default 120s) for a scan from **the phone signed into the business WhatsApp account**: Settings → Linked devices → Link a device. The session then persists in `.secrets/wweb-auth/` (git-ignored) — no more QR needed unless the device is unlinked.
- `.secrets/` is gitignored. **Never print, copy or commit anything from it.**

## Steps

1. **Load config** from `.secrets/whatsapp.env`; env vars override. Validate `WA_MODE`; refuse to run if it's missing/empty/unrecognized. Never print any value in full — everything is masked to its last 4 digits.
2. **Read module 01 output** (`outputs/01-calendar-todo/todo_by_name.json`). Missing, unreadable, or not from today (Asia/Dhaka) → **stop without sending anything**, naming the file and the date found.
3. **Read contacts** (tab-separated, CRLF, header row). Build `Person → {number, send_days}`. A duplicate `Person` stops the run.
4. **Decide per group** (union of module 01's groups and the contact list, A→Z, `No Name` last): no contact row → `skipped_no_contact`; already sent **live** today → `already_sent` (a `self`-mode test never counts toward this); today not in `Send days` → `skipped_day`; no items → `skipped_no_items`; otherwise build the message.
5. **Build the message** — `Hello <Person>,` + `Your pending items (<D Mon YYYY>):` + numbered list. The one `(...)` group naming this person is stripped from each title (with no dangling connector word left behind); other parentheses stay. Bodies over 4000 characters split into ` (k/n)` parts.
6. **Cap and send** — keep the first `WA_MAX_PER_RUN` recipients; the rest are `skipped_over_cap`. In `dry-run`, the browser is never launched — every recipient is logged `dry-run` with the full message text. Otherwise, hand the job to `wa_send.js` (Node worker): it checks `isRegisteredUser` per recipient (`not_registered` if false), sends each part with a random `WA_MIN_DELAY`–`WA_MAX_DELAY` second pause between people, and settles 5s after the last send before closing. `self` mode redirects every send to `WA_TEST_NUMBER` while the log still names the real person.
7. **Write the log** — `outputs/02-whatsapp-message/send_log.json`, prepending this run and keeping the last 30 days. No phone number ever appears; message ids and the sender are masked to their last 4 digits (or `"unconfirmed"` if the library didn't hand back an id, which can still mean success — see Known limitations).
8. **Print a run summary** — counts per status, any unrecognized `Send days` values, anyone left by the cap.

## Known limitations (do not try to "fix" these without re-opening the plan)
- **Not an officially supported channel.** This automates WhatsApp Web against its terms; volume and pacing are deliberately conservative to reduce (not eliminate) the risk of the number being flagged.
- **`sendMessage()` can succeed without returning a usable message id** (a `whatsapp-web.js`/WhatsApp Web version-drift quirk). A logged `message_id: "unconfirmed"` does not mean the send failed — a genuine failure surfaces as a thrown error and `status: "failed"` instead.
- No delivery/read receipts (would need a public webhook — out of scope, backlog in `docs/UPGRADE.md`).
- The owner's group (`A F M Nasir Uddin`) and `No Name` are never messaged — no contact row exists for them by design.
- Group chats, media, and replying to incoming messages are all out of scope.

## If it fails
See `docs/TROUBLESHOOTING.md#02-whatsapp-message` for symptom → cause → fix. Common ones: a QR code every run (session not persisting — check `.secrets/wweb-auth/` wasn't cleaned), `sender-mismatch` (wrong account scanned), `Execution context was destroyed` (Chrome/Puppeteer version drift, or a corrupted session — delete `.secrets/wweb-auth/` and re-link), stale module 01 output (run `01-calendar-todo` first).
