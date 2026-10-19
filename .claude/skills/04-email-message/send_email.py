#!/usr/bin/env python3
"""04-email-message — email each person their module 01 to-do list, through the owner's Outlook.

The text is module 02's (imported, not copied), the layout is the owner's reference screenshot, and
the transport is classic Outlook via email_worker.py in a child process. This half owns config,
inputs, schedule, idempotency, the lock, the logs, the delivery ledger and the hard ceiling; it
never imports win32com and never touches Outlook itself.
See plan/stableMD/04-email-message.stable.md for the contract this implements.
"""
import argparse, csv, ctypes, html, importlib.util, json, os, random, re, subprocess, sys, tempfile, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Dhaka")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SECRETS = os.path.join(ROOT, ".secrets", "email.env")
CONTACTS = os.path.join(ROOT, "src", "EmailContent", "EmailContacts.txt")
TODO = os.path.join(ROOT, "outputs", "01-calendar-todo", "todo_by_name.json")
OUT_DIR = os.path.join(ROOT, "outputs", "04-email-message")
LOG = os.path.join(OUT_DIR, "send_log.json")
LOCK = os.path.join(OUT_DIR, "run.lock")
WORKER = os.path.join(HERE, "email_worker.py")
sys.path.insert(0, os.path.join(ROOT, "src", "common"))
import delivery_ledger  # noqa: E402  (R19)

MODES = ("dry-run", "self", "live")
DEFAULT_SUBJECT = "Pending Task List, Please communicate if any obstacle and close the issue if finished."
DEFAULT_SIGNATURE = "Regards|A F M Nasir Uddin|HOD of Fastener Production|ID 9781"
ABORT_REASONS = {"outlook-unavailable", "send-timeout"}
DEFAULTS = {"EMAIL_MAX_PER_RUN": "10", "EMAIL_MIN_DELAY": "1", "EMAIL_MAX_DELAY": "3",
            "EMAIL_SUBJECT": DEFAULT_SUBJECT, "EMAIL_SIGNATURE": DEFAULT_SIGNATURE,
            "EMAIL_OUTLOOK_PROFILE": "", "EMAIL_STARTUP_TIMEOUT": "90", "EMAIL_SEND_TIMEOUT": "60",
            "EMAIL_VERIFY_TIMEOUT": "30", "EMAIL_BOUNCE_WAIT": "30", "EMAIL_KILL_MARGIN": "60",
            "EMAIL_PYTHON": sys.executable, "EMAIL_WORKER": WORKER}
SETTINGS = ("EMAIL_MODE", "EMAIL_TEST_ADDRESS") + tuple(DEFAULTS)
NUMERIC = ("EMAIL_MAX_PER_RUN", "EMAIL_MIN_DELAY", "EMAIL_MAX_DELAY", "EMAIL_STARTUP_TIMEOUT",
           "EMAIL_SEND_TIMEOUT", "EMAIL_VERIFY_TIMEOUT", "EMAIL_BOUNCE_WAIT", "EMAIL_KILL_MARGIN")
ADDR_ANY = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
ADDR_OK = re.compile(r"^[^@\s,;<>()]+@[^@\s,;<>()]+\.[^@\s,;<>()]+$")


def fail(msg):
    sys.exit(f"send_email: {msg}")


def scrub(value):
    """R20: no address and no 8+ digit run in anything that is printed or written."""
    text = ADDR_ANY.sub("<address>", str(value or ""))
    return re.sub(r"\d{8,}", lambda m: "****" + m.group(0)[-4:], text)


