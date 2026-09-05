"""
WhatsApp Webhook & Command Controller Server
--------------------------------------------
Listens for incoming WhatsApp messages via webhooks (Twilio API / Meta Cloud API)
to control, start, stop, and inspect the YouTube Live Chat Alert Bot.

Runs 24/7 on cloud platforms (Render, Railway, Fly.io, Oracle VPS) or local server.
"""

import os
import sys
import logging
import signal
from xml.sax.saxutils import escape as xml_escape
from typing import Optional, Tuple

from flask import Flask, request, Response, jsonify

from yt_live_chat_alert import (
    LiveChatAlertBot,
    ADMIN_WHATSAPP_NUMBER,
    TWILIO_WHATSAPP_NUMBER,
    FAVORITE_CHANNEL,
    KEYWORDS,
)

# Ensure UTF-8 output on console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Webhook] %(message)s",
)
log = logging.getLogger("webhook_server")

app = Flask(__name__)

# Initialize bot singleton
bot = LiveChatAlertBot()

# Auto-start bot if requested by environment
AUTO_START_BOT = os.environ.get("AUTO_START_BOT", "false").strip().lower() in ("true", "1", "yes")
if AUTO_START_BOT:
    if FAVORITE_CHANNEL:
        log.info("AUTO_START_BOT=true: Launching bot on server startup...")
        bot.start()
    else:
        log.warning("AUTO_START_BOT is true, but YT_CHANNEL is not set. Bot idle until configured.")


def normalize_phone_number(num: str) -> str:
    """Normalize phone numbers for safe comparison (e.g. '+91 9876-543210' -> '+919876543210')."""
    if not num:
        return ""
    num = num.strip().replace("whatsapp:", "")
    # Keep leading + and all digits
    has_plus = num.startswith("+")
    digits = "".join(ch for ch in num if ch.isdigit())
    return f"+{digits}" if has_plus else digits


def is_authorized_sender(sender: str) -> str:
    """Verify if the sender matches ADMIN_WHATSAPP_NUMBER if configured."""
    admin_num = normalize_phone_number(ADMIN_WHATSAPP_NUMBER)
    if not admin_num:
        # If no admin number configured, allow all senders
        return True

    sender_num = normalize_phone_number(sender)
    return sender_num == admin_num


def format_twiml_response(message_body: str) -> Response:
    """Return a valid Twilio TwiML XML response."""
    safe_body = xml_escape(message_body)
    xml_content = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response>\n"
        f"    <Message><Body>{safe_body}</Body></Message>\n"
        f"</Response>"
    )
    return Response(xml_content, mimetype="application/xml")


def handle_bot_command(raw_text: str, sender: str = "") -> str:
    """
    Parses and executes a command string received from WhatsApp or API.
    Returns the response message to send back to the user.
    """
    text = raw_text.strip()
    if not text:
        return "⚠️ Empty message received. Send *HELP* to see available commands."

    parts = text.split(maxsplit=1)
    command = parts[0].upper()
    args = parts[1].strip() if len(parts) > 1 else ""

    log.info(f"Processing command '{command}' with args='{args}' from sender='{sender}'")

    if command in ("START", "RUN", "RESUME", "MONITOR"):
        success, msg = bot.start()
        return msg

    elif command in ("STOP", "PAUSE", "HALT"):
        success, msg = bot.stop()
        return msg

    elif command in ("STATUS", "STATE", "INFO"):
        return bot.get_status_text()

    elif command in ("CHANNEL", "SETCHANNEL", "TARGET"):
        if not args:
            current = bot.channel or "None configured"
            return f"📺 Current target channel: *{current}*\nTo change: `CHANNEL <@handle or URL>`"
        success, msg = bot.update_channel(args)
        return msg

    elif command in ("KEYWORDS", "SETKEYWORDS", "KW"):
        if not args:
            kws = ", ".join(bot.keywords)
            return f"🔑 Current keywords ({len(bot.keywords)}):\n{kws}\n\nTo update: `KEYWORDS solo, 1v1, ff`"
        new_kws = [k.strip() for k in args.split(",") if k.strip()]
        if not new_kws:
            return "⚠️ Please provide comma-separated keywords, e.g.: `KEYWORDS solo, 1v1, ff`"
        success, msg = bot.update_keywords(new_kws)
        return msg

    elif command in ("HELP", "MENU", "COMMANDS"):
        return (
            "🤖 *YouTube Live Chat Alert Bot*\n\n"
            "• *START* - Start monitoring stream\n"
            "• *STOP* - Stop / pause monitoring\n"
            "• *STATUS* - Show status, active stream & uptime\n"
            "• *CHANNEL <url/@handle>* - Switch target channel\n"
            "• *KEYWORDS <k1, k2>* - Update watched keywords\n"
            "• *HELP* - Show this command menu"
        )

    else:
        return (
            f"❓ Unrecognized command: *{command}*\n\n"
            f"Send *HELP* to see the list of valid commands."
        )


