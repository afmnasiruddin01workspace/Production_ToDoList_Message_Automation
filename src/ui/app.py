"""05-manual-ui — a local web page to send the to-do list by hand and to see whether each send went.

Loopback only. Sends nothing itself: every send is the command line of module 02 (WhatsApp) or
module 04 (email), run exactly as published with `--people … --trigger manual-ui`. Plan: plan/stableMD/05-manual-ui.stable.md.
"""
import argparse, atexit, csv, ctypes, hmac, importlib.util, io, ipaddress, json, os, re, secrets
import shutil, socket, subprocess, sys, threading, time, webbrowser
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, Response, abort, jsonify, make_response, redirect, render_template, request

TZ = ZoneInfo("Asia/Dhaka")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
OUT = os.path.join(ROOT, "outputs", "05-manual-ui")
JOBS_DIR = os.path.join(OUT, "jobs")
INSTANCE = os.path.join(OUT, "ui.json")
SCHED_LOCK = os.path.join(ROOT, "outputs", "03-scheduler", "run.lock")
RUN_LOG = os.path.join(ROOT, "outputs", "03-scheduler", "run_log.json")
SCHED_ENV = os.path.join(ROOT, "src", "scheduler", "scheduler.env")
SCHED_SETTINGS = os.path.join(ROOT, "src", "scheduler", "scheduler.settings.json")
REBUILD_PROMPT = os.path.join(HERE, "rebuild_prompt.txt")
TODO = os.path.join(ROOT, "outputs", "01-calendar-todo", "todo_by_name.json")
WA_CONTACTS = os.path.join(ROOT, "src", "messageListNDetails", "WhatsappContacts.txt")
EMAIL_CONTACTS = os.path.join(ROOT, "src", "EmailContent", "EmailContacts.txt")
EMAIL_ENV = os.path.join(ROOT, ".secrets", "email.env")
WA_LOG = os.path.join(ROOT, "outputs", "02-whatsapp-message", "send_log.json")
EMAIL_LOG = os.path.join(ROOT, "outputs", "04-email-message", "send_log.json")
LEDGER = os.environ.get("DELIVERY_LEDGER") or os.path.join(ROOT, "outputs", "delivery", "delivery_log.json")
WA_SCRIPT = os.path.join(ROOT, ".claude", "skills", "02-whatsapp-message", "send_whatsapp.py")
EMAIL_SCRIPT = os.path.join(ROOT, ".claude", "skills", "04-email-message", "send_email.py")

MODES = ("dry-run", "self", "live")
OWNER_GROUP = "a f m nasir uddin"
LOCK_MAX_AGE_MIN = 30
GUARD_BEFORE_MIN, GUARD_AFTER_MIN = 6, 3
JOB_KEEP_DAYS = 30
STEP_LIMIT_SEC = {"whatsapp": 25 * 60, "email": 15 * 60, "reconcile": 10 * 60, "rebuild": 10 * 60}
SENT_STATUSES = ("sent", "already_sent", "unconfirmed")
GOOD = {"read", "delivered", "accepted"}
TROUBLE = {"whatsapp": "02-whatsapp-message", "email": "04-email-message", "rebuild": "05-manual-ui",
           "reconcile": "04-email-message"}

ADDR_ANY = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")

TOKEN = secrets.token_urlsafe(32)  # in memory only (R2)
app = Flask(__name__, template_folder=os.path.join(HERE, "templates"), static_folder=os.path.join(HERE, "static"))
app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]  # R1: a forged Host (DNS rebinding) -> 400
app.config["JSON_SORT_KEYS"] = False

_mods = {}


# ------------------------------------------------------------------ helpers

def scrub(value):
    """R16: no address and no 8+ digit run in anything shown, saved or exported."""
    text = ADDR_ANY.sub("<address>", str(value if value is not None else ""))
    return re.sub(r"\d{8,}", lambda m: "****" + m.group(0)[-4:], text)


def now():
    return datetime.now(TZ)


def local_hms(ts):
    """HH:MM:SS in Asia/Dhaka for an ISO timestamp. Module 02 stores sent_at in UTC ('…Z'), module 04 with +06:00."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt.astimezone(TZ) if dt.tzinfo else dt).strftime("%H:%M:%S")
    except ValueError:
        return ""


def today():
    """Table logic only (R4); sends always use the real date. UI_TODAY is a test hook."""
    override = os.environ.get("UI_TODAY")
    return datetime.strptime(override, "%Y-%m-%d").date() if override else now().date()


def load_module(name, path):
    if name not in _mods:
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # defines functions only (the modules' main() is guarded)
        _mods[name] = mod
    return _mods[name]


def m02():
    return load_module("send_whatsapp", WA_SCRIPT)


def m04():
    return load_module("send_email", EMAIL_SCRIPT)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def read_env(path):
    out = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def sched_setting(key, default):
    """Same precedence as the wrapper's Get-Setting: environment beats the file."""
    return os.environ.get(key) or read_env(SCHED_ENV).get(key) or default