def load_module02():
    path = os.path.join(ROOT, ".claude", "skills", "02-whatsapp-message", "send_whatsapp.py")
    spec = importlib.util.spec_from_file_location("send_whatsapp", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # defines functions only: no worker starts, no WhatsApp setting is read
    return mod


def from02(fn, *args):
    """Call a module 02 helper; its fail() exits with its own name on the message, so re-badge it."""
    try:
        return fn(*args)
    except SystemExit as exc:
        msg = str(exc.code)
        fail(msg.split(": ", 1)[1] if msg.startswith("send_whatsapp: ") else msg)


# ---------------------------------------------------------------- config

def load_config(path):
    cfg = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
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
    mode = cfg.get("EMAIL_MODE", "").strip()
    if mode not in MODES:
        fail(f"EMAIL_MODE must be one of {', '.join(MODES)} (got {mode!r}). Set it in {path}.")
    test = cfg.get("EMAIL_TEST_ADDRESS", "").strip()
    if mode == "self" and not (test == "me" or ADDR_OK.match(test)):
        fail("EMAIL_TEST_ADDRESS is required for mode 'self': an address, or 'me' for your own Outlook address.")
    for k in NUMERIC:
        try:
            float(cfg[k])
        except ValueError:
            fail(f"{k} must be a number (got {cfg[k]!r}).")
    if float(cfg["EMAIL_MIN_DELAY"]) > float(cfg["EMAIL_MAX_DELAY"]):
        fail("EMAIL_MIN_DELAY must not be greater than EMAIL_MAX_DELAY.")
    cfg["EMAIL_MODE"], cfg["EMAIL_TEST_ADDRESS"] = mode, test
    return cfg


# ---------------------------------------------------------------- contacts (R1, R2, R3)

def read_contacts(path, sw):
    if not os.path.exists(path):
        fail(f"missing {path}.")
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    if not rows or not rows[0] or rows[0][0].strip() != "Person":
        fail(f"{path} needs a header row starting with a 'Person' column.")
    contacts, bad_days = {}, []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        person = row[0].strip()
        key = person.lower()
        if key in contacts:
            fail(f"duplicate Person {person!r} in {path} — each person must appear once.")
        # R1: the WhatsApp name and number columns are never kept. Field 4 is "<address> <Send days>".
        tail = row[3].split() if len(row) > 3 else []
        address = tail[0] if tail else ""
        raw_days = " ".join(tail[1:])
        days, unknown = sw.parse_days(raw_days)
        if unknown:
            bad_days.append((person, unknown))
        contacts[key] = {"person": person, "address": address if ADDR_OK.match(address) else None,
                         "days": days, "raw_days": raw_days}
    return contacts, bad_days


# ---------------------------------------------------------------- message (R5)

def render_html(text, signature):
    body = html.escape(text).replace("\n", "<br>\n")
    sig = "<br>\n".join(html.escape(line) for line in signature)
    # item line ends (1), then two blank lines (2) — as in the owner's screenshot
    return ('<div style="font-family:Aptos,Calibri,Arial,sans-serif;font-size:12pt">'
            f"{body}<br><br><br>\n{sig}</div>")


# ---------------------------------------------------------------- Outlook facts (no COM here)

def default_profile():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\Outlook") as key:
            return winreg.QueryValueEx(key, "DefaultProfile")[0]
    except (ImportError, OSError):
        return ""


def outlook_exe():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE") as key:
            path = winreg.QueryValueEx(key, "")[0]
            if path and os.path.exists(path):
                return path
    except (ImportError, OSError):
        pass
    for path in (r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE",
                 r"C:\Program Files (x86)\Microsoft Office\root\Office16\OUTLOOK.EXE"):
        if os.path.exists(path):
            return path
    return ""


def outlook_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE", "/NH"], capture_output=True, text=True).stdout
    return "OUTLOOK.EXE" in out


# ---------------------------------------------------------------- lock (R23)

def pid_alive(pid):
    # os.kill(pid, 0) would TERMINATE the process on Windows, so ask the OS directly.
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
    k.CloseHandle(handle)
    return bool(ok) and code.value == 259  # STILL_ACTIVE


def lock_held():
    try:
        age = time.time() - os.path.getmtime(LOCK)
        with open(LOCK, encoding="utf-8") as fh:
            info = json.load(fh)
    except (OSError, json.JSONDecodeError):
        # Unreadable: the holder may be mid-write. Trust it only briefly.
        try:
            return time.time() - os.path.getmtime(LOCK) < 60
        except OSError:
            return False
    return age < 3 * 3600 and pid_alive(info.get("pid", 0))


def acquire_lock():
    os.makedirs(OUT_DIR, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_held():
                return False
            try:
                os.remove(LOCK)
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": datetime.now(TZ).isoformat(timespec="seconds")}, fh)
        return True
    return False


def release_lock():
    try:
        with open(LOCK, encoding="utf-8") as fh:
            if json.load(fh).get("pid") != os.getpid():
                return
        os.remove(LOCK)
    except (OSError, json.JSONDecodeError):
        pass


# ---------------------------------------------------------------- worker (R13)

def kill_tree(proc):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


def run_worker(cfg, job, ceiling):
    """Hand the job to the Outlook worker and read its result back, even after a kill. The job file holds
    real addresses, so it lives in the system temp dir and is always deleted."""
    job = {**job, "exe": outlook_exe(), "profile": cfg["EMAIL_OUTLOOK_PROFILE"] or default_profile(),
           "startup_timeout": float(cfg["EMAIL_STARTUP_TIMEOUT"]), "send_timeout": float(cfg["EMAIL_SEND_TIMEOUT"]),
           "verify_timeout": float(cfg["EMAIL_VERIFY_TIMEOUT"]), "bounce_wait": float(cfg["EMAIL_BOUNCE_WAIT"]),
           "min_delay": float(cfg["EMAIL_MIN_DELAY"]), "max_delay": float(cfg["EMAIL_MAX_DELAY"])}
    fd, job_path = tempfile.mkstemp(prefix="email_job_", suffix=".json")
    os.close(fd)
    fd, res_path = tempfile.mkstemp(prefix="email_res_", suffix=".json")
    os.close(fd)
    try:
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False)
        proc = subprocess.Popen([cfg["EMAIL_PYTHON"], "-u", cfg["EMAIL_WORKER"], job_path, res_path])
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
            print(f"send_email: worker killed after {int(ceiling)}s (last stage: {result.get('stage', 'unknown')})",
                  file=sys.stderr)
            result["reason"] = "worker-timeout"
            result["killed_after"] = int(ceiling)
        if not result and proc.returncode != 0:
            fail(f"outlook worker exited {proc.returncode} without reporting — nothing was sent.")
        reason = result.get("reason")
        if reason in ABORT_REASONS and not result.get("results"):
            fail(f"Outlook unusable ({reason}) — nothing was sent. See §12 of the plan.")
        return result, reason
    finally:
        for path in (job_path, res_path, res_path + ".tmp"):
            try:
                os.remove(path)
            except OSError:
                pass


def ceiling_for(cfg, n):
    return (float(cfg["EMAIL_STARTUP_TIMEOUT"])
            + n * (float(cfg["EMAIL_SEND_TIMEOUT"]) + float(cfg["EMAIL_MAX_DELAY"]) + float(cfg["EMAIL_VERIFY_TIMEOUT"]))
            + float(cfg["EMAIL_BOUNCE_WAIT"]) + float(cfg["EMAIL_KILL_MARGIN"]))


# ---------------------------------------------------------------- log

def load_previous(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("runs", [])
    except (json.JSONDecodeError, AttributeError):
        return []


def write_log(runs, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"runs": runs}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def ledger(rows):
    try:
        delivery_ledger.append(rows)
    except Exception as exc:  # noqa: BLE001 - R19: never allowed to change the run's outcome
        print(f"ledger: not written ({scrub(exc)})", file=sys.stderr)


# ---------------------------------------------------------------- --check, --reconcile

def do_check(cfg_path, args, sw):
    print(f"config file : {cfg_path}")
    mode = os.environ.get("EMAIL_MODE") or ""
    try:
        cfg = load_config(cfg_path)
        mode = cfg["EMAIL_MODE"]
        print(f"mode        : {mode}")
        test = cfg["EMAIL_TEST_ADDRESS"]
        print(f"test address: {'me (own Outlook address)' if test == 'me' else ('set (masked)' if test else 'MISSING')}")
        for key in ("EMAIL_MAX_PER_RUN", "EMAIL_MIN_DELAY", "EMAIL_MAX_DELAY", "EMAIL_STARTUP_TIMEOUT"):
            print(f"{key:<22}: {cfg[key]}")
        profile = cfg["EMAIL_OUTLOOK_PROFILE"] or default_profile()
        print(f"outlook profile       : {'set (name withheld)' if profile else 'NONE FOUND'}")
    except SystemExit as exc:
        print(f"settings    : {exc.code}")
    exe = outlook_exe()
    print(f"classic Outlook       : {'found' if exe else 'MISSING (COM automation needs classic Outlook, not New Outlook)'}")
    print(f"Outlook running now   : {'yes' if outlook_running() else 'no (the run would start it)'}")
    try:
        contacts, bad = read_contacts(args.contacts, sw)
        valid = sum(1 for c in contacts.values() if c["address"])
        print(f"contacts    : {len(contacts)} people, {valid} with a valid address, {len(bad)} with unreadable Send days")
    except SystemExit as exc:
        print(f"contacts    : {exc.code}")
    return 0


def do_reconcile(cfg, args, sw):
    contacts, _ = read_contacts(args.contacts, sw)
    runs = load_previous(args.log)
    cutoff = str((datetime.now(TZ) - timedelta(days=3)).date())
    targets, where = [], {}
    for ri, run in enumerate(runs):
        if run.get("mode") != "live" or run.get("date", "") < cutoff:
            continue
        for ei, entry in enumerate(run.get("entries", [])):
            contact = contacts.get(str(entry.get("person", "")).strip().lower())
            if (entry.get("status") in ("sent", "unconfirmed") and entry.get("delivery") != "bounced"
                    and contact and contact["address"] and entry.get("sent_at")):
                key = f"{ri}:{ei}"
                since = datetime.fromisoformat(entry["sent_at"]).timestamp()
                targets.append({"key": key, "to": contact["address"], "since": since})
                where[key] = (run, entry)
    if not targets:
        print("reconcile: nothing to check (no live sends in the last 3 days that are still unchecked)")
        return 0
    print(f"reconcile: checking {len(targets)} send(s) from the last 3 days for non-delivery reports")
    result, reason = run_worker(cfg, {"mode": "reconcile", "run_id": "reconcile", "targets": targets}, 210)
    rows = []
    for key in result.get("bounced", []):
        run, entry = where[key]
        entry["delivery"] = "bounced"
        entry["detail"] = "non-delivery report received"
        rows.append({"channel": "email", "person": entry["person"], "trigger": "reconcile", "mode": "live",
                     "status": "sent", "delivery": "bounced", "detail": "non-delivery report received",
                     "date": run.get("date", "")})
        print(f"  ! {entry['person']}: non-delivery report received for the send on {run.get('date')}", file=sys.stderr)
    if rows:
        write_log(runs, args.log)
        ledger(rows)
    print(f"reconcile: {len(rows)} bounce(s) newly found")
    return 1 if reason and reason != "worker-timeout" else 0


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Email module 01 to-do lists through Outlook.")
    ap.add_argument("--secrets", default=SECRETS)
    ap.add_argument("--todo", default=TODO)
    ap.add_argument("--contacts", default=CONTACTS)
    ap.add_argument("--log", default=LOG)
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD), for tests")
    ap.add_argument("--check", action="store_true", help="show settings and Outlook's state; send nothing")
    ap.add_argument("--reconcile", action="store_true", help="look for bounces of the last 3 days; send nothing")
    ap.add_argument("--people", help="comma-separated Persons to mail, ignoring their Send days")
    ap.add_argument("--resend", action="store_true", help="with --people: send even if already sent today")
    ap.add_argument("--trigger", choices=("schedule", "manual", "manual-ui"), default="manual")
    args = ap.parse_args()
    if args.resend and not args.people:
        fail("--resend needs --people: name exactly who should get a second copy.")

    sw = load_module02()
    if args.check:
        return do_check(args.secrets, args, sw)
    cfg = load_config(args.secrets)
    mode = cfg["EMAIL_MODE"]
    signature = [s for s in cfg["EMAIL_SIGNATURE"].split("|") if s != ""]
    subject_base = cfg["EMAIL_SUBJECT"]

    holds_lock = False
    if mode != "dry-run" or args.reconcile:
        if not acquire_lock():
            print("send_email: skipped_locked — another run (scheduler or manual UI) is in progress; nothing sent.")
            return 0
        holds_lock = True
    try:
        if args.reconcile:
            return do_reconcile(cfg, args, sw)
        return run(cfg, mode, args, sw, signature, subject_base)
    finally:
        if holds_lock:
            release_lock()


def run(cfg, mode, args, sw, signature, subject_base):
    now = datetime.now(TZ)
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else now.date()
    weekday = sw.DAYS[today.weekday()]

    todo = from02(sw.read_todo, args.todo, today)
    contacts, bad_days = read_contacts(args.contacts, sw)
    chosen = None
    if args.people:
        chosen = {p.strip().lower() for p in args.people.split(",") if p.strip()}
        unknown = sorted(p for p in chosen if p not in contacts)
        if unknown:
            fail(f"--people names nobody in the email contacts: {', '.join(scrub(u) for u in unknown)} - nothing was sent.")
    previous = load_previous(args.log)
    sent_today = {}
    for prev in previous:
        # R10: only a real (live) send counts; unconfirmed counts as done so a retry can never double-send.
        if prev.get("date") == str(today) and prev.get("mode") == "live":
            for entry in prev.get("entries", []):
                if entry.get("status") in ("sent", "already_sent", "unconfirmed"):
                    sent_today[entry["person"]] = entry

    entries, queue = [], []
    cap = int(float(cfg["EMAIL_MAX_PER_RUN"]))
    groups = set(todo) | {c["person"] for c in contacts.values()}
    if chosen is not None:
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
        if not contact["address"]:
            entries.append({"person": group, "status": "invalid_address", "item_count": len(items),
                            "error": "no single well-formed address in the 4th field of the contacts row"})
            continue
        if chosen is None and weekday not in contact["days"]:
            entries.append({"person": group, "status": "skipped_day", "item_count": len(items),
                            "send_days": contact["raw_days"]})
            continue
        if not items:
            entries.append({"person": group, "status": "skipped_no_items", "item_count": 0})
            continue
        text = from02(sw.build_message, contact["person"], items, today)
        subject = f"[SELF-TEST for {contact['person']}] {subject_base}" if mode == "self" else subject_base
        entry = {"person": group, "item_count": len(items), "subject": subject, "message": text}
        if resend:
            entry["resend"] = True
        entries.append(entry)
        if mode == "dry-run":
            entry["status"] = "dry-run"
            continue
        if len(queue) >= cap:
            entry["status"] = "skipped_over_cap"
            continue
        to = "@me" if (mode == "self" and cfg["EMAIL_TEST_ADDRESS"] == "me") else (
            cfg["EMAIL_TEST_ADDRESS"] if mode == "self" else contact["address"])
        queue.append({"key": group, "to": to, "subject": subject, "html": render_html(text, signature)})
        entry["status"] = "pending"

    reason, started_outlook = None, False
    if queue:
        run_id = now.strftime("%Y%m%d%H%M%S") + f"-{os.getpid()}"
        result, reason = run_worker(cfg, {"mode": "send", "run_id": run_id, "recipients": queue}, ceiling_for(cfg, len(queue)))
        started_outlook = bool(result.get("outlook_started"))
        reported = {row.get("key"): row for row in result.get("results", [])}
        for entry in entries:
            if entry.get("status") != "pending":
                continue
            row = reported.get(entry["person"])
            if row is None and entry["person"] == result.get("in_flight"):
                entry["status"] = "unconfirmed"
                entry["delivery"] = "pending"
                entry["error"] = f"worker stopped mid-send ({reason}) - delivery unknown, check Sent Items"
            elif row is None:
                entry["status"] = "failed"
                if reason == "send-timeout":
                    entry["error"] = "not attempted (send-timeout)"
                elif reason == "worker-timeout":
                    entry["error"] = f"worker killed after {result.get('killed_after')}s - not attempted"
                else:
                    entry["error"] = f"worker did not report ({reason})" if reason else "worker did not report"
            elif row.get("status") == "ok":
                entry["status"] = "sent"
                entry["sent_at"] = row.get("at") or datetime.now(TZ).isoformat(timespec="seconds")
                entry["delivery"] = row.get("delivery", "pending")
                entry["to_resolved"] = bool(row.get("to_resolved"))
            elif row.get("status") == "unconfirmed":
                entry["status"] = "unconfirmed"
                entry["delivery"] = row.get("delivery", "pending")
                entry["to_resolved"] = bool(row.get("to_resolved"))
                entry["error"] = scrub(row.get("error", ""))[:200]
            else:
                entry["status"] = "failed"
                entry["error"] = scrub(row.get("error", ""))[:200]
        for key in result.get("bounced", []):
            for entry in entries:
                if entry["person"] == key and entry.get("status") in ("sent", "unconfirmed"):
                    entry["delivery"] = "bounced"
                    entry["detail"] = "non-delivery report received"

    counts = {}
    for entry in entries:
        counts[entry["status"]] = counts.get(entry["status"], 0) + 1
    delivered_to = [e for e in entries if e.get("status") in ("sent", "unconfirmed")]
    summary = {"sent": counts.get("sent", 0), "dry_run": counts.get("dry-run", 0), "failed": counts.get("failed", 0),
               "unconfirmed": counts.get("unconfirmed", 0), "invalid_address": counts.get("invalid_address", 0),
               "skipped": sum(v for k, v in counts.items() if k.startswith("skipped") or k == "already_sent"),
               "bad_days": [f"{p}: {', '.join(u)}" for p, u in bad_days],
               "accepted": sum(1 for e in delivered_to if e.get("delivery") == "accepted"),
               "queued": sum(1 for e in delivered_to if e.get("delivery") == "queued"),
               "bounced": sum(1 for e in delivered_to if e.get("delivery") == "bounced")}
    run_row = {"date": str(today), "mode": mode, "trigger": args.trigger, "started_at": now.isoformat(timespec="seconds"),
               "outlook_profile_started": started_outlook, "entries": entries, "summary": summary}
    cutoff = str(today - timedelta(days=30))
    write_log([run_row] + [r for r in previous if r.get("date", "") >= cutoff], args.log)

    ledger([{"date": str(today), "channel": "email", "person": e["person"], "trigger": args.trigger, "mode": mode,
             "status": "resent" if e.get("resend") and e.get("status") == "sent" else e["status"],
             "delivery": e.get("delivery", ""),
             "detail": e.get("error") or e.get("detail") or (f"send days: {e['send_days']}" if e.get("send_days") else "")}
            for e in entries])

    print(f"mode={mode} date={today} ({weekday})")
    for e in entries:
        extra = f" error={scrub(e['error'])}" if e.get("error") else ""
        if e.get("delivery"):
            extra = f" delivery={e['delivery']}" + (" (resend)" if e.get("resend") else "") + extra
        print(f"  {e['person']:<20} {e['status']:<18} items={e['item_count']}{extra}")
    print("  " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) if counts else "(no groups)"))
    for person, unknown in bad_days:
        if chosen is not None and person.lower() not in chosen:
            continue  # not part of this --people run: nothing to warn about
        tail = "ignored because --people was given" if chosen is not None else "not mailed today"
        print(f"  ! unknown 'Send days' value for {person}: {', '.join(unknown)} — {tail}")
    if counts.get("skipped_over_cap"):
        print(f"  ! {counts['skipped_over_cap']} people left for the next run (EMAIL_MAX_PER_RUN={cap})")
    for e in entries:
        if e.get("delivery") == "bounced":
            print(f"  ! {e['person']}: non-delivery report received - the address is probably wrong", file=sys.stderr)
        elif e.get("delivery") == "queued":
            print(f"  ! {e['person']}: still in the Outbox (Outlook offline or not synchronised) - it will go when Outlook reconnects", file=sys.stderr)
        elif e.get("status") == "invalid_address":
            print(f"  ! {e['person']}: invalid or missing address in the contacts file - not mailed", file=sys.stderr)
    if reason:
        print(f"  ! worker stopped early: {reason}")
    print(f"log: {args.log}")
    failed = summary["failed"] or summary["unconfirmed"] or (reason is not None)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
