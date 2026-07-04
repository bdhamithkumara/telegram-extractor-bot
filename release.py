"""
release.py  ─  Daily queue processor (Pyrogram for large-file support)
Runs once a day via GitHub Actions. Downloads every queued archive,
extracts it, posts files to the channel, and notifies each submitter.
"""

import os
import json
import zipfile
import shutil
import logging
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import rarfile
from pyrogram import Client
from pyrogram.errors import FloodWait

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

API_ID     = int(os.environ["API_ID"])
API_HASH   = os.environ["API_HASH"]
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]

TEMP_DIR        = Path("temp")
STATE_FILE      = Path("state.json")
TEMP_DIR.mkdir(exist_ok=True)

MAX_ARCHIVE_MB    = int(os.environ.get("MAX_ARCHIVE_MB", 200))
MAX_ARCHIVE_BYTES = MAX_ARCHIVE_MB * 1024 * 1024
MAX_FILE_BYTES    = 2 * 1024 * 1024 * 1024   # 2 GB Telegram upload cap
DEFAULTS          = {"update_offset": 0, "queue": [], "processed": []}


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


# ── Helpers ────────────────────────────────────────────────────────────────────
def human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def extract_archive(archive_path: Path, dest: Path, ext: str) -> list[Path]:
    if ext == ".zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(dest)
    else:
        with rarfile.RarFile(archive_path, "r") as rf:
            rf.extractall(dest)
    return sorted(
        [f for f in dest.rglob("*") if f.is_file()],
        key=lambda p: (p.parent != dest, str(p)),
    )


# ── Upload with retry ──────────────────────────────────────────────────────────
async def upload_file(client: Client, file_path: Path, caption: str) -> bool:
    for attempt in range(3):
        try:
            await client.send_document(
                CHANNEL_ID,
                document=str(file_path),
                caption=caption,
                force_document=True,
            )
            return True
        except FloodWait as fw:
            logger.warning(f"FloodWait {fw.value}s…")
            await asyncio.sleep(fw.value)
        except Exception as exc:
            if attempt == 2:
                logger.error(f"Upload failed: {file_path.name} — {exc}")
                return False
            await asyncio.sleep(2 ** attempt)
    return False


# ── Notify submitter via DM ────────────────────────────────────────────────────
async def notify_user(client: Client, item: dict, success: bool, detail: str = "") -> None:
    chat_id = item.get("chat_id")
    if not chat_id:
        return
    if success:
        msg = (
            f"🎉 Your archive has been released!\n"
            f"📦 `{item['file_name']}`\n"
            f"Check the channel for the extracted files."
        )
    else:
        msg = (
            f"❌ Could not process your archive.\n"
            f"📦 `{item['file_name']}`\n"
            f"Reason: {detail}"
        )
    try:
        await client.send_message(chat_id, msg)
    except Exception as exc:
        logger.warning(f"Could not notify user {chat_id}: {exc}")


