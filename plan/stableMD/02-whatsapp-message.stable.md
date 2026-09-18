---
module: whatsapp-message
id: "02"
phase: stable
version: "1.0"
depends_on: ["01-calendar-todo"]
input_from: outputs/01-calendar-todo/todo_by_name.json + src/messageListNDetails/WhatsappContacts.txt
output_to: outputs/02-whatsapp-message/
approved_by: A F M Nasir Uddin (2026-09-18 — "Yes, promote now" — 16 of 17 tests pass, T17 carried over as pending/scheduled for 2026-09-19)
date: 2026-09-18
---

# 02 — WhatsApp Message per Person

## 1. Frontmatter
See YAML header above. Every field must be filled before validation.

## 2. Goal
Send each person in `WhatsappContacts.txt` their own to-do list from module 01 as a single WhatsApp message from the owner's WhatsApp Business number, **at no cost**, on the weekday chosen for that person — and record exactly what was sent, skipped or failed in `send_log.json` for module 03 (scheduler).

**Why v1.0 replaces v0.1.** v0.1 used the Meta WhatsApp Business Cloud API. That path needs a Meta developer app and an access token, both obtainable only through `developers.facebook.com`. The user's Facebook account is restricted, so no token can ever be issued and the Cloud API is permanently unavailable to this project. v1.0 therefore changes the transport to **WhatsApp Web automation**: the owner's own WhatsApp Business account is linked as a *linked device* (the same mechanism as WhatsApp Web in a browser) and messages are sent through that session. Everything upstream of the transport — inputs, schedule, message format, log contract — is carried over unchanged.

**Consequences of the transport change (all user-visible):**
- **No 24-hour window.** WhatsApp Web sends the way a person does, so anyone can be messaged at any time without waiting for them to write first. The `window_closed` status and `summary.needs_inbound` disappear.
- **No token and no Meta account.** `.secrets/whatsapp.env` no longer holds a secret token; the session credential is a linked-device session folder.
- **A one-time QR scan** from the owner's phone links the session. It persists across runs, and is only needed again if the owner unlinks the device or the session is invalidated.
- **This is not an officially supported interface.** WhatsApp's terms permit only the official Business API for automated sending; driving WhatsApp Web with a script is against those terms and carries a real (if low, at this volume) risk of the number being blocked. The user was told this and chose this path. The design answers it with conservative pacing, a hard per-run cap, and human-looking behaviour — see §10.

## 3. Inputs
| Name | Path / Source | Format | Schema / Columns | Private? |
|---|---|---|---|---|
| ToDo by person | `outputs/01-calendar-todo/todo_by_name.json` | json | `{ "<group>": [ {type, title} ] }` — module 01 v3.0 §4 contract, matched exactly | yes |
| Contacts + schedule | `src/messageListNDetails/WhatsappContacts.txt` | tsv (header row, CRLF, no trailing newline) | `Person`, `WhatsApp contact name`, `Number`, `Send days` | yes |
| Run settings | `.secrets/whatsapp.env` | `KEY=VALUE` txt | `WA_MODE`, `WA_SENDER_NUMBER`, `WA_TEST_NUMBER`, `WA_MIN_DELAY`, `WA_MAX_DELAY`, `WA_MAX_PER_RUN`, `WA_CHROME_PATH` | **private** |
| WhatsApp session | `.secrets/wweb-auth/` (git-ignored) | Chromium profile written by `whatsapp-web.js` `LocalAuth` | created by the one-time QR scan; never read or printed by the skill | **secret** |
| Send transport | `whatsapp-web.js` (npm), driving the installed Chrome | node | `client.sendMessage(chatId, body)`; `chatId` = `<digits>@c.us` | — |

> Explore findings (2026-09-18): `node v26.7.0` and `npm 12.0.2` are installed; Chrome is at `C:\Program Files\Google\Chrome\Application\chrome.exe`, so Puppeteer needs no bundled browser download. Module 01 v3.0 output holds 24 items in 6 groups (owner 2, Mamun 3, Rabbani 6, Rashikul 7, Robiul 3, No Name 3). Contacts hold 6 people, all numbers normalize to 13 digits; the `Send days` column gives one person per weekday Sat–Thu and nobody on Fri. Longest message body today is 429 characters.

**Cost rule.** Nothing in this path is billed by Meta: the messages are ordinary personal messages sent from a linked device, identical to the owner typing them in WhatsApp Web. No template is ever used, and there is no per-message charge to monitor.

