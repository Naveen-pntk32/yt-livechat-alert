"""
Telegram Bot Two-Way Remote Controller
--------------------------------------
Allows controlling the YouTube Live Chat Alert Bot directly from Telegram:
- /start   : Start live chat monitoring
- /stop    : Stop / pause monitoring
- /status  : Check live stream status, channel & uptime
- /channel : Change target streamer dynamically (/channel @handle)
- /keywords: Update watched keywords (/keywords solo, 1v1, ff)
- /help    : Show interactive command menu

Uses 100% free Telegram Bot API with secure long-polling.
Requires zero open ports, zero webhooks, and zero paid subscriptions.
"""

import os
import sys
import time
import logging
import threading
from typing import Optional, List, Dict, Any

import requests

from yt_live_chat_alert import (
    LiveChatAlertBot,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

log = logging.getLogger("telegram_controller")


class TelegramController:
    """
    Listens for incoming Telegram commands via long-polling
    and executes actions on the LiveChatAlertBot instance.
    """

    def __init__(
        self,
        bot_instance: LiveChatAlertBot,
        token: Optional[str] = None,
        admin_chat_id: Optional[str] = None,
        scheduler: Optional[Any] = None,
    ):
        self.bot = bot_instance
        self.token = (token or TELEGRAM_BOT_TOKEN).strip()
        self.admin_chat_id = str(admin_chat_id or TELEGRAM_CHAT_ID).strip()
        self.scheduler = scheduler
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.last_update_id = 0

    @property
    def is_configured(self) -> bool:
        return bool(self.token)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def send_message(self, chat_id: str, text: str, reply_markup: Optional[Dict] = None) -> bool:
        """Send a formatted message to a Telegram chat."""
        if not self.token:
            return False

        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": False,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            resp = requests.post(url, json=payload, timeout=10)
            if not resp.ok:
                # Fallback to plain text if markdown formatting failed
                payload.pop("parse_mode", None)
                resp = requests.post(url, json=payload, timeout=10)
            return resp.ok
        except Exception as e:
            log.error(f"Failed to send Telegram message to {chat_id}: {e}")
            return False

    def is_authorized(self, chat_id: str, user_id: Optional[str] = None) -> bool:
        """Check if incoming message is from the authorized administrator."""
        if not self.admin_chat_id:
            return True
        chat_id_str = str(chat_id).strip()
        admin_str = self.admin_chat_id.strip()
        return chat_id_str == admin_str or (user_id and str(user_id).strip() == admin_str)

    def handle_command(self, text: str, chat_id: str) -> str:
        """
        Parse and execute a Telegram command string.
        Returns the markdown reply text.
        """
        raw = text.strip()
        if not raw:
            return "Send `/help` to see available commands."

        parts = raw.split(maxsplit=1)
        cmd = parts[0].split("@")[0].lower()  # Strip bot username if mentioned (e.g. /status@MyBot -> /status)
        args = parts[1].strip() if len(parts) > 1 else ""

        log.info(f"Telegram command '{cmd}' args='{args}' from chat_id={chat_id}")

        if cmd in ("/start", "start", "/run", "run"):
            success, msg = self.bot.start()
            return msg

        elif cmd in ("/stop", "stop", "/pause", "pause"):
            success, msg = self.bot.stop()
            return msg

        elif cmd in ("/status", "status", "/info", "info"):
            status_text = self.bot.get_status_text()
            if self.scheduler:
                sc = self.scheduler.get_status()
                sched_state = "Active" if sc["enabled"] else "Disabled"
                status_text += f"\n• *Daily Schedule:* {sched_state} (`{sc['start_time']} - {sc['stop_time']}` {sc['timezone']})"
            return status_text

        elif cmd in ("/schedule", "schedule", "/hours", "/timer"):
            if not self.scheduler:
                return "⏰ Daily scheduler is not configured in this deployment."
            if args.lower() in ("on", "enable", "start"):
                self.scheduler.set_enabled(True)
                sc = self.scheduler.get_status()
                return f"⏰ *Daily Schedule Enabled!*\n• Window: `{sc['start_time']} - {sc['stop_time']}` ({sc['timezone']})\n• Next: {sc['next_action']}"
            elif args.lower() in ("off", "disable", "pause", "stop"):
                self.scheduler.set_enabled(False)
                return "⏸️ *Daily Schedule Paused.*\nBot will only start when you manually send `/start`."
            else:
                sc = self.scheduler.get_status()
                state_str = "🟢 Active (Daily Auto 9am-5pm)" if sc["enabled"] else "⏸️ Paused (Manual Only)"
                return (
                    f"⏰ *Daily Schedule Status:* {state_str}\n"
                    f"• *Window:* `{sc['start_time']} - {sc['stop_time']}` ({sc['timezone']})\n"
                    f"• *Current Time:* `{sc['current_time']}`\n"
                    f"• *Next Action:* {sc['next_action']}\n\n"
                    f"Commands:\n"
                    f"• `/schedule off` - Pause automatic daily triggers\n"
                    f"• `/schedule on` - Re-enable daily auto triggers"
                )

        elif cmd in ("/channel", "channel", "/setchannel"):
            if not args:
                curr = self.bot.get_target_display() if hasattr(self.bot, "get_target_display") else (self.bot.channel or "None configured")
                return (
                    f"📺 *Current Monitored Target:* `{curr}`\n\n"
                    "To switch targets, send:\n"
                    "• `/channel @StreamerHandle`\n"
                    "• `/channel UC...`\n"
                    "• `/channel https://youtu.be/<live_video_id>`"
                )
            success, msg = self.bot.update_channel(args)
            return msg

        elif cmd in ("/keywords", "keywords", "/kw"):
            if not args:
                kws = ", ".join(self.bot.keywords)
                return (
                    f"🔑 *Watched Keywords ({len(self.bot.keywords)}):*\n`{kws}`\n\n"
                    f"To update keywords, type:\n`/keywords solo, 1v1, ff, free fire`"
                )
            new_kws = [k.strip() for k in args.split(",") if k.strip()]
            if not new_kws:
                return "⚠️ Please provide comma-separated keywords, e.g.: `/keywords solo, 1v1, ff`"
            success, msg = self.bot.update_keywords(new_kws)
            return msg

        elif cmd in ("/help", "help", "/menu", "menu"):
            return (
                "🤖 *YouTube Live Chat Alert Bot - Controls*\n\n"
                "• `/status` - Check bot state, streamer live status, chat stats & uptime\n"
                "• `/schedule` - View or toggle daily 9am-5pm schedule\n"
                "• `/start` - Start monitoring YouTube live stream\n"
                "• `/stop` - Pause / stop monitoring\n"
                "• `/channel <@handle or link>` - Switch target channel or live video link\n"
                "• `/keywords <k1, k2>` - Update watched keywords\n"
                "• `/help` - Show this menu"
            )

        else:
            return (
                f"❓ Unknown command: `{cmd}`\n\n"
                "Send `/help` to view all available commands."
            )

    def _make_keyboard(self) -> Dict[str, Any]:
        """Creates quick-reply buttons for easy 1-tap mobile control."""
        return {
            "keyboard": [
                [{"text": "/status"}, {"text": "/schedule"}],
                [{"text": "/start"}, {"text": "/stop"}],
                [{"text": "/channel"}, {"text": "/keywords"}, {"text": "/help"}],
            ],
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }

    def _poll_loop(self) -> None:
        """Long-polling worker loop using Telegram getUpdates."""
        log.info("Telegram Bot long-polling worker started.")
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"

        while not self._stop_event.is_set():
            try:
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 20,
                }
                resp = requests.get(url, params=params, timeout=30)
                if not resp.ok:
                    log.warning(f"Telegram getUpdates returned HTTP {resp.status_code}")
                    if self._stop_event.wait(5.0):
                        break
                    continue

                data = resp.json()
                updates = data.get("result", [])

                for update in updates:
                    update_id = update.get("update_id", 0)
                    if update_id > self.last_update_id:
                        self.last_update_id = update_id

                    message = update.get("message") or update.get("channel_post")
                    if not message:
                        continue

                    text = message.get("text", "").strip()
                    chat = message.get("chat", {})
                    chat_id = str(chat.get("id", ""))
                    user = message.get("from", {})
                    user_id = str(user.get("id", ""))

                    if not text or not chat_id:
                        continue

                    if not self.is_authorized(chat_id, user_id):
                        log.warning(f"Rejected unauthorized Telegram command from chat_id={chat_id}")
                        self.send_message(
                            chat_id,
                            "⛔ *Access Denied*\nThis bot only accepts commands from its configured admin.",
                        )
                        continue

                    reply = self.handle_command(text, chat_id)
                    self.send_message(chat_id, reply, reply_markup=self._make_keyboard())

            except requests.RequestException as e:
                log.debug(f"Telegram polling network issue (will retry): {e}")
                if self._stop_event.wait(3.0):
                    break
            except Exception as e:
                log.error(f"Unexpected error in Telegram polling loop: {e}", exc_info=True)
                if self._stop_event.wait(5.0):
                    break

        log.info("Telegram Bot long-polling worker exited.")

    def start(self) -> bool:
        """Starts the Telegram polling loop in a background daemon thread."""
        if not self.is_configured:
            log.warning("TELEGRAM_BOT_TOKEN is not configured. Telegram remote control disabled.")
            return False

        if self.is_running:
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="tg_bot_poller", daemon=True)
        self._thread.start()
        log.info("Telegram Remote Controller is ACTIVE.")
        return True

    def stop(self) -> None:
        """Stops the Telegram polling worker cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        log.info("Telegram Remote Controller stopped.")
