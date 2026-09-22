"""03-scheduler: failure alerting on four independent channels (R10).

Channels fire independently: one failing never suppresses the others. That matters most when the
failure IS the WhatsApp session - the toast, window and log still have to get through.

Never prints or sends a phone number: numbers are masked to their last 4 digits, exactly as module 02 does.
"""
import argparse, json, os, subprocess, sys, tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
WORKER = os.path.join(REPO, ".claude", "skills", "02-whatsapp-message", "wa_send.js")
# Classic AppUserModelID for Windows PowerShell - a registered Start Menu AppId, which WinRT
# requires before it will show a toast. Avoids taking a dependency on BurntToast (not installed).
TOAST_APPID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def mask(value):
    d = "".join(ch for ch in str(value) if ch.isdigit())
    return "****" + d[-4:] if len(d) >= 4 else "****"


def read_env(path):
    cfg = {}
    if not os.path.exists(path):
        return cfg
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip().strip('"').strip("'")
    return cfg


def ps_literal(text):
    """Single-quoted PowerShell literal - the only escape inside one is a doubled quote."""
    return "'" + str(text).replace("'", "''") + "'"


def toast(title, body):
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$tpl = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $tpl.GetElementsByTagName('text')
$texts.Item(0).AppendChild($tpl.CreateTextNode({ps_literal(title)})) | Out-Null
$texts.Item(1).AppendChild($tpl.CreateTextNode({ps_literal(body)})) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier({ps_literal(TOAST_APPID)}).Show($toast)
"""
    # Windows PowerShell (not pwsh 7) - the WinRT type projection is native there.
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or "toast failed").strip().splitlines()[-1])


def window(title, body):
    """A visible console the owner cannot miss. Detached, so it outlives this process."""
    script = (
        f"$Host.UI.RawUI.WindowTitle = {ps_literal(title)}; "
        f"Write-Host {ps_literal(title)} -ForegroundColor Red; "
        f"Write-Host ''; Write-Host {ps_literal(body)}; Write-Host ''; "
        "Write-Host 'Press Enter to close...' -ForegroundColor Yellow; "
        "[void][System.Console]::ReadLine()"
    )
    subprocess.Popen(["powershell.exe", "-NoProfile", "-NoExit", "-Command", script],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))


def whatsapp(body, secrets, node="node", timeout=90):
    """One-recipient send to WA_TEST_NUMBER, reusing module 02's worker rather than a second transport."""
    cfg = read_env(os.path.join(secrets, "whatsapp.env"))
    sender = "".join(ch for ch in cfg.get("WA_SENDER_NUMBER", "") if ch.isdigit())
    to = "".join(ch for ch in cfg.get("WA_TEST_NUMBER", "") if ch.isdigit())
    if not sender or not to:
        raise RuntimeError("WA_SENDER_NUMBER or WA_TEST_NUMBER not set")
    if not os.path.exists(WORKER):
        raise RuntimeError("module 02 wa_send.js missing")
    job = {
        "mode": "live", "sender": sender,
        "auth_dir": os.path.join(secrets, "wweb-auth"),
        "chrome": cfg.get("WA_CHROME_PATH", ""),
        "min_delay": 0, "max_delay": 0, "qr_timeout": 15,
        "recipients": [{"person": "scheduler", "to": to, "parts": [body]}],
    }
    jd, job_path = tempfile.mkstemp(prefix="sched_alert_", suffix=".json")
    os.close(jd)
    rd, res_path = tempfile.mkstemp(prefix="sched_alert_res_", suffix=".json")
    os.close(rd)
    try:
        with open(job_path, "w", encoding="utf-8") as fh:
            json.dump(job, fh, ensure_ascii=False)
        p = subprocess.run([node, WORKER, job_path, res_path],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            tail = (p.stderr or p.stdout).strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"worker exit {p.returncode}")
        with open(res_path, encoding="utf-8") as fh:
            res = json.load(fh)
        rows = res.get("results", res if isinstance(res, list) else [])
        if rows and rows[0].get("status") not in ("ok", None):
            raise RuntimeError(str(rows[0].get("error", rows[0].get("status"))))
        return f"alert sent to {mask(to)}"
    finally:
        for path in (job_path, res_path):
            try:
                os.remove(path)
            except OSError:
                pass


def notify(title, body, channels, secrets, node="node"):
    fired, failed = [], []
    # Order matters: the cheap local channels go first, so a broken WhatsApp session
    # can never delay or block the alert the owner will actually see on screen.
    for name, fn in (("toast", lambda: toast(title, body)),
                     ("whatsapp", lambda: whatsapp(f"{title}\n\n{body}", secrets, node)),
                     ("window", lambda: window(title, body))):
        if name not in channels:
            continue
        try:
            fn()
            fired.append(name)
        except Exception as e:
            failed.append({"channel": name, "error": f"{type(e).__name__}: {e}"})
    return {"fired": fired, "failed": failed}


def main():
    ap = argparse.ArgumentParser(description="03-scheduler failure alerting.")
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", required=True)
    ap.add_argument("--channels", default="toast,whatsapp,window")
    ap.add_argument("--secrets", default=os.path.join(REPO, ".secrets"))
    ap.add_argument("--node", default="node")
    args = ap.parse_args()
    channels = {c.strip() for c in args.channels.split(",") if c.strip()}
    result = notify(args.title, args.body, channels, args.secrets, args.node)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if not result["failed"] else 2  # 2 = alerted partially; never fatal to the run


if __name__ == "__main__":
    sys.exit(main())
