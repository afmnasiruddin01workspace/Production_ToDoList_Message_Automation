#!/usr/bin/env python3
"""04-email-message — Outlook worker (the only process that touches Outlook).

Usage: python email_worker.py <job.json> <result.json>

send_email.py decides who gets what; this only drives Outlook. It exists as its own process so a
wedged Outlook can never wedge the caller: every phase below runs under a watchdog (R13), the
result file is rewritten atomically after every recipient, and the parent kills this whole
process tree if even the watchdog is not enough.

Real addresses live only in the job file (system temp dir, deleted by the parent). Nothing this
file prints or writes contains an address or an 8+ digit run (R20).
"""
import datetime, json, os, random, re, subprocess, sys, threading, time

job_path, result_path = sys.argv[1:3]
with open(job_path, encoding="utf-8") as fh:
    job = json.load(fh)

results = []
bounced = []
state = {"stage": "starting", "in_flight": None, "outlook_started": False, "reason": None}
phase = {"name": None, "deadline": None, "t0": None}

ADDR = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def scrub(value):
    text = ADDR.sub("<address>", str(value or ""))
    return re.sub(r"\d{8,}", lambda m: "****" + m.group(0)[-4:], text)


def stamp():
    return time.strftime("%H:%M:%S")


def stage(text):
    state["stage"] = scrub(text)
    print(f"  [{stamp()}] stage: {state['stage']}", flush=True)


def write_result(reason=None):
    if reason:
        state["reason"] = reason
    tmp = result_path + ".tmp"
    payload = {"stage": state["stage"], "reason": state["reason"], "in_flight": state["in_flight"],
               "outlook_started": state["outlook_started"], "results": list(results), "bounced": list(bounced)}
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, result_path)


def set_phase(name, seconds):
    """Arm the watchdog for the phase that is about to start; None disarms it."""
    phase["name"] = name
    phase["t0"] = time.time()
    phase["deadline"] = (time.time() + seconds) if name else None


def watchdog():
    # R13: COM calls cannot be interrupted from inside, so a second thread decides when to give up
    # and ends the process. Whatever has been recorded so far is already on disk.
    while True:
        time.sleep(1)
        dl, name = phase["deadline"], phase["name"]
        if not dl or time.time() <= dl:
            continue
        if name == "startup":
            print(f"email_worker: Outlook not ready within {job['startup_timeout']}s", file=sys.stderr, flush=True)
            write_result("outlook-unavailable")
            os._exit(7)
        if name == "send":
            who = state["in_flight"]
            if who is not None:
                # Delivery is unknown: never call this a failure, or a retry could double-send (R13).
                results.append({"key": who, "status": "unconfirmed", "delivery": "pending",
                                "error": "no answer from Outlook in time - delivery unknown, check Sent Items"})
                state["in_flight"] = None
            print(f"email_worker: no answer within the send deadline - delivery unknown", file=sys.stderr, flush=True)
            write_result("send-timeout")
            os._exit(8)
        # bounce scan or anything else: keep what we have; a missed scan is not a failure
        print("email_worker: bounce scan timed out; continuing without it", file=sys.stderr, flush=True)
        write_result(None)
        os._exit(0)


def outlook_running():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE", "/NH"], capture_output=True, text=True).stdout
    return "OUTLOOK.EXE" in out


def ensure_outlook():
    """R12: Dispatch is only ever called while an Outlook process exists, because a bare Dispatch
    on a closed Outlook starts it and then blocks on a 'Choose Profile' dialog nobody can click."""
    import win32com.client
    if not outlook_running():
        exe, profile = job.get("exe") or "", job.get("profile") or ""
        if not exe or not os.path.exists(exe):
            print("email_worker: classic Outlook (OUTLOOK.EXE) not found", file=sys.stderr, flush=True)
            write_result("outlook-unavailable")
            os._exit(7)
        if not profile:
            print("email_worker: no Outlook profile configured (EMAIL_OUTLOOK_PROFILE or registry DefaultProfile)",
                  file=sys.stderr, flush=True)
            write_result("outlook-unavailable")
            os._exit(7)
        stage("starting Outlook")
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 7  # SW_SHOWMINNOACTIVE: minimised, does not steal focus
        subprocess.Popen([exe, "/profile", profile], startupinfo=si)
        state["outlook_started"] = True
    while True:
        if outlook_running():
            try:
                app = win32com.client.Dispatch("Outlook.Application")
                app.Session.GetDefaultFolder(5)  # Sent Items answers => the profile is really loaded
                return app
            except Exception:  # noqa: BLE001 - still starting; the watchdog owns the deadline
                pass
        time.sleep(2)


def own_address(ns):
    """EMAIL_TEST_ADDRESS=me: the signed-in user's own SMTP address, resolved here, never stored."""
    try:
        ex = ns.CurrentUser.AddressEntry.GetExchangeUser()
        if ex is not None and ex.PrimarySmtpAddress:
            return ex.PrimarySmtpAddress
    except Exception:  # noqa: BLE001
        pass
    return ns.CurrentUser.Address


def _first(items, limit=25):
    it = items.GetFirst()
    n = 0
    while it is not None and n < limit:
        yield it
        n += 1
        it = items.GetNext()


