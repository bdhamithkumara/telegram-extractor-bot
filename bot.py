import os
import json
import shutil
import zipfile
import rarfile
import py7zr
from pyrogram import Client, filters

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID"))
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
    return {"last_id": 0}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


# ----------------------------
# EXTRACT
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
# CLIENT
# ----------------------------
app = Client(
    "extractor",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

print("🚀 BOT STARTED (EVENT MODE)")


# ----------------------------
# ONLY NEW CHANNEL POSTS
# ----------------------------
@app.on_message(filters.channel)
def handler(client, message):

    try:
        state = load_state()
        last_id = state.get("last_id", 0)

        if message.id <= last_id:
            return

        if not message.document:
            return

        file_name = message.document.file_name or ""

        if not file_name.endswith((".zip", ".rar", ".7z")):
            return

        print(f"📦 New file: {file_name}")

        os.makedirs(TEMP_DIR, exist_ok=True)

        file_path = client.download_media(
            message,
            file_name=f"{TEMP_DIR}/{file_name}"
        )

        out_dir = f"{TEMP_DIR}/out_{message.id}"
        os.makedirs(out_dir, exist_ok=True)

        extract_file(file_path, out_dir)

        for root, _, files in os.walk(out_dir):
            for f in files:
                full = os.path.join(root, f)
                client.send_document(message.chat.id, full)

        state["last_id"] = message.id
        save_state(state)

        shutil.rmtree(TEMP_DIR, ignore_errors=True)

        print("✅ DONE")

    except Exception as e:
        print("⚠️ Error:", e)


# ----------------------------
# RUN
# ----------------------------
app.run()