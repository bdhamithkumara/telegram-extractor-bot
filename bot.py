import os
import json
import zipfile
import shutil
import logging
import asyncio
from pathlib import Path

import rarfile
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
API_ID     = int(os.environ["API_ID"])
API_HASH   = os.environ["API_HASH"]
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]          # e.g. "-100xxxxxxxxxx" or "@channelusername"

TEMP_DIR   = Path("temp")
STATE_FILE = Path("state.json")
TEMP_DIR.mkdir(exist_ok=True)

# Supported archive extensions
ARCHIVE_EXTS = {".zip", ".rar"}

# Telegram single-file upload limit (2 GB via MTProto)
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024


# ── State helpers ──────────────────────────────────────────────────────────────
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            pass
    return {"processed": []}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def mark_processed(msg_id: int) -> None:
    state = load_state()
    if msg_id not in state["processed"]:
        state["processed"].append(msg_id)
        # Keep only the last 1000 IDs to avoid unbounded growth
        state["processed"] = state["processed"][-1000:]
        save_state(state)


def already_processed(msg_id: int) -> bool:
    return msg_id in load_state()["processed"]


# ── Extraction helpers ─────────────────────────────────────────────────────────
def extract_zip(archive_path: Path, dest: Path) -> list[Path]:
    with zipfile.ZipFile(archive_path, "r") as zf:
        zf.extractall(dest)
    return [f for f in dest.rglob("*") if f.is_file()]


def extract_rar(archive_path: Path, dest: Path) -> list[Path]:
    with rarfile.RarFile(archive_path, "r") as rf:
        rf.extractall(dest)
    return [f for f in dest.rglob("*") if f.is_file()]


def human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ── Bot client ─────────────────────────────────────────────────────────────────
bot = Client(
    "extractor_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


# ── Message handler ────────────────────────────────────────────────────────────
@bot.on_message(filters.channel & filters.document)
async def handle_archive(client: Client, message: Message) -> None:
    doc = message.document
    if not doc or not doc.file_name:
        return

    ext = Path(doc.file_name).suffix.lower()
    if ext not in ARCHIVE_EXTS:
        return

    if already_processed(message.id):
        logger.info(f"Skipping already-processed message {message.id}")
        return

    original_name = doc.file_name
    file_size     = doc.file_size or 0
    logger.info(f"New archive: {original_name} ({human_size(file_size)})")

    download_path = TEMP_DIR / f"{message.id}_{original_name}"
    extract_dir   = TEMP_DIR / f"extracted_{message.id}"

    # ── Status message ─────────────────────────────────────────────────────────
    status = await client.send_message(
        CHANNEL_ID,
        f"⏳ **Downloading** `{original_name}` ({human_size(file_size)})…",
    )

    try:
        # ── Download ───────────────────────────────────────────────────────────
        await message.download(file_name=str(download_path))
        logger.info(f"Downloaded to {download_path}")

        await status.edit_text(f"📂 **Extracting** `{original_name}`…")

        # ── Extract ────────────────────────────────────────────────────────────
        extract_dir.mkdir(exist_ok=True)

        if ext == ".zip":
            files = extract_zip(download_path, extract_dir)
        else:  # .rar
            files = extract_rar(download_path, extract_dir)

        if not files:
            await status.edit_text(f"⚠️ `{original_name}` is empty — no files found inside.")
            return

        # Sort: folders first, then by path for readability
        files.sort(key=lambda p: (p.parent != extract_dir, str(p)))

        total     = len(files)
        skipped   = 0
        too_large = []

        await status.edit_text(
            f"📤 **Uploading** {total} file(s) from `{original_name}`…"
        )

        # ── Upload each file ───────────────────────────────────────────────────
        for idx, file_path in enumerate(files, start=1):
            relative = file_path.relative_to(extract_dir)
            size     = file_path.stat().st_size

            if size > MAX_FILE_BYTES:
                too_large.append(str(relative))
                skipped += 1
                logger.warning(f"Skipping {relative} — too large ({human_size(size)})")
                continue

            caption = (
                f"📄 `{relative}`\n"
                f"📦 From: `{original_name}`  •  {idx}/{total}"
            )

            for attempt in range(3):
                try:
                    await client.send_document(
                        CHANNEL_ID,
                        document=str(file_path),
                        caption=caption,
                        force_document=True,
                    )
                    logger.info(f"Uploaded [{idx}/{total}]: {relative}")
                    break
                except FloodWait as fw:
                    logger.warning(f"FloodWait {fw.value}s — waiting…")
                    await asyncio.sleep(fw.value)
                except Exception as exc:
                    if attempt == 2:
                        logger.error(f"Failed to upload {relative}: {exc}")
                        await client.send_message(
                            CHANNEL_ID, f"❌ Could not upload `{relative}`: {exc}"
                        )
                    else:
                        await asyncio.sleep(2 ** attempt)

        # ── Done summary ───────────────────────────────────────────────────────
        uploaded = total - skipped
        summary_lines = [
            f"✅ **Done!** Extracted `{original_name}`",
            f"• Uploaded: **{uploaded}** file(s)",
        ]
        if too_large:
            summary_lines.append(
                f"• Skipped (>2 GB): {', '.join(f'`{f}`' for f in too_large)}"
            )

        await status.edit_text("\n".join(summary_lines))
        mark_processed(message.id)

    except zipfile.BadZipFile:
        await status.edit_text(f"❌ `{original_name}` is not a valid ZIP file.")
        logger.error(f"Bad ZIP: {original_name}")

    except rarfile.BadRarFile:
        await status.edit_text(f"❌ `{original_name}` is not a valid RAR file.")
        logger.error(f"Bad RAR: {original_name}")

    except Exception as exc:
        await status.edit_text(f"❌ Error processing `{original_name}`:\n`{exc}`")
        logger.exception(f"Unexpected error on {original_name}")

    finally:
        # ── Cleanup temp files ─────────────────────────────────────────────────
        if download_path.exists():
            download_path.unlink(missing_ok=True)
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        logger.info("Temp files cleaned up.")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("🤖 Telegram Extractor Bot starting…")
    bot.run()