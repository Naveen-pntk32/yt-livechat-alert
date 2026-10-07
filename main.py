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
import socket
import requests

try:
    import urllib3.util.connection as urllib3_cn
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

from flask import Flask, Response, jsonify, request

from yt_live_chat_alert import (
    LiveChatAlertBot,
    FAVORITE_CHANNEL,
    NTFY_TOPIC,
    NTFY_SERVER,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    find_live_video_id_rss,
    find_candidate_video_id,
)
from ntfy_controller import NtfyController
from telegram_controller import TelegramController
from scheduler import DailyScheduler

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
ntfy_controller = NtfyController(bot, topic=NTFY_TOPIC, server_url=NTFY_SERVER)
tg_controller = TelegramController(bot)


def broadcast_notify(message: str, title: str):
    """Dispatches announcement notifications to both ntfy and Telegram."""
    if ntfy_controller.is_configured:
        try:
            ntfy_controller.publish_response(message, title=title, priority=4, tags=["clock", "robot"])
        except Exception as e:
            log.warning(f"ntfy broadcast failed: {e}")

    if tg_controller.is_configured and tg_controller.admin_chat_id:
        try:
            tg_controller.send_message(tg_controller.admin_chat_id, f"*{title}*\n\n{message}")
        except Exception as e:
            log.warning(f"Telegram broadcast failed: {e}")


# Initialize Daily Automation Scheduler (09:00 - 17:00 IST)
daily_scheduler = DailyScheduler(bot, notify_callback=broadcast_notify)
ntfy_controller.scheduler = daily_scheduler
tg_controller.scheduler = daily_scheduler


@app.route("/", methods=["GET"])
def health_dashboard():
    """Health check endpoint pinged by UptimeRobot to keep Render free tier awake 24/7."""
    status = bot.get_status()
    state_color = "#10b981" if status["is_running"] else "#ef4444"
    if status["is_stream_live"] and status.get("current_video_id"):
        stream_url = status.get("stream_url") or f"https://youtu.be/{status['current_video_id']}"
        stream_badge = (
            f'<a href="{stream_url}" target="_blank" style="background:#ef4444;color:#fff;'
            f'padding:3px 10px;border-radius:6px;text-decoration:none;font-weight:700;'
            f'display:inline-flex;align-items:center;gap:4px;">'
            f'🔴 LIVE NOW ({status["current_video_id"]}) ↗</a>'
        )
    else:
        stream_badge = '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:4px;">Offline</span>'

    ntfy_status = (
        f'<span style="color:#10b981;">Active ({ntfy_controller.topic} on {ntfy_controller.server_url})</span>'
        if ntfy_controller.is_running
        else f'<span style="color:#f59e0b;">Configured ({ntfy_controller.topic})</span>'
        if ntfy_controller.is_configured
        else '<span style="color:#6b7280;">Not Configured</span>'
    )
    tg_status = (
        '<span style="color:#10b981;">Active</span>'
        if tg_controller.is_running
        else f'<span style="color:#f59e0b;">Configured</span>'
        if tg_controller.is_configured
        else '<span style="color:#6b7280;">Disabled</span>'
    )
    sched_status = daily_scheduler.get_status()
    sched_badge = (
        f'<span style="color:#10b981;">Active ({sched_status["start_time"]} - {sched_status["stop_time"]} {sched_status["timezone"]})</span>'
        if sched_status["enabled"]
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
        <div class="row"><span class="label">Daily Schedule</span><span class="val">{sched_badge}</span></div>
        <div class="row"><span class="label">Next Schedule Action</span><span class="val">{sched_status['next_action']}</span></div>
        <div class="row"><span class="label">ntfy Remote Control</span><span class="val">{ntfy_status}</span></div>
        <div class="row"><span class="label">Telegram Remote</span><span class="val">{tg_status}</span></div>
        <div class="row"><span class="label">Uptime</span><span class="val">{status['uptime']}</span></div>
        <div class="row"><span class="label">Matches Found</span><span class="val">{status['total_matches']}</span></div>
        <div class="row"><span class="label">Keywords</span><span class="val">{' '.join(f'<span class="kw-tag">{k}</span>' for k in status['keywords'][:8])}</span></div>
        <div class="tip">
            ⏰ <b>Daily Schedule:</b> Runs automatically <b>{sched_status['start_time']} - {sched_status['stop_time']}</b> daily.<br>
            💡 <b>Remote Trigger:</b> You can still type <b>start</b> or <b>stop</b> anytime in ntfy or Telegram!
        </div>
    </div>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


@app.route("/status", methods=["GET"])
def status_api():
    """Returns JSON status."""
    data = bot.get_status()
    data["scheduler"] = daily_scheduler.get_status()
    data["ntfy_configured"] = ntfy_controller.is_configured
    data["ntfy_running"] = ntfy_controller.is_running
    data["ntfy_topic"] = ntfy_controller.topic
    data["ntfy_server"] = ntfy_controller.server_url
    data["telegram_configured"] = tg_controller.is_configured
    data["telegram_running"] = tg_controller.is_running
    return jsonify(data)


@app.route("/debug", methods=["GET"])
def debug_api():
    """Diagnostic endpoint to inspect live stream detection signals and API state."""
    data = bot.get_status()
    data["scheduler"] = daily_scheduler.get_status()
    data["api_has_keys"] = bot.api_manager.has_keys if bot.api_manager else False
    data["api_quota_exhausted"] = bot.api_manager.quota_exhausted if bot.api_manager else False
    data["api_keys_count"] = len(bot.api_manager.api_keys) if bot.api_manager else 0

    cid = bot.channel_id
    if cid:
        try:
            data["rss_test_result"] = find_live_video_id_rss(cid, bot.api_manager)
        except Exception as e:
            data["rss_test_result"] = f"Error: {e}"

        try:
            data["candidate_test_result"] = find_candidate_video_id(
                cid,
                bot.api_manager,
                channel_handle=bot.channel_handle,
            )
        except Exception as e:
            data["candidate_test_result"] = f"Error: {e}"

    return jsonify(data)


@app.route("/debug/ntfy", methods=["GET"])
def debug_ntfy():
    """Inspects ntfy controller thread, state, and tests connectivity."""
    thread_obj = getattr(ntfy_controller, "_thread", None)
    res = {
        "version": "v1.3.0-poller-verified",
        "thread_name": thread_obj.name if thread_obj else None,
        "is_alive": thread_obj.is_alive() if thread_obj else False,
        "is_running": ntfy_controller.is_running,
        "last_id": getattr(ntfy_controller, "last_id", None),
        "seen_ids_count": len(getattr(ntfy_controller, "seen_ids", set())),
        "topic": ntfy_controller.topic,
        "server": ntfy_controller.server_url,
    }

    auth_token = os.environ.get("NTFY_AUTH_TOKEN", "").strip() or "tk_h4ulqyctrc6110279nh9k9w414mmk"
    auth_headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}

    # 1. Egress IP check
    try:
        ip_r = requests.get("https://api.ipify.org?format=json", timeout=4)
        res["egress_ip"] = ip_r.json().get("ip")
    except Exception as e:
        res["egress_ip_error"] = str(e)

    # 2. Raw socket connection tests
    for target_host in ["ntfy.sh", "ntfy.adminforge.de", "api.telegram.org"]:
        key = f"socket_{target_host.replace('.', '_')}"
        try:
            s_t0 = time.time()
            s = socket.create_connection((target_host, 443), timeout=4)
            s.close()
            res[key] = f"OK in {round((time.time() - s_t0) * 1000, 1)}ms"
        except Exception as e:
            res[key] = f"FAILED: {e}"

    # 3. Test authenticated GET on configured server
    try:
        t0 = time.time()
        g = requests.get(
            f"{ntfy_controller.server_url}/{ntfy_controller.topic}/json",
            params={"poll": "1", "since": "30s"},
            headers=auth_headers,
            timeout=6,
        )
        res["outbound_get_status"] = g.status_code
        res["outbound_get_ms"] = round((time.time() - t0) * 1000, 1)
        res["outbound_get_bytes"] = len(g.content)
    except Exception as e:
        res["outbound_get_error"] = str(e)

    # 4. Test official ntfy.sh with auth
    try:
        t_sh = time.time()
        g_sh = requests.get(
            f"https://ntfy.sh/{ntfy_controller.topic}/json",
            params={"poll": "1", "since": "30s"},
            headers=auth_headers,
            timeout=6,
        )
        res["ntfy_sh_get_status"] = g_sh.status_code
        res["ntfy_sh_get_ms"] = round((time.time() - t_sh) * 1000, 1)
    except Exception as e:
        res["ntfy_sh_get_error"] = str(e)

    # 5. Test telegram reachability
    try:
        t_tg = time.time()
        g_tg = requests.get("https://api.telegram.org", timeout=4)
        res["telegram_api_status"] = g_tg.status_code
        res["telegram_api_ms"] = round((time.time() - t_tg) * 1000, 1)
    except Exception as e:
        res["telegram_api_error"] = str(e)

    # 6. Test executing a command on demand if ?cmd=... provided
    test_cmd = request.args.get("cmd")
    if test_cmd:
        try:
            ntfy_controller._execute_and_reply(test_cmd)
            res["cmd_executed"] = test_cmd
        except Exception as e:
            res["cmd_error"] = str(e)

    # 7. Test direct publish if ?test_pub=1 provided
    test_pub = request.args.get("test_pub")
    if test_pub:
        res["test_pub_result"] = ntfy_controller.publish_response(
            message=f"Manual test ping from debug endpoint at {time.strftime('%X')}",
            title="🔍 Bot Test Ping",
            priority=4,
        )

    return jsonify(res)


