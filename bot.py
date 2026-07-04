import os
import json
import shutil
import zipfile
import rarfile
import py7zr
from pyrogram import Client
from pyrogram.errors import PeerIdInvalid, UsernameNotOccupied


BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH")
CHANNEL_RAW = os.environ.get("CHANNEL_ID")

STATE_FILE = "state.json"
TEMP_DIR = "temp"


# ----------------------------
# SAFE CHANNEL HANDLING
# ----------------------------
def resolve_channel(value):
    value = str(value).strip()

    if not value:
        raise Exception("CHANNEL_ID is empty in GitHub Secrets")

    # username format
    if value.startswith("@"):
        return value

    # numeric ID
    try:
        return int(value)
    except:
        return value


CHANNEL_ID = resolve_channel(CHANNEL_RAW)


app = Client(
    "extractor",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)


# ----------------------------
# DEBUG (IMPORTANT FOR YOU)
# ----------------------------
print("🔍 DEBUG INFO")
print("CHANNEL_RAW:", repr(CHANNEL_RAW))
print("CHANNEL_ID:", repr(CHANNEL_ID))
print("TYPE:", type(CHANNEL_ID))


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


# ----------------------------
# SAFE HISTORY FETCH (FIXED)
# ----------------------------
def safe_history(app, channel):
    try:
        # IMPORTANT FIX: resolve chat first
        chat = app.get_chat(channel)
        return app.get_chat_history(chat.id, limit=50)

    except (PeerIdInvalid, UsernameNotOccupied) as e:
        print("❌ Telegram peer error:", e)
        return []

    except Exception as e:
        print("❌ Failed to fetch history:", e)
        return []


def main():
    os.makedirs(TEMP_DIR, exist_ok=True)

    state = load_state()
    last_id = state.get("last_message_id", 0)

    with app:

        messages = safe_history(app, CHANNEL_ID)

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

                print(f"📦 Processing: {file_name}")

                file_path = app.download_media(
                    msg,
                    file_name=f"{TEMP_DIR}/{file_name}"
                )

                out_dir = f"{TEMP_DIR}/out_{msg.id}"
                os.makedirs(out_dir, exist_ok=True)

                try:
                    extract_file(file_path, out_dir)

                    for root, _, files in os.walk(out_dir):
                        for f in files:
                            full = os.path.join(root, f)
                            app.send_document(CHANNEL_ID, full)

                    print(f"✅ Done: {file_name}")

                except Exception as e:
                    app.send_message(
                        CHANNEL_ID,
                        f"❌ Extract failed: {file_name}\n{e}"
                    )

            except Exception as e:
                print(f"⚠️ Skipping message {msg.id}: {e}")
                continue

    state["last_message_id"] = new_last_id
    save_state(state)

    shutil.rmtree(TEMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()