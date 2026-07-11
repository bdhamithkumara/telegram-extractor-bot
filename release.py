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
from pyrogram.types import InputMediaPhoto, InputMediaVideo

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

MAX_ARCHIVE_MB    = int(os.environ.get("MAX_ARCHIVE_MB") or 200)
MAX_ARCHIVE_BYTES = MAX_ARCHIVE_MB * 1024 * 1024
MAX_FILE_BYTES    = 2 * 1024 * 1024 * 1024   # 2 GB Telegram upload cap
DEFAULTS          = {"update_offset": 0, "queue": [], "processed": []}

MAX_RETRY_ATTEMPTS = 3   # give up on an item after this many transient failures
MAX_MEDIA_GROUP    = 10  # Telegram's hard cap per album post
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

def media_kind(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTS:
        return "photo"
    if ext in VIDEO_EXTS:
        return "video"
    return None


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


# ── Upload a batch of photos/videos as a single album post ────────────────────
async def upload_media_group(client: Client, batch: list[dict]) -> tuple[int, int]:
    media = [
        (InputMediaPhoto if item["kind"] == "photo" else InputMediaVideo)(
            media=str(item["path"]), caption=item["caption"]
        )
        for item in batch
    ]
    for attempt in range(3):
        try:
            await client.send_media_group(CHANNEL_ID, media)
            return len(batch), 0
        except FloodWait as fw:
            logger.warning(f"FloodWait {fw.value}s (media group)…")
            await asyncio.sleep(fw.value)
        except Exception as exc:
            if attempt == 2:
                logger.error(f"Media group upload failed ({len(batch)} files): {exc}")
                return 0, len(batch)
            await asyncio.sleep(2 ** attempt)
    return 0, len(batch)


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
# Returns "success", "permanent" (unrecoverable — drop from queue), or
# "retry" (transient failure — keep in queue for the next run).
async def process_item(client: Client, item: dict, index: int, total: int) -> str:
    file_name  = item["file_name"]
    file_id    = item["file_id"]
    file_size  = item.get("file_size", 0)
    from_user  = item.get("from_user", "unknown")
    ext        = "." + file_name.rsplit(".", 1)[-1].lower()

    logger.info(f"[{index}/{total}] Processing: {file_name} from @{from_user}")

    # Size guard — this will never pass on a retry, so drop it for good.
    if file_size > MAX_ARCHIVE_BYTES:
        reason = f"Size {human_size(file_size)} exceeds {MAX_ARCHIVE_MB} MB limit"
        await client.send_message(
            CHANNEL_ID,
            f"🚫 **Skipped** `{file_name}` (from @{from_user})\n{reason}\n"
            f"This won't be retried — please re-upload a smaller/split archive.",
        )
        await notify_user(client, item, success=False, detail=reason)
        return "permanent"

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
            return "permanent"

        file_count = len(files)

        # Header message in channel
        await client.send_message(
            CHANNEL_ID,
            f"📦 **Releasing** `{file_name}`\n"
            f"👤 Submitted by: @{from_user}\n"
            f"📄 {file_count} file(s) inside",
        )

        # Upload each file — consecutive images/videos are grouped into album
        # posts (max 10 per Telegram album); everything else is sent as before.
        uploaded, failed = 0, 0
        media_batch: list[dict] = []

        async def flush_media_batch():
            nonlocal uploaded, failed, media_batch
            if not media_batch:
                return
            ok_count, fail_count = await upload_media_group(client, media_batch)
            uploaded += ok_count
            failed += fail_count
            media_batch = []

        for idx, file_path in enumerate(files, start=1):
            relative = file_path.relative_to(extract_dir)
            size     = file_path.stat().st_size

            if size > MAX_FILE_BYTES:
                await flush_media_batch()
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

            kind = media_kind(file_path)
            if kind:
                media_batch.append({"path": file_path, "kind": kind, "caption": caption})
                if len(media_batch) == MAX_MEDIA_GROUP:
                    await flush_media_batch()
            else:
                await flush_media_batch()
                ok = await upload_file(client, file_path, caption)
                if ok:
                    uploaded += 1
                else:
                    failed += 1

        await flush_media_batch()

        # Summary in channel
        await client.send_message(
            CHANNEL_ID,
            f"✅ **Done:** `{file_name}`\n"
            f"• Uploaded: **{uploaded}** file(s)"
            + (f"\n• Failed: {failed}" if failed else ""),
        )

        await notify_user(client, item, success=True)
        return "success"

    except (zipfile.BadZipFile, rarfile.BadRarFile) as exc:
        reason = "Invalid or corrupted archive"
        await client.send_message(
            CHANNEL_ID, f"❌ `{file_name}` — {reason}\nThis won't be retried."
        )
        await notify_user(client, item, success=False, detail=reason)
        logger.error(f"{reason}: {file_name}")
        return "permanent"

    except Exception as exc:
        reason = str(exc)
        await client.send_message(
            CHANNEL_ID,
            f"⚠️ Error processing `{file_name}`: `{reason}`\nWill retry on the next run.",
        )
        await notify_user(client, item, success=False, detail=f"{reason} (will retry)")
        logger.exception(f"Unexpected error: {file_name}")
        return "retry"

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

        succeeded, retry_items, dropped = 0, [], 0

        for idx, item in enumerate(queue, start=1):
            status = await process_item(client, item, idx, total)

            if status == "success":
                succeeded += 1

            elif status == "retry":
                item["attempts"] = item.get("attempts", 0) + 1
                if item["attempts"] >= MAX_RETRY_ATTEMPTS:
                    dropped += 1
                    await client.send_message(
                        CHANNEL_ID,
                        f"🚫 Giving up on `{item['file_name']}` after "
                        f"{item['attempts']} failed attempts — dropping from queue.",
                    )
                    await notify_user(
                        client, item, success=False,
                        detail="Repeated failures — giving up after multiple attempts",
                    )
                else:
                    retry_items.append(item)

            else:  # "permanent"
                dropped += 1

        # Final summary in channel
        await client.send_message(
            CHANNEL_ID,
            f"🏁 **Daily Release Complete**\n"
            f"• Processed: **{succeeded}/{total}**\n"
            + (f"• Retrying next run: **{len(retry_items)}**\n" if retry_items else "")
            + (f"• Dropped (unrecoverable): **{dropped}**\n" if dropped else "")
            + ("• All succeeded ✅" if not retry_items and not dropped else ""),
        )

    # Keep only transient failures queued for the next run; drop the rest for good
    state["queue"] = retry_items
    save_state(state)
    logger.info(
        f"Release done. {succeeded} succeeded, {len(retry_items)} kept for retry, "
        f"{dropped} dropped."
    )


if __name__ == "__main__":
    asyncio.run(main())