import unittest
from datetime import datetime, time as dtime, timezone, timedelta
from unittest.mock import MagicMock

from scheduler import DailyScheduler, parse_time_str, get_timezone
from ntfy_controller import NtfyController
from telegram_controller import TelegramController


class TestDailyScheduler(unittest.TestCase):
    def setUp(self):
        self.mock_bot = MagicMock()
        self.mock_bot.is_running = False
        self.mock_bot.start.return_value = (True, "Bot started!")
        self.mock_bot.stop.return_value = (True, "Bot stopped!")
        self.mock_bot.get_target_display.return_value = "@StreamerTest"
        self.mock_bot.get_status.return_value = {
            "is_running": False,
            "total_matches": 12,
            "state": "STOPPED",
        }
        self.mock_notify = MagicMock()

        self.scheduler = DailyScheduler(
            bot_instance=self.mock_bot,
            notify_callback=self.mock_notify,
            start_time_str="09:00",
            stop_time_str="17:00",
            timezone_str="Asia/Kolkata",
            enabled=True,
        )

    def test_parse_time_str(self):
        t1 = parse_time_str("09:00")
        self.assertEqual(t1.hour, 9)
        self.assertEqual(t1.minute, 0)

        t2 = parse_time_str("17:30")
        self.assertEqual(t2.hour, 17)
        self.assertEqual(t2.minute, 30)

        with self.assertRaises(ValueError):
            parse_time_str("invalid")

    def test_get_timezone(self):
        tz = get_timezone("Asia/Kolkata")
        self.assertIsNotNone(tz)

        # Invalid timezone should safely fallback without crashing
        fallback_tz = get_timezone("NonExistent/Zone_123")
        self.assertIsNotNone(fallback_tz)

    def test_start_trigger_at_9am(self):
        # Time: 2026-09-12 09:00:05
        now_9am = datetime(2026, 9, 12, 9, 0, 5, tzinfo=self.scheduler.tz)
        res = self.scheduler.check_and_trigger(now=now_9am)

        self.assertIsNotNone(res)
        action, msg = res
        self.assertEqual(action, "START")
        self.mock_bot.start.assert_called_once()
        self.mock_notify.assert_called_once()

        # Check that calling again in the same day does not re-trigger
        res_duplicate = self.scheduler.check_and_trigger(now=now_9am)
        self.assertIsNone(res_duplicate)
        self.assertEqual(self.mock_bot.start.call_count, 1)

    def test_stop_trigger_at_5pm(self):
        # Mock bot as running
        self.mock_bot.is_running = True
        self.mock_bot.get_status.return_value = {
            "is_running": True,
            "total_matches": 42,
            "state": "MONITORING",
        }

        # Time: 2026-09-12 17:00:15
        now_5pm = datetime(2026, 9, 12, 17, 0, 15, tzinfo=self.scheduler.tz)
        res = self.scheduler.check_and_trigger(now=now_5pm)

        self.assertIsNotNone(res)
        action, msg = res
        self.assertEqual(action, "STOP")
        self.mock_bot.stop.assert_called_once()
        self.mock_notify.assert_called_once()

        # Check duplicate trigger prevention
        res_duplicate = self.scheduler.check_and_trigger(now=now_5pm)
        self.assertIsNone(res_duplicate)
        self.assertEqual(self.mock_bot.stop.call_count, 1)

    def test_no_trigger_at_other_times(self):
        # Time: 2026-09-12 12:30:00 (midday)
        now_noon = datetime(2026, 9, 12, 12, 30, 0, tzinfo=self.scheduler.tz)
        res = self.scheduler.check_and_trigger(now=now_noon)
        self.assertIsNone(res)
        self.mock_bot.start.assert_not_called()
        self.mock_bot.stop.assert_not_called()

    def test_disabled_scheduler_does_not_trigger(self):
        self.scheduler.set_enabled(False)
        now_9am = datetime(2026, 9, 12, 9, 0, 0, tzinfo=self.scheduler.tz)
        res = self.scheduler.check_and_trigger(now=now_9am)
        self.assertIsNone(res)
        self.mock_bot.start.assert_not_called()

    def test_status_reporting(self):
        status = self.scheduler.get_status()
        self.assertTrue(status["enabled"])
        self.assertEqual(status["start_time"], "09:00")
        self.assertEqual(status["stop_time"], "17:00")
        self.assertIn("Asia/Kolkata", status["timezone"])
        self.assertIn("next_action", status)


class TestSchedulerControllerIntegration(unittest.TestCase):
    def setUp(self):
        self.mock_bot = MagicMock()
        self.mock_bot.start.return_value = (True, "Bot started!")
        self.mock_bot.stop.return_value = (True, "Bot stopped!")
        self.mock_bot.get_status_text.return_value = "🟢 Bot Running"
        self.mock_bot.get_target_display.return_value = "@StreamerTest"
        self.mock_bot.keywords = ["solo", "ff"]

        self.scheduler = DailyScheduler(
            bot_instance=self.mock_bot,
            start_time_str="09:00",
            stop_time_str="17:00",
            enabled=True,
        )
        self.ntfy = NtfyController(self.mock_bot, topic="test-sched", scheduler=self.scheduler)
        self.tg = TelegramController(self.mock_bot, token="test_token", scheduler=self.scheduler)

    def test_ntfy_schedule_command(self):
        # Query status
        resp = self.ntfy.handle_command("schedule")
        self.assertIsNotNone(resp)
        msg, title, prio = resp
        self.assertIn("Daily Schedule Status", msg)

        # Toggle off
        resp_off = self.ntfy.handle_command("schedule off")
        self.assertFalse(self.scheduler.enabled)
        self.assertIn("paused", resp_off[0].lower())

        # Toggle on
        resp_on = self.ntfy.handle_command("schedule on")
        self.assertTrue(self.scheduler.enabled)
        self.assertIn("enabled", resp_on[0].lower())

    def test_telegram_schedule_command(self):
        # Query status
        resp = self.tg.handle_command("/schedule", chat_id="123")
        self.assertIn("Daily Schedule Status", resp)

        # Toggle off
        resp_off = self.tg.handle_command("/schedule off", chat_id="123")
        self.assertFalse(self.scheduler.enabled)

        # Toggle on
        resp_on = self.tg.handle_command("/schedule on", chat_id="123")
        self.assertTrue(self.scheduler.enabled)

    def test_manual_start_stop_still_works_with_scheduler(self):
        # Manual start in ntfy
        resp_start = self.ntfy.handle_command("start")
        self.mock_bot.start.assert_called_once()

        # Manual stop in ntfy
        resp_stop = self.ntfy.handle_command("stop")
        self.mock_bot.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
