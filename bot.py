import os
import json
import shutil
import zipfile
import rarfile
import py7zr
from pyrogram import Client

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
CHANNEL_ID = os.environ.get("CHANNEL_ID")

STATE_FILE = "state.json"
TEMP_DIR = "temp"


# ----------------------------
# STATE (prevent duplicates)
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

print("🚀 Bot started (BATCH MODE)")


# ----------------------------
# MAIN LOGIC
# ----------------------------
with app:

    state = load_state()
    last_id = state.get("last_id", 0)

    print("📡 Fetching recent messages...")

    messages = app.get_chat_history(CHANNEL_ID, limit=30)

    new_last_id = last_id

    for msg in reversed(list(messages)):

        try:
            if msg.id <= last_id:
                continue

            new_last_id = max(new_last_id, msg.id)

            if not msg.document:
                continue

            file_name = msg.document.file_name or ""

            if not file_name.endswith((".zip", ".rar", ".7z")):
                continue

            print(f"📦 Found: {file_name}")

            os.makedirs(TEMP_DIR, exist_ok=True)

            file_path = app.download_media(
                msg,
                file_name=f"{TEMP_DIR}/{file_name}"
            )

            out_dir = f"{TEMP_DIR}/out_{msg.id}"
            os.makedirs(out_dir, exist_ok=True)

            print("📂 Extracting...")

            extract_file(file_path, out_dir)

            print("📤 Sending files...")

            for root, _, files in os.walk(out_dir):
                for f in files:
                    full = os.path.join(root, f)
                    app.send_document(CHANNEL_ID, full)

            print(f"✅ Done: {file_name}")

        except Exception as e:
            print(f"⚠️ Error: {e}")

    state["last_id"] = new_last_id
    save_state(state)

    shutil.rmtree(TEMP_DIR, ignore_errors=True)

print("🏁 Finished cleanly")