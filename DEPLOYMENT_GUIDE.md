# 🚀 100% Free Forever: 24/7 Cloud Deployment & ntfy Remote Control

Keep your YouTube Live Chat Alert Bot running 24/7 in the cloud—**completely free forever with your PC turned off**—and control it directly from your phone using the **ntfy** app (type `init` to start).

---

## 📋 Table of Contents
1. [Architecture Overview](#1-architecture-overview)
2. [Step 1: Set Up ntfy on Your Phone (30 Seconds)](#step-1-set-up-ntfy-on-your-phone-30-seconds)
3. [Step 2: Deploy to Render.com (Free 24/7 Cloud Host)](#step-2-deploy-to-rendercom-free-247-cloud-host)
4. [Step 3: Keep it Awake 24/7 with UptimeRobot (Free Ping)](#step-3-keep-it-awake-247-with-uptimerobot-free-ping)
5. [Step 4: Control & Trigger from Your Phone](#step-4-control--trigger-from-your-phone)
6. [Testing Locally on Your PC](#testing-locally-on-your-pc)

---

## 1. Architecture Overview

```
[ Your Phone (ntfy App) ]
   │
   ├── 1. You type "init" in topic "my-yt-alert-room"
   │   │
   │   ▼
   │  [ ntfy.sh Cloud (HTTPS Pub/Sub Stream) ]
   │   │
   │   ▼
   │  [ 24/7 Cloud Host (Render.com) ]  ◄── (Kept awake by UptimeRobot free ping)
   │   │
   │   ├── ntfy_controller.py receives "init"
   │   │
   │   ▼
   │  [ LiveChatAlertBot Engine ]
   │   │
   │   └── Starts 0-Quota Innertube Live Chat Scraper
   │
   └── 2. Keyword matched in chat -> Instant alert notification on your phone!
```

* **No Accounts / No Logins**: You don't need BotFather, developer portals, or phone number verification.
* **No Credit Cards**: Everything used is 100% free forever.
* **PC Can Be Off**: The bot runs continuously on Render's cloud servers.

---

## Step 1: Set Up ntfy on Your Phone (30 Seconds)

1. Download the free **ntfy** app:
   - [ntfy for Android (Google Play)](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [F-Droid](https://f-droid.org/packages/io.heckel.ntfy/)
   - [ntfy for iOS (App Store)](https://apps.apple.com/us/app/ntfy/id1625396347)
2. Open the app, and you can use your **existing topic**:
   ```
   yt-alert-naveen
   ```
   *(You do NOT need to create a new topic! The bot safely shares this same topic for both receiving alerts and typing commands like `init`).*
3. If not already subscribed, tap **"+"** in the app and subscribe to `yt-alert-naveen`.

---

## Step 2: Deploy to Render.com (Free 24/7 Cloud Host)

1. Create a free account at [render.com](https://render.com/) (sign in with your GitHub account).
2. Push this folder to a GitHub repository:
   ```bash
   git init
   git add .
   git commit -m "Deploy YouTube Live Chat Alert Bot"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/yt-livechat-alert.git
   git push -u origin main
   ```
3. In Render Dashboard, click **New +** > **Web Service**.
4. Connect your GitHub repository.
5. Set the service settings:
   - **Name**: `yt-livechat-alert`
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Instance Type**: `Free`
6. Scroll down to **Environment Variables** and add:
   | Key | Value | Description |
   |---|---|---|
   | `NTFY_TOPIC` | `yt-live-alert-naveen-77` | Your ntfy topic name |
   | `YT_CHANNEL` | `@YourStreamerHandle` | Target YouTube channel |
   | `KEYWORDS` | `solo, 1v1, ff, free fire` | Keywords to detect |
   | `AUTO_START_BOT` | `false` | Set to `false` so it waits for your `init` message! |
7. Click **Create Web Service**.
8. Render will deploy your service and give you a public URL (e.g. `https://yt-livechat-alert.onrender.com`).

---

## Step 3: Keep it Awake 24/7 with UptimeRobot (Free Ping)

Free Render apps go to sleep after 15 minutes of inactivity. To keep it **awake and running 24/7 forever at $0**:

1. Create a free account at [uptimerobot.com](https://uptimerobot.com/).
2. Click **Add New Monitor**:
   - **Monitor Type**: `HTTP(s)`
   - **Friendly Name**: `YouTube Alert Bot`
   - **URL (or IP)**: `https://yt-livechat-alert.onrender.com` *(your Render URL)*
   - **Monitoring Interval**: `Every 5 minutes`
3. Click **Create Monitor**.

UptimeRobot will ping your server every 5 minutes so it never goes to sleep!

---

## Step 4: Control & Trigger from Your Phone

Open the **ntfy** app on your phone, open your topic, tap the **Send / Pen icon**, and send any command:

| Type this in ntfy | What it does | Confirmation You Receive |
|---|---|---|
| **`init`** or **`start`** | **Triggers and starts chat monitoring** | 🟢 *Bot started! Monitoring channel: @Streamer* |
| **`status`** | Checks bot state, stream live status & uptime | 📊 *Bot Status Report (State, Channel, Matches)* |
| **`stop`** | Pauses chat monitoring | 🛑 *Bot stopped successfully.* |
| **`channel @NewStreamer`** | Switches target channel dynamically | ✅ *Target channel updated to: @NewStreamer* |
| **`keywords solo, 1v1`** | Updates watched keywords on the fly | ✅ *Keywords updated: solo, 1v1* |
| **`help`** | Shows command list | 🤖 *Command Guide* |

When any keyword is typed in the live chat, an alert with a clickable YouTube link will pop up on your phone inside the same topic!

---

## Testing Locally on Your PC

Before deploying to the cloud, you can test it on your local PC:

1. Create a `.env` file in this folder:
   ```env
   NTFY_TOPIC=yt-live-alert-naveen-77
   YT_CHANNEL=@YourStreamerHandle
   AUTO_START_BOT=false
   ```
2. Start the app:
   ```bash
   python main.py
   ```
3. Open the ntfy app on your phone, open your topic, and type **`init`**.
4. You will see the bot start monitoring in your console and send you a confirmation notification!