# ─────────────────────────────────────────────────────────────────────────────
# HTTP ENDPOINTS
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    """Health check and web status dashboard."""
    status = bot.get_status()
    state_color = "#10b981" if status["is_running"] else "#ef4444"
    stream_badge = (
        '<span style="background:#ef4444;color:#fff;padding:2px 8px;border-radius:4px;">🔴 LIVE</span>'
        if status["is_stream_live"]
        else '<span style="background:#6b7280;color:#fff;padding:2px 8px;border-radius:4px;">Offline</span>'
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
    </style>
</head>
<body>
    <div class="card">
        <h1><span class="status-dot"></span> YouTube Alert Bot</h1>
        <div class="row"><span class="label">State</span><span class="val" style="color:{state_color};">{status['state']}</span></div>
        <div class="row"><span class="label">Target Channel</span><span class="val">{status['target_channel'] or 'None'}</span></div>
        <div class="row"><span class="label">Stream Status</span><span class="val">{stream_badge}</span></div>
        <div class="row"><span class="label">Uptime</span><span class="val">{status['uptime']}</span></div>
        <div class="row"><span class="label">Matches Found</span><span class="val">{status['total_matches']}</span></div>
        <div class="row"><span class="label">Keywords</span><span class="val">{' '.join(f'<span class="kw-tag">{k}</span>' for k in status['keywords'][:8])}</span></div>
    </div>
</body>
</html>
"""
    return Response(html, mimetype="text/html")


@app.route("/status", methods=["GET"])
def get_status_json():
    """REST endpoint returning current bot status in JSON."""
    return jsonify(bot.get_status())


@app.route("/whatsapp/webhook", methods=["POST"])
def whatsapp_webhook():
    """
    Twilio WhatsApp Webhook endpoint.
    Called whenever a user sends a message to your WhatsApp number.
    """
    sender = request.values.get("From", "").strip()
    body = request.values.get("Body", "").strip()

    if not body:
        # Check if JSON payload (e.g. from Meta Cloud API or custom tests)
        if request.is_json:
            data = request.get_json(silent=True) or {}
            body = data.get("Body", data.get("message", data.get("text", ""))).strip()
            sender = data.get("From", data.get("sender", "")).strip()

    log.info(f"Incoming WhatsApp webhook message: '{body}' from '{sender}'")

    # Verify authorization
    if not is_authorized_sender(sender):
        log.warning(f"Rejected unauthorized WhatsApp message from {sender}")
        reject_msg = (
            "⛔ *Access Denied*\n\n"
            "This YouTube Alert Bot is locked to its authorized admin phone number."
        )
        return format_twiml_response(reject_msg)

    # Process command
    reply = handle_bot_command(body, sender=sender)
    return format_twiml_response(reply)


@app.route("/api/command", methods=["POST"])
def api_command():
    """Direct REST endpoint to trigger commands with JSON: {"command": "START"}."""
    data = request.get_json(force=True, silent=True) or {}
    cmd = data.get("command", "")
    if not cmd:
        return jsonify({"error": "Missing 'command' field"}), 400

    reply = handle_bot_command(cmd, sender="REST_API")
    return jsonify({
        "reply": reply,
        "status": bot.get_status(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# SHUTDOWN HANDLER
# ─────────────────────────────────────────────────────────────────────────────

def graceful_shutdown(signum, frame):
    log.info("Signal received. Shutting down bot gracefully...")
    bot.stop()
    sys.exit(0)


signal.signal(signal.SIGINT, graceful_shutdown)
signal.signal(signal.SIGTERM, graceful_shutdown)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    log.info(f"Starting WhatsApp Webhook & Controller Server on {host}:{port}")
    app.run(host=host, port=port, debug=False)