## 4. Outputs (contract for next module)
| Name | Path | Format | Schema |
|---|---|---|---|
| Send log | `outputs/02-whatsapp-message/send_log.json` | json | `{ "runs": [ { date, mode, started_at, sender_verified, entries[], summary } ] }`, newest run first, last 30 days kept |

Entry fields: `person` (group name), `status`, `item_count`, and depending on status `sent_at`, `message_id`, `error`, `send_days`, `message` (the exact text built for that person).

Statuses: `sent` · `dry-run` · `already_sent` · `skipped_day` · `skipped_no_items` · `skipped_no_contact` · `skipped_over_cap` · `not_registered` · `failed`.

Summary fields: `sent`, `dry_run`, `failed`, `skipped`, `not_registered`, `bad_days` (rows whose `Send days` value could not be understood).

Field rules:
- **No phone number is ever written** to the log, printed, or committed (§6 R12). There is no token in this design at all.
- `message` holds the full message text, so module 03 and the user can see exactly what went out.
- `sender_verified` holds the linked account's own number **masked to its last 4 digits** (`****9607`).
- `message_id` is the `id._serialized` value whatsapp-web.js returns. It contains the recipient's number, so the number segment is masked to its last 4 digits before it is stored.
- No groups at all (`{}` from module 01) → a run entry with an empty `entries` list is still written (this is not an error).

```json
{
  "runs": [
    {
      "date": "2026-09-18",
      "mode": "live",
      "started_at": "2026-09-18T09:00:03+06:00",
      "sender_verified": "****9607",
      "entries": [
        {"person": "Rabbani", "status": "sent", "item_count": 6, "sent_at": "2026-09-18T09:00:21+06:00", "message_id": "true_****1559@c.us_3EB0…", "message": "Hello Rabbani,\nYour pending items (18 Sep 2026):\n\n1. …"},
        {"person": "Alim", "status": "skipped_no_items", "item_count": 0},
        {"person": "Kalam", "status": "skipped_day", "item_count": 0, "send_days": "Thu"},
        {"person": "A F M Nasir Uddin", "status": "skipped_no_contact", "item_count": 2},
        {"person": "No Name", "status": "skipped_no_contact", "item_count": 3}
      ],
      "summary": {"sent": 1, "dry_run": 0, "failed": 0, "skipped": 4, "not_registered": 0, "bad_days": []}
    }
  ]
}
```

## 5. Scope
**In scope**
- Reading module 01's output and the contacts file (including the `Send days` column).
- Building one English message per person: greeting + numbered list.
- Sending through a linked-device WhatsApp Web session in three modes (`dry-run`, `self`, `live`).
- Conservative human-like pacing and a per-run cap.
- Not sending the same person twice on the same day.
- Writing `send_log.json` and printing a run summary.

**Out of scope**
- The Meta Cloud API and template messages (unavailable — restricted Facebook account; see §2).
- The one-time QR link itself: the skill prints the QR and waits, but the owner scans it by hand. No automated login.
- Scheduling / automatic daily runs (module 03).
- Reading, replying to, or reacting to incoming messages; read receipts; presence.
- Messaging the owner's own group (`A F M Nasir Uddin`) and `No Name` — no contact row exists for them, by user decision.
- Group chats, broadcast lists, media, buttons.
- Editing module 01's output or any calendar/task data.

