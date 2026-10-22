# 🎙️ Discord Voice Channel Counter Bot & Real-Time Web Dashboard

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.7%2B-5865F2.svg)](https://github.com/Rapptz/discord.py)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![WebSockets](https://img.shields.io/badge/WebSockets-Real--Time-23a55a.svg)](https://websockets.readthedocs.io/)
[![Security](https://img.shields.io/badge/Security-Full--Defense-green.svg)](#-5-security-defense-architecture)

A complete, production-ready **Discord Voice Channel Counter Bot** integrated with a high-performance **FastAPI backend** and a modern **real-time statistics web dashboard** inspired by Discord's dark aesthetics.

---

## 📋 Table of Contents

1. [Key Features](#-key-features)
2. [Architecture Overview](#-architecture-overview)
3. [Discord Bot Setup & Privileged Intents](#-discord-bot-setup--privileged-intents)
4. [Environment Configuration](#-environment-configuration)
5. [Installation & Local Launch](#-installation--local-launch)
6. [24/7 VPS Deployment & Always-On Operation](#-247-vps-deployment--always-on-operation)
7. [Nginx Reverse Proxy & HTTPS Configuration](#-nginx-reverse-proxy--https-configuration)
8. [Discord Commands Reference](#-discord-commands-reference)
9. [Security Defense Architecture](#-security-defense-architecture)
10. [Discord Rate Limit Protections](#-discord-rate-limit-protections)
11. [Troubleshooting & FAQ](#-troubleshooting--faq)
12. [Automated Testing & Known Limitations](#-automated-testing--known-limitations)

---

## 🚀 Key Features

- **⚡ Unified Async Runtime**: Runs Discord gateway events (`discord.py` 2.x) and `FastAPI` / `Uvicorn` in a unified `asyncio` event loop for lightning-fast in-memory state propagation.
- **🔄 Instant WebSocket Broadcasts**: Updates voice occupants, active channels, and server statistics immediately whenever a member joins, leaves, moves, mutes, deafens, or streams in a voice/stage channel. No page reloads required!
- **📊 Automatic Voice Counter Channels**: Creates and synchronizes dedicated statistics channels under a clean Discord category (e.g. `👥 Members: 150`, `🔊 In Voice: 12`, `🎙️ Active VCs: 4`).
- **🛡️ Rate-Limit Safe**: Built-in throttler and cooldown queue respects Discord's strict **2 channel renames per 10 minutes** limit to prevent API suspensions and `429 Too Many Requests` errors.
- **🌐 Multi-Server Support**: Automatically handles multiple Discord servers with an intuitive dropdown selector in the web dashboard.
- **🔒 Full Security Defense**:
  - Secure credential management (Discord tokens never exposed to the client or browser).
  - Timing-attack-resistant constant-time secret comparison (`secrets.compare_digest`).
  - Dual dashboard modes: **Public Read-Only** or **Private Authenticated**.
  - In-memory sliding-window IP rate limiting against brute-force attacks.
  - Automatic credential redaction filter in logs to mask tokens and secrets.
  - Hardened browser security headers (`CSP`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`).
- **🏆 Voice Time Leaderboard**: Persistent tracking of member voice activity and time spent connected in voice channels.
- **☁️ 24/7 VPS Ready**: Production-grade systemd service templates and SQLite WAL-mode persistence for always-on operation.

---

## 🏛️ Architecture Overview

```text
discord-vc-counter/
├── bot.py                     # Main runner: Discord Bot + FastAPI in unified loop
├── requirements.txt           # Pinned production dependencies
├── .env.example               # Detailed environment configuration template
├── README.md                  # Comprehensive documentation
├── .gitignore                 # Git ignore rules for virtualenvs, .env, and databases
├── data/                      # Persistent storage directory
│   └── bot.db                 # Persistent SQLite database (WAL mode)
├── services/
│   ├── __init__.py
│   ├── config.py              # Pydantic-validated environment configuration
│   ├── database.py            # Async SQLite CRUD operations & migrations
│   ├── security.py            # Secret comparison, log redaction, rate limiting
│   ├── statistics.py          # Guild metrics computation & channel serialization
│   └── counters.py            # Rate-limit-safe counter channel manager
├── api/
│   ├── __init__.py
│   ├── routes.py              # REST endpoints (/health, /api/guilds, /api/auth)
│   └── websocket.py           # Real-time WebSocket connection manager & broadcaster
├── dashboard/
│   ├── index.html             # Discord-inspired glassmorphism frontend
│   ├── style.css              # Modern responsive CSS design system
│   └── app.js                 # Reconnecting WebSocket client & live UI updates
└── tests/
    ├── __init__.py
    ├── test_statistics.py     # Unit tests for voice & member calculations
    └── test_security.py       # Unit tests for authentication, rate limits, and redaction
```

---

## 🤖 Discord Bot Setup & Privileged Intents

To enable the bot to count server members and voice participants, follow these setup steps in the **Discord Developer Portal**:

### 1. Create Application & Bot
1. Navigate to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** in the top right, name your bot (e.g. `VC Counter`), and accept the Developer Terms.
3. In the left sidebar, click **Bot**.
4. Click **Reset Token** and copy your **Bot Token**. Save this in your `.env` file as `DISCORD_TOKEN`.

### 2. Enable Privileged Gateway Intents (CRITICAL)
Under the **Bot** tab, scroll down to **Privileged Gateway Intents** and enable:
- ✅ **Server Members Intent** (Required to accurately count human vs bot members and resolve member avatars).
- ✅ **Message Content Intent** (Required if using legacy prefix commands like `!stats` or `!setup`).

> [!IMPORTANT]
> Failure to enable the **Server Members Intent** will cause Discord to return `discord.errors.PrivilegedIntentsRequired` on startup.

### 3. Generate Bot Invite URL
1. Go to **OAuth2** -> **URL Generator** in the sidebar.
2. Under **Scopes**, select:
   - ✅ `bot`
   - ✅ `applications.commands` (for Slash Commands)
3. Under **Bot Permissions**, select:
   - ✅ `Manage Channels` (Required to create and rename counter channels)
   - ✅ `View Channels`
   - ✅ `Connect`
   - ✅ `Speak`
   - ✅ `Send Messages`
   - ✅ `Embed Links`
   - ✅ `Read Message History`
4. Copy the generated URL at the bottom and open it in your browser to invite the bot to your Discord server.

Permissions Integer: `268443664` or Administrator `8`.

---

## ⚙️ Environment Configuration

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DISCORD_TOKEN` | *(Required)* | Secret Discord bot token from the Developer Portal. |
| `DISCORD_CLIENT_ID` | `""` | Discord Application / Client ID. |
| `BOT_PREFIX` | `!` | Prefix for legacy text commands (e.g. `!stats`, `!help`). |
| `HOST` | `0.0.0.0` | Network interface to bind the web server to. |
| `PORT` | `8000` | HTTP and WebSocket port. |
| `DASHBOARD_URL` | `http://localhost:8000` | Public base URL sent in `/dashboard` command responses. |
| `DASHBOARD_SECRET` | *(Random 32-char)* | Secret key for administrative dashboard access and private views. |
| `DEFAULT_PUBLIC_DASHBOARD` | `false` | Default dashboard privacy mode (`true` = public, `false` = private). |
| `COUNTER_UPDATE_INTERVAL_SECONDS` | `300` | Minimum seconds between counter channel renames (min 300s / 5 min). |
| `RATE_LIMIT_PER_MINUTE` | `60` | Maximum requests per minute per IP for sensitive endpoints. |
| `DATABASE_PATH` | `data/bot.db` | Path to persistent SQLite database file. |
| `ENVIRONMENT` | `production` | Runtime mode (`production` or `development`). |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

> [!TIP]
> Generate a strong `DASHBOARD_SECRET` by running:
> ```bash
> python -c "import secrets; print(secrets.token_urlsafe(32))"
> ```

---

## 💻 Installation & Local Launch

### Prerequisites
- Python 3.12 or newer.
- Git.

### Windows Setup
```powershell
# 1. Clone or navigate to the workspace
cd "c:\Users\jaisj\OneDrive\Desktop\VC COUNTE"

# 2. Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy configuration
copy .env.example .env
# Edit .env and paste your DISCORD_TOKEN

# 5. Launch Bot & Web Server
python bot.py
```

### Linux / macOS Setup
```bash
# 1. Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy configuration
cp .env.example .env
# Edit .env with your favorite editor (e.g. nano .env)

# 4. Launch Bot & Web Server
python3 bot.py
```

Once started, access the web dashboard at **`http://localhost:8000/dashboard/`**.

---

## ☁️ 24/7 VPS Deployment & Always-On Operation

> [!IMPORTANT]
> **Understanding 24/7 Uptime**: A systemd service or background process manager automatically recovers your bot from software crashes and reboots. However, **24/7 uninterrupted operation requires an always-on cloud hosting provider** (e.g., Hetzner, DigitalOcean, Linode, OVH, or AWS Lightsail). If your home PC goes to sleep, loses power, or loses internet, your bot will go offline.

### Setting up on Ubuntu / Debian VPS via Systemd

1. **Install Python & Git on VPS**:
   ```bash
   sudo apt update && sudo apt install -y python3-pip python3-venv git sqlite3 curl
   ```

2. **Clone and Configure**:
   ```bash
   sudo useradd -m -s /bin/bash vccounter
   sudo -u vccounter git clone <your-repo-url> /home/vccounter/app
   cd /home/vccounter/app
   sudo -u vccounter python3 -m venv .venv
   sudo -u vccounter .venv/bin/pip install -r requirements.txt
   sudo -u vccounter cp .env.example .env
   # Edit .env with your credentials
   ```

3. **Create Systemd Service Unit**:
   Create `/etc/systemd/system/vccounter.service`:
   ```ini
   [Unit]
   Description=Discord VC Counter Bot & Web Dashboard
   After=network.target

   [Service]
   Type=simple
   User=vccounter
   WorkingDirectory=/home/vccounter/app
   ExecStart=/home/vccounter/app/.venv/bin/python bot.py
   Restart=always
   RestartSec=5
   EnvironmentFile=/home/vccounter/app/.env

   # Resource and security sandboxing
   NoNewPrivileges=true
   ProtectSystem=full
   ProtectHome=false

   [Install]
   WantedBy=multi-user.target
   ```

4. **Enable and Start Service**:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now vccounter.service
   sudo systemctl status vccounter.service
   ```

---

## 🔒 Nginx Reverse Proxy & HTTPS Configuration

To serve your dashboard over HTTPS with SSL and WebSocket support, configure Nginx as a reverse proxy:

### 1. Install Nginx & Certbot
```bash
sudo apt update
sudo apt install -y nginx certbot python3-certbot-nginx
```

### 2. Configure Nginx Server Block
Create `/etc/nginx/sites-available/vc-counter.conf`:

```nginx
server {
    server_name vc.yourdomain.com;

    # Maximum upload size
    client_max_body_size 10M;

    # API and Dashboard Proxy
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        # CRITICAL: WebSocket Upgrade Headers
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # Forward host headers and client IP
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket timeouts (prevent disconnects on idle connections)
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
```

Enable the site:
```bash
sudo ln -s /etc/nginx/sites-available/vc-counter.conf /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 3. Obtain Free Let's Encrypt SSL Certificate
```bash
sudo certbot --nginx -d vc.yourdomain.com
```

---

## 💬 Discord Commands Reference

## 💬 Discord Commands Reference

All commands support both modern **Discord Slash Commands** (`/cmd`) and standard **Prefix Commands** (default: `,cmd`):

| Slash Command | Prefix Command | Permission | Description |
| :--- | :--- | :--- | :--- |
| `/vc [member]` | `,vc [@user]` | Everyone | Displays detailed voice stats (All-Time, Daily, Weekly) with speaking, muted, deafened, and AFK breakdowns. |
| `/stats` | `,stats` | Everyone | Displays rich embed with server members, voice counts, and active channels. |
| `/leaderboard [timeframe] [target/page]` | `,lb [timeframe] [@user/page]` | Everyone | Interactive voice leaderboard with `[🏆 All-Time]`, `[🌅 Daily]`, `[📅 Weekly]` buttons. Supports `,lb`, `,lb daily`, `,lb weekly`, `,lb @user`, `,lb @user daily`, `,lb daily @user`, `,lb @user weekly`, `,lb weekly @user`. |
| `/dailylb [target/page]` | `,dailylb [@user/page]` or `,daily lb` | Everyone | Dedicated Daily voice leaderboard shortcut. Supports `,dailylb`, `,daily lb`, `,daily lb @user`. |
| `/weeklylb [target/page]` | `,weeklylb [@user/page]` or `,weekly lb` | Everyone | Dedicated Weekly voice leaderboard shortcut. Supports `,weeklylb`, `,weekly lb`, `,weekly lb @user`. |
| `/alltimelb [target/page]` | `,alltimelb [@user/page]` or `,all time lb` | Everyone | Dedicated All-Time voice leaderboard shortcut. Supports `,alltimelb`, `,all time lb`. |
| `/rank [target] [timeframe]` | `,rank [@user] [timeframe]` | Everyone | Checks your or any member's exact server rank & voice time. Supports `,rank`, `,rank daily`, `,rank weekly`, `,rank @user`, `,rank @user daily`, `,rank daily @user`, `,rank @user weekly`, `,rank weekly @user`. |
| `/dashboard` | `,dashboard` | Everyone | Sends the web dashboard link and shows current public/private access mode. |
| `/refresh` | `,refresh` | Everyone | Manually forces statistics re-computation and syncs counter channels. |
| `/help` | `,help` | Everyone | Displays complete interactive command manual, permissions, and dashboard guide. |
| `/customstatus <text>` | `,customstatus <text>` | **Administrator** | Sets the **Speech Bubble Custom Status** directly next to the profile pic ("THIS ONE"). |
| `/bannerstatus <text> [type]` | `,bannerstatus <text> [type]` | **Administrator** | Sets the **Profile Banner Activity** box on the profile card ("THE BANNER ONE"). |
| `/bothstatus <bubble> <banner>` | `,bothstatus <bubble> <banner>` | **Administrator** | Sets **BOTH** the speech bubble status AND the profile banner activity at the same time. |
| `/clearstatus [target]` | `,clearstatus [target]` | **Administrator** | Clears bot statuses: `,clearstatus` (both), `,clearstatus bubble` (bubble only), `,clearstatus banner` (banner only). |
| `/setstatus <text> [type]` | `,setstatus <text> [type]` | **Administrator** | Universal status setter (speech bubble or banner activity). |
| `/clear [amount]` | `,clear [amount]` | **Manage Messages** | Bulk deletes recent messages in the current text channel (1-100, default: 5). |
| `/resetdaily [target]` | `,resetdaily [@user/all]` | **Administrator** | Resets Daily breakdown to `0h 0m 0s` (All-Time and Weekly remain 100% intact!). |
| `/resetweekly [target]` | `,resetweekly [@user/all]` | **Administrator** | Resets Weekly breakdown to `0h 0m 0s` (All-Time remains 100% intact!). |
| `/resetvc [target]` | `,resetvc [@user/all]` | **Administrator** | Resets All-Time voice statistics and history for a member or everyone. |
| `/setup` | `,setup` | **Administrator** | Creates statistics category & voice counter channels (`👥 Members`, `🔊 In Voice`, `🎙️ Active VCs`). |
| `/removesetup` | `,removesetup` | **Administrator** | Deletes and removes counter channels and category from the server. |
| `/config` | `,config` | **Administrator** | Configures server settings (e.g. `/config public_dashboard:True`). |
| `/noprefix [action] [target]` | `,noprefix [action] [target]` | **Administrator** | Manage No-Prefix mode. Whitelisted members or everyone can use commands without typing `,` (e.g. `rank`, `lb`). |

---

### ⚡ No-Prefix Mode (How to Setup & Use)

The bot natively supports **No-Prefix Mode**, allowing members to type commands directly without needing the `,` prefix (e.g. typing `rank`, `lb`, `vc`, `stats`, `help` directly).

#### 👑 Default Access
- **Server Owner** and members with **Administrator** permissions always have No-Prefix access enabled by default.

#### ⚙️ Setup Commands
- **Add a member to No-Prefix**:
  ```text
  ,noprefix add @user
  /noprefix action:add target:@user
  ```
- **Remove a member from No-Prefix**:
  ```text
  ,noprefix remove @user
  /noprefix action:remove target:@user
  ```
- **View all No-Prefix members in the server**:
  ```text
  ,noprefix list
  /noprefix action:list
  ```
- **Enable No-Prefix for EVERYONE in the server**:
  ```text
  ,noprefix server enable
  /noprefix action:server target:enable
  ```
  *(To revert back to whitelist only: `,noprefix server disable`)*
- **Shortcuts & Aliases**: `,np`, `,setnoprefix` or `/noprefix`. If you have No-Prefix, you can also type `np list`, `noprefix add @user`, etc. directly!

---

### 🧹 How to Clear & Reset Commands (Complete Manual)

The bot provides comprehensive clearing commands for bot presence, voice analytics, and chat moderation:

#### 1. How to Control Both Types of Status & How to Clear Them

Discord provides **two distinct** status locations on a profile:
1. 💬 **"THIS ONE" (Speech Bubble Custom Status)**: The speech bubble cloud appearing directly next to the profile picture/avatar.
2. 🏷️ **"THE BANNER ONE" (Profile Banner Activity)**: The floating activity card on the profile (e.g. `Watching Voice Channels`, `Playing Games`, `Streaming`).

##### A. Set Speech Bubble Status ("THIS ONE")
```text
,customstatus Hiring Weekly Paid staffs For .gg/heartbeatz
/customstatus status_text:Hiring Weekly Paid staffs For .gg/heartbeatz
```
*(Aliases: `,speechbubble`, `,bubble`)*

##### B. Set Profile Banner Activity ("THE BANNER ONE")
```text
,bannerstatus Over 50 members watching
,bannerstatus Spotify listening
,bannerstatus Minecraft playing
/bannerstatus status_text:Voice Channels activity_type:watching
```

##### C. Set BOTH Statuses Simultaneously
```text
,bothstatus "Hiring Weekly Paid staffs For .gg/heartbeatz" "Voice Channels" watching
/bothstatus bubble_text:"Hiring staffs" banner_text:"Voice Channels" banner_type:watching
```

##### D. How to Clear Statuses
- **Clear BOTH statuses completely**:
  ```text
  ,clearstatus
  /clearstatus
  ```
  *(Aliases: `,resetstatus`, `,removestatus`, `,delstatus`)*
- **Clear ONLY the Speech Bubble (near avatar)**:
  ```text
  ,clearstatus bubble
  ,customstatus clear
  ```
- **Clear ONLY the Profile Banner**:
  ```text
  ,clearstatus banner
  ,bannerstatus clear
  ```

#### 2. How to Clear Chat Messages (`/clear` or `,clear`)
Cleans up recent text messages in the current channel.
- **Permission Required**: `Manage Messages`
- **Aliases**: `,purge`, `,clean`, `,prune`
- **Usage**:
  ```text
  ,clear 10       - Deletes the last 10 messages in the channel
  ,clear 50       - Deletes the last 50 messages
  ,clear          - Deletes the default 5 messages
  /clear amount:20 - Slash command alternative
  ```
- **Self-Cleaning**: After clearing, the bot posts a confirmation notice that automatically self-deletes after 5 seconds to keep the channel clean!

#### 3. How to Clear Daily Voice Stats (`/resetdaily` or `,resetdaily`)
Resets only the **Daily Breakdown** to `0h 0m 0s`.
- **Decoupling Guarantee**: Does **NOT** delete or reduce time from the **All Time Breakdown** or the **Weekly Breakdown**.
- **Permission Required**: `Administrator`
- **Aliases**: `,dailyreset`, `,resetday`
- **Usage**:
  ```text
  ,resetdaily @user   - Resets daily breakdown for the tagged member
  ,resetdaily all     - Resets daily breakdown for ALL members in the server
  /resetdaily target:@User
  /resetdaily target:all
  ```

#### 3. How to Clear Weekly Voice Stats (`/resetweekly` or `,resetweekly`)
Resets only the **Weekly Breakdown** to `0h 0m 0s`.
- **Decoupling Guarantee**: Does **NOT** delete or reduce time from the **All Time Breakdown**.
- **Permission Required**: `Administrator`
- **Aliases**: `,weeklyreset`, `,resetweek`
- **Usage**:
  ```text
  ,resetweekly @user  - Resets weekly breakdown for the tagged member
  ,resetweekly all    - Resets weekly breakdown for ALL members in the server
  /resetweekly target:@User
  /resetweekly target:all
  ```

#### 4. How to Clear All-Time Voice Time & History (`/resetvc` or `,resetvc`)
Performs a full reset of voice time records and state logs.
- **Permission Required**: `Administrator`
- **Aliases**: `,vcreset`, `,resettime`
- **Usage**:
  ```text
  ,resetvc @user      - Wipes all voice history for a specific member
  ,resetvc all        - Wipes all voice history for EVERYONE in the server
  ,resetvc @user daily   - Alternative way to trigger daily reset
  ,resetvc @user weekly  - Alternative way to trigger weekly reset
  ```

#### 5. How to Clear / Remove Counter Channels (`/removesetup` or `,removesetup`)
Deletes the voice counter channels and category created by `,setup`.
- **Permission Required**: `Administrator`
- **Aliases**: `,delsetup`, `,setupremove`, `,teardown`
- **Usage**:
  ```text
  ,removesetup        - Safely deletes counter channels and clears DB settings
  /removesetup
  ```

---

### 📖 Detailed Command Usage & Examples

#### 1. `/vc` or `,vc` — Detailed Voice Statistics Breakdown
Displays a comprehensive breakdown of voice activity across All-Time, Daily, and Weekly periods:
- **All Time Breakdown**: Total, Unmuted, Muted, Deafened, AFK.
- **Daily Breakdown**: Total, Unmuted, Muted, Deafened, AFK (last 24 hours).
- **Weekly Breakdown**: Total, Unmuted, Muted, Deafened, AFK (last 7 days).
- **Live Voice Presence**: If currently in voice, shows current channel and state in real time.
- **Usage**:
  ```text
  ,vc          - View your own voice stats
  ,vc @user    - View voice stats for another member
  /vc member:@User
  ```

#### 2. `/clear` or `,clear` — Chat Message Purge (Manage Messages)
```text
,clear 25
/clear amount:25
```
Deletes 25 messages from the current channel with self-deleting confirmation.

#### 3. `/stats` or `,stats` — Live Voice & Server Statistics
Generates a real-time status card for your server:
- **Server Members**: Total member count broken down into humans vs bots.
- **In Voice Channels**: Total live participants in voice/stage channels (humans vs bots).
- **Active Voice Channels**: Channels with at least 1 occupant.
- **Top Channels Preview**: Lists the most active channels and their current occupancy.

#### 4. `/setup` or `,setup` — Auto Counter Channels (Admin Only)
Automatically provisions a statistics category with 3 locked, auto-updating voice channels:
```text
📊 SERVER STATS 📊
├── 👥 Members: 150
├── 🔊 In Voice: 12
└── 🎙️ Active VCs: 4
```

#### 5. `/removesetup` or `,removesetup` — Teardown Counter Channels (Admin Only)
Deletes all counter channels and removes persistent category tracking from SQLite.

#### 6. `/setstatus` or `,setstatus` — Change Bot Status (Admin Only)
Updates the bot's Discord activity presence:
```text
,setstatus "Hiring Weekly Paid staffs For .gg/heartbeatz" watching
,setstatus "Hiring Weekly Paid staffs For .gg/heartbeatz" streaming
,setstatus "Hiring Weekly Paid staffs For .gg/heartbeatz" playing
```

#### 7. `/dashboard` or `,dashboard` — Web Dashboard Link
Sends the public or private URL to access the web dashboard:
- Direct clickable link: `https://<YOUR_DASHBOARD_URL>/dashboard/?guild=<YOUR_GUILD_ID>`

#### 8. Leaderboard Commands — `,lb` & `/leaderboard`
Interactive multi-timeframe voice activity ranking:
- **General Leaderboards**:
  - `,lb` — All-Time server leaderboard (Page 1)
  - `,lb daily` — Daily server leaderboard
  - `,lb weekly` — Weekly server leaderboard
  - `,lb 2` / `,lb daily 2` / `,lb weekly 2` — Direct page jump
- **Member-Focused Leaderboards**:
  - `,lb @user` — Opens All-Time leaderboard focused directly on `@user`'s page with their rank highlighted (`👉`)
  - `,lb @user daily` or `,lb daily @user` — Daily leaderboard focused on `@user`'s rank
  - `,lb @user weekly` or `,lb weekly @user` — Weekly leaderboard focused on `@user`'s rank
- **Dedicated Shortcut Commands**:
  - `,dailylb` (or `,daily lb`, `,daily lb @user`, `,dlb`)
  - `,weeklylb` (or `,weekly lb`, `,weekly lb @user`, `,wlb`)
  - `,alltimelb` (or `,all time lb`, `,atlb`)
- **Interactive Embed Buttons**:
  - `[🏆 All-Time]` • `[🌅 Daily]` • `[📅 Weekly]` — Switch timeframes with a single click in-place.
  - `[◀️ Prev]` • `[Page X / Y]` • `[Next ▶️]` • `[🎯 My Rank]` — Seamless navigation and instant personal jump.

#### 9. Server Rank Commands — `,rank` & `/rank`
Check exact server placement and voice time for yourself or another member:
- **Check Your Own Rank**:
  - `,rank` — Your All-Time server rank
  - `,rank daily` — Your Daily server rank
  - `,rank weekly` — Your Weekly server rank
- **Check Another Member's Rank**:
  - `,rank @user` — Tagged member's All-Time server rank
  - `,rank @user daily` or `,rank daily @user` — Tagged member's Daily rank
  - `,rank @user weekly` or `,rank weekly @user` — Tagged member's Weekly rank

#### 10. `/config` or `,config` — Configure Server Settings (Admin Only)
- `,config public_dashboard:True` — Makes the server dashboard public (no password required).
- `,config public_dashboard:False` — Locks the dashboard (requires `DASHBOARD_SECRET`).

#### 10. `/refresh` or `,refresh` — Force Resynchronization
Instantly recalculates statistics, pushes fresh data to all connected web clients, and checks counter channel cooldowns.

#### 11. `/help` or `,help` — Interactive Bot Manual
Displays the full command manual, how to clear guide, and dashboard instructions.


## 🛡️ Security Defense Architecture

The application adheres to defense-in-depth principles:

1. **Zero Client Secret Exposure**: The Discord Bot Token is never transmitted to the browser or embedded into HTML/JS assets.
2. **Constant-Time Verification**: Admin secrets are verified with `secrets.compare_digest` to prevent side-channel timing attacks.
3. **Log Credential Redaction**: A specialized `logging.Filter` continuously scans log records and masks Discord bot tokens, bearer strings, and configured dashboard secrets.
4. **Dual Dashboard Authorization Modes**:
   - **Private Mode (Default)**: Restricts the statistics API and WebSocket access to clients holding the `DASHBOARD_SECRET`.
   - **Public Mode**: Administrators can explicitly enable public read-only views using `/config public_dashboard:True`.
5. **In-Memory Rate Limiting**: Sensitive endpoints (`/api/auth/verify`, `/api/guilds/{id}/refresh`) are protected by an in-memory sliding-window rate limiter per client IP.
6. **Hardened HTTP Headers**: Responses automatically enforce `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, and `X-Frame-Options: DENY`.
7. **Least Privilege Enforcement**: Administrative Discord commands verify Discord's native `administrator` permission bit.

---

## ⏱️ Discord Rate Limit Protections

Discord enforces a strict rate limit on channel name edits:

> **Discord Channel Rename Limit**: Maximum of **2 edits per 10 minutes** per channel.

If a bot attempts to rename channels on every voice join/leave naively, Discord will respond with HTTP 429 and eventually suspend the bot token.

### How this Bot Solves It:
- **Intelligent Diffing**: A channel is only queued for rename if the formatted string differs from the current channel name.
- **Throttling Queue**: Updates are held in a background worker that enforces `COUNTER_UPDATE_INTERVAL_SECONDS` (default: 300 seconds / 5 minutes) per channel.
- **Instant WebSockets**: While Discord channels are throttled safely, the **Web Dashboard updates immediately** (sub-millisecond) over WebSockets so users always have live data.

---

## 🔧 Troubleshooting & FAQ

### 1. Bot connects but slash commands don't show up
- Discord slash commands can take up to a few minutes to propagate globally across all client caches.
- Ensure the bot was invited with the `applications.commands` scope.
- Restart the bot process to trigger `bot.tree.sync()`.

### 2. Error: `PrivilegedIntentsRequired`
- Open the [Discord Developer Portal](https://discord.com/developers/applications), navigate to your application's **Bot** tab, and toggle **Server Members Intent** and **Message Content Intent** to **ON**.

### 3. Counter channels are not created when running `/setup`
- Verify that the bot has the **Manage Channels** permission in the server.
- Ensure the bot's highest role is positioned above the channels it needs to edit.

### 4. WebSocket repeatedly disconnects behind Nginx
- Ensure your Nginx configuration includes the WebSocket upgrade headers:
  ```nginx
  proxy_set_header Upgrade $http_upgrade;
  proxy_set_header Connection "upgrade";
  proxy_read_timeout 86400s;
  ```

### 5. Error: `[WinError 10048] Only one usage of each socket address is normally permitted`
- This error occurs when another instance of `bot.py` or another application is already listening on **port 8000**.
- **Fix on Windows PowerShell**:
  ```powershell
  # Check which process is using port 8000:
  Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object OwningProcess -Unique

  # Terminate the process holding port 8000:
  Stop-Process -Id (Get-NetTCPConnection -LocalPort 8000).OwningProcess -Force
  ```
- **Fix on Linux / VPS**:
  ```bash
  sudo fuser -k 8000/tcp
  ```


---

## 🧪 Automated Testing & Known Limitations

### Running Automated Tests
Run unit tests with `pytest`:

```bash
python -m pytest tests/ -v
```

All 7 test suites verify:
- Accurate human vs bot member calculation.
- Voice channel occupants, stage channels, and user limits.
- Search filtering and sorting algorithms.
- Constant-time secret comparison.
- Sensitive token redaction filter.
- In-memory rate limiting budgets.
- Pydantic configuration validation.

### Known Limitations
- Discord rate limits channel name edits to 2 per 10 minutes. Real-time counter channel names reflect batched updates, whereas the web dashboard is instantaneous.
- Channel occupancy tracking requires the bot to be present in the server with View Channel permissions.

---

## 📄 License
MIT License. Built for high-reliability Discord communities.
