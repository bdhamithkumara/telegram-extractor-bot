import os
import shutil
import zipfile
import rarfile
import py7zr
from pyrogram import Client

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

TEMP_DIR = "temp"

app = Client(
    "extractor",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

print("🚀 TEST MODE STARTED")


# ----------------------------
# EXTRACT FUNCTION
# ----------------------------
def extract_file(file_path, out_dir):
    if file_path.endswith(".zip"):
        with zipfile.ZipFile(file_path, 'r') as z:
            z.extractall(out_dir)

    elif file_path.endswith(".rar"):
        with rarfile.RarFile(file_path) as r:
            r.extractall(out_dir)

    elif file_path.endswith(".7z"):
        with py7zr.SevenZipFile(file_path, mode='r') as z:
            z.extractall(path=out_dir)


# ----------------------------
# MAIN TEST FLOW
# ----------------------------
with app:

    print("📡 Fetching latest messages...")

    messages = app.get_chat_history(CHANNEL_ID, limit=10)

    target_msg = None

    for msg in messages:
        if msg.document:
            file_name = msg.document.file_name or ""
            if file_name.endswith((".zip", ".rar", ".7z")):
                target_msg = msg
                break

    if not target_msg:
        print("❌ No archive found in last 10 messages")
        exit()

    print(f"📦 Found file: {target_msg.document.file_name}")

    os.makedirs(TEMP_DIR, exist_ok=True)

    file_path = app.download_media(
        target_msg,
        file_name=f"{TEMP_DIR}/{target_msg.document.file_name}"
    )

    out_dir = f"{TEMP_DIR}/out"
    os.makedirs(out_dir, exist_ok=True)

    print("📂 Extracting...")

    extract_file(file_path, out_dir)

    print("📤 Sending extracted files...")

    for root, _, files in os.walk(out_dir):
        for f in files:
            full = os.path.join(root, f)
            app.send_document(CHANNEL_ID, full)

    print("✅ TEST COMPLETE")

    shutil.rmtree(TEMP_DIR, ignore_errors=True)