## 6. Requirements
| ID | Requirement (testable) |
|---|---|
| R1 | Contacts are parsed from the tab-separated file with a header row, CRLF line endings and no trailing newline; `Person` is matched case-insensitively and trimmed. A duplicate `Person` value stops the run with an error. |
| R2 | `Send days` accepts comma-separated 3-letter day names (case-insensitive). `Daily`, `All`, an empty value, or a missing column all mean "every day". Today's weekday is taken in `Asia/Dhaka`. A person whose days exclude today gets `skipped_day` and no message. An unrecognized day token is reported in `summary.bad_days` and treated as "not today". |
| R3 | `Number` is normalized to digits only (leading `+`, spaces and dashes removed) and used as chat id `<digits>@c.us`. |
| R4 | Only groups that match a `Person` row are messaged. Every other group — including `A F M Nasir Uddin` and `No Name` — gets `skipped_no_contact` and no message. A contact with an empty item list gets `skipped_no_items`. |
| R5 | The message is `Hello <Person>,` + `Your pending items (<D Mon YYYY>):` + blank line + `N. <title>` per item. The one `(...)` group that contained this person's matched name is removed from the title, leaving no dangling connector word; all other parentheses stay. No `[task]`/`[event]` tag appears. Non-ASCII (Bengali) text is preserved. |
| R6 | In `dry-run` mode **the browser is never launched and no message leaves the machine**; every messageable person gets status `dry-run` with the full `message` text in the log. |
| R7 | In `self` mode every message is sent to `WA_TEST_NUMBER` instead of the person's own number, while the log still names the intended person. `self` mode without `WA_TEST_NUMBER` refuses to run. |
| R8 | Before the first send in `self`/`live` mode, the linked session's own number is read from the client; if its digits differ from `WA_SENDER_NUMBER`, the run aborts before sending anything. |
| R9 | A recipient whose number is not registered on WhatsApp gets `not_registered`; the run continues with the remaining people. Registration is checked before sending, so no message is sent into the void. |
| R10 | Sends are spaced by a random delay between `WA_MIN_DELAY` and `WA_MAX_DELAY` seconds (defaults 8 and 25). At most `WA_MAX_PER_RUN` messages (default 10) are sent in one run; recipients beyond the cap get `skipped_over_cap`, not `failed`. A send error for one person gives `failed` with the error text and does not stop the run; a session or authentication failure aborts the run. |
| R11 | If `send_log.json` already records `status: "sent"` for a person on today's date, that person is skipped as `already_sent` — a re-run never delivers a second copy. |
| R12 | No full phone number appears in `send_log.json`, in stdout, or in any committed file; `message_id` and `sender_verified` are masked to the last 4 digits. `.secrets/` (including `wweb-auth/`) stays git-ignored, and the QR string is never written to disk. |
| R13 | If `todo_by_name.json` is missing, unreadable, or its file date is older than today (Asia/Dhaka), the run stops without sending anything and says which file and what date it found. |
| R14 | `WA_MODE` must be exactly `dry-run`, `self` or `live`. Missing, empty or unknown value → refuse to run. There is no default that sends. |
| R15 | A message body longer than 4000 characters is split into parts sent in order, each suffixed ` (k/n)`; all parts share one log entry listing every masked `message_id`. |
| R16 | If no linked session exists, the run prints the QR code in the terminal and waits for the owner to scan it, then continues; if the scan does not happen within 120 seconds the run exits without sending. A session dropped mid-run aborts the run, with the remaining people left unsent and recorded as such. |

## 7. Steps / Design
Two processes, so the logic already tested in v0.1 is kept and only the transport is new:

- `.claude/skills/02-whatsapp-message/send_whatsapp.py` — Python 3, standard library only. Owns config, inputs, schedule, message building, idempotency, the log and the summary. It never touches WhatsApp itself.
- `.claude/skills/02-whatsapp-message/wa_send.js` — Node + `whatsapp-web.js`. Owns the session and the actual sending. It is a pure worker: reads a job file, writes a result file, and prints only progress lines with masked numbers.

Dependencies are installed once into `.claude/skills/02-whatsapp-message/node_modules` from a committed `package.json` (`npm install` in that folder). `node_modules/` is git-ignored. Puppeteer is pointed at the installed Chrome via `WA_CHROME_PATH` (default `C:\Program Files\Google\Chrome\Application\chrome.exe`) so no browser is downloaded.

**Python side**
1. **Load config** (R14, R7) — read `.secrets/whatsapp.env` with a plain `KEY=VALUE` parser (ignore blanks and `#` lines, strip quotes); real environment variables override the file. Validate `WA_MODE`; require `WA_SENDER_NUMBER` unless mode is `dry-run`; require `WA_TEST_NUMBER` when mode is `self`. Never print any value.
2. **Read module 01 output** (R13) — check `outputs/01-calendar-todo/todo_by_name.json` exists and its modification date is today in Asia/Dhaka; otherwise stop with the path and the date found. Parse json; `{}` is valid and means "no items".
3. **Read contacts** (R1, R2, R3) — `csv.DictReader(delimiter="\t")` over the file read with `newline=""`; strip `\r` and surrounding spaces from every value. Build `lowercase Person → {canonical, number_digits, send_days_raw}`. Stop on duplicate `Person`. Missing `Send days` column → every row means "every day".
4. **Load today's previous entries** (R11) — read the existing `send_log.json` if present; collect the people already recorded as `sent` today.
5. **Decide per group** (R4, R2, R11), iterating the union of module 01's groups and the contact list, sorted A→Z with `No Name` last, one log entry each:
   - no contact row → `skipped_no_contact`
   - already sent today → `already_sent`
   - today not in `Send days` → `skipped_day` (record `send_days`)
   - empty item list → `skipped_no_items`
   - otherwise → build the message and add it to the job.
