import os
import json
import shutil
import zipfile
import rarfile
import py7zr
from pyrogram import Client, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

STATE_FILE = "state.json"
TEMP_DIR = "temp"


# ----------------------------
# STATE
# ----------------------------
def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ----------------------------
# EXTRACT FILES
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
# PYROGRAM CLIENT
# ----------------------------
app = Client(
    "extractor",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


print("🚀 Bot started (LIVE MODE)")
print("📡 Listening for new messages...")


# ----------------------------
# HANDLER (NEW MESSAGES ONLY)
# ----------------------------
@app.on_message(filters.channel)
def handle_channel_posts(client, message):

    try:
        if not message.document:
            return

        file_name = message.document.file_name or ""

        if not file_name.endswith((".zip", ".rar", ".7z")):
            return

        print(f"📦 New file received: {file_name}")

        os.makedirs(TEMP_DIR, exist_ok=True)

        file_path = client.download_media(
            message,
            file_name=f"{TEMP_DIR}/{file_name}"
        )

        out_dir = f"{TEMP_DIR}/out_{message.id}"
        os.makedirs(out_dir, exist_ok=True)

        try:
            extract_file(file_path, out_dir)

            for root, _, files in os.walk(out_dir):
                for f in files:
                    full = os.path.join(root, f)
                    client.send_document(message.chat.id, full)

            print(f"✅ Done: {file_name}")

        except Exception as e:
            client.send_message(
                message.chat.id,
                f"❌ Extract failed: {file_name}\n{e}"
            )

        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    except Exception as e:
        print(f"⚠️ Error processing message: {e}")


# ----------------------------
# RUN
# ----------------------------
app.run()