def find_copy(folder, sort_field, marker, subject, t_before, claimed):
    """Is our message in this folder? Preferred match: the run marker we stamped on it. Fallback for a
    folder copy that dropped custom properties: same subject, created after we pressed Send, and not
    already claimed by an earlier recipient of this run."""
    items = folder.Items
    items.Sort(sort_field, True)
    for it in _first(items):
        try:
            prop = it.UserProperties.Find("TodoRunId")
            if prop is not None and prop.Value == marker:
                return True
        except Exception:  # noqa: BLE001
            pass
    items = folder.Items
    items.Sort(sort_field, True)
    for it in _first(items):
        try:
            if it.Subject == subject and it.EntryID not in claimed:
                stamp_t = (it.SentOn if sort_field == "[SentOn]" else it.CreationTime).timestamp()
                if stamp_t >= t_before - 2:
                    claimed.add(it.EntryID)
                    return True
        except Exception:  # noqa: BLE001
            pass
    return False


def send_one(app, ns, r, n, claimed):
    to = own_address(ns) if r["to"] == "@me" else r["to"]
    marker = f"{job['run_id']}-{n}"
    item = app.CreateItem(0)
    item.Subject = r["subject"]
    item.HTMLBody = r["html"]
    item.Recipients.Add(to)
    resolved = bool(item.Recipients.ResolveAll())
    prop = item.UserProperties.Add("TodoRunId", 1)
    prop.Value = marker
    t_before = time.time()
    item.Send()
    t_sent = time.time()
    # R15: proof of hand-over. Sent Items first; then the Outbox (Outlook offline / not yet synced).
    deadline = t_sent + job["verify_timeout"]
    where = None
    while time.time() < deadline:
        if find_copy(ns.GetDefaultFolder(5), "[SentOn]", marker, r["subject"], t_before, claimed):
            where = "sent"
            break
        time.sleep(2)
    if where is None and find_copy(ns.GetDefaultFolder(4), "[CreationTime]", marker, r["subject"], t_before, claimed):
        where = "outbox"
    at = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    base = {"key": r["key"], "to_resolved": resolved, "at": at, "to_actual": to}
    if where == "sent":
        return {**base, "status": "ok", "delivery": "accepted"}
    if where == "outbox":
        return {**base, "status": "ok", "delivery": "queued"}
    return {**base, "status": "unconfirmed", "delivery": "pending",
            "error": "Send() returned but the message is in neither Sent Items nor the Outbox"}


def scan_bounces(ns, targets):
    """R16: non-delivery reports in the Inbox naming one of our recipients. Addresses are compared in
    memory and never written anywhere; only the keys of the matches leave this function."""
    if not targets:
        return []
    earliest = min(t["since"] for t in targets) - 60
    since = datetime.datetime.fromtimestamp(earliest).strftime("%m/%d/%Y %I:%M %p")
    found = []
    inbox = ns.GetDefaultFolder(6)
    seen = reports = unreadable = 0
    for m in inbox.Items.Restrict(f"[ReceivedTime] >= '{since}'"):
        seen += 1
        try:
            mc = str(m.MessageClass)
            if not (mc.startswith("REPORT.") and mc.upper().endswith(".NDR")):
                continue
            reports += 1
            text = f"{m.Subject or ''}\n{m.Body or ''}".lower()
            # A report is a ReportItem, which has NO ReceivedTime property: reading it raised, and the
            # report was silently skipped (found by T14 against a real bounce). CreationTime is its arrival.
            try:
                received = m.ReceivedTime.timestamp()
            except Exception:  # noqa: BLE001
                received = m.CreationTime.timestamp()
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        for t in targets:
            if t["key"] in found:
                continue
            if t["to"].lower() in text and received >= t["since"] - 60:
                found.append(t["key"])
    # Say what was looked at, so a scan that could not read what it found can never pass for "no bounce".
    stage(f"bounce scan: {seen} inbox item(s) in window, {reports} non-delivery report(s), {unreadable} unreadable, {len(found)} matched")
    return found


def main():
    threading.Thread(target=watchdog, daemon=True).start()
    write_result()
    set_phase("startup", job["startup_timeout"])
    stage("launching")
    app = ensure_outlook()
    ns = app.Session
    set_phase(None, 0)
    stage("outlook ready")

    targets = []
    if job["mode"] == "send":
        claimed = set()
        recips = job["recipients"]
        for i, r in enumerate(recips):
            state["in_flight"] = r["key"]
            stage(f"sending {r['key']}")
            write_result()
            set_phase("send", job["send_timeout"] + job["verify_timeout"])
            try:
                row = send_one(app, ns, r, i + 1, claimed)
            except Exception as exc:  # noqa: BLE001 - one bad recipient must not cost the others (R14)
                row = {"key": r["key"], "status": "error", "error": scrub(exc)[:200]}
            set_phase(None, 0)
            results.append({k: v for k, v in row.items() if k != "to_actual"})
            state["in_flight"] = None
            write_result()
            if row.get("status") in ("ok", "unconfirmed"):
                targets.append({"key": r["key"], "to": row.get("to_actual") or r["to"], "since": time.time() - 5})
            if i < len(recips) - 1:
                time.sleep(random.uniform(job["min_delay"], job["max_delay"]))
        if targets:
            stage("waiting for non-delivery reports")
            set_phase("bounce", job["bounce_wait"] + 60)
            time.sleep(job["bounce_wait"])
    else:  # reconcile: look back for bounces of earlier sends, send nothing
        targets = [{"key": t["key"], "to": t["to"], "since": t["since"]} for t in job["targets"]]
        set_phase("bounce", 120)
    if targets:
        stage("scanning for non-delivery reports")
        bounced.extend(scan_bounces(ns, targets))
    set_phase(None, 0)
    stage("done")
    write_result()
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"email_worker: {scrub(exc)}", file=sys.stderr, flush=True)
        write_result("worker-error")
        sys.exit(5)
