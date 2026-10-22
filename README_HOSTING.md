# 🌐 Hosting Discord VC Counter Bot on bot-hosting.net (Step-by-Step Guide)

This guide walks you through deploying and running your **Discord Voice Channel Counter Bot & Web Dashboard** 24/7 for free on **[bot-hosting.net](https://bot-hosting.net)** using their Pterodactyl-based hosting panel.

---

## 📋 Table of Contents
1. [Prerequisites & Discord Portal Settings](#1-prerequisites--discord-portal-settings)
2. [Create a Server on bot-hosting.net](#2-create-a-server-on-bot-hostingnet)
3. [Prepare & Upload Project Files](#3-prepare--upload-project-files)
4. [Create & Configure the `.env` File](#4-create--configure-the-env-file)
5. [Verify Startup Settings](#5-verify-startup-settings)
6. [Start Your Bot & Check Console](#6-start-your-bot--check-console)
7. [Keeping Your Bot Online 24/7 (Coins & Renewals)](#7-keeping-your-bot-online-247-coins--renewals)
8. [Troubleshooting & Common Errors](#8-troubleshooting--common-errors)

---

## 1. Prerequisites & Discord Portal Settings

Before uploading the bot, make sure your Discord bot application has the required **Privileged Gateway Intents** enabled:

1. Open the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click your bot application.
3. In the left menu, select **Bot**.
4. Scroll down to the **Privileged Gateway Intents** section:
   - ✅ **Presence Intent** — Enable
   - ✅ **Server Members Intent** — Enable (*Required to count server members and human vs bot counts*)
   - ✅ **Message Content Intent** — Enable (*Required for prefix commands like `,stats`, `,lb`, `,rank` and no-prefix*)
5. Click **Save Changes**.
6. Under the token section, click **Reset Token** (or **Copy**) and keep your bot token ready.

---

## 2. Create a Server on bot-hosting.net

1. Navigate to **[bot-hosting.net](https://bot-hosting.net)**.
2. Click **Login with Discord** and authorize your account.
3. On the main dashboard, click **Create Server**.
4. Fill in the server details:
   - **Server Name**: `VC-Counter` (or your preferred name)
   - **Software / Language**: Select **Python**
   - **Python Version**: Select **Python 3.12** (or latest Python 3.11+)
   - **Location / Node**: Select any available free node
   - **Memory & Disk**: Allocate available free resources (e.g. 512MB – 1024MB RAM is more than enough)
5. Click **Create Server**.
6. Once created, click on your server or click **Manage** to open the **Pterodactyl Control Panel**.

---

## 3. Prepare & Upload Project Files

### What Files to Upload
Your bot requires these files and folders:
```text
├── bot.py                  # Main application entry point
├── requirements.txt        # Python dependency list
├── .env.example            # Environment template
├── api/                    # REST routes & WebSocket broadcaster
├── dashboard/              # Web dashboard frontend files (HTML/CSS/JS)
├── data/                   # Directory containing .gitkeep (for bot.db persistence)
├── services/               # Database, security, counters, and statistics modules
└── tests/                  # Unit test suite
```

> [!WARNING]
> **DO NOT upload these folders**:
> - ❌ `.venv/` (The host container has its own Python environment; uploading your Windows `.venv` will cause errors)
> - ❌ `__pycache__/` (Compiled bytecode)
> - ❌ `.git/` (Not needed in production)
> - ❌ `cloudflared.exe` (Windows executable; unnecessary on Linux containers)

---

### Upload Method (ZIP & Unarchive — Recommended)

1. **On your computer**:
   - Select `bot.py`, `requirements.txt`, `.env.example`, `api`, `dashboard`, `data`, and `services`.
   - Right-click ➔ **Send to** ➔ **Compressed (zipped) folder** (e.g. name it `bot-files.zip`).
2. **On bot-hosting.net panel**:
   - Click the **File Manager** (Files) tab on the left sidebar.
   - Drag and drop `bot-files.zip` into the file manager window (or click **Upload**).
   - Once uploaded, click the **three dots (`...`)** next to `bot-files.zip` and choose **Unarchive**.
   - After the files extract, delete the `bot-files.zip` file to save disk space.

---

## 4. Create & Configure the `.env` File

1. In the **File Manager** tab, click **New File** (or copy from `.env.example`).
2. Paste the following configuration:

```env
# ==============================================================================
# DISCORD VC COUNTER BOT - PRODUCTION SETTINGS
# ==============================================================================

# Discord Bot Credentials (Replace with your actual values)
DISCORD_TOKEN=YOUR_BOT_TOKEN_HERE
DISCORD_CLIENT_ID=YOUR_APPLICATION_CLIENT_ID_HERE
BOT_PREFIX=,
ENABLE_PRIVILEGED_INTENTS=true

# Bot Presence & Status
BOT_STATUS=Hiring Weekly Paid staffs For .gg/heartbeatz
BOT_CUSTOM_STATUS=Hiring Weekly Paid staffs For .gg/heartbeatz
BOT_BANNER_STATUS=
BOT_BANNER_TYPE=watching
BOT_ACTIVITY_TYPE=custom

# Web Server & Host Configuration
HOST=0.0.0.0
PORT=8000
DASHBOARD_URL=http://localhost:8000

# Security & Dashboard Access
DASHBOARD_SECRET=vc_counter_admin_secret_random_key_here
DEFAULT_PUBLIC_DASHBOARD=true
CORS_ORIGINS=*
RATE_LIMIT_PER_MINUTE=60

# Persistent Storage
DATABASE_PATH=data/bot.db

# Cooldown Settings (minimum 300 seconds to respect Discord's rate limits)
COUNTER_UPDATE_INTERVAL_SECONDS=300
```

3. Replace `YOUR_BOT_TOKEN_HERE` with your actual Discord Bot Token from the Developer Portal.
4. Replace `YOUR_APPLICATION_CLIENT_ID_HERE` with your Discord Application ID.
5. In the file name box at the bottom, name the file:
   ```text
   .env
   ```
6. Click **Save Content**.

---

## 5. Verify Startup Settings

1. Click the **Startup** tab in the Pterodactyl left sidebar.
2. Verify:
   - **Main File / Start Script**: `bot.py`
   - **Auto Install Requirements**: Enabled / Yes
   - **Python Version**: `3.12` (or `3.11`)
3. Ensure the start command is configured to execute `python bot.py` (this is the default on bot-hosting.net Python eggs).

---

## 6. Start Your Bot & Check Console

1. In the left menu, click **Console**.
2. Click the green **Start** button in the top right.
3. Observe the output in the console:
   - The server will read `requirements.txt` and automatically install dependencies (`discord.py`, `fastapi`, `uvicorn`, `aiosqlite`, etc.).
   - You should see startup messages similar to:
     ```text
     [INFO] [root]: Starting Web Dashboard & API on http://0.0.0.0:8000
     [INFO] [root]: Connecting to Discord Gateway...
     [INFO] [database]: Database initialized successfully at data/bot.db
     [INFO] [counters]: Counter channel background synchronization worker started.
     [INFO] [root]: Real-time leaderboard synchronization worker started.
     [INFO] [root]: Successfully synced 23 application slash commands.
     [INFO] [root]: Bot connected as: HeartBeatZ Stats#1137
     [INFO] [root]: Serving 1 guilds across Discord.
     ```
4. If you see `Bot connected as: YourBotName`, your bot is **100% online and functional**!

---

## 7. Keeping Your Bot Online 24/7 (Coins & Renewals)

bot-hosting.net is a free hosting provider that utilizes a coin/renewal system to keep active servers running:

1. **Daily Coin Collection**:
   - Go to the bot-hosting.net main website.
   - Click the **Coins** or **Earn Coins** tab.
   - Claim your free daily coins or complete simple sponsor visits.
2. **Server Renewal**:
   - On your bot-hosting.net server list, check the **Renewal Time** countdown.
   - Click **Renew Server** using your free coins to extend runtime (you can queue weeks or months in advance).
   - As long as you maintain renewal, your bot stays online **24/7/365** without interruption.

---

## 8. Troubleshooting & Common Errors

### Error: `PrivilegedIntentsRequired`
```text
discord.errors.PrivilegedIntentsRequired: Shard ID None is requesting privileged intents that have not been explicitly enabled
```
- **Fix**: Go to the [Discord Developer Portal](https://discord.com/developers/applications) ➔ Select your application ➔ **Bot** tab ➔ Scroll down and turn ON **Server Members Intent** and **Message Content Intent** ➔ Save changes and click **Restart** on bot-hosting.net console.

---

### Error: `ModuleNotFoundError: No module named '...'`
- **Fix**: Ensure `requirements.txt` is in the root directory (same folder as `bot.py`). If the panel did not auto-install it, go to the console or use the startup toggle to re-trigger dependency installation.

---

### Error: `Database file locked` or database errors
- **Fix**: Make sure the `data` folder exists in your file manager. The bot will automatically create `data/bot.db` with SQLite WAL mode.

---

### Bot crashes immediately on start
- **Fix**: Check your `.env` file:
  - Make sure the file is named exactly `.env` (not `.env.txt`).
  - Verify that there are no quotes or spaces around `DISCORD_TOKEN`.

---

## 🎯 Verification in Discord
Once started, test your bot in your Discord server:
- Type `,help` or `/help`
- Type `,stats` or `/stats`
- Type `,lb` or `/leaderboard`
- Type `,rank` or `/rank`

Enjoy your 24/7 Discord VC Counter Bot!
