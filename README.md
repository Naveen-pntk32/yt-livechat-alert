# 🚨 YouTube Live Chat Alert Bot

> **A 100% Free, 24/7 Cloud-Ready YouTube Live Chat Keyword Watcher & Phone Alert Automation System.**
> Monitored 24/7 with zero PC usage required, controlled directly from your smartphone via **ntfy** or **Telegram**.

---

## 🌟 Key Highlights

- **⚡ Zero-Quota Web Scraping First**: Leverages YouTube Innertube live chat scraping without consuming your daily 10,000 unit YouTube Data API quota.
- **🛡️ Official API Fallback**: Automatically falls back to YouTube Data API v3 if web scraping encounters captchas or network limits, ensuring 100% uptime.
- **🏷️ Human-Readable Channel @Handles**: Resolves raw channel IDs (`UC...`), URLs, or video links to the creator's `@handle` name (e.g., `@iamkokkikumar (KOKKI KUMAR YT)`).
- **📱 Two-Way Remote Phone Control**: Start, stop, check status, switch channels, and update keywords directly from your phone using free push notifications.
- **🚫 Duplicate Anti-Spam Engine**: Rejects identical repeated chat spam while instantly catching all unique keyword occurrences.
- **☁️ 24/7 Cloud Hosting ($0 Forever)**: Deployable to Render or Railway with automatic keep-alive endpoints for UptimeRobot.

---

## 📐 Architecture Overview

```text
[ Your Phone (ntfy / Telegram) ]
   │
   ├── 1. Send "init" / "/start" to trigger monitoring
   │   │
   │   ▼
   │  [ HTTPS Stream / Webhook ]
   │   │
   │   ▼
   │  [ Cloud Server (Render.com / Docker) ] ◄── (Kept awake 24/7 by UptimeRobot ping)
   │   │
   │   ├── ntfy_controller.py / telegram_controller.py receives command
   │   │
   │   ▼
   │  [ LiveChatAlertBot Engine ]
   │   │
   │   ├── Resolves channel ID -> @handle & channel name
   │   └── Starts 0-Quota Live Chat Scraper (with API Fallback)
   │
   └── 2. Keyword matched in chat -> Instant alert notification with clickable stream URL!
```

---

## 📋 Table of Contents

