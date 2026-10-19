"""Shared delivery ledger — one row per person per channel per run, across WhatsApp and email.

Written by 02-whatsapp-message (v2.0, R26) and 04-email-message; read by the scheduler and the
manual-send UI. Each module's own log stays the source of truth for idempotency: this file is the
human-facing answer to "did it actually go out?", so a failure to write it never fails a run.

Rows hold no phone number and no address — only person, channel, outcome and receipt.
"""
import json, os, re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Dhaka")
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LEDGER = os.environ.get("DELIVERY_LEDGER") or os.path.join(ROOT, "outputs", "delivery", "delivery_log.json")
RETENTION_DAYS = 60
FIELDS = ("date", "time", "channel", "person", "trigger", "mode", "status", "delivery", "detail")


def _scrub(value):
    """Belt and braces: no long digit run or address ever lands in the ledger."""
    value = re.sub(r"\d{8,}", lambda m: "****" + m.group(0)[-4:], str(value or ""))
    return re.sub(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", "<address>", value)


def read(path=LEDGER):
    try:
        with open(path, encoding="utf-8") as fh:
            rows = json.load(fh).get("rows", [])
        return rows if isinstance(rows, list) else []
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def append(rows, path=LEDGER, now=None):
    """Prepend rows (newest first), drop rows past retention, write atomically."""
    now = now or datetime.now(TZ)
    clean = []
    for row in rows:
        out = {k: _scrub(row.get(k, "")) for k in FIELDS}
        out["date"] = out["date"] or str(now.date())
        out["time"] = out["time"] or now.strftime("%H:%M:%S")
        clean.append(out)
    cutoff = str(now.date() - timedelta(days=RETENTION_DAYS))
    kept = clean + [r for r in read(path) if str(r.get("date", "")) >= cutoff]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"rows": kept}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return len(clean)
