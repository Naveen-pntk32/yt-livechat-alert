"""
Main Application Entrypoint (100% Free 24/7 Cloud Architecture)
---------------------------------------------------------------
Launches:
1. LiveChatAlertBot: YouTube 0-Quota Live Chat Keyword Watcher.
2. NtfyController: Two-way phone remote control via ntfy.sh (type 'init' in app).
3. TelegramController: Optional Telegram Bot remote control.
4. Keep-Alive Web Server: Minimal Flask endpoint for UptimeRobot / Render 24/7 ping.

Usage:
    python main.py         -> Starts Cloud/Local Service (Bot + Remote Controllers + Keep-Alive)
    python main.py --cli   -> Runs Bot directly in foreground (Console CLI Mode)
"""

import os
import sys
import time
import signal
import logging
from flask import Flask, Response, jsonify

from yt_live_chat_alert import (
    LiveChatAlertBot,
    FAVORITE_CHANNEL,
    NTFY_TOPIC,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)
from ntfy_controller import NtfyController
from telegram_controller import TelegramController

# Ensure UTF-8 output on console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Main] %(message)s",
)
log = logging.getLogger("app")

# Initialize Flask Keep-Alive Server
app = Flask(__name__)

# Initialize Bot Singleton
bot = LiveChatAlertBot()

# Initialize Remote Controllers
ntfy_controller = NtfyController(bot)
tg_controller = TelegramController(bot)


@app.route("/", methods=["GET"])
def health_dashboard():
    """Health check endpoint pinged by UptimeRobot to keep Render free tier awake 24/7."""
    status = bot.get_status()
    state_color = "#10b981" if status["is_running"] else "#ef4444"
    stream_badge = (
        '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:4px;">🔴 LIVE</span>'
        if status["is_stream_live"]
        else '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:4px;">Offline</span>'
    )
    ntfy_status = (
        f'<span style="color:#10b981;">Active ({ntfy_controller.topic})</span>'
        if ntfy_controller.is_running or ntfy_controller.is_configured
        else '<span style="color:#f59e0b;">Not Configured</span>'
    )
    tg_status = (
        '<span style="color:#10b981;">Active</span>'
        if tg_controller.is_running or tg_controller.is_configured
        else '<span style="color:#6b7280;">Disabled</span>'
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>YouTube Live Chat Alert Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; }}
        .card {{ max-width: 600px; margin: 0 auto; background: #1e293b; border-radius: 12px; padding: 1.5rem; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.3); }}
        h1 {{ font-size: 1.5rem; margin-top: 0; display: flex; align-items: center; gap: 0.5rem; }}
        .status-dot {{ width: 12px; height: 12px; border-radius: 50%; background: {state_color}; display: inline-block; }}
        .row {{ display: flex; justify-content: space-between; padding: 0.75rem 0; border-bottom: 1px solid #334155; }}
        .row:last-child {{ border-bottom: none; }}
        .label {{ color: #94a3b8; font-weight: 500; }}
        .val {{ font-weight: 600; text-align: right; }}
        .kw-tag {{ display: inline-block; background: #334155; padding: 2px 6px; border-radius: 4px; margin: 2px; font-size: 0.85rem; }}
        .tip {{ background: #0f172a; border-left: 4px solid #3b82f6; padding: 0.75rem; border-radius: 4px; margin-top: 1rem; font-size: 0.9rem; }}
    </style>
</head>
<body>
    <div class="card">
        <h1><span class="status-dot"></span> YouTube Alert Bot</h1>
        <div class="row"><span class="label">State</span><span class="val" style="color:{state_color};">{status['state']}</span></div>
        <div class="row"><span class="label">Target Channel</span><span class="val">{status.get('target_display') or status['target_channel'] or 'None'}</span></div>
        <div class="row"><span class="label">Stream Status</span><span class="val">{stream_badge}</span></div>
        <div class="row"><span class="label">ntfy Remote Control</span><span class="val">{ntfy_status}</span></div>
        <div class="row"><span class="label">Telegram Remote</span><span class="val">{tg_status}</span></div>
        <div class="row"><span class="label">Uptime</span><span class="val">{status['uptime']}</span></div>
        <div class="row"><span class="label">Matches Found</span><span class="val">{status['total_matches']}</span></div>
        <div class="row"><span class="label">Keywords</span><span class="val">{' '.join(f'<span class="kw-tag">{k}</span>' for k in status['keywords'][:8])}</span></div>
        <div class="tip">
            💡 <b>Remote Trigger:</b> Open your ntfy app, open topic <code>{ntfy_controller.topic or 'your-topic'}</code>, and type <b>init</b> to start monitoring.
        </div>
    </div>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


@app.route("/status", methods=["GET"])
def status_api():
    """Returns JSON status."""
    return jsonify(bot.get_status())


def graceful_shutdown(signum, frame):
    log.info("Shutdown signal received. Stopping services...")
    ntfy_controller.stop()
    tg_controller.stop()
    bot.stop()
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


def start_services():
    # 1. Start ntfy two-way controller (type 'init' in ntfy to trigger bot)
    if ntfy_controller.is_configured:
        log.info(f"Starting ntfy Two-Way Remote Controller for topic '{ntfy_controller.topic}'...")
        ntfy_controller.start()
    else:
        log.warning("NTFY_TOPIC is not set. ntfy remote control is disabled.")

    # 2. Start Telegram controller if token configured
    if tg_controller.is_configured:
        log.info("Starting Telegram Remote Controller...")
        tg_controller.start()

    # 3. Auto-start bot on boot if requested
    auto_start = os.environ.get("AUTO_START_BOT", "false").strip().lower() in ("true", "1", "yes")
    if auto_start:
        if FAVORITE_CHANNEL:
            log.info(f"AUTO_START_BOT=true: Launching bot on startup for {FAVORITE_CHANNEL}...")
            bot.start()
        else:
            log.warning("AUTO_START_BOT is true, but YT_CHANNEL is not set. Waiting for 'init' command.")
    else:
        log.info("Bot is in on-demand mode. Send 'init' in your ntfy topic to start monitoring!")

    # 4. Start Keep-Alive Web Server
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    log.info(f"Starting Keep-Alive Web Server on {host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from yt_live_chat_alert import run
        run()
    else:
        start_services()