def pid_alive(pid):
    # os.kill(pid, 0) would TERMINATE the process on Windows, so ask the OS directly.
    try:
        k = ctypes.windll.kernel32
        handle = k.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(handle, ctypes.byref(code))
        k.CloseHandle(handle)
        return bool(ok) and code.value == 259
    except (OSError, ValueError, AttributeError):
        return False


def kill_tree(proc):
    if proc.poll() is not None:
        return
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass


# ------------------------------------------------------------------ the scheduler's lock (R9, R10)

def lock_info():
    """The live holder of the scheduler lock as {pid, started, who}, or None if free/stale."""
    try:
        with open(SCHED_LOCK, encoding="utf-8-sig") as fh:
            info = json.load(fh)
        started = datetime.fromisoformat(str(info["started"]))
        if started.tzinfo is None:
            started = started.replace(tzinfo=TZ)
        age_min = (now() - started).total_seconds() / 60
        pid = int(info["pid"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        try:  # unreadable: the holder may be mid-write, so trust it only briefly
            if time.time() - os.path.getmtime(SCHED_LOCK) < 60:
                return {"pid": 0, "started": "", "who": "another run (lock being written)"}
        except OSError:
            pass
        return None
    if age_min > LOCK_MAX_AGE_MIN or not pid_alive(pid):
        return None
    who = "this UI" if pid == os.getpid() else "the scheduled run or another job"
    return {"pid": pid, "started": started.strftime("%H:%M"), "who": who}


def acquire_sched_lock():
    os.makedirs(os.path.dirname(SCHED_LOCK), exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(SCHED_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_info():
                return False
            try:
                os.remove(SCHED_LOCK)
            except OSError:
                pass
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "started": now().isoformat(timespec="seconds")}, fh)
        return True
    return False


def release_sched_lock():
    try:
        with open(SCHED_LOCK, encoding="utf-8-sig") as fh:
            if int(json.load(fh).get("pid", -1)) != os.getpid():
                return
        os.remove(SCHED_LOCK)
    except (OSError, ValueError, json.JSONDecodeError):
        pass


def sched_time():
    raw = sched_setting("SCHED_TIME", "10:00")
    try:
        hh, mm = raw.split(":")
        return int(hh), int(mm)
    except ValueError:
        return 10, 0


def email_time():
    """Module 03 v3.0's SCHED_EMAIL_TIME as (h, m), or None for `off` / unusable / equal to SCHED_TIME (R25)."""
    raw = sched_setting("SCHED_EMAIL_TIME", "13:30")
    if raw == "off":
        return None
    try:
        hh, mm = raw.split(":")
        t = (int(hh), int(mm))
        if not (0 <= t[0] < 24 and 0 <= t[1] < 60) or t == sched_time():
            return None
        return t
    except ValueError:
        return None


def next_at(hm):
    n = now()
    nxt = n.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    return nxt + timedelta(days=1) if nxt <= n else nxt


def guard_reason():
    """R10: refuse a lock-taking job when holding the lock could make the scheduled run skip the day."""
    hh, mm = sched_time()
    n = now()
    at = n.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if at - timedelta(minutes=GUARD_BEFORE_MIN) <= n <= at + timedelta(minutes=GUARD_AFTER_MIN):
        return (f"The scheduled run starts at {hh:02d}:{mm:02d}. Holding the lock now would make it skip the day "
                f"(a skipped run is not retried). Try again after {(at + timedelta(minutes=GUARD_AFTER_MIN)):%H:%M}.")
    return None


# ------------------------------------------------------------------ data (R4, R5)

def list_state():
    try:
        mtime = datetime.fromtimestamp(os.path.getmtime(TODO), TZ)
    except OSError:
        return {"exists": False, "fresh": False, "date": "", "when": "", "data": {}}
    data = read_json(TODO, {})
    return {"exists": True, "fresh": mtime.date() == today(), "date": str(mtime.date()),
            "when": mtime.strftime("%d %b %Y %H:%M"), "data": data if isinstance(data, dict) else {}}


def wa_contacts():
    """R16: only Person and Send days are read — never the contact name or the number."""
    sw = m02()
    out = {}
    try:
        with open(WA_CONTACTS, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                person = (row.get("Person") or "").strip()
                if person:
                    days, _ = sw.parse_days((row.get("Send days") or "").strip())
                    out[person.lower()] = {"person": person, "days": days, "valid": True}
    except OSError:
        pass
    return out


def email_contacts():
    """Module 04's own reader, reduced at once to Person, Send days and a valid-address boolean."""
    try:
        contacts, _ = m04().read_contacts(EMAIL_CONTACTS, m02())
    except SystemExit:
        return {}
    return {k: {"person": c["person"], "days": c["days"], "valid": bool(c["address"])} for k, c in contacts.items()}


def sent_today_map(log_path, day):
    """person -> newest live send info for `day` (status sent/unconfirmed preferred over already_sent)."""
    best = {}
    data = read_json(log_path, {})
    for run in (data.get("runs", []) if isinstance(data, dict) else []):
        if run.get("date") != str(day) or run.get("mode") != "live":
            continue
        for e in run.get("entries", []):
            if e.get("status") not in SENT_STATUSES:
                continue
            cur = best.get(e["person"])
            if cur is None or (cur.get("status") == "already_sent" and e["status"] != "already_sent"):
                best[e["person"]] = e
    return best


def last_entry_today(log_path, day):
    """person -> newest entry of any mode for `day` (runs are stored newest first)."""
    seen = {}
    data = read_json(log_path, {})
    for run in (data.get("runs", []) if isinstance(data, dict) else []):
        if run.get("date") != str(day):
            continue
        for e in run.get("entries", []):
            seen.setdefault(e.get("person"), {**e, "_mode": run.get("mode")})
    return seen


def cell_state(person, contact, items, due, sent, last, channel, fresh):
    """One table cell: the state text, its colour class, the pre-tick and resend eligibility (R4)."""
    if contact is None:
        return {"present": False, "state": "not on this channel", "cls": "mute", "tick": False}
    n = len(items)
    cell = {"present": True, "due": due, "count": n, "resend": False, "tick": False}
    e = sent.get(person)
    if e:
        at = local_hms(e.get("sent_at"))[:5]
        delivery = e.get("delivery") or ""
        if e.get("status") == "unconfirmed":
            cell.update(state="unconfirmed" + (f" · {delivery}" if delivery else ""), cls="warn")
        else:
            cls = "ok" if delivery in GOOD else "bad" if delivery == "bounced" else "amber" if delivery in ("server", "queued") else "warn"
            cell.update(state=f"sent {at}".strip() + (f" · {delivery}" if delivery else ""), cls=cls)
        cell["resend"] = True
        cell["first_sent"] = at
        return cell
    l = last.get(person)
    if l and l.get("status") == "failed":
        cell.update(state="failed", cls="bad")
    elif l and l.get("status") == "invalid_address":
        cell.update(state="invalid address", cls="bad")
    elif l and l.get("_mode") != "live" and l.get("status") in ("dry-run", "sent"):
        cell.update(state=f"{l['_mode']} done", cls="mute")
    elif n == 0:
        cell.update(state="no items", cls="mute")
    else:
        cell.update(state="not sent", cls="mute")
    if channel == "email" and not contact["valid"]:
        cell.update(state="invalid address", cls="bad")
    cell["tick"] = bool(fresh and due and n > 0 and not (channel == "email" and not contact["valid"]))
    return cell


def build_table():
    ls = list_state()
    day = today()
    weekday = m02().DAYS[day.weekday()]
    data = ls["data"]
    wa, em = wa_contacts(), email_contacts()
    wa_sent, em_sent = sent_today_map(WA_LOG, day), sent_today_map(EMAIL_LOG, day)
    wa_last, em_last = last_entry_today(WA_LOG, day), last_entry_today(EMAIL_LOG, day)
    names = {}
    for src in (wa, em):
        for k, c in src.items():
            if k != "no name" and k != OWNER_GROUP:
                names.setdefault(k, c["person"])
    rows = []
    for k in sorted(names, key=lambda x: x):
        person = names[k]
        items = data.get(person, [])
        row = {"person": person}
        for ch, src, sent, last in (("whatsapp", wa, wa_sent, wa_last), ("email", em, em_sent, em_last)):
            c = src.get(k)
            row[ch] = cell_state(person, c, items, bool(c and weekday in c["days"]), sent, last, ch, ls["fresh"])
        rows.append(row)
    return {"list": {k: v for k, v in ls.items() if k != "data"}, "weekday": weekday, "date": str(day), "rows": rows}


def status_strip():
    runs = read_json(RUN_LOG, {})
    runs = runs.get("runs", []) if isinstance(runs, dict) else []
    last = None
    if runs:
        r = runs[0]
        ch = r.get("channels") or {}
        last = {"when": scrub(str(r.get("started_at", ""))[:16].replace("T", " ")), "trigger": scrub(r.get("trigger", "")),
                "kind": scrub(r.get("run_kind", "")) + " run" if r.get("run_kind") in ("email", "whatsapp") else "",
                "status": scrub(r.get("status", "")),
                "whatsapp": scrub((ch.get("whatsapp") or {}).get("status", "")),
                "email": scrub((ch.get("email") or {}).get("status", "")),
                "warnings": [scrub(w) for w in (r.get("warnings") or [])][:5]}
    fmt = "%a %d %b %H:%M"
    sched_email = sched_setting("SCHED_EMAIL", "1") != "0"
    et = email_time()
    wa_next = next_at(sched_time()).strftime(fmt)
    if not sched_email:
        next_run = f"WhatsApp {wa_next} · Email off"
    elif et:
        next_run = f"WhatsApp {wa_next} · Email {next_at(et).strftime(fmt)}"
    else:
        next_run = f"WhatsApp {wa_next} · Email with the {sched_time()[0]:02d}:{sched_time()[1]:02d} run"
    lk = lock_info()
    return {"last_run": last, "sched_email": sched_email,
            "next_run": next_run,
            "lock": ({"who": lk["who"], "since": lk["started"]} if lk else None)}


# ------------------------------------------------------------------ the log page (R13)

def legacy_rows(before):
    """WhatsApp rows from module 02's own log that happened strictly before `before` ("date time")."""
    rows = []
    data = read_json(WA_LOG, {})
    for run in (data.get("runs", []) if isinstance(data, dict) else []):
        for e in run.get("entries", []):
            sent_at = e.get("sent_at") or run.get("started_at") or ""
            if f"{run.get('date', '')} {local_hms(sent_at)}" >= before:
                continue
            rows.append({"date": run.get("date", ""), "time": local_hms(sent_at), "channel": "whatsapp",
                         "person": e.get("person", ""), "trigger": run.get("trigger") or "unknown",
                         "mode": run.get("mode", ""), "status": e.get("status", ""), "delivery": e.get("delivery", ""),
                         "detail": scrub(e.get("error") or (f"send days: {e['send_days']}" if e.get("send_days") else "")),
                         "source": "legacy"})
    return rows


def merge_rows():
    ledger = read_json(LEDGER, {})
    ledger = ledger.get("rows", []) if isinstance(ledger, dict) else []
    rows = [{**{k: scrub(r.get(k, "")) for k in ("date", "time", "channel", "person", "trigger", "mode", "status", "delivery", "detail")},
             "source": "ledger"} for r in ledger]
    # The ledger is authoritative from its first row on; module 02's own log fills in what came before
    # (down to the second, since the first ledger row may be mid-day), so nothing is listed twice.
    first = min((f"{r['date']} {r['time']}" for r in rows), default="9999-12-31 99:99:99")
    rows += legacy_rows(first)
    rows.sort(key=lambda r: (r["date"], r["time"]), reverse=True)
    return rows


def filter_rows(rows, args):
    show_skipped = args.get("skipped") == "1"
    out = []
    for r in rows:
        if args.get("from") and r["date"] < args["from"]:
            continue
        if args.get("to") and r["date"] > args["to"]:
            continue
        if args.get("channel") and r["channel"] != args["channel"]:
            continue
        if args.get("person") and r["person"].lower() != args["person"].lower():
            continue
        if args.get("status") and r["status"] != args["status"]:
            continue
        if args.get("trigger") and r["trigger"] != args["trigger"]:
            continue
        if args.get("mode") and r["mode"] != args["mode"]:
            continue
        if not show_skipped and r["status"].startswith("skipped"):
            continue
        out.append(r)
    return out


def row_class(r):
    d, s = r["delivery"], r["status"]
    if d in GOOD:
        return "ok"
    if d in ("bounced",) or s in ("failed", "error", "invalid_address", "not_registered"):
        return "bad"
    if d in ("server", "queued"):
        return "amber"
    if d == "pending" or s == "unconfirmed":
        return "warn"
    return "mute" if s.startswith(("skipped", "already", "dry")) else ""


# ------------------------------------------------------------------ the job (R8, R9, R11, R12, R19)

JOB_SLOT = threading.Lock()
job = {"id": 0, "state": "idle"}  # state: idle | running | done | failed
job_view = threading.Lock()


_transcript = {"path": None}


def new_job(kind):
    started = now()
    path = os.path.join(JOBS_DIR, f"{started:%Y-%m-%dT%H%M%S}-{kind}.txt")
    with job_view:
        job.clear()
        job.update({"id": int(time.time() * 1000), "kind": kind, "state": "running", "step": "starting",
                    "lines": [], "steps": [], "outcome": [], "started": started.isoformat(timespec="seconds"),
                    "ended": "", "message": ""})
        _transcript["path"] = path
        _final.update(state="failed", message="the job ended without a result")
    try:  # R19: written as the job runs, so a transcript survives even if this process is killed mid-job
        os.makedirs(JOBS_DIR, exist_ok=True)
        open(path, "w", encoding="utf-8", newline="\n").close()
    except OSError:
        _transcript["path"] = None


def add_line(text):
    text = scrub(text).rstrip()
    if text:
        with job_view:
            job["lines"].append(text)
            del job["lines"][:-2000]
            if _transcript["path"]:
                try:
                    with open(_transcript["path"], "a", encoding="utf-8", newline="\n") as fh:
                        fh.write(text + "\n")
                except OSError:
                    pass


def set_step(name):
    with job_view:
        job["step"] = name


def save_transcript():
    """Close the transcript with the step results, then drop transcripts older than JOB_KEEP_DAYS."""
    os.makedirs(JOBS_DIR, exist_ok=True)
    if _transcript["path"]:
        with open(_transcript["path"], "a", encoding="utf-8", newline="\n") as fh:
            fh.write("steps: " + scrub(json.dumps(job["steps"])) + "\n")
    cutoff = time.time() - JOB_KEEP_DAYS * 86400
    for name in os.listdir(JOBS_DIR):
        p = os.path.join(JOBS_DIR, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
        except OSError:
            pass


def run_step(label, kind, argv, env, limit, stdin_path=None, quiet=False):
    """Run one module command; returns (exit_code, timed_out)."""
    set_step(label)
    add_line(f"== {label} ==")
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    timed_out = {"v": False}

    def watchdog():
        try:
            proc.wait(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out["v"] = True
            kill_tree(proc)

    threading.Thread(target=watchdog, daemon=True).start()
    for line in proc.stdout:
        if not quiet:
            add_line(line)
    proc.wait()
    return proc.returncode, timed_out["v"]


def child_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("WA_MODE", "EMAIL_MODE")}
    env.update({k: v for k, v in extra.items() if v is not None})
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def newest_row(log_path, mode, since):
    """The newest run row written by this job: trigger manual-ui, this mode, started after the step began."""
    data = read_json(log_path, {})
    for run in (data.get("runs", []) if isinstance(data, dict) else []):
        if run.get("trigger") == "manual-ui" and run.get("mode") == mode and str(run.get("started_at", "")) >= since:
            return run
    return None


def step_result(channel, label, mode, people, argv, env, since):
    code, timed_out = run_step(label, channel, argv, env, STEP_LIMIT_SEC[channel])
    log = WA_LOG if channel == "whatsapp" else EMAIL_LOG
    row = newest_row(log, mode, since)
    res = {"channel": channel, "exit": code, "status": "ok", "message": ""}
    if timed_out:
        res.update(status="failed", message="timed out and was stopped")
    elif row is None:
        res.update(status="failed", message="the module finished without writing a log row")
    elif code != 0:
        res.update(status="failed", message=f"the module reported a failure (exit {code})")
    if "skipped_locked" in " ".join(job["lines"][-5:]) and channel == "email":
        res.update(status="failed", message="another email run holds module 04's lock; nothing was sent")
    res["help"] = f"docs/TROUBLESHOOTING.md#{TROUBLE[channel]}"
    out = []
    if row:
        for e in row.get("entries", []):
            if e.get("person", "").lower() in {p.lower() for p in people}:
                out.append({"person": e["person"], "channel": channel, "status": scrub(e.get("status", "")),
                            "delivery": scrub(e.get("delivery", "")), "resend": bool(e.get("resend")),
                            "detail": scrub(e.get("error", ""))[:200]})
    return res, out


def job_send(plan):
    """plan: {mode, whatsapp:[names], email:[names], resend:{whatsapp:[], email:[]}}"""
    holds = False
    try:
        mode = plan["mode"]
        if plan["whatsapp"] and mode != "dry-run":  # dry-run never starts the WhatsApp browser (R9)
            reason = guard_reason()
            if reason:
                raise RuntimeError(reason)
            if not acquire_sched_lock():
                lk = lock_info() or {}
                raise RuntimeError(f"The scheduler lock is held by {lk.get('who', 'another run')} since {lk.get('started', '?')}.")
            holds = True
        for channel in ("whatsapp", "email"):
            people = plan[channel]
            if not people:
                continue
            script = WA_SCRIPT if channel == "whatsapp" else EMAIL_SCRIPT
            argv = [sys.executable, "-u", script, "--people", ",".join(people), "--trigger", "manual-ui"]
            if plan["resend"][channel]:
                argv.append("--resend")
            env = child_env(**({"WA_MODE": mode} if channel == "whatsapp" else {"EMAIL_MODE": mode}))
            since = now().isoformat(timespec="seconds")
            res, out = step_result(channel, f"{channel} — {len(people)} person(s), {mode}", mode, people, argv, env, since)
            with job_view:
                job["steps"].append(res)
                job["outcome"].extend(out)
        failed = [s for s in job["steps"] if s["status"] != "ok"]
        conclude("failed" if failed else "done", "; ".join(f"{s['channel']}: {s['message']}" for s in failed) if failed else "finished")
    except Exception as exc:  # noqa: BLE001 - shown to the owner, scrubbed
        conclude("failed", scrub(exc))
    finally:
        if holds:
            release_sched_lock()
        finish_job()


_final = {"state": "failed", "message": "the job ended without a result"}


def conclude(state, message):
    """Remember the outcome; it is published by finish_job() only after the scheduler lock is released."""
    _final["state"], _final["message"] = state, message


def finish_job():
    with job_view:
        job["ended"] = now().isoformat(timespec="seconds")
        job["step"] = "finished"
    try:
        save_transcript()
    except OSError:
        pass
    with job_view:  # published last: the lock is already released and the transcript is closed
        job["state"], job["message"] = _final["state"], _final["message"]
    JOB_SLOT.release()


def job_reconcile():
    try:
        argv = [sys.executable, "-u", EMAIL_SCRIPT, "--reconcile"]
        env = child_env(EMAIL_MODE="live")  # reconcile sends nothing; the module only needs a valid mode
        code, timed_out = run_step("email — refresh receipts", "reconcile", argv, env, STEP_LIMIT_SEC["reconcile"])
        ok = code == 0 and not timed_out
        res = {"channel": "email", "exit": code, "status": "ok" if ok else "failed",
               "message": "" if ok else ("timed out and was stopped" if timed_out else f"exit {code}"),
               "help": "docs/TROUBLESHOOTING.md#04-email-message"}
        job["steps"].append(res)
        conclude("done" if ok else "failed", "receipts refreshed" if ok else res["message"])
    except Exception as exc:  # noqa: BLE001
        conclude("failed", scrub(exc))
    finally:
        finish_job()


def job_rebuild():
    holds = False
    try:
        reason = guard_reason()
        if reason:
            raise RuntimeError(reason)
        if not acquire_sched_lock():
            lk = lock_info() or {}
            raise RuntimeError(f"The scheduler lock is held by {lk.get('who', 'another run')} since {lk.get('started', '?')}.")
        holds = True
        exe = os.environ.get("UI_CLAUDE_EXE") or shutil.which("claude")
        if not exe:
            raise RuntimeError("the 'claude' command was not found on PATH")
        model = sched_setting("SCHED_MODEL", "sonnet")
        os.makedirs(OUT, exist_ok=True)
        transcript = os.path.join(OUT, "rebuild_envelope.json")
        argv = [exe, "-p", "--settings", SCHED_SETTINGS, "--model", model, "--permission-mode", "manual",
                "--permission-prompts", "none", "--output-format", "json"]
        before = os.path.getmtime(TODO) if os.path.exists(TODO) else 0
        set_step("rebuild — module 01 (headless Claude)")
        add_line("== rebuild — module 01 (headless Claude) ==")
        t0 = time.time()
        with open(REBUILD_PROMPT, "rb") as pin, open(transcript, "wb") as pout:
            proc = subprocess.Popen(argv, cwd=ROOT, stdin=pin, stdout=pout, stderr=subprocess.STDOUT, env=child_env(),
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timed_out = False
            try:
                proc.wait(timeout=STEP_LIMIT_SEC["rebuild"])
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_tree(proc)
        secs = int(time.time() - t0)
        cost, denials, text = None, 0, ""
        env_json = read_json(transcript, None)
        if isinstance(env_json, dict):
            cost = env_json.get("total_cost_usd")
            denials = len(env_json.get("permission_denials") or [])
            text = str(env_json.get("result", ""))[:600]
        try:
            os.remove(transcript)
        except OSError:
            pass
        add_line(f"claude finished in {secs}s" + (f", cost ${cost:.2f}" if isinstance(cost, (int, float)) else "")
                 + (f", {denials} permission denial(s)" if denials else ""))
        if text:
            add_line(text)
        fresh = os.path.exists(TODO) and datetime.fromtimestamp(os.path.getmtime(TODO), TZ).date() == now().date() \
            and os.path.getmtime(TODO) > before
        ok = fresh and not timed_out
        res = {"channel": "rebuild", "exit": proc.returncode, "status": "ok" if ok else "failed",
               "message": "" if ok else (f"timed out after {STEP_LIMIT_SEC['rebuild'] // 60} minutes and was stopped" if timed_out
                                         else "the list was not rewritten for today; the old file is unchanged"),
               "help": "docs/TROUBLESHOOTING.md#05-manual-ui", "cost": cost, "seconds": secs}
        job["steps"].append(res)
        conclude("done" if ok else "failed", "list rebuilt" if ok else res["message"])
    except Exception as exc:  # noqa: BLE001
        conclude("failed", scrub(exc))
    finally:
        if holds:
            release_sched_lock()
        finish_job()


def start_job(kind, target, *args):
    if not JOB_SLOT.acquire(blocking=False):
        abort(make_response(jsonify(error="A job is already running."), 409))
    new_job(kind)
    threading.Thread(target=target, args=args, daemon=True).start()


# ------------------------------------------------------------------ request validation (R17)

def bad(msg, code=400):
    abort(make_response(jsonify(error=msg), code))


def validate_send(body):
    if not isinstance(body, dict):
        bad("The request must be a JSON object.")
    mode = body.get("mode")
    if mode not in MODES:
        bad("mode must be dry-run, self or live.")
    contacts = {"whatsapp": wa_contacts(), "email": email_contacts()}
    plan = {"mode": mode, "whatsapp": [], "email": [], "resend": {"whatsapp": [], "email": []}}
    for ch in ("whatsapp", "email"):
        names = body.get(ch) or []
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            bad(f"{ch} must be a list of names.")
        for n in names:
            c = contacts[ch].get(n.strip().lower())
            if not c:
                bad(f"'{scrub(n)[:40]}' is not in the {ch} contacts.")
            if c["person"] not in plan[ch]:
                plan[ch].append(c["person"])
    if not plan["whatsapp"] and not plan["email"]:
        bad("Choose at least one person on at least one channel.")
    ls = list_state()
    if not ls["fresh"]:
        bad("The to-do list is not from today. Rebuild it first.")
    if mode == "live" and body.get("confirm_live") is not True:
        bad("live needs the confirmation box ticked.")
    resend = body.get("resend") or {}
    for ch, sent_log in (("whatsapp", WA_LOG), ("email", EMAIL_LOG)):
        sent = sent_today_map(sent_log, today())
        asked = [n for n in (resend.get(ch) or []) if isinstance(n, str)]
        for n in asked:
            c = contacts[ch].get(n.strip().lower())
            if not c or c["person"] not in plan[ch]:
                bad(f"'{scrub(n)[:40]}' cannot be resent: not selected on {ch}.")
            if c["person"] not in sent:
                bad(f"{c['person']} has no live send today on {ch}, so there is nothing to resend.")
            plan["resend"][ch].append(c["person"])
        if asked and (mode != "live" or body.get("confirm_resend") is not True):
            bad("A resend needs live mode and its own confirmation.")
        if mode == "live":
            unconfirmed = [p for p in plan[ch] if p in sent and p not in plan["resend"][ch]]
            if unconfirmed:
                bad(f"{', '.join(unconfirmed)} already got today's {ch} message. Untick them or confirm a resend.")
    return plan


def precheck_lock(needs_lock):
    """R9/R10 at request time so the owner sees the reason at once (the job re-checks when it starts)."""
    if not needs_lock:
        return
    reason = guard_reason()
    if reason:
        bad(reason, 409)
    lk = lock_info()
    if lk:
        bad(f"A run is in progress (the scheduler lock is held by {lk['who']} since {lk['started']}).", 409)


# ------------------------------------------------------------------ routes

def authed():
    return hmac.compare_digest(request.cookies.get("ui_auth", ""), TOKEN)


@app.before_request
def guard():
    _ = request.host  # R1: a forged Host header (DNS rebinding) raises here -> 400, before anything else
    if request.path == "/" and request.method == "GET" and hmac.compare_digest(request.args.get("t", ""), TOKEN):
        return None  # first visit: the route sets the cookie
    if not authed():
        return Response("Not authorised. Start the UI from the launcher (start_ui.cmd): it opens the right link.\n",
                        status=401, mimetype="text/plain")
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            return Response("Cross-site request refused.\n", status=403, mimetype="text/plain")
        site = request.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return Response("Cross-site request refused.\n", status=403, mimetype="text/plain")
    return None


@app.after_request
def no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return resp


@app.route("/")
def index():
    if request.args.get("t"):
        resp = redirect("/")
        resp.set_cookie("ui_auth", TOKEN, httponly=True, samesite="Strict", path="/")
        return resp
    return render_template("send.html", table=build_table(), strip=status_strip(), page="send")


@app.route("/api/state")
def api_state():
    strip = status_strip()
    return jsonify(table=build_table(), strip=strip, strip_html=render_template("_strip.html", strip=strip))


@app.route("/api/preview")
def api_preview():
    person, channel = request.args.get("person", ""), request.args.get("channel", "")
    ls = list_state()
    if not ls["fresh"]:
        bad("The to-do list is not from today.")
    src = (wa_contacts() if channel == "whatsapp" else email_contacts() if channel == "email" else None)
    if src is None or person.lower() not in src:
        bad("Unknown person or channel.")
    name = src[person.lower()]["person"]
    items = ls["data"].get(name, [])
    if not items:
        return jsonify(text="(no items today, so nothing would be sent)", subject="")
    text = m02().build_message(name, items, today())
    if channel == "whatsapp":
        return jsonify(text=scrub(text), subject="")
    env = read_env(EMAIL_ENV)
    m = m04()
    subject = os.environ.get("EMAIL_SUBJECT") or env.get("EMAIL_SUBJECT") or m.DEFAULT_SUBJECT
    sig = os.environ.get("EMAIL_SIGNATURE") or env.get("EMAIL_SIGNATURE") or m.DEFAULT_SIGNATURE
    return jsonify(subject=scrub(subject), text=scrub(text + "\n\n\n" + "\n".join(s for s in sig.split("|") if s)))


@app.route("/api/send", methods=["POST"])
def api_send():
    plan = validate_send(request.get_json(silent=True))
    precheck_lock(bool(plan["whatsapp"]) and plan["mode"] != "dry-run")
    start_job("send", job_send, plan)
    return jsonify(ok=True)


@app.route("/api/rebuild", methods=["POST"])
def api_rebuild():
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        bad("Rebuild needs the confirmation (it costs money and takes minutes).")
    precheck_lock(True)
    start_job("rebuild", job_rebuild)
    return jsonify(ok=True)


@app.route("/api/reconcile", methods=["POST"])
def api_reconcile():
    body = request.get_json(silent=True) or {}
    if body.get("confirm") is not True:
        bad("Refresh needs the confirmation (Outlook may be started).")
    start_job("reconcile", job_reconcile)
    return jsonify(ok=True)


@app.route("/api/job")
def api_job():
    with job_view:
        return jsonify({k: job.get(k) for k in ("id", "kind", "state", "step", "lines", "steps", "outcome", "started", "ended", "message")})


@app.route("/log")
def log_page():
    rows = merge_rows()
    shown = filter_rows(rows, request.args)
    for r in shown:
        r["cls"] = row_class(r)
    opts = {"channels": sorted({r["channel"] for r in rows}), "people": sorted({r["person"] for r in rows}),
            "statuses": sorted({r["status"] for r in rows}), "triggers": sorted({r["trigger"] for r in rows}),
            "modes": sorted({r["mode"] for r in rows})}
    return render_template("log.html", rows=shown[:1000], total=len(shown), opts=opts, q=request.args,
                           strip=status_strip(), page="log")


@app.route("/log.csv")
def log_csv():
    shown = filter_rows(merge_rows(), request.args)
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = ("date", "time", "channel", "person", "trigger", "mode", "status", "delivery", "detail", "source")
    w.writerow(cols)
    for r in shown:
        # a cell that starts with = + - @ would be run as a formula by a spreadsheet
        w.writerow([("'" + str(r[c]) if str(r[c])[:1] in ("=", "+", "-", "@") else r[c]) for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=delivery_log_{now():%Y%m%d}.csv"})


# ------------------------------------------------------------------ server start / stop (R1, R3)

def loopback_host(host):
    if host == "localhost":
        return "127.0.0.1"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return None
    return str(ip) if ip.version == 4 and ip.is_loopback else None


def read_instance():
    info = read_json(INSTANCE, None)
    if isinstance(info, dict) and info.get("pid") and pid_alive(info["pid"]):
        return info
    return None


def stop_instance():
    info = read_instance()
    if not info:
        try:
            os.remove(INSTANCE)
        except OSError:
            pass
        print("The UI is not running.")
        return 0
    subprocess.run(["taskkill", "/PID", str(info["pid"]), "/T", "/F"], capture_output=True)
    try:
        os.remove(INSTANCE)
    except OSError:
        pass
    print("The UI was stopped.")
    return 0


def cleanup_instance():
    info = read_json(INSTANCE, {})
    if isinstance(info, dict) and info.get("pid") == os.getpid():
        try:
            os.remove(INSTANCE)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description="Local manual-send UI (loopback only).")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("UI_PORT", "8765")))
    ap.add_argument("--stop", action="store_true", help="stop the running UI and exit")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser (tests)")
    args = ap.parse_args()
    if args.stop:
        return stop_instance()
    host = loopback_host(args.host)
    if host is None:
        print(f"ui: refusing to listen on {args.host!r}: only a loopback address (127.0.0.1) is allowed.", file=sys.stderr)
        return 2
    running = read_instance()
    if running:
        print(f"The UI is already running at http://127.0.0.1:{running.get('port')}/ (started {running.get('started')}). "
              "Use the open browser tab, or 'start_ui.cmd --stop' and start again.")
        return 3
    os.makedirs(OUT, exist_ok=True)
    with open(INSTANCE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"pid": os.getpid(), "port": args.port, "started": now().isoformat(timespec="seconds")}, fh)
    atexit.register(cleanup_instance)
    url = f"http://{host}:{args.port}/?t={TOKEN}"
    if not args.no_browser:
        def opener():
            for _ in range(50):
                try:
                    socket.create_connection((host, args.port), timeout=0.2).close()
                    webbrowser.open(url)
                    return
                except OSError:
                    time.sleep(0.2)
        threading.Thread(target=opener, daemon=True).start()
    print(f"Manual-send UI on http://{host}:{args.port}/  (close this window or Ctrl+C to stop)")
    if args.no_browser:
        print(f"token-url: {url}")
    else:
        print("If the browser did not open, copy this link once (it is valid only for this session):")
        print(url)
    app.run(host=host, port=args.port, threaded=True, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