6. **Build the message** (R5, R15) — for each item take `title`, delete the single `(...)` group whose contents (after trimming, stripping a leading `From `/`By `, splitting on `,`) contain this person's name, then remove a connector word (`by`/`from`/`for`) and any separators left dangling at the end, collapse doubled spaces, trim. Compose `Hello <Person>,\nYour pending items (<D Mon YYYY>):\n\n` then `N. <title>` lines. Split into ` (k/n)` parts if over 4000 characters.
7. **Cap the job** (R10) — keep the first `WA_MAX_PER_RUN` recipients in A→Z order; the rest are logged `skipped_over_cap` and counted under `skipped`.
8. **Send** — in `dry-run` (R6) the job is never handed over: each recipient is recorded `dry-run` and the run ends. Otherwise write the job to a temp file in the system temp dir (never in the repo), run `node wa_send.js <job> <result>`, and read the result file back. `to` is `WA_TEST_NUMBER` in `self` mode. Map each result row onto a status: `ok` → `sent` (+ masked `message_id`), `not_registered` → `not_registered`, `error` → `failed` (+ error text), missing row → `failed` ("worker did not report"). A non-zero worker exit whose reason is `auth`, `auth-timeout` or `sender-mismatch` aborts the run (R8, R16). The job and result files are deleted afterwards.
9. **Write the log** (R12) — prepend this run to `runs`, keep the last 30 days, write `outputs/02-whatsapp-message/send_log.json` UTF-8, 2-space indent, `ensure_ascii=False`. Mask every id; never write a number.
10. **Print the summary** — counts per status, unrecognized `Send days` tokens, failed people with their error text, and anybody left unsent by the cap.

**Node worker (`wa_send.js`)**
1. Start `new Client({ authStrategy: new LocalAuth({ dataPath: <repo>/.secrets/wweb-auth }), puppeteer: { executablePath: WA_CHROME_PATH, headless: true } })`.
2. `qr` event (R16) — render the QR in the terminal with `qrcode-terminal` and print "scan this with WhatsApp → Linked devices"; after 120 s without a scan, exit non-zero with reason `auth-timeout`.
3. `ready` event (R8) — read `client.info.wid.user` and compare it with `WA_SENDER_NUMBER`, which the worker receives in the job file. Mismatch → exit non-zero with reason `sender-mismatch`, nothing sent. The result file records only the masked form.
4. For each job row, in order: `client.isRegisteredUser(chatId)` (R9) → if false record `not_registered` and continue; else `client.sendMessage(chatId, body)` for each part and record `ok` with the ids; then wait a random `WA_MIN_DELAY…WA_MAX_DELAY` seconds before the next recipient (R10).
5. On a throw for one recipient, record `error` with `err.message` and keep going. On `disconnected` / `auth_failure`, stop, write what has been done so far, and exit non-zero with reason `auth` (R16).
6. `client.destroy()` and exit 0.

Configuration (all in `.secrets/whatsapp.env`, git-ignored; real env vars override):
| Name | Purpose | Default |
|---|---|---|
| `WA_MODE` | `dry-run` \| `self` \| `live` (no sending default) | none — required |
| `WA_SENDER_NUMBER` | The owner's business number, digits only — checked against the linked session before sending (R8) | none — required outside `dry-run` |
| `WA_TEST_NUMBER` | Recipient for every message in `self` mode (a second number owned by the user) | none — required in `self` |
| `WA_MIN_DELAY` / `WA_MAX_DELAY` | Random pause between recipients, seconds | 8 / 25 |
| `WA_MAX_PER_RUN` | Hard cap on messages per run | 10 |
| `WA_CHROME_PATH` | Chrome executable for Puppeteer | `C:\Program Files\Google\Chrome\Application\chrome.exe` |
| `Send days` | Per-person weekday schedule | 4th column of `WhatsappContacts.txt` |
| Timezone | `Asia/Dhaka` (fixed, same as module 01) | this document §7 |

## 8. Validation Checklist
- [x] All frontmatter fields filled
- [x] No `TBD`, `TODO`, `<placeholder>` left
- [x] §3 Inputs match upstream §4 Outputs (or source file actually exists)
- [x] Every R# in §6 is implemented by at least one step in §7
- [x] Every R# in §6 is covered by at least one test in §9
- [x] No secrets or real phone numbers written in this file
- [x] §12 Troubleshooting filled for known failure points

Validation result: pass — checked by Claude on 2026-09-18 — approved by user: yes (2026-09-18)