def graceful_shutdown(signum, frame):
    log.info("Shutdown signal received. Stopping services...")
    daily_scheduler.stop()
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

    # 3. Start Daily Automation Scheduler
    if daily_scheduler.enabled:
        log.info(f"Starting Daily Automation Scheduler ({daily_scheduler.start_time_str} - {daily_scheduler.stop_time_str} {daily_scheduler.timezone_str})...")
        daily_scheduler.start()

    # 4. Auto-start bot on boot if requested or currently within scheduled hours
    auto_start = os.environ.get("AUTO_START_BOT", "false").strip().lower() in ("true", "1", "yes")
    if auto_start:
        if FAVORITE_CHANNEL:
            log.info(f"AUTO_START_BOT=true: Launching bot on startup for {FAVORITE_CHANNEL}...")
            bot.start()
        else:
            log.warning("AUTO_START_BOT is true, but YT_CHANNEL is not set. Waiting for 'init' command.")
    elif daily_scheduler.enabled:
        now = daily_scheduler.get_current_time()
        if daily_scheduler.start_time <= now.time() < daily_scheduler.stop_time:
            log.info(
                f"Auto-starting bot on boot because current time ({now.strftime('%I:%M %p')}) "
                f"is within active daily schedule window ({daily_scheduler.start_time_str} - {daily_scheduler.stop_time_str} {daily_scheduler.timezone_str})..."
            )
            bot.start()
        else:
            log.info("Bot is in on-demand mode. Send 'init' in your ntfy topic to start monitoring!")
    else:
        log.info("Bot is in on-demand mode. Send 'init' in your ntfy topic to start monitoring!")

    # 5. Start Keep-Alive Web Server
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