1. [Step 1: Set Up ntfy on Your Phone (30 Seconds)](#step-1-set-up-ntfy-on-your-phone-30-seconds)
2. [Step 2: Local Testing on PC](#step-2-local-testing-on-pc)
3. [Step 3: Deploy to Cloud (Render.com - Free 24/7)](#step-3-deploy-to-cloud-rendercom---free-247)
4. [Step 4: Keep Alive 24/7 with UptimeRobot](#step-4-keep-alive-247-with-uptimerobot)
5. [Phone Remote Control Commands](#phone-remote-control-commands)
6. [Environment Variables Reference](#environment-variables-reference)
7. [Docker & Docker Compose](#docker--docker-compose)
8. [Folder Structure](#folder-structure)
9. [Automated Testing](#automated-testing)

---

## Step 1: Set Up ntfy on Your Phone (30 Seconds)

**ntfy** requires **no accounts**, **no passwords**, and **no developer portals**.

1. Download the free app:
   - **Android**: [Google Play Store](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/)
   - **iOS**: [Apple App Store](https://apps.apple.com/us/app/ntfy/id1625396347)
   - **Web Browser**: [ntfy.sh](https://ntfy.sh)
2. Tap the **"+"** button and choose a unique topic name (or use your existing topic, e.g. `yt-alert-naveen`).
3. Tap **Subscribe**. You are ready to receive alerts and send remote commands!

---

## Step 2: Local Testing on PC

### 1. Clone the repository
```bash
git clone https://github.com/Naveen-pntk32/yt-livechat-alert.git
cd yt-livechat-alert
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Create your `.env` configuration
Copy `.env.example` to `.env`:
```ini
# Target YouTube Channel (Channel ID, @handle, or live link)
YT_CHANNEL=@YourStreamerHandle

# (Recommended) YouTube API Key for fallback
YOUTUBE_API_KEY=AIzaSy...

# ntfy Remote Control & Alerts
NTFY_TOPIC=yt-alert-naveen
NTFY_SERVER=https://ntfy.sh

# (Optional) Telegram Remote Control
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Watched Keywords (comma-separated)
KEYWORDS=solo, 1v1, 1 v 1, 1vs1, ff, free fire, NAVEEN-PNTk

# Server Settings
AUTO_START_BOT=false
PORT=5000
HOST=0.0.0.0
```

### 4. Run the app
```bash
# Unified Cloud/Local Service (Bot + Remote Controllers + Web Dashboard)
python main.py

# Or CLI foreground mode
python main.py --cli
```

Open your ntfy app, open your topic, and type **`init`**. The bot will immediately wake up and report:
```text
🟢 Bot started! Monitoring channel: @YourStreamerHandle
```

---

## Step 3: Deploy to Cloud (Render.com - Free 24/7)

Deploying to [Render.com](https://render.com/) allows the bot to run continuously in the cloud even when your computer is shut down.

1. Sign up for a free account at [render.com](https://render.com/) (log in with your GitHub account).
2. In Render Dashboard, click **New +** > **Web Service**.
3. Select and connect your repository: `Naveen-pntk32/yt-livechat-alert`.
4. Configure the service settings:
   - **Name**: `yt-livechat-alert`
   - **Region**: Closest to you (e.g., Singapore, Frankfurt, Oregon)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Instance Type**: `Free`
5. Scroll down to **Environment Variables** and add:

   | Key | Example Value | Description |
   |---|---|---|
   | `NTFY_TOPIC` | `yt-alert-naveen` | Your private ntfy topic |
   | `NTFY_SERVER` | `https://ntfy.sh` | ntfy server URL (or custom server like `https://ntfy.adminforge.de`) |
   | `YT_CHANNEL` | `@iamkokkikumar` | Streamer channel ID (`UC...`) or `@handle` |
   | `YOUTUBE_API_KEY` | `AIzaSy...` | *(Recommended)* Official API live detection fallback |
   | `KEYWORDS` | `solo, 1v1, ff, free fire` | Keywords to trigger alerts on |
   | `AUTO_START_BOT` | `false` | Set to `false` so it waits for your `init` command |
   | `PORT` | `5000` | Cloud server port |

6. Click **Create Web Service**.
7. Render will build and deploy your service, providing you with a live URL (e.g. `https://yt-livechat-alert.onrender.com`).

---

## Step 4: Keep Alive 24/7 with UptimeRobot

Render free-tier web services sleep after 15 minutes of inactivity. To keep your bot running **24/7 forever for $0**:

1. Create a free account at [uptimerobot.com](https://uptimerobot.com/).
2. Click **Add New Monitor**:
   - **Monitor Type**: `HTTP(s)`
   - **Friendly Name**: `YouTube Live Chat Bot`
   - **URL (or IP)**: `https://yt-livechat-alert.onrender.com` *(your Render service URL)*
   - **Monitoring Interval**: `Every 5 minutes`
3. Click **Create Monitor**.

UptimeRobot will ping the built-in `/` dashboard every 5 minutes, keeping Render awake 24/7/365.

---

## 📱 Phone Remote Control Commands

Send these commands inside your **ntfy** topic or via **Telegram**:

| ntfy Command | Telegram Command | Action | Bot Response |
|---|---|---|---|
| *(Automatic)* | *(Automatic)* | **Starts automatically at 9:00 AM & stops at 5:00 PM daily** | ⏰ *Auto-Start / Auto-Stop Notifications* |
| **`schedule`** | `/schedule` | **Views daily 9am-5pm schedule status** | ⏰ *Schedule status (`schedule on` / `schedule off`)* |
| **`init`** or **`start`** | `/start` | **Manually starts chat monitoring anytime** | 🟢 *Bot started! Monitoring channel: @handle (Title)* |
| **`status`** | `/status` | **Checks bot status, schedule & stats** | 📊 *Status report with channel handle, stream URL, scan count & uptime* |
| **`stop`** | `/stop` | **Manually pauses chat monitoring anytime** | 🛑 *Bot stopped successfully.* |
| **`channel`** | `/channel` | **Displays current target** | 📺 *Current target: @handle (Channel Title)* |
| **`channel @NewName`** | `/channel @NewName` | **Switches streamer target** | ✅ *Target channel updated to: @NewName* |
| **`keywords`** | `/keywords` | **Displays active keywords** | 🔑 *Watched Keywords: solo, 1v1, ff...* |
| **`keywords solo, 1v1, ff`** | `/keywords solo, 1v1` | **Updates keywords dynamically** | ✅ *Keywords updated (3 keywords): solo, 1v1, ff* |
| **`help`** | `/help` | **Shows command list** | 🤖 *Interactive command reference* |

---

## ⚙️ Environment Variables Reference

| Variable | Default | Required? | Description |
|---|---|---|---|
| `YT_CHANNEL` | `""` | **Yes** | Target streamer YouTube Channel ID (`UC...`), `@handle`, or direct live video link. |
| `DAILY_SCHEDULE_ENABLED` | `true` | No | Automatically start at 9:00 AM and stop at 5:00 PM every day without manual typing. |
| `DAILY_START_TIME` | `09:00` | No | Daily auto-start time in 24-hour format (`HH:MM`). |
| `DAILY_STOP_TIME` | `17:00` | No | Daily auto-stop time in 24-hour format (`HH:MM`). |
| `SCHEDULE_TIMEZONE` | `Asia/Kolkata` | No | Timezone for the daily schedule (default Indian Standard Time). |
| `NTFY_TOPIC` | `""` | **Yes** | ntfy topic name used for phone notifications and two-way control. |
| `NTFY_SERVER` | `https://ntfy.sh` | No | Base ntfy server URL (allows custom or self-hosted servers to avoid public rate limits). |
| `YOUTUBE_API_KEY` | `""` | Recommended | Official YouTube Data API v3 key used as fallback for 100% reliable live detection. |
| `KEYWORDS` | Default gaming set | No | Comma-separated keywords to monitor in the live chat. |
| `AUTO_START_BOT` | `false` | No | If `true`, bot begins monitoring immediately upon server boot; if `false`, waits for schedule or `init`. |
| `TELEGRAM_BOT_TOKEN` | `""` | Optional | Telegram Bot API token from @BotFather. |
| `TELEGRAM_CHAT_ID` | `""` | Optional | Your Telegram User ID for alerts. |
| `PORT` | `5000` | No | HTTP server port for the keep-alive dashboard. |
| `HOST` | `0.0.0.0` | No | HTTP bind host. |

---

## 🐳 Docker & Docker Compose

### Run with Docker Compose:
```bash
docker-compose up -d --build
```

### Run with Docker CLI:
```bash
# Build image
docker build -t yt-livechat-alert .

# Run container
docker run -d --name yt-alert --env-file .env -p 5000:5000 yt-livechat-alert
```

---

## 📁 Folder Structure

```text
YT-LiveChat-Alert/
├── main.py                 # Primary entrypoint (Cloud service, controllers, Keep-Alive server)
├── yt_live_chat_alert.py   # Core monitoring engine, handle resolver, spam filter & alert dispatcher
├── ntfy_controller.py      # Two-way phone remote controller via ntfy.sh
├── telegram_controller.py  # Telegram remote controller & command handler
├── requirements.txt        # Python package dependencies
├── .env                    # Active secrets & configuration (git-ignored)
├── .env.example            # Environment template for new setups
├── Dockerfile              # Production Docker image definition
├── docker-compose.yml      # Local container orchestration
├── render.yaml             # Render infrastructure blueprint
├── DEPLOYMENT_GUIDE.md     # Dedicated cloud deployment instructions
├── README.md               # Complete project manual & setup guide
└── tests/
    └── test_bot.py         # Automated unit test suite
```

---

## 🧪 Automated Testing

The project includes an automated test suite covering keyword matching, handle resolution, spam prevention, and remote control workflows.

Run tests using:
```bash
python -m unittest discover tests
```
Output:
```text
Ran 22 tests in 11.894s
OK
```

---

## 📄 License

MIT License. Free for personal and commercial automation use.
