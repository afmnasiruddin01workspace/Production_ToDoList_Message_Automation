"""03-scheduler: health check run before the pipeline. Read-only except for a refreshed OAuth token.

Prints one JSON object {"ok": bool, "checks": [{id, status, detail, fix}]} and exits non-zero if any
check failed. Never prints a phone number, a token, or any other .secrets value.

The Google check deliberately NEVER constructs an InstalledAppFlow: module 01's fetch_tasks.py falls
back to an interactive browser sign-in when the token is dead, which would hang an unattended run
forever. Here that case is reported as `needs_signin` instead (test plan R3a / T3).
"""
import argparse, csv, json, os, shutil, socket, subprocess, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
SCOPES = ["https://www.googleapis.com/auth/tasks.readonly"]
MIN_FREE_MB = 500
HOLIDAY = "#holiday@group.v.calendar.google.com"


def check(cid, status, detail, fix=""):
    return {"id": cid, "status": status, "detail": detail, "fix": fix}


def google_token(secrets):
    """ok | needs_signin | fail — never opens a browser."""
    gdir = os.path.join(secrets, "google")
    cred, token = os.path.join(gdir, "credentials.json"), os.path.join(gdir, "token.json")
    if not os.path.exists(cred):
        return check("google_token", "fail", "no OAuth client credentials on disk",
                     "drop the Google Desktop OAuth client JSON into .secrets/downloads/ and run skill 01-calendar-todo once")
    if not os.path.exists(token):
        return check("google_token", "needs_signin", "no stored token",
                     "run skill 01-calendar-todo once by hand to complete the browser sign-in")
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as e:
        return check("google_token", "fail", f"google auth libraries missing ({e.name})",
                     "python -m pip install --user google-api-python-client google-auth-oauthlib")
    try:
        creds = Credentials.from_authorized_user_file(token, SCOPES)
    except Exception as e:
        return check("google_token", "needs_signin", f"stored token unreadable ({type(e).__name__})",
                     "delete .secrets/google/token.json and run skill 01-calendar-todo once to sign in again")
    if creds and creds.valid:
        return check("google_token", "ok", "token valid")
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:
            return check("google_token", "needs_signin", f"refresh rejected ({type(e).__name__})",
                         "run skill 01-calendar-todo once by hand to sign in again; if this recurs weekly, "
                         "publish the OAuth consent screen to 'In production' in Google Cloud Console")
        try:
            with open(token, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
        except OSError as e:
            return check("google_token", "fail", f"refreshed but could not save token ({type(e).__name__})",
                         "check write permissions on .secrets/google/")
        return check("google_token", "ok", "token refreshed")
    return check("google_token", "needs_signin", "token has no usable refresh grant",
                 "run skill 01-calendar-todo once by hand to sign in again")


def lists_file(src):
    path = os.path.join(src, "calendarLinks", "lists.txt")
    if not os.path.exists(path):
        return check("lists", "fail", "src/calendarLinks/lists.txt missing",
                     "restore the file (one calendar ID per line); see lists.sample.txt")
    usable = holiday = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if HOLIDAY in line:
                holiday += 1
            else:
                usable += 1
    if not usable:
        return check("lists", "fail", f"no usable calendar IDs ({holiday} holiday lines skipped)",
                     "add at least one non-holiday calendar ID to src/calendarLinks/lists.txt")
    return check("lists", "ok", f"{usable} calendar id(s), {holiday} holiday line(s) skipped")


def contacts_file(src):
    """Reads Person and the headers only. The Number column is never read or reported."""
    path = os.path.join(src, "messageListNDetails", "WhatsappContacts.txt")
    if not os.path.exists(path):
        return check("contacts", "fail", "src/messageListNDetails/WhatsappContacts.txt missing",
                     "restore the file; see WhatsappContacts.sample.txt for the columns")
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    except Exception as e:
        return check("contacts", "fail", f"unreadable ({type(e).__name__})",
                     "check the file is tab-separated with a header row")
    if not rows:
        return check("contacts", "fail", "no contact rows", "add at least one person")
    headers = {(h or "").strip().lower() for h in rows[0].keys()}
    for need in ("person", "number"):
        if need not in headers:
            return check("contacts", "fail", f"required column '{need}' missing",
                         "restore the header row: Person, WhatsApp contact name, Number, Send days")
    seen, dupes = set(), []
    for r in rows:
        key = (r.get("Person") or "").strip().lower()
        if not key:
            continue
        if key in seen:
            dupes.append(key)
        seen.add(key)
    if dupes:
        return check("contacts", "fail", f"duplicate Person value(s): {', '.join(sorted(set(dupes)))}",
                     "each person must appear exactly once in WhatsappContacts.txt")
    days = "send days" in headers
    return check("contacts", "ok", f"{len(seen)} people, Send days column {'present' if days else 'absent (everyone every day)'}")


def wa_session(secrets):
    path = os.path.join(secrets, "wweb-auth")
    if not os.path.isdir(path) or not os.listdir(path):
        return check("wa_session", "fail", "no linked WhatsApp session",
                     "run skill 02-whatsapp-message by hand and scan the QR from WhatsApp -> Settings -> Linked devices")
    return check("wa_session", "ok", "linked session present")


def wa_config(repo, secrets, python_exe):
    """Reuses module 02's own --check, forcing live so WA_SENDER_NUMBER is validated."""
    script = os.path.join(repo, ".claude", "skills", "02-whatsapp-message", "send_whatsapp.py")
    if not os.path.exists(script):
        return check("wa_config", "fail", "module 02 send_whatsapp.py missing",
                     "restore .claude/skills/02-whatsapp-message/")
    env_file = os.path.join(secrets, "whatsapp.env")  # --secrets is the env FILE, not the directory
    if not os.path.exists(env_file):
        return check("wa_config", "fail", ".secrets/whatsapp.env missing",
                     "create it with WA_MODE, WA_SENDER_NUMBER and WA_TEST_NUMBER (see module 02's SKILL.md)")
    env = dict(os.environ, WA_MODE="live")
    try:
        p = subprocess.run([python_exe, script, "--secrets", env_file, "--check"],
                           capture_output=True, text=True, env=env, timeout=60)
    except subprocess.TimeoutExpired:
        return check("wa_config", "fail", "send_whatsapp.py --check timed out", "run it by hand to see why")
    if p.returncode != 0:
        # The last traceback line is the actual error; the first is just "Traceback (most recent...)".
        lines = [l.strip() for l in (p.stderr or p.stdout).strip().splitlines() if l.strip()]
        return check("wa_config", "fail", lines[-1] if lines else f"exit {p.returncode}",
                     "fix .secrets/whatsapp.env (WA_MODE, WA_SENDER_NUMBER, WA_TEST_NUMBER)")
    out = p.stdout
    if "MISSING" in out:
        missing = [l.split(":")[0].strip() for l in out.splitlines() if "MISSING" in l]
        return check("wa_config", "fail", f"unset in whatsapp.env: {', '.join(missing)}",
                     "set the missing key(s) in .secrets/whatsapp.env")
    if "NOT FOUND" in out:
        return check("wa_config", "fail", "WA_CHROME_PATH points at a missing Chrome",
                     "correct WA_CHROME_PATH in .secrets/whatsapp.env")
    return check("wa_config", "ok", "settings usable for a live run")


def tools(python_exe):
    missing = [n for n in ("claude", "node") if not shutil.which(n)]
    if not os.path.exists(python_exe) and not shutil.which(python_exe):
        missing.append("python")
    if missing:
        return check("tools", "fail", f"not on PATH: {', '.join(missing)}",
                     "the scheduled task runs as your user - confirm these resolve in a plain terminal")
    return check("tools", "ok", "claude, node and python all resolve")


def disk(repo):
    free_mb = shutil.disk_usage(repo).free // (1024 * 1024)
    if free_mb < MIN_FREE_MB:
        return check("disk", "fail", f"{free_mb} MB free, need {MIN_FREE_MB} MB",
                     "free space on the repo drive - Chrome needs room for the session profile")
    return check("disk", "ok", f"{free_mb} MB free")


def network():
    down = []
    for host in ("www.googleapis.com", "web.whatsapp.com"):
        try:
            socket.create_connection((host, 443), timeout=6).close()
        except OSError:
            down.append(host)
    if down:
        return check("network", "fail", f"unreachable: {', '.join(down)}",
                     "check the internet connection, VPN or firewall")
    return check("network", "ok", "googleapis and whatsapp reachable")


def main():
    ap = argparse.ArgumentParser(description="03-scheduler pre-flight health check.")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--secrets", default=None, help="override .secrets (for tests)")
    ap.add_argument("--src", default=None, help="override src/ (for tests)")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--skip", default="", help="comma-separated check ids to skip (for tests)")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    secrets = os.path.abspath(args.secrets) if args.secrets else os.path.join(repo, ".secrets")
    src = os.path.abspath(args.src) if args.src else os.path.join(repo, "src")
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    runners = [
        ("google_token", lambda: google_token(secrets)),
        ("lists", lambda: lists_file(src)),
        ("contacts", lambda: contacts_file(src)),
        ("wa_session", lambda: wa_session(secrets)),
        ("wa_config", lambda: wa_config(repo, secrets, args.python)),
        ("tools", lambda: tools(args.python)),
        ("disk", lambda: disk(repo)),
        ("network", network),
    ]
    checks = []
    for cid, fn in runners:
        if cid in skip:
            checks.append(check(cid, "skipped", "skipped by --skip"))
            continue
        try:
            checks.append(fn())
        except Exception as e:  # a crashing check is a failed check, never a crashed run
            checks.append(check(cid, "fail", f"check crashed: {type(e).__name__}: {e}",
                                "this is a bug in preflight.py - see the run transcript"))
    ok = all(c["status"] in ("ok", "skipped") for c in checks)
    json.dump({"ok": ok, "checks": checks}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
