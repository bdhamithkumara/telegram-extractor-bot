"""
receive.py  ─  Lightweight receiver (plain Bot API, no Pyrogram)
Runs every 30 min via GitHub Actions. Checks bot DMs for new .zip / .rar files,
adds them to the queue in state.json, and replies to the sender.
"""

import os
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

BOT_TOKEN  = os.environ["BOT_TOKEN"]
BASE       = f"https://api.telegram.org/bot{BOT_TOKEN}"
STATE_FILE = Path("state.json")
ARCHIVE_EXTS = {".zip", ".rar"}
DEFAULTS   = {"update_offset": 0, "queue": [], "processed": []}

MAX_ARCHIVE_MB    = int(os.environ.get("MAX_ARCHIVE_MB") or 200)
MAX_ARCHIVE_BYTES = MAX_ARCHIVE_MB * 1024 * 1024

def human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


# ── State ──────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    state = {}
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, ValueError):
            pass
    return {**DEFAULTS, **state}

def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ── Bot API helpers ────────────────────────────────────────────────────────────
def get_updates(offset: int) -> list:
    try:
        r = requests.get(
            f"{BASE}/getUpdates",
            params={"offset": offset, "timeout": 10},
            timeout=15,
        )
        return r.json().get("result", [])
    except Exception as exc:
        logger.error(f"getUpdates failed: {exc}")
        return []

def send_message(chat_id: int, text: str) -> None:
    try:
        requests.post(
            f"{BASE}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.error(f"sendMessage failed: {exc}")


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    state   = load_state()
    updates = get_updates(state["update_offset"])
    logger.info(f"Fetched {len(updates)} update(s). Queue size: {len(state['queue'])}")

    added = 0

    for update in updates:
        # Always advance the offset so we never re-read the same update
        state["update_offset"] = update["update_id"] + 1

        message = update.get("message") or update.get("channel_post") or {}
        doc     = message.get("document")
        if not doc:
            continue

        file_name = doc.get("file_name", "")
        ext = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if ext not in ARCHIVE_EXTS:
            continue

        file_id        = doc["file_id"]
        file_unique_id = doc.get("file_unique_id", "")
        file_size      = doc.get("file_size", 0)
        chat_id        = message.get("chat", {}).get("id")
        from_info      = message.get("from", {})
        from_user      = from_info.get("username") or from_info.get("first_name", "unknown")
        from_user_id   = from_info.get("id")

        # Reject oversized archives up front — they'd never make it through
        # the daily release anyway, so don't queue something that can't run.
        if file_size > MAX_ARCHIVE_BYTES:
            send_message(
                chat_id,
                f"🚫 `{file_name}` ({human_size(file_size)}) exceeds the "
                f"{MAX_ARCHIVE_MB} MB limit and was *not* queued.\n"
                f"Please split it into smaller archives and re-upload.",
            )
            logger.info(f"Rejected (too large): {file_name} ({human_size(file_size)})")
            continue

        # Deduplicate by file_unique_id
        existing = [q["file_unique_id"] for q in state["queue"]]
        if file_unique_id in existing:
            send_message(chat_id, f"⚠️ `{file_name}` is already in the queue.")
            logger.info(f"Duplicate skipped: {file_name}")
            continue

        state["queue"].append({
            "file_id":        file_id,
            "file_unique_id": file_unique_id,
            "file_name":      file_name,
            "file_size":      file_size,
            "from_user":      from_user,
            "from_user_id":   from_user_id,
            "chat_id":        chat_id,
            "queued_at":      datetime.now(timezone.utc).isoformat(),
        })
        added += 1
        position = len(state["queue"])

        send_message(
            chat_id,
            f"✅ *Queued!*\n"
            f"📦 `{file_name}`\n"
            f"📍 Position: *#{position}*\n\n"
            f"All archives are released once daily. You'll be notified when yours is extracted.",
        )
        logger.info(f"Queued [{position}]: {file_name} from @{from_user}")

    save_state(state)
    logger.info(f"Done. Added {added} new file(s). Total queue: {len(state['queue'])}")


if __name__ == "__main__":
    main()