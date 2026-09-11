"""
Daily Automation Scheduler for YouTube Live Chat Alert Bot
------------------------------------------------------------
Automatically starts the bot at a scheduled time (e.g., 09:00 AM)
and stops the bot at a scheduled time (e.g., 17:00 / 05:00 PM)
in a specified timezone (defaults to Asia/Kolkata / IST).

Allows manual override at any time via ntfy or Telegram without conflict:
triggers only fire on boundary transitions (when entering the start or stop minute).
"""

import os
import time
import logging
import threading
from datetime import datetime, time as dtime, timezone, timedelta
from typing import Optional, Callable, Dict, Any, Tuple

log = logging.getLogger("scheduler")


def parse_time_str(time_str: str) -> dtime:
    """Parse 'HH:MM' string into datetime.time object."""
    clean = time_str.strip()
    parts = clean.split(":")
    if len(parts) >= 2:
        hour = int(parts[0])
        minute = int(parts[1])
        return dtime(hour=hour, minute=minute)
    raise ValueError(f"Invalid time format: '{time_str}', expected 'HH:MM' (24-hour)")


def get_timezone(tz_name: str):
    """
    Returns a tzinfo object for the given timezone name.
    Falls back to IST (UTC+5:30) or UTC if timezone name cannot be loaded.
    """
    tz_name = (tz_name or "Asia/Kolkata").strip()
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name)
    except Exception as e:
        log.warning(f"Could not load timezone '{tz_name}' via zoneinfo ({e}). Using UTC+5:30 fallback.")
        # Default fallback to Indian Standard Time (UTC +5:30)
        return timezone(timedelta(hours=5, minutes=30), name="IST")


