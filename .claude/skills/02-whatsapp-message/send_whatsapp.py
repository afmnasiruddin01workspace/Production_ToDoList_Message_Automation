#!/usr/bin/env python3
"""02-whatsapp-message — send each person their module 01 to-do list on WhatsApp.

Transport is a linked-device WhatsApp Web session (see wa_send.js), so sending costs
nothing and needs no Meta account. This half owns the inputs, the schedule, the message
text, idempotency and the log; it never touches WhatsApp itself.
See plan/stableMD/02-whatsapp-message.stable.md for the contract this implements.
"""
import argparse, csv, json, os, re, subprocess, sys, tempfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Dhaka")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SECRETS = os.path.join(ROOT, ".secrets", "whatsapp.env")
AUTH_DIR = os.path.join(ROOT, ".secrets", "wweb-auth")
TODO = os.path.join(ROOT, "outputs", "01-calendar-todo", "todo_by_name.json")
CONTACTS = os.path.join(ROOT, "src", "messageListNDetails", "WhatsappContacts.txt")
LOG = os.path.join(ROOT, "outputs", "02-whatsapp-message", "send_log.json")
WORKER = os.path.join(HERE, "wa_send.js")
sys.path.insert(0, os.path.join(ROOT, "src", "common"))
import delivery_ledger  # noqa: E402  (v2.0, R26)

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
FULL = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu",
        "friday": "fri", "saturday": "sat", "sunday": "sun"}
EVERY_DAY = {"", "daily", "all", "everyday", "every day"}
MODES = ("dry-run", "self", "live")
MAX_BODY = 4000
# Worker exit reasons that mean "the session is unusable" — the whole run stops (R8, R16).
ABORT_REASONS = {"auth", "auth-timeout", "sender-mismatch", "launch-failed", "startup-timeout", "send-timeout"}
DEFAULTS = {"WA_MIN_DELAY": "8", "WA_MAX_DELAY": "25", "WA_MAX_PER_RUN": "10",
            # Empty means "let Puppeteer use its own downloaded Chromium" — its exact
            # protocol version is matched to the pinned puppeteer/whatsapp-web.js
            # versions, which the system's own (possibly much newer) Chrome is not.
            # Set this only to point at a specific browser deliberately.
            "WA_CHROME_PATH": "",
            "WA_QR_TIMEOUT": "120", "WA_NODE": "node",
            # v1.2 (R18, R19, R21): every wait on the worker is bounded.
            "WA_READY_TIMEOUT": "120", "WA_SEND_TIMEOUT": "60", "WA_KILL_MARGIN": "60",
            # v2.0 (R23): how long to wait after the last send for every message to reach the phone.
            "WA_ACK_TIMEOUT": "45"}
SETTINGS = ("WA_MODE", "WA_SENDER_NUMBER", "WA_TEST_NUMBER", "WA_MIN_DELAY", "WA_MAX_DELAY",
            "WA_MAX_PER_RUN", "WA_CHROME_PATH", "WA_QR_TIMEOUT", "WA_NODE", "WA_WORKER",
            "WA_READY_TIMEOUT", "WA_SEND_TIMEOUT", "WA_KILL_MARGIN", "WA_ACK_TIMEOUT")
# v2.0 (R23): whatsapp-web.js ack values -> what the log calls them. Ordered weakest first.
ACK_NAMES = {-1: "error", 0: "pending", 1: "server", 2: "delivered", 3: "read", 4: "read"}
DELIVERED = {"delivered", "read"}


def fail(msg):
    sys.exit(f"send_whatsapp: {msg}")


def mask(value):
    """Keep only the last 4 digits of every long digit run (R12)."""
    return re.sub(r"\d{8,}", lambda m: "****" + m.group(0)[-4:], str(value or ""))


