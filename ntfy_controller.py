"""
ntfy.sh Two-Way Remote Controller
---------------------------------
Listens for incoming command messages published to an ntfy topic via real-time stream:
- init / start   : Triggers live chat monitoring (starts the bot)
- stop / pause   : Stops / pauses monitoring
- status         : Publishes current bot state & stream status back to ntfy
- channel <name> : Switches monitored channel dynamically
- keywords <kws> : Updates watched keywords on the fly
- help           : Displays available commands

100% Free Forever. Zero accounts, zero API tokens, and zero open ports required.
"""

import os
import sys
import json
import time
import logging
import threading
from typing import Optional, List, Dict, Any, Tuple

import socket
import requests

try:
    import urllib3.util.connection as urllib3_cn
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
except Exception:
    pass

from yt_live_chat_alert import (
    LiveChatAlertBot,
    NTFY_TOPIC,
)

log = logging.getLogger("ntfy_controller")


class NtfyController:
    """
    Connects to ntfy.sh streaming endpoint for a given topic,
    listens for user commands (like 'init'), and publishes responses.
    """

    def __init__(
        self,
        bot_instance: LiveChatAlertBot,
        topic: Optional[str] = None,
        server_url: Optional[str] = None,
        scheduler: Optional[Any] = None,
    ):
        self.bot = bot_instance
        self.topic = (topic or NTFY_TOPIC).strip()
        env_server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")
        self.server_url = (server_url or env_server).rstrip("/")
        self.scheduler = scheduler
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.seen_ids = set()
        self.last_id: Optional[str] = None
        self.poll_interval = float(os.environ.get("NTFY_POLL_INTERVAL", "2.0"))

    @property
    def is_configured(self) -> bool:
        return bool(self.topic)

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def publish_response(
        self,
        message: str,
        title: str = "YouTube Alert Bot",
        priority: int = 3,
        tags: Optional[List[str]] = None,
        click: Optional[str] = None,
    ) -> bool:
        """Publish a response notification back to the ntfy topic (with Telegram backup)."""
        if not self.topic:
            return False

        tags_list = tags or ["robot", "gear"]
        auth_token = os.environ.get("NTFY_AUTH_TOKEN", "").strip()
        published = False

        # 1. Primary: Root JSON publish (safe for all Unicode/emojis, supported by ntfy)
        try:
            json_headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
            payload = {
                "topic": self.topic,
                "title": title,
                "message": message,
                "priority": priority,
                "tags": tags_list,
            }
            if click:
                payload["click"] = click
            resp = requests.post(self.server_url, json=payload, headers=json_headers, timeout=10)
            log.info(f"Published response via root JSON to ntfy '{self.topic}': status={resp.status_code}")
            if resp.ok:
                published = True
        except Exception as e:
            log.warning(f"Root JSON publish failed: {e}. Trying direct topic publish fallback...")

        # 2. Fallback: Direct topic publish with clean ASCII headers
        if not published:
            try:
                topic_url = f"{self.server_url}/{self.topic}"
                clean_title = title.encode("ascii", "ignore").decode("ascii").strip() or "YouTube Alert Bot"
                headers = {
                    "Title": clean_title,
                    "Priority": str(priority),
                    "Tags": ",".join(tags_list),
                }
                if click:
                    headers["Click"] = click
                if auth_token:
                    headers["Authorization"] = f"Bearer {auth_token}"

                resp = requests.post(topic_url, data=message.encode("utf-8"), headers=headers, timeout=10)
                log.info(f"Published direct response to ntfy '{self.topic}': status={resp.status_code}")
                if resp.ok:
                    published = True
                elif resp.status_code == 429:
                    log.warning(f"ntfy rate-limited (HTTP 429): {resp.text}")
            except Exception as e:
                log.error(f"Failed direct fallback publish to ntfy topic {self.topic}: {e}")

        # 3. Mirror confirmation to official ntfy.sh if configured server is custom
        if self.server_url != "https://ntfy.sh":
            try:
                requests.post(
                    "https://ntfy.sh",
                    json={
                        "topic": self.topic,
                        "title": title,
                        "message": message,
                        "priority": priority,
                        "tags": tags_list,
                    },
                    timeout=4,
                )
            except Exception:
                pass

        # 4. Mirror confirmation to Telegram if configured
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        if tg_token and tg_chat_id:
            try:
                tg_url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                # Strip markdown asterisks for title line to avoid entity parse issues
                requests.post(
                    tg_url,
                    json={
                        "chat_id": tg_chat_id,
                        "text": f"*{title}*\n\n{message}",
                        "parse_mode": "Markdown",
                    },
                    timeout=5,
                )
            except Exception as e:
                log.debug(f"Telegram mirror failed: {e}")

        return published

    def is_bot_alert_message(self, text: str, title: str = "", tags: Optional[List[str]] = None) -> bool:
        """
        Detects if a message was generated by the bot itself (to avoid infinite loops).
        """
        # Check title markers
        bot_titles = (
            "Live Chat:",
            "Streamer is LIVE!",
            "YouTube Alert Bot",
            "Bot Initialized",
            "Bot Status",
            "Bot Stopped",
            "Channel Updated",
            "Target Updated",
            "Keywords Updated",
            "Command Guide",
            "Unknown Command",
            "Target Stream / Channel",
            "Watched Keywords",
            "Keywords Error",
            "⏰ Daily Schedule",
            "⏰ Auto-Start",
            "⏰ Auto-Stop",
            "⏰ Schedule",
        )
        if any(marker in title for marker in bot_titles):
            return True

        # Check tag markers used by outgoing alerts
        if tags and any(t in tags for t in ("rotating_light", "red_circle", "robot", "gear")):
            return True

        # Check message prefixes
        if text.startswith(("🚨", "🔴", "🟢", "🛑", "📊", "✅", "📺", "🔑", "❓", "🤖", "ℹ️", "⚠️", "⏰")):
            return True

        return False

    def handle_command(self, raw_text: str) -> Optional[Tuple[str, str, int]]:
        """
        Parses and executes a command string received from the ntfy topic.
        Returns (response_message, title, priority) or None if ignored.
        """
        text = raw_text.strip()
        if not text:
            return None

        # Allow commands prefixed with slash (e.g. /start, /status from Telegram habits)
        if text.startswith("/"):
            text = text[1:].strip()

        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1].strip() if len(parts) > 1 else ""

        log.info(f"Received ntfy command: '{command}' (args='{args}')")

        if command in ("init", "start", "run", "monitor", "trigger", "triggered", "begin", "launch"):
            success, msg = self.bot.start()
            title = "🟢 Bot Initialized" if success else "ℹ️ Bot Status"
            return msg, title, 4

        elif command in ("stop", "pause", "halt", "kill", "off"):
            success, msg = self.bot.stop()
            return msg, "🛑 Bot Stopped", 3

        elif command in ("status", "state", "info", "report", "check", "ping"):
            status_text = self.bot.get_status_text()
            if self.scheduler:
                sc = self.scheduler.get_status()
                sched_state = "Active" if sc["enabled"] else "Disabled"
                status_text += f"\n• *Daily Schedule:* {sched_state} ({sc['start_time']} - {sc['stop_time']} {sc['timezone']})"
            return status_text, "📊 Bot Status Report", 3

        elif command in ("schedule", "timer", "cron", "timing", "hours"):
            if not self.scheduler:
                return "Daily scheduler is not enabled in this deployment.", "⏰ Schedule", 3
            if args.lower() in ("on", "enable", "start"):
                self.scheduler.set_enabled(True)
                sc = self.scheduler.get_status()
                return f"⏰ Daily schedule enabled!\n• Window: {sc['start_time']} - {sc['stop_time']} ({sc['timezone']})\n• Next: {sc['next_action']}", "⏰ Schedule Enabled", 3
            elif args.lower() in ("off", "disable", "pause", "stop"):
                self.scheduler.set_enabled(False)
                return "⏸️ Daily schedule paused. Bot will only start when you manually type 'start' or 'init'.", "⏰ Schedule Paused", 3
            else:
                sc = self.scheduler.get_status()
                state_str = "🟢 Active (Daily Auto 9am-5pm)" if sc["enabled"] else "⏸️ Paused (Manual Only)"
                msg = (
                    f"⏰ *Daily Schedule Status:* {state_str}\n"
                    f"• *Window:* {sc['start_time']} - {sc['stop_time']} ({sc['timezone']})\n"
                    f"• *Current Time:* {sc['current_time']}\n"
                    f"• *Next Action:* {sc['next_action']}\n\n"
                    f"Controls:\n"
                    f"• `schedule off` - Pause automatic daily triggers\n"
                    f"• `schedule on` - Re-enable daily auto triggers"
                )
                return msg, "⏰ Daily Schedule", 3

        elif command in ("channel", "setchannel", "target", "streamer"):
            if not args:
                curr = self.bot.get_target_display() if hasattr(self.bot, "get_target_display") else (self.bot.channel or "None configured")
                return (
                    f"Current target: {curr}\n\n"
                    "To change, send:\n"
                    "• channel @Streamer\n"
                    "• channel UC...\n"
                    "• channel https://youtu.be/<stream_id>",
                    "📺 Target Stream / Channel",
                    3,
                )
            success, msg = self.bot.update_channel(args)
            return msg, "✅ Target Updated", 3

        elif command in ("keywords", "setkeywords", "kw"):
            if not args:
                kws = ", ".join(self.bot.keywords)
                return f"Keywords: {kws}\nTo update: keywords solo, 1v1, ff", "🔑 Watched Keywords", 3
            new_kws = [k.strip() for k in args.split(",") if k.strip()]
            if not new_kws:
                return "Please provide comma-separated keywords: keywords solo, 1v1, ff", "⚠️ Keywords Error", 3
            success, msg = self.bot.update_keywords(new_kws)
            return msg, "✅ Keywords Updated", 3

        elif command in ("help", "menu", "commands", "guide"):
            help_msg = (
                "YouTube Alert Bot Commands:\n\n"
                "• init / start - Trigger & start chat monitoring\n"
                "• stop - Pause chat monitoring\n"
                "• status - View bot state, live status, chat stats & uptime\n"
                "• schedule - View or toggle daily 9am-5pm schedule\n"
                "• channel <@handle or link> - Set streamer channel or live video link\n"
                "• keywords <k1, k2> - Update keywords (e.g. keywords solo, 1v1)\n"
                "• help - Show this command guide"
            )
            return help_msg, "🤖 Command Guide", 3

        else:
            return (
                f"Unrecognized command: '{command}'\n\n"
                "Available commands:\n"
                "• init / start - Start monitoring\n"
                "• status - Check current status\n"
                "• stop - Pause monitoring\n"
                "• channel <@handle or link> - Change target\n"
                "• keywords <k1, k2> - Update keywords\n"
                "• help - View command guide",
                "🤖 Unknown Command",
                2,
            )

    def _execute_and_reply(self, msg_text: str) -> None:
        """Parses and executes a command, then dispatches the reply."""
        try:
            res = self.handle_command(msg_text)
            if res:
                reply_body, reply_title, reply_priority = res
                click_url = None
                if hasattr(self.bot, "get_status"):
                    b_status = self.bot.get_status()
                    if b_status.get("is_stream_live"):
                        click_url = b_status.get("stream_url")

                self.publish_response(
                    message=reply_body,
                    title=reply_title,
                    priority=reply_priority,
                    click=click_url,
                )
        except Exception as cmd_err:
            log.error(f"Error handling ntfy command '{msg_text}': {cmd_err}", exc_info=True)

    def _listen_loop(self) -> None:
        """
        Continuous, highly resilient command poller for ntfy topic.
        Never hangs on dead TCP connections or silent proxy timeouts.
        """
        log.info(f"Connecting to ntfy listener for topic: '{self.topic}' on {self.server_url}")
        poll_url = f"{self.server_url}/{self.topic}/json"

        # Check server reachability; auto-switch to official https://ntfy.sh if configured server times out or is unreachable
        try:
            test_resp = requests.get(poll_url, params={"poll": "1", "since": "10s"}, timeout=4)
            if not test_resp.ok and test_resp.status_code != 404:
                raise ConnectionError(f"HTTP {test_resp.status_code}")
        except Exception as reach_err:
            if self.server_url != "https://ntfy.sh":
                log.warning(f"ntfy server '{self.server_url}' is unreachable ({reach_err}). Auto-switching to official 'https://ntfy.sh'...")
                self.server_url = "https://ntfy.sh"
                poll_url = f"{self.server_url}/{self.topic}/json"

        # Initial catch-up on boot: query all recent messages to establish cursor and check for recent commands
        try:
            resp = requests.get(poll_url, params={"poll": "1", "since": "all"}, timeout=8)
            if resp.ok and resp.text.strip():
                now = time.time()
                for line in resp.text.strip().split("\n"):
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        msg_id = data.get("id")
                        event = data.get("event")
                        msg_time = data.get("time", 0)
                        if msg_id:
                            self.seen_ids.add(msg_id)
                            if event == "message":
                                self.last_id = msg_id
                        # Process if sent in the last 120 seconds before boot
                        if event == "message" and (now - msg_time) <= 120:
                            msg_text = data.get("message", "").strip()
                            msg_title = data.get("title", "")
                            msg_tags = data.get("tags", [])
                            if msg_text and not self.is_bot_alert_message(msg_text, title=msg_title, tags=msg_tags):
                                log.info(f"Processing recent startup ntfy command: '{msg_text}' (id={msg_id})")
                                self._execute_and_reply(msg_text)
                    except Exception:
                        pass
        except Exception as e:
            log.debug(f"Startup ntfy catch-up check: {e}")

        log.info(f"ntfy poller active on '{self.topic}'. Cursor last_id='{self.last_id}'. Waiting for commands...")

        while not self._stop_event.is_set():
            params = {"poll": "1"}
            if self.last_id:
                params["since"] = self.last_id
            else:
                params["since"] = "60s"

            try:
                resp = requests.get(poll_url, params=params, timeout=10)
                if resp.ok and resp.text.strip():
                    for line in resp.text.strip().split("\n"):
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            msg_id = data.get("id")
                            event = data.get("event")

                            if msg_id:
                                self.last_id = msg_id
                                if msg_id in self.seen_ids:
                                    continue
                                self.seen_ids.add(msg_id)
                                if len(self.seen_ids) > 2000:
                                    self.seen_ids = set(list(self.seen_ids)[-1000:])

                            if event != "message":
                                continue

                            msg_text = data.get("message", "").strip()
                            msg_title = data.get("title", "")
                            msg_tags = data.get("tags", [])

                            if not msg_text or self.is_bot_alert_message(msg_text, title=msg_title, tags=msg_tags):
                                continue

                            log.info(f"Processing ntfy command: '{msg_text}' (id={msg_id})")
                            self._execute_and_reply(msg_text)

                        except Exception as err:
                            log.debug(f"Error parsing ntfy message line: {err}")
                elif resp.status_code == 404:
                    self.last_id = None
            except requests.RequestException as e:
                log.debug(f"ntfy poll error ({e}). Retrying...")
            except Exception as e:
                log.warning(f"Unexpected error in ntfy poll: {e}")

            if self._stop_event.wait(self.poll_interval):
                break

        log.info("ntfy listener loop exited.")

    def start(self) -> bool:
        """Starts the ntfy listener in a background daemon thread."""
        if not self.is_configured:
            log.warning("NTFY_TOPIC is not set. ntfy remote control is disabled.")
            return False

        if self.is_running:
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen_loop, name="ntfy_poller", daemon=True)
        self._thread.start()
        log.info(f"ntfy Two-Way Controller is ACTIVE on topic: '{self.topic}' ({self.server_url})")
        return True

    def stop(self) -> None:
        """Stops the ntfy listener thread cleanly."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        log.info("ntfy Two-Way Controller stopped.")