# ── Process one queued item ────────────────────────────────────────────────────
async def process_item(client: Client, item: dict, index: int, total: int) -> bool:
    file_name  = item["file_name"]
    file_id    = item["file_id"]
    file_size  = item.get("file_size", 0)
    from_user  = item.get("from_user", "unknown")
    ext        = "." + file_name.rsplit(".", 1)[-1].lower()

    logger.info(f"[{index}/{total}] Processing: {file_name} from @{from_user}")

    # Size guard
    if file_size > MAX_ARCHIVE_BYTES:
        reason = f"Size {human_size(file_size)} exceeds {MAX_ARCHIVE_MB} MB limit"
        await client.send_message(
            CHANNEL_ID,
            f"🚫 **Skipped** `{file_name}` (from @{from_user})\n{reason}",
        )
        await notify_user(client, item, success=False, detail=reason)
        return False

    download_path = TEMP_DIR / f"queue_{index}_{file_name}"
    extract_dir   = TEMP_DIR / f"extracted_{index}"

    try:
        # Download
        await client.download_media(file_id, file_name=str(download_path))
        logger.info(f"Downloaded: {file_name}")

        # Extract
        extract_dir.mkdir(exist_ok=True)
        files = extract_archive(download_path, extract_dir, ext)

        if not files:
            await client.send_message(
                CHANNEL_ID, f"⚠️ `{file_name}` is empty — no files inside."
            )
            await notify_user(client, item, success=False, detail="Archive was empty")
            return False

        file_count = len(files)

        # Header message in channel
        await client.send_message(
            CHANNEL_ID,
            f"📦 **Releasing** `{file_name}`\n"
            f"👤 Submitted by: @{from_user}\n"
            f"📄 {file_count} file(s) inside",
        )

        # Upload each file
        uploaded, failed = 0, 0
        for idx, file_path in enumerate(files, start=1):
            relative = file_path.relative_to(extract_dir)
            size     = file_path.stat().st_size

            if size > MAX_FILE_BYTES:
                await client.send_message(
                    CHANNEL_ID, f"⏭ Skipped `{relative}` — exceeds 2 GB Telegram limit"
                )
                failed += 1
                continue

            caption = (
                f"📄 `{relative}`\n"
                f"📦 From: `{file_name}`  •  {idx}/{file_count}\n"
                f"👤 @{from_user}"
            )
            ok = await upload_file(client, file_path, caption)
            if ok:
                uploaded += 1
            else:
                failed += 1

        # Summary in channel
        await client.send_message(
            CHANNEL_ID,
            f"✅ **Done:** `{file_name}`\n"
            f"• Uploaded: **{uploaded}** file(s)"
            + (f"\n• Failed: {failed}" if failed else ""),
        )

        await notify_user(client, item, success=True)
        return True

    except (zipfile.BadZipFile, rarfile.BadRarFile) as exc:
        reason = "Invalid or corrupted archive"
        await client.send_message(CHANNEL_ID, f"❌ `{file_name}` — {reason}")
        await notify_user(client, item, success=False, detail=reason)
        logger.error(f"{reason}: {file_name}")
        return False

    except Exception as exc:
        reason = str(exc)
        await client.send_message(CHANNEL_ID, f"❌ Error processing `{file_name}`: `{reason}`")
        await notify_user(client, item, success=False, detail=reason)
        logger.exception(f"Unexpected error: {file_name}")
        return False

    finally:
        download_path.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)


# ── Main ───────────────────────────────────────────────────────────────────────
async def main() -> None:
    state = load_state()
    queue = state.get("queue", [])

    if not queue:
        logger.info("Queue is empty — nothing to release today.")
        return

    total = len(queue)
    logger.info(f"Starting daily release — {total} archive(s) in queue")

    async with Client(
        "extractor_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        in_memory=True,        # no session file written to disk
    ) as client:

        # Announce release in channel
        await client.send_message(
            CHANNEL_ID,
            f"📬 **Daily Release Started**\n"
            f"Processing {total} queued archive(s)…\n"
            f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        )

        succeeded, failed_items = 0, []

        for idx, item in enumerate(queue, start=1):
            ok = await process_item(client, item, idx, total)
            if ok:
                succeeded += 1
            else:
                failed_items.append(item)

        # Final summary in channel
        await client.send_message(
            CHANNEL_ID,
            f"🏁 **Daily Release Complete**\n"
            f"• Processed: **{succeeded}/{total}**\n"
            + (f"• Failed: **{len(failed_items)}** (kept in queue for next run)" if failed_items else "• All succeeded ✅"),
        )

    # Keep failed items in queue for next run; clear succeeded ones
    state["queue"] = failed_items
    save_state(state)
    logger.info(f"Release done. {succeeded} succeeded, {len(failed_items)} kept for retry.")


if __name__ == "__main__":
    asyncio.run(main())