def load_config(secrets_path):
    cfg = {}
    if os.path.exists(secrets_path):
        with open(secrets_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip().strip('"').strip("'")
    for k in SETTINGS:
        if os.environ.get(k):
            cfg[k] = os.environ[k]
    for k, v in DEFAULTS.items():
        cfg.setdefault(k, v)
    cfg.setdefault("WA_WORKER", WORKER)

    mode = cfg.get("WA_MODE", "").strip()
    if mode not in MODES:
        fail(f"WA_MODE must be one of {', '.join(MODES)} (got {mode!r}). Set it in {secrets_path}.")
    if mode != "dry-run" and not digits(cfg.get("WA_SENDER_NUMBER", "")):
        fail(f"WA_SENDER_NUMBER is required for mode {mode}. Set it in {secrets_path}.")
    if mode == "self" and not digits(cfg.get("WA_TEST_NUMBER", "")):
        fail("WA_TEST_NUMBER is required for mode 'self'.")
    for k in ("WA_MIN_DELAY", "WA_MAX_DELAY", "WA_MAX_PER_RUN", "WA_QR_TIMEOUT",
              "WA_READY_TIMEOUT", "WA_SEND_TIMEOUT", "WA_KILL_MARGIN", "WA_ACK_TIMEOUT"):
        try:
            float(cfg[k])
        except ValueError:
            fail(f"{k} must be a number (got {cfg[k]!r}).")
    if float(cfg["WA_MIN_DELAY"]) > float(cfg["WA_MAX_DELAY"]):
        fail("WA_MIN_DELAY must not be greater than WA_MAX_DELAY.")
    cfg["WA_MODE"] = mode
    return cfg


def digits(value):
    return re.sub(r"\D", "", value or "")


def read_todo(path, today):
    if not os.path.exists(path):
        fail(f"missing {path} — run skill 01-calendar-todo first.")
    file_date = datetime.fromtimestamp(os.path.getmtime(path), TZ).date()
    if file_date != today:
        fail(f"{path} is from {file_date}, not today ({today}) — run skill 01-calendar-todo first.")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid json: {exc}")
    if not isinstance(data, dict):
        fail(f"{path} must be an object of group -> items.")
    return data


def read_contacts(path):
    if not os.path.exists(path):
        fail(f"missing {path}.")
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    if not rows or "Person" not in rows[0]:
        fail(f"{path} needs a header row with a 'Person' column.")
    contacts, bad_days = {}, []
    for row in rows:
        person = (row.get("Person") or "").strip()
        if not person:
            continue
        key = person.lower()
        if key in contacts:
            fail(f"duplicate Person {person!r} in {path} — each person must appear once.")
        raw_days = (row.get("Send days") or "").strip()
        days, unknown = parse_days(raw_days)
        if unknown:
            bad_days.append((person, unknown))
        contacts[key] = {"person": person, "number": digits(row.get("Number")),
                         "days": days, "raw_days": raw_days}
    return contacts, bad_days


def parse_days(raw):
    if raw.strip().lower() in EVERY_DAY:
        return set(DAYS), []
    days, unknown = set(), []
    for token in raw.split(","):
        token = token.strip().lower()
        if not token:
            continue
        abbr = FULL.get(token, token)
        if abbr in DAYS:
            days.add(abbr)
        else:
            unknown.append(token)
    return days, unknown


def strip_own_name(title, person):
    """Remove the one (...) group that names this person; leave other parentheses alone."""
    target = person.strip().lower()
    for match in re.finditer(r"\(([^()]*)\)", title):
        inner = re.sub(r"^(from|by)\s+", "", match.group(1).strip(), flags=re.I)
        if any(part.strip().lower() == target for part in inner.split(",")):
            title = title[:match.start()] + title[match.end():]
            break
    title = re.sub(r"\s{2,}", " ", title).strip()
    # Removing a trailing "(Name)" can leave a dangling connector, e.g. "… Team by".
    title = re.sub(r"[\s\-–—:,]*\b(?:by|from|for)\s*$", "", title, flags=re.I)
    return re.sub(r"[\s\-–—:,]+$", "", title).strip()


def build_message(person, items, today):
    lines = [f"Hello {person},", f"Your pending items ({today.day} {today:%b %Y}):", ""]
    for n, item in enumerate(items, 1):
        lines.append(f"{n}. {strip_own_name(item['title'], person)}")
    return "\n".join(lines)


def split_message(text):
    if len(text) <= MAX_BODY:
        return [text]
    lines, parts, current = text.split("\n"), [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > MAX_BODY - 10:
            parts.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        parts.append(current)
    return [f"{p} ({i}/{len(parts)})" for i, p in enumerate(parts, 1)]


def delivery_of(acks):
    """The weakest receipt across a message's parts (R23, R24). No receipt seen -> pending."""
    if not acks:
        return "pending"
    weakest = min(0 if a is None else a for a in acks)  # a part with no receipt counts as pending
    return ACK_NAMES.get(weakest, "pending")


def kill_tree(proc):
    """Kill the worker and everything it started (Chrome). taskkill /T walks the tree on Windows."""
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def run_worker(cfg, recipients):
    """Hand the built messages to the node worker and read its result back.

    The job file holds real numbers, so it is written to the system temp dir (never the
    repo) and deleted again; only masked values ever reach the log (R12).
    """
    job = {"mode": cfg["WA_MODE"], "sender": digits(cfg["WA_SENDER_NUMBER"]),
           "auth_dir": AUTH_DIR, "chrome": cfg["WA_CHROME_PATH"],
           "min_delay": float(cfg["WA_MIN_DELAY"]), "max_delay": float(cfg["WA_MAX_DELAY"]),
           "qr_timeout": float(cfg["WA_QR_TIMEOUT"]),
           "ready_timeout": float(cfg["WA_READY_TIMEOUT"]),
           "send_timeout": float(cfg["WA_SEND_TIMEOUT"]),
           "ack_timeout": float(cfg["WA_ACK_TIMEOUT"]), "recipients": recipients}
    fd, job_path = tempfile.mkstemp(prefix="wa_job_", suffix=".json")
    os.close(fd)
    fd, res_path = tempfile.mkstemp(prefix="wa_res_", suffix=".json")
    os.close(fd)
    try:
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False)
        # R21: a hard ceiling. The worker has its own timers (R18, R19), but a wedged Chrome or an
        # event loop that never turns would defeat them, and on 2026-09-20/21 an unbounded worker
        # sat for 10+ minutes. Beyond the ceiling the whole process tree is killed.
        ceiling = (float(cfg["WA_READY_TIMEOUT"])
                   + len(recipients) * (float(cfg["WA_SEND_TIMEOUT"]) + float(cfg["WA_MAX_DELAY"]))
                   + float(cfg["WA_ACK_TIMEOUT"]) + float(cfg["WA_KILL_MARGIN"]))
        proc = subprocess.Popen([cfg["WA_NODE"], cfg["WA_WORKER"], job_path, res_path])
        killed = False
        try:
            proc.wait(timeout=ceiling)
        except subprocess.TimeoutExpired:
            killed = True
            kill_tree(proc)
        result = {}
        if os.path.exists(res_path):
            try:
                with open(res_path, encoding="utf-8") as fh:
                    result = json.load(fh)
            except json.JSONDecodeError:
                result = {}
        if killed:
            print(f"send_whatsapp: worker killed after {int(ceiling)}s "
                  f"(last stage: {result.get('stage', 'unknown')})", file=sys.stderr)
            result["reason"] = "worker-timeout"
            result["killed_after"] = int(ceiling)
        if not result and proc.returncode != 0:
            fail(f"whatsapp worker exited {proc.returncode} without reporting — nothing was sent.")
        reason = result.get("reason")
        if reason in ABORT_REASONS and not result.get("results"):
            fail(f"whatsapp session unusable ({reason}) — nothing was sent. See §12 of the plan.")
        return result, reason
    finally:
        for path in (job_path, res_path, res_path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass


def load_previous(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("runs", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def main():
    ap = argparse.ArgumentParser(description="Send module 01 to-do lists over WhatsApp.")
    ap.add_argument("--secrets", default=SECRETS)
    ap.add_argument("--todo", default=TODO)
    ap.add_argument("--contacts", default=CONTACTS)
    ap.add_argument("--log", default=LOG)
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    ap.add_argument("--check", action="store_true",
                    help="show the settings and the session state, then exit without sending")
    # v2.0 (R25): options for the manual-send UI.
    ap.add_argument("--people", help="comma-separated Persons to send to, ignoring their Send days")
    ap.add_argument("--resend", action="store_true",
                    help="with --people: send even if already sent today (logged resend: true)")
    ap.add_argument("--trigger", choices=("schedule", "manual", "manual-ui"), default="manual",
                    help="who started this run; stored on the run row")
    args = ap.parse_args()
    if args.resend and not args.people:
        fail("--resend needs --people: name exactly who should get a second copy.")

    cfg = load_config(args.secrets)
    mode = cfg["WA_MODE"]

    if args.check:
        print(f"config file : {args.secrets}")
        print(f"mode        : {mode}")
        for key in ("WA_SENDER_NUMBER", "WA_TEST_NUMBER"):
            value = cfg.get(key, "")
            print(f"{key:<13}: {mask(value) if value else 'MISSING'}")
        for key in ("WA_MIN_DELAY", "WA_MAX_DELAY", "WA_MAX_PER_RUN"):
            print(f"{key:<13}: {cfg[key]}")
        if cfg["WA_CHROME_PATH"]:
            print(f"WA_CHROME_PATH: {cfg['WA_CHROME_PATH']}")
            print(f"chrome      : {'found' if os.path.exists(cfg['WA_CHROME_PATH']) else 'NOT FOUND'}")
        else:
            print("chrome      : using Puppeteer's own downloaded Chromium (WA_CHROME_PATH unset)")
        print(f"session     : {'linked (' + AUTH_DIR + ')' if os.path.isdir(AUTH_DIR) else 'none — the first send will show a QR code'}")
        return 0

    now = datetime.now(TZ)
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else now.date()
    weekday = DAYS[today.weekday()]

    todo = read_todo(args.todo, today)
    contacts, bad_days = read_contacts(args.contacts)
    chosen = None
    if args.people:
        chosen = {p.strip().lower() for p in args.people.split(",") if p.strip()}
        unknown = sorted(p for p in chosen if p not in contacts)
        if unknown:
            fail(f"--people names nobody in {args.contacts}: {', '.join(unknown)} - nothing was sent.")
    previous = load_previous(args.log)
    sent_today = {}
    for run in previous:
        # Only a real ("live") send should ever count toward "already sent today" — a
        # self-mode test redirects to WA_TEST_NUMBER, so it must never make a same-day
        # live run skip a real recipient as already_sent.
        if run.get("date") == str(today) and run.get("mode") == "live":
            for entry in run.get("entries", []):
                # v1.2 (R19): "unconfirmed" counts as done - a person whose delivery is unknown
                # must never be re-sent automatically; the owner checks the chat and decides.
                if entry.get("status") in ("sent", "already_sent", "unconfirmed"):
                    sent_today[entry["person"]] = entry

    entries, queue = [], []
    cap = int(float(cfg["WA_MAX_PER_RUN"]))

    groups = set(todo) | {c["person"] for c in contacts.values()}
    if chosen is not None:  # R25: everyone not named is left out of the run entirely
        groups = {g for g in groups if g.strip().lower() in chosen}
    for group in sorted(groups, key=lambda g: (g == "No Name", g.lower())):
        items = todo.get(group, [])
        contact = contacts.get(group.strip().lower())
        if not contact:
            entries.append({"person": group, "status": "skipped_no_contact", "item_count": len(items)})
            continue
        resend = bool(args.resend and chosen is not None and group in sent_today)
        if group in sent_today and not resend:
            entries.append({**sent_today[group], "status": "already_sent"})
            continue
        if chosen is None and weekday not in contact["days"]:
            entries.append({"person": group, "status": "skipped_day", "item_count": len(items),
                            "send_days": contact["raw_days"]})
            continue
        if not items:
            entries.append({"person": group, "status": "skipped_no_items", "item_count": 0})
            continue

        message = build_message(contact["person"], items, today)
        entry = {"person": group, "item_count": len(items), "message": message}
        if resend:
            entry["resend"] = True
        entries.append(entry)
        if mode == "dry-run":
            entry["status"] = "dry-run"
            continue
        if len(queue) >= cap:
            entry["status"] = "skipped_over_cap"
            continue
        to = digits(cfg["WA_TEST_NUMBER"]) if mode == "self" else contact["number"]
        queue.append({"person": group, "to": to, "parts": split_message(message)})
        entry["status"] = "pending"

    sender, reason = None, None
    if queue:
        result, reason = run_worker(cfg, queue)
        sender = mask(result.get("sender", ""))
        reported = {row.get("person"): row for row in result.get("results", [])}
        for entry in entries:
            if entry.get("status") != "pending":
                continue
            row = reported.get(entry["person"])
            if row is None and entry["person"] == result.get("in_flight"):
                # The worker died with this person's send in progress: it may or may not have gone
                # out, so it is unconfirmed, not failed (R20, R21). Any receipt seen says which (R24).
                entry["status"] = "unconfirmed"
                entry["error"] = f"worker stopped mid-send ({reason}) - delivery unknown, check the chat"
                entry["delivery"] = delivery_of(result.get("in_flight_acks"))
            elif row is None:
                entry["status"] = "failed"
                if reason == "send-timeout":
                    entry["error"] = "not attempted (send-timeout)"
                elif reason == "worker-timeout":
                    entry["error"] = f"worker killed after {result.get('killed_after')}s - not attempted"
                else:
                    entry["error"] = f"worker did not report ({reason})" if reason else "worker did not report"
            elif row.get("status") == "unconfirmed":
                entry["status"] = "unconfirmed"
                entry["error"] = mask(row.get("error", ""))[:200]
                entry["delivery"] = delivery_of(row.get("acks"))
            elif row.get("status") == "ok":
                ids = [mask(i) for i in row.get("ids", [])]
                entry["status"] = "sent"
                entry["sent_at"] = row.get("at") or datetime.now(TZ).isoformat(timespec="seconds")
                entry["message_id"] = ids[0] if len(ids) == 1 else ids
                entry["delivery"] = delivery_of(row.get("acks"))
            elif row.get("status") == "not_registered":
                entry["status"] = "not_registered"
            else:
                entry["status"] = "failed"
                entry["error"] = mask(row.get("error", ""))[:200]

    counts = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    summary = {"sent": counts.get("sent", 0), "dry_run": counts.get("dry-run", 0),
               "failed": counts.get("failed", 0),
               "unconfirmed": counts.get("unconfirmed", 0),
               "not_registered": counts.get("not_registered", 0),
               "skipped": sum(v for k, v in counts.items()
                              if k.startswith("skipped") or k == "already_sent"),
               "bad_days": [f"{p}: {', '.join(u)}" for p, u in bad_days]}
    # v2.0 (R24): only a receipt proves anything; status alone never did.
    reached = [e for e in entries if e.get("status") in ("sent", "unconfirmed")]
    not_delivered = [e for e in reached if e.get("delivery") not in DELIVERED]
    summary["delivered"] = len(reached) - len(not_delivered)
    summary["not_delivered"] = len(not_delivered)

    run = {"date": str(today), "mode": mode, "trigger": args.trigger,
           "started_at": now.isoformat(timespec="seconds"),
           "entries": entries, "summary": summary}
    if sender:
        run["sender_verified"] = sender
    cutoff = str(today - timedelta(days=30))
    # v1.1: keep every run inside the window, including earlier runs from TODAY. The old filter
    # also dropped `r["date"] == today`, collapsing each date to one row — so a later dry-run or
    # self-mode test destroyed the same day's live row. That lost the delivery audit trail and,
    # because sent_today only counts live rows, re-armed a double-send (R17).
    # See docs/incidents/2026-09-19-02-whatsapp-message-sendlog-overwrite.md
    runs = [run] + [r for r in previous if r.get("date", "") >= cutoff]
    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    with open(args.log, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"runs": runs}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # v2.0 (R26): the cross-channel ledger. Never allowed to change this run's outcome.
    try:
        delivery_ledger.append([
            {"date": str(today), "channel": "whatsapp", "person": e["person"], "trigger": args.trigger,
             "mode": mode, "status": "resent" if e.get("resend") and e.get("status") == "sent" else e["status"],
             "delivery": e.get("delivery", ""),
             "detail": e.get("error") or (f"send days: {e['send_days']}" if e.get("send_days") else "")}
            for e in entries])
    except Exception as exc:  # noqa: BLE001 - any failure here is reported, never raised
        print(f"ledger: not written ({exc})", file=sys.stderr)

    print(f"mode={mode} date={today} ({weekday})")
    for entry in entries:
        extra = f" error={entry['error']}" if entry.get("error") else ""
        if entry.get("delivery"):
            extra = f" delivery={entry['delivery']}" + (" (resend)" if entry.get("resend") else "") + extra
        print(f"  {entry['person']:<20} {entry['status']:<18} items={entry['item_count']}{extra}")
    print("  " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "(no groups)"))
    for person, unknown in bad_days:
        tail = "ignored because --people was given" if chosen is not None else "not messaged today"
        print(f"  ! unknown 'Send days' value for {person}: {', '.join(unknown)} — {tail}")
    if counts.get("skipped_over_cap"):
        print(f"  ! {counts['skipped_over_cap']} people left for the next run (WA_MAX_PER_RUN={cap})")
    if reached:
        print(f"  delivered={summary['delivered']} not_delivered={summary['not_delivered']}")
    for entry in not_delivered:
        print(f"  ! {entry['person']}: not confirmed delivered (receipt: {entry.get('delivery')}) - check the chat")
    if reason:
        print(f"  ! worker stopped early: {reason}")
    print(f"log: {args.log}")
    return 1 if (summary["failed"] or summary["unconfirmed"]) else 0


if __name__ == "__main__":
    sys.exit(main())