## 9. Test Cases
Run in ladder order: all `dry-run` tests first (no browser at all), then a stub worker for the transport paths, then `self` against the owner's second number, then `live`. Fixture files live in the scratch directory, never in the repo.

| ID | Covers | Input / Setup | Expected | Result | Evidence |
|---|---|---|---|---|---|
| T1 | R1 | Real contacts file (CRLF, no trailing newline) + fixture with a duplicate `Person` and padded values | 6 people parsed from the real file; duplicate fixture stops the run with a clear error | pass | dry-run 2026-09-18: real file parsed, 6 people, CRLF + no trailing newline handled; `contacts_dup.txt` -> exit 1 "duplicate Person 'mamun' … each person must appear once" |
| T2 | R2 | Fixtures: `Daily`, empty, missing column, `Sat,Mon`, `sat , mon`, `Funday`; plus the real schedule on each weekday | Every-day cases send; `Sat,Mon` sends only on those days; `Funday` → not sent and listed in `bad_days` | pass | `contacts_days.txt` on Mon -> Mamun(Daily)+Rabbani(Sat,Mon) sent, Rashikul(` fri , sun `) skipped_day, Robiul(`Funday`) skipped + `bad_days=['Robiul: funday']`; same fixture on Sun -> Mamun+Rashikul; `contacts_nocol.txt` (no 4th column) -> everyone every day. Real file (two days each: Rabbani Sun/Wed, Rashikul Tue/Sat, Mamun Sat/Tue, Robiul Mon/Thu, Alim Wed/Sun, Kalam Thu/Mon) -> Fri = nobody, Sat = Mamun+Rashikul, Sun = Rabbani, Tue = Mamun+Rashikul |
| T3 | R3 | Real numbers in `+880 1678-…` form | Chat id is `<13 digits>@c.us`, no `+`/space/dash | pass | stub worker call log: chat ids `8801700000001@c.us` … all matching `\d{13}@c\.us`, no `+`/space/dash; job file deleted after the run |
| T4 | R4 | Real module 01 output (6 groups) + 6 contacts | Scheduled person messageable; owner + `No Name` → `skipped_no_contact`; contacts with no items → `skipped_no_items` | pass | real data 2026-09-20: Rabbani `dry-run`; owner + `No Name` `skipped_no_contact`; Alim `skipped_no_items`; Kalam/Mamun/Rashikul/Robiul `skipped_day` |
| T5 | R5 | Rabbani (name in every title), Robiul (`…Team by (Robiul)`), owner group (Bengali text) | Greeting + numbered list; `(Rabbani)` removed with no dangling `by`; `(SOP, HT)` kept; no tags; Bengali intact | pass | log message text: `Hello Rabbani,` / `Your pending items (20 Sep 2026):` / 6 numbered lines, `(Rabbani)` gone with no dangling `by`, `(SOP, HT)` and `(emailed on 30 Aug-26)` kept, no `[task]`/`[event]` |
| T6 | R6 | `WA_MODE=dry-run` full run with `WA_CHROME_PATH` pointing at a non-existent file | Run succeeds, no browser process starts, all statuses `dry-run` | pass | `WA_CHROME_PATH=Z:\no\such\chrome.exe` + `WA_NODE=definitely-not-node` -> exit 0, all statuses `dry-run`; chrome process count 16 before and 16 after |
| T7 | R7 | `WA_MODE=self` with and without `WA_TEST_NUMBER` | With: message arrives on the test phone, log names the real person. Without: run refuses to start | pass | real self-mode run (after the session was linked): all 4 recipients (Mamun/Rabbani/Rashikul/Robiul) delivered to the test phone `****4553`, confirmed by the user on-device; log entries correctly named the real person, not the test number; empty `WA_TEST_NUMBER` -> exit 1 "WA_TEST_NUMBER is required for mode 'self'" |
| T8 | R8 | `self` run with `WA_SENDER_NUMBER` altered by one digit | Run aborts before any send, reason `sender-mismatch` | pass | worker reporting `sender-mismatch` -> exit 1 "whatsapp session unusable (sender-mismatch) — nothing was sent", no log written, zero recipients reached |
| T9 | R9 | Stub worker returning `not_registered`; then a real check against a number known not to be on WhatsApp | Status `not_registered`, run continues, counted in the summary | pass | stub: number ending 5 -> `not_registered`, remaining people still sent, `summary.not_registered=1`. Real: `live` run against an isolated single-contact fixture (`+8801000000000`, not a real WhatsApp number) -> real `client.isRegisteredUser()` returned false, logged `not_registered`, run completed cleanly with exit 0; no real contact was in that run's fixture, so nothing else was touched |
| T10 | R10 | Stub worker + a 12-person fixture with `WA_MAX_PER_RUN=3`, `WA_MIN_DELAY=1`, `WA_MAX_DELAY=2`; one row forced to throw | 3 sent, 9 `skipped_over_cap`; gaps between sends land in range; the throwing row is `failed` and the rest still send | pass | 12 people, `WA_MAX_PER_RUN=3` -> `sent=3, skipped_over_cap=9`, worker handed only P01–P03, gaps 1.43 s and 1.72 s inside the asked-for 1–2 s; a recipient the worker failed -> `failed` ("stub send failure") while the others still sent; a dropped row -> `failed` ("worker did not report") |
| T11 | R11 | `self` run for one person, then immediately re-run | Second run shows `already_sent`; only one message arrives | pass | stub run then immediate re-run: Mamun/Rabbani `already_sent`, worker called on the re-run only for the earlier `not_registered`/`failed` pair — no second copy. Also verified the idempotency fix (§12): a synthetic log with a `mode: "self"` `sent` entry for today does **not** block a same-day run (dry-run showed the people as sendable, not `already_sent`); a synthetic log with a `mode: "live"` `sent` entry for today correctly still blocks with `already_sent` |
| T12 | R12 | After every test | `grep` the log, stdout and the repo for any 8+ digit run → no hits; `git check-ignore -v .secrets/wweb-auth .secrets/whatsapp.env` passes; `git status` shows no session files | pass | every log written this stage scanned for `\d{8,}|Bearer|WA_TOKEN` -> no hits; `message_id` stored as `true_****0011@c.us_3EB0…`, `sender_verified` as `****0001`; `git check-ignore` passes for `.secrets/whatsapp.env`, `.secrets/wweb-auth` and `node_modules/` |
| T13 | R13 | Rename `todo_by_name.json`; then restore it and back-date it to yesterday | Both runs stop without sending, naming the file and the date found | pass | missing file -> exit 1 "missing … — run skill 01-calendar-todo first"; yesterday's file -> exit 1 "is from 2026-09-17, not today (2026-09-18)"; nothing sent in either case |
| T14 | R14 | `WA_MODE` unset, empty, and `LIVE!` | All three refuse to run; nothing sent | pass | unset / empty / `LIVE!` -> all exit 1 "WA_MODE must be one of dry-run, self, live"; no log, no worker call |
| T15 | R15 | Fixture group with ~60 long items (>4000 chars), `self` mode | Split into ` (1/2)`, ` (2/2)`, both arrive in order, one log entry with both masked ids | pass | stub: 5014-character body split into 2 parts, both handed to the worker in order. Real: `self`-mode run against the same 60-item fixture -> 2 messages actually delivered to the test phone `****4553`, in order, with ` (1/2)`/` (2/2)` suffixes, confirmed by the user on-device; one log entry with both `ids` |
| T16 | R16 | First run with `.secrets/wweb-auth` absent; then a run once the session exists; then a session dropped mid-run | QR printed and the scan links the account; the second run starts with no QR; a dropped session aborts with reason `auth` | pass | stub: worker reasons `auth-timeout` and `auth` both abort the run with "whatsapp session unusable (…) — nothing was sent". Real: QR printed (both as terminal ASCII and a PNG file, since the terminal rendering proved unreliable — see §12), a real scan linked the account, and every run since reconnects with no QR. A real `sender-mismatch` also occurred live (an earlier scan linked the wrong account) and correctly aborted before sending anything — see §12 for the corrupted-session incident this also uncovered |
| T17 | R10 + terms | `live` run on a normal day (one scheduled person) | Message delivered from the business number, the delay is observed, and the account is not restricted afterwards | pending | session is linked and every other real-send path is now proven (T7, T9, T15, T16); nobody is scheduled today (Friday). **Deliberately held for the next real scheduled day** (Saturday 2026-09-19: Mamun, Rashikul) rather than simulated with `--today`, per the user's decision on 2026-09-18. That run doubles as the pipeline's Execute step |