class DailyScheduler:
    """
    Automates daily bot start and stop cycles based on wall-clock time.
    """

    def __init__(
        self,
        bot_instance: Any,
        notify_callback: Optional[Callable[[str, str], None]] = None,
        start_time_str: Optional[str] = None,
        stop_time_str: Optional[str] = None,
        timezone_str: Optional[str] = None,
        enabled: Optional[bool] = None,
        poll_interval_seconds: int = 15,
    ):
        self.bot = bot_instance
        self.notify_callback = notify_callback
        self.poll_interval = max(5, poll_interval_seconds)

        # Config from parameters or environment
        raw_enabled = os.environ.get("DAILY_SCHEDULE_ENABLED", "true").strip().lower()
        self.enabled = (
            enabled
            if enabled is not None
            else raw_enabled in ("true", "1", "yes", "on")
        )

        self.start_time_str = (
            start_time_str or os.environ.get("DAILY_START_TIME", "09:00")
        ).strip()
        self.stop_time_str = (
            stop_time_str or os.environ.get("DAILY_STOP_TIME", "17:00")
        ).strip()
        self.timezone_str = (
            timezone_str or os.environ.get("SCHEDULE_TIMEZONE", "Asia/Kolkata")
        ).strip()

        try:
            self.start_time = parse_time_str(self.start_time_str)
        except Exception:
            self.start_time = dtime(hour=9, minute=0)
            self.start_time_str = "09:00"

        try:
            self.stop_time = parse_time_str(self.stop_time_str)
        except Exception:
            self.stop_time = dtime(hour=17, minute=0)
            self.stop_time_str = "17:00"

        self.tz = get_timezone(self.timezone_str)

        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

        # Track which date triggers have fired for: "YYYY-MM-DD"
        self._last_started_date: Optional[str] = None
        self._last_stopped_date: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> None:
        """Start the background scheduler thread."""
        with self._lock:
            if self.is_running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="daily_scheduler",
                daemon=True,
            )
            self._thread.start()
            log.info(
                f"DailyScheduler started: Daily Window {self.start_time_str} - {self.stop_time_str} "
                f"({self.timezone_str}) | Enabled={self.enabled}"
            )

    def stop(self) -> None:
        """Stop the background scheduler thread."""
        with self._lock:
            self._stop_event.set()
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        log.info("DailyScheduler stopped.")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable scheduling at runtime."""
        self.enabled = enabled
        log.info(f"DailyScheduler enabled set to: {self.enabled}")

    def get_current_time(self) -> datetime:
        """Return current datetime in the configured timezone."""
        return datetime.now(self.tz)

    def check_and_trigger(self, now: Optional[datetime] = None) -> Optional[Tuple[str, str]]:
        """
        Check current time against start and stop boundaries and execute action if due.
        Returns tuple of (action_type, message) or None if no action triggered.
        action_type can be 'START' or 'STOP'.
        """
        if not self.enabled:
            return None

        if now is None:
            now = self.get_current_time()

        today_str = now.strftime("%Y-%m-%d")
        current_time = now.time()

        # 1. Check START trigger (e.g. 09:00 AM)
        if (
            current_time.hour == self.start_time.hour
            and current_time.minute == self.start_time.minute
        ):
            if self._last_started_date != today_str:
                self._last_started_date = today_str
                return self._execute_start(now)

        # 2. Check STOP trigger (e.g. 17:00 / 05:00 PM)
        if (
            current_time.hour == self.stop_time.hour
            and current_time.minute == self.stop_time.minute
        ):
            if self._last_stopped_date != today_str:
                self._last_stopped_date = today_str
                return self._execute_stop(now)

        return None

    def _execute_start(self, now: datetime) -> Tuple[str, str]:
        """Executes scheduled start action and notifies."""
        target = getattr(self.bot, "get_target_display", lambda: "stream")()
        time_display = now.strftime("%I:%M %p")
        log.info(f"⏰ DailyScheduler: Triggering scheduled START at {time_display}...")

        is_running = getattr(self.bot, "is_running", False)
        if not is_running:
            success, msg = self.bot.start()
            notify_msg = (
                f"⏰ *Auto-Start Triggered ({time_display})*\n"
                f"Good morning! Daily monitoring automatically started for *{target}*.\n"
                f"Will monitor until {self.stop_time.strftime('%I:%M %p')}."
            )
        else:
            notify_msg = (
                f"⏰ *Auto-Start Notice ({time_display})*\n"
                f"Daily schedule reached ({self.start_time_str}), bot is already active monitoring *{target}*."
            )

        title = "⏰ Daily Schedule: Bot Started"
        self._dispatch_notification(notify_msg, title)
        return ("START", notify_msg)

    def _execute_stop(self, now: datetime) -> Tuple[str, str]:
        """Executes scheduled stop action and notifies."""
        time_display = now.strftime("%I:%M %p")
        log.info(f"⏰ DailyScheduler: Triggering scheduled STOP at {time_display}...")

        status = getattr(self.bot, "get_status", lambda: {})()
        total_matches = status.get("total_matches", 0)

        is_running = getattr(self.bot, "is_running", False)
        if is_running:
            self.bot.stop()
            notify_msg = (
                f"⏰ *Auto-Stop Triggered ({time_display})*\n"
                f"Daily monitoring period ended. Bot stopped for today.\n"
                f"📊 *Matches captured today:* {total_matches}\n"
                f"Will resume tomorrow at {self.start_time.strftime('%I:%M %p')}."
            )
        else:
            notify_msg = (
                f"⏰ *Auto-Stop Notice ({time_display})*\n"
                f"Daily schedule ended ({self.stop_time_str}). Bot was already stopped."
            )

        title = "⏰ Daily Schedule: Bot Stopped"
        self._dispatch_notification(notify_msg, title)
        return ("STOP", notify_msg)

    def _dispatch_notification(self, message: str, title: str) -> None:
        """Invokes notify callback if provided."""
        if self.notify_callback:
            try:
                self.notify_callback(message, title)
            except Exception as e:
                log.error(f"Error in scheduler notify_callback: {e}")

    def _worker_loop(self) -> None:
        """Background thread polling loop."""
        log.debug("DailyScheduler worker loop started.")
        while not self._stop_event.is_set():
            try:
                self.check_and_trigger()
            except Exception as e:
                log.error(f"Unexpected error in DailyScheduler worker: {e}", exc_info=True)

            self._stop_event.wait(timeout=self.poll_interval)
        log.debug("DailyScheduler worker loop exited.")

    def get_status(self) -> Dict[str, Any]:
        """Returns scheduler state dictionary for dashboard/APIs."""
        now = self.get_current_time()
        curr_time = now.time()

        if self.enabled:
            if curr_time < self.start_time:
                next_action = f"Auto-Start at {self.start_time.strftime('%I:%M %p')}"
            elif curr_time < self.stop_time:
                next_action = f"Auto-Stop at {self.stop_time.strftime('%I:%M %p')}"
            else:
                next_action = f"Auto-Start tomorrow at {self.start_time.strftime('%I:%M %p')}"
        else:
            next_action = "Disabled"

        return {
            "enabled": self.enabled,
            "is_running": self.is_running,
            "start_time": self.start_time_str,
            "stop_time": self.stop_time_str,
            "timezone": self.timezone_str,
            "current_time": now.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
            "next_action": next_action,
            "last_started_date": self._last_started_date,
            "last_stopped_date": self._last_stopped_date,
        }
