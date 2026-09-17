"""01-calendar-todo step 4: print open Google Tasks due today..today+30 (all lists) as JSON. Read-only."""
import argparse, glob, json, os, shutil, sys
from datetime import date, timedelta

SCOPES = ["https://www.googleapis.com/auth/tasks.readonly"]
DEFAULT_SECRETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".secrets")


def fail(msg):
    print(f"fetch_tasks: {msg}", file=sys.stderr)
    sys.exit(1)


def credentials(secrets):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    gdir = os.path.join(secrets, "google")
    cred, token = os.path.join(gdir, "credentials.json"), os.path.join(gdir, "token.json")
    if not os.path.exists(cred):
        found = sorted(glob.glob(os.path.join(secrets, "downloads", "client_secret*.json")), key=os.path.getmtime)
        if not found:
            fail(f"missing {cred} and no client_secret*.json in {os.path.join(secrets, 'downloads')}")
        os.makedirs(gdir, exist_ok=True)
        shutil.copyfile(found[-1], cred)
    creds = Credentials.from_authorized_user_file(token, SCOPES) if os.path.exists(token) else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as e:  # expired/revoked refresh token -> fresh sign-in
            print(f"fetch_tasks: token refresh failed ({type(e).__name__}), signing in again", file=sys.stderr)
            creds = None
    if not creds or not creds.valid:
        creds = InstalledAppFlow.from_client_secrets_file(cred, SCOPES).run_local_server(port=0)
    with open(token, "w") as fh:
        fh.write(creds.to_json())
    return creds


def pages(call, **kw):
    tok = None
    while True:
        r = call(pageToken=tok, **kw).execute()
        yield from r.get("items", [])
        tok = r.get("nextPageToken")
        if not tok:
            return


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--today", required=True)
    p.add_argument("--secrets", default=DEFAULT_SECRETS)
    a = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    today = date.fromisoformat(a.today)
    last = today + timedelta(days=30)
    try:
        from googleapiclient.discovery import build
        svc = build("tasks", "v1", credentials=credentials(os.path.abspath(a.secrets)), cache_discovery=False)
        default_id = svc.tasklists().get(tasklist="@default").execute()["id"]
        out = []
        for tl in pages(svc.tasklists().list, maxResults=100):
            for t in pages(svc.tasks().list, tasklist=tl["id"], showCompleted=False, showHidden=False,
                           showDeleted=False, dueMin=f"{today}T00:00:00Z", dueMax=f"{last}T23:59:59Z",
                           maxResults=100):
                due = t.get("due", "")[:10]
                if t.get("status") == "needsAction" and due and a.today <= due <= last.isoformat():
                    out.append({"list": tl["title"], "is_default": tl["id"] == default_id,
                                "title": t.get("title", ""), "due": due})
    except SystemExit:
        raise
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")
    print(json.dumps({"tasks": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
