# 📦 Telegram Archive Extractor Bot

A Telegram bot that lets users DM `.zip` or `.rar` files directly to the bot. All received archives are queued and released together to a private channel once a day - fully automated via GitHub Actions, no server needed.

---

## How It Works

```
User DMs zip/rar to bot
         ↓
 Every 30 min: receiver checks DMs
   → adds file to queue
   → replies "✅ Queued at position #2"
         ↓
 Once daily (noon UTC): release runs
   → downloads every queued archive
   → extracts all files
   → posts to channel
   → DMs each user "🎉 Your file was released!"
```

**Example channel messages on release day:**

```
📬 Daily Release Started — 3 archive(s) in queue

📦 Releasing archive.zip
👤 Submitted by: @johndoe  •  4 files inside

📄 `folder/document.pdf`   📦 From: archive.zip  •  1/4
📄 `folder/image.png`      📦 From: archive.zip  •  2/4
...

✅ Done: archive.zip  •  Uploaded: 4 file(s)

🏁 Daily Release Complete  •  3/3 succeeded ✅
```

---

## Features

- ✅ Users DM the bot directly — no channel access needed
- ✅ Supports `.zip` and `.rar` (including WinRAR) files
- ✅ Handles files up to **2 GB** via Telegram's MTProto API
- ✅ Queue survives restarts — stored in `state.json`, committed to the repo
- ✅ Failed archives are retried automatically the next day
- ✅ Each submitter is notified by DM when their file is released
- ✅ Duplicate detection — same file can't be queued twice
- ✅ File size cap — oversized archives are rejected before downloading
- ✅ Folder structure preserved in file captions
- ✅ Temp files cleaned up after every job

---

## Setup

### 1. Create a Telegram Bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the steps
3. Copy the **Bot Token**

### 2. Get your Telegram API credentials

1. Go to [my.telegram.org](https://my.telegram.org)
2. Log in → **API development tools** → Create an app
3. Copy your **API ID** and **API Hash**

### 3. Add the bot to your channel as admin

1. Open your private channel → **Administrators** → **Add Administrator**
2. Search for your bot and add it
3. Give it **Post Messages** and **Edit Messages** permissions

### 4. Get your Channel ID

Forward any message from your private channel to [@userinfobot](https://t.me/userinfobot) — it replies with the numeric ID (e.g. `-1001234567890`).

### 5. Fork this repo and add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Description |
|---|---|
| `API_ID` | From my.telegram.org |
| `API_HASH` | From my.telegram.org |
| `BOT_TOKEN` | From @BotFather |
| `CHANNEL_ID` | e.g. `-1001234567890` |

### 6. Allow Actions to write to the repo

Go to **Settings → Actions → General → Workflow permissions → Read and write permissions → Save**

This is required so workflows can commit `state.json` (the queue) back to the repo after each run.

### 7. Enable the workflows

Go to the **Actions** tab in your repo and enable both workflows if prompted.

---

## Configuration

Optional secrets to tune limits without touching code:

| Secret | Default | Description |
|---|---|---|
| `MAX_ARCHIVE_MB` | `200` | Max archive size in MB — larger files are rejected before downloading |

---

## Changing the Release Time

In `.github/workflows/release.yml`, edit the cron line:

```yaml
- cron: '0 12 * * *'   # noon UTC — change 12 to any hour (0–23)
```

Examples: `0 6 * * *` = 6 AM UTC, `0 18 * * *` = 6 PM UTC.

---

## Project Structure

```
telegram-extractor/
├── .github/
│   └── workflows/
│       ├── receiver.yml   # Runs every 30 min — checks bot DMs, queues files
│       └── release.yml    # Runs once daily — extracts queue, posts to channel
├── receive.py             # Lightweight receiver using Bot API
├── release.py             # Daily processor using Pyrogram (large file support)
├── requirements.txt       # Python dependencies
├── state.json             # Auto-generated — queue + update offset
└── temp/                  # Auto-generated — cleaned after each job
```

> `state.json` is committed to the repo automatically by the workflows so the queue survives between runs. `temp/` is in `.gitignore` and never committed.

---

## GitHub Actions Usage

| Workflow | Schedule | Avg runtime | Purpose |
|---|---|---|---|
| `receiver.yml` | Every 30 min | ~30 sec | Checks DMs, queues new files |
| `release.yml` | Once daily | 2–10 min | Extracts queue, posts to channel |

Both workflows run on public repos with **unlimited free minutes**.

---

## Stack

| Tool | Purpose |
|---|---|
| [Pyrogram](https://github.com/pyrogram/pyrogram) | MTProto client — downloads files up to 2 GB |
| `requests` | Lightweight Bot API calls for the receiver |
| `zipfile` (stdlib) | ZIP extraction |
| [rarfile](https://github.com/markokr/rarfile) | RAR extraction |
| GitHub Actions | Free hosting, scheduling, and queue persistence |

---

## FAQ

**A user sent a file but didn't get a confirmation reply.**
Check the Actions tab to confirm `receiver.yml` is running. Also make sure the user sent the file directly to the bot (DM), not to the channel.

**The release ran but some archives failed.**
Failed archives stay in the queue and are retried automatically the next day. Check the Actions log for the specific error.

**Can I trigger a release manually?**
Yes — go to **Actions → Daily Release → Run workflow**.

**Can I run this locally?**
Yes. Set the environment variables and run either script directly.

```bash
export API_ID=...
export API_HASH=...
export BOT_TOKEN=..
export CHANNEL_ID=..

pip install -r requirements.txt

python receive.py    # check DMs and queue files
python release.py   # process queue and post to channel
```

**Will GitHub charge me?**
No. Public repos get unlimited free minutes. GitHub's default spending limit is $0, so even on a private repo, jobs stop rather than charge you.

---

## License

MIT
