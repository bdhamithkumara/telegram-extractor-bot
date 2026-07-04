import os
import json
import shutil
import zipfile
import rarfile
import py7zr
from pyrogram import Client

BOT_TOKEN = os.environ["BOT_TOKEN"]
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
CHANNEL_ID = int(os.environ["CHANNEL_ID"])

STATE_FILE = "state.json"
TEMP_DIR = "temp"

app = Client(
    "extractor",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"last_message_id": 0}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

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

def main():
    os.makedirs(TEMP_DIR, exist_ok=True)

    state = load_state()
    last_id = state["last_message_id"]

    with app:
        messages = app.get_chat_history(CHANNEL_ID, limit=50)

        new_last_id = last_id

        for msg in reversed(list(messages)):

            if msg.id <= last_id:
                continue

            if msg.document:
                file_name = msg.document.file_name or ""
                if file_name.endswith((".zip", ".rar", ".7z")):

                    file_path = app.download_media(msg, file_name=f"{TEMP_DIR}/{file_name}")

                    out_dir = f"{TEMP_DIR}/out_{msg.id}"
                    os.makedirs(out_dir, exist_ok=True)

                    try:
                        extract_file(file_path, out_dir)

                        for root, _, files in os.walk(out_dir):
                            for f in files:
                                full = os.path.join(root, f)
                                app.send_document(CHANNEL_ID, full)

                    except Exception as e:
                        app.send_message(CHANNEL_ID, f"❌ Extract failed: {file_name}\n{e}")

            new_last_id = max(new_last_id, msg.id)

        state["last_message_id"] = new_last_id
        save_state(state)

    shutil.rmtree(TEMP_DIR, ignore_errors=True)

if __name__ == "__main__":
    main()