Test data rule: prefer real data. Any live test that messages a real colleague needs the user's go-ahead first.

Test result: 16 of 17 pass, 1 pending (T17, deliberately scheduled for 2026-09-19) — dry-run, stub and self-mode stages complete on 2026-09-18, all against the real linked WhatsApp session and a real test phone. Four real bugs were found and fixed getting the session working (see §12); the idempotency and `.wwebjs_cache` fixes from that work are also recorded there — approved by user: no

## 10. Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| **Automating WhatsApp Web breaks WhatsApp's terms; the number could be blocked** | The owner's business number stops working — the worst outcome in this design | Volume is tiny (one message per person per week, at most 10 per run), recipients are known colleagues who expect the message, pacing is randomized 8–25 s, the cap is hard, and nothing is ever sent to a stranger or in bulk. The user accepted this risk on 2026-09-18 after being told. If the account is ever warned or restricted: stop the module (`WA_MODE=dry-run`), write an incident, and fall back to sending by hand |
| The unofficial library breaks when WhatsApp Web changes | Nothing sends, possibly mid-run | Pin the `whatsapp-web.js` version in `package.json`; failures abort loudly and the log shows who was not reached; `already_sent` makes a re-run after an upgrade safe |
| Session expires or the device is unlinked | Run stops at the QR step | R16 prints the QR and waits; the runbook row says to scan from Settings → Linked devices |
| A run goes out in `live` by accident during testing | Real colleagues get test messages | `WA_MODE` has no sending default (R14), `.secrets` ships with `dry-run`, the ladder is enforced, the sender check aborts on a wrong account (R8), and the cap limits the blast radius |
| Module 01 output is stale, so yesterday's list is sent | People act on wrong information | R13 stops the run unless the file is from today |
| Phone number leaks into the committed log | Private data in a public repo | R12 + T12 grep check; `outputs/**` and `.secrets/` are git-ignored; ids and the sender are masked to 4 digits |
| The Chromium profile in `.secrets/wweb-auth` is a live login | Anyone holding the folder can message as the owner | It lives only under git-ignored `.secrets/`; treat it exactly like a password; unlink the device from the phone to revoke |
| Headless Chrome fails to start or is detected | Nothing sends | `WA_CHROME_PATH` points at the installed Chrome; the worker can be run headful for diagnosis (runbook row) |

## 11. Change Log
| Version | Date | Phase | Change | Reason |
|---|---|---|---|---|
| 0.1 | 2026-09-18 | draft → test | Initial draft, Meta Cloud API transport, free-form text inside the 24-hour window; `Send days` column added to the contacts file; owner group and `No Name` never messaged; secrets in git-ignored `.secrets/whatsapp.env`; log holds no phone numbers. Reached 14 of 16 tests passing | Explore (2026-09-18) against module 01 v3.0; user decisions of 2026-09-17/18 |
| 1.0 | 2026-09-18 | draft | **Transport replaced**: WhatsApp Web linked-device automation (`whatsapp-web.js` + the installed Chrome) instead of the Meta Cloud API. Removed the token, the sender-verification API call, the 24-hour window, `window_closed` and `needs_inbound`. Added `not_registered`, `skipped_over_cap`, randomized pacing, a per-run cap, QR linking (R16) and id masking. Inputs, schedule, message format and the rest of the log contract are unchanged from 0.1 | The user's Facebook account is restricted, so no Meta developer app and no Cloud API token can ever be issued; the user chose WhatsApp Web automation on 2026-09-18 after being told it is against WhatsApp's terms |

## 12. Troubleshooting & Rollback
| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| A QR code appears every run | The session folder is not being reused or was deleted | `.secrets/wweb-auth` missing or empty | Scan once more and leave the folder alone; check `.secrets/` was not cleaned |
| Run stops: `auth-timeout` | Nobody scanned the QR within 120 s | stderr reason | Re-run with the phone in hand: WhatsApp → Settings → Linked devices → Link a device |
| Run stops: `sender-mismatch` | A different WhatsApp account is linked, or `WA_SENDER_NUMBER` is wrong | stderr shows the masked linked number | Unlink the wrong account from the phone and re-link the business account, or fix `WA_SENDER_NUMBER` |
| Run stops: reason `auth` mid-run | The device was unlinked or the session was invalidated | log shows who was reached | Re-link and re-run; `already_sent` stops duplicates |
| One person is `not_registered` | The number is not on WhatsApp, or is mistyped | log status | Fix the row in `WhatsappContacts.txt` |
| Chrome fails to launch | `WA_CHROME_PATH` wrong, or Chrome updated/removed | worker stderr | Correct the path; run the worker headful once to see the browser error |
| Someone never gets a message though items exist | `Send days` excludes today, they're not in contacts, or the run hit the cap | log status `skipped_day` / `skipped_no_contact` / `skipped_over_cap` | Edit `Send days`, add the contact row, or raise `WA_MAX_PER_RUN` |
| Duplicate message arrives | Log file deleted or the date rolled over mid-run | compare `send_log.json` entries for the date | Keep the log; re-runs rely on it (R11) |
| Run stops: stale module 01 output | Module 01 wasn't run today | stderr shows the file date | Run skill `01-calendar-todo` first |
| Refuses to start | `WA_MODE` missing or misspelled | stderr | Set `WA_MODE` to `dry-run`, `self` or `live` |
| **WhatsApp warns or blocks the number** | Automated sending detected | The Business app shows the warning | Stop immediately (`WA_MODE=dry-run`), unlink the device, write `docs/incidents/`, and send by hand until decided otherwise |
| Library errors after a WhatsApp Web update | `whatsapp-web.js` out of date | worker stack trace | `npm update whatsapp-web.js` in the skill folder, re-pin the version, re-run the `self` ladder before `live` |
| `Execution context was destroyed` during launch or mid-session | The installed Chrome is much newer than the protocol version Puppeteer/`whatsapp-web.js` were built against (hit with system Chrome v153 against `whatsapp-web.js` 1.34.7 / Puppeteer 24.38.0) | worker stderr, often right after `client.initialize()` | Already mitigated in `wa_send.js` with `protocolTimeout: 300000` and extra launch args (`--disable-gpu`, `--disable-setuid-sandbox`, etc.). If it recurs: try `WA_HEADFUL=1` (a visible browser, for diagnosis only) or point `WA_CHROME_PATH` at an older Chrome/Chromium build closer to Puppeteer's pinned version |
| Puppeteer's own downloaded Chromium install is missing `chrome.exe` after `npx puppeteer browsers install chrome` | The zip downloaded fully but extraction was silently incomplete (only a few small files land) — observed on this machine, cause unconfirmed, possibly security software interfering | `du -sh` the extracted folder — a real Chrome install is hundreds of MB, an incomplete one is a few MB | Add an antivirus/folder exclusion for the Puppeteer cache dir and retry, or stay on the system Chrome path (`WA_CHROME_PATH`) as this project does |
| Every run shows `Execution context was destroyed` immediately, even freshly launched, even headful | The `.secrets/wweb-auth/` session folder itself is corrupted (this happens if a previous run was interrupted mid-authentication, e.g. by a hard timeout) | A clean, empty `dataPath` launches without error (confirmed by testing) | Delete `.secrets/wweb-auth/` entirely and re-link with a fresh QR scan — don't try to reuse a session that failed partway through linking |
| A real send is logged `failed` with `Cannot read properties of undefined (reading 'id')`, but the message actually arrived | `client.sendMessage()` in this `whatsapp-web.js` version can resolve without a `.id` even on a successful send (a version-drift quirk against current WhatsApp Web) | Compare the log against the phone — this was observed to under-report real deliveries | Already fixed: the worker no longer reads `.id` unguarded; a missing id is recorded as message id `"unconfirmed"` rather than treated as a failure. A chat-history re-check was tried as an alternative and found unreliable in this environment, so it was not kept |
| The **last** recipient in a run consistently never receives their message, while everyone before them does | The worker closed the browser (`client.destroy()`) immediately after the last send's local promise resolved, with no trailing pacing delay to absorb it (every other recipient's delay came from the "wait before next send" pause) — `sendMessage` can resolve slightly ahead of the actual network transmission | Reproduced twice: the alphabetically-last recipient in the batch was the one consistently missing | Fixed with a 5-second settle delay before `client.destroy()` at the end of the run |
| A `self`-mode test run seems to make a same-day `live` run skip real people as `already_sent` | The idempotency check used to count any `sent`/`already_sent` entry for today regardless of run `mode`, so a same-day self-test polluted the same bookkeeping a live run reads | Check `send_log.json` — a `mode: "self"` run showing `sent` for a real person, on the same date you're about to go live | Fixed: `sent_today` now also requires `run["mode"] == "live"`. Confirmed both directions in T11: a same-day `self` `sent` entry no longer blocks; a same-day `live` `sent` entry still correctly does |
| A `.wwebjs_cache/` folder appears at the repo root, untracked | `whatsapp-web.js`'s default `LocalWebCache` writes to `./.wwebjs_cache/` relative to the process's cwd, which is the repo root here | `git status` shows it as untracked | Fixed: `wa_send.js` now passes `webVersionCache: { type: "local", path: <under .secrets/wweb-auth/> }` so it's created inside the already git-ignored session directory instead |

**Rollback:** `git checkout 02-whatsapp-message/v<prev> -- .claude/skills/02-whatsapp-message plan/stableMD/02-whatsapp-message.stable.md`
See also `docs/TROUBLESHOOTING.md#02-whatsapp-message`.
