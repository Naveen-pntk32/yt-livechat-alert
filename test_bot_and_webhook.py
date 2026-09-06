"""
Automated Test Suite for YouTube Alert Bot & ntfy Remote Controller
-------------------------------------------------------------------
Validates:
1. Keyword matching regex with word boundaries (zero false positives).
2. LiveChatAlertBot thread management, state transitions, dynamic updates.
3. NtfyController command parsing ('init', 'status', 'stop', 'channel', 'keywords') & loop prevention.
4. Keep-alive web server endpoints (GET / and GET /status).
"""

import sys
import unittest
import json

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from yt_live_chat_alert import (
    LiveChatAlertBot,
    compile_keyword_patterns,
    matches_keyword,
    extract_video_id,
)
from ntfy_controller import NtfyController
from main import app


class TestKeywordMatching(unittest.TestCase):
    def test_regex_word_boundaries(self):
        patterns = compile_keyword_patterns(["solo", "1v1", "ff", "free fire"])
        self.assertEqual(matches_keyword("hey let's play solo match", patterns), "solo")
        self.assertEqual(matches_keyword("room 1v1 fast", patterns), "1v1")
        self.assertEqual(matches_keyword("come on ff now", patterns), "ff")
        # Ensure no false positives
        self.assertIsNone(matches_keyword("offline stream", patterns))
        self.assertIsNone(matches_keyword("different game", patterns))
        self.assertIsNone(matches_keyword("wolverine", patterns))


class TestLiveChatAlertBotController(unittest.TestCase):
    def setUp(self):
        self.bot = LiveChatAlertBot(
            channel="@TestChannel",
            keywords=["solo", "custom"],
            idle_check_interval=999,
        )

    def tearDown(self):
        if self.bot.is_running:
            self.bot.stop()

    def test_initial_state(self):
        status = self.bot.get_status()
        self.assertEqual(status["state"], "STOPPED")
        self.assertFalse(status["is_running"])
        self.assertEqual(status["target_channel"], "@TestChannel")
        self.assertIn("solo", status["keywords"])

    def test_update_channel(self):
        success, msg = self.bot.update_channel("@NewStreamer")
        self.assertTrue(success)
        self.assertEqual(self.bot.channel, "@NewStreamer")

    def test_update_keywords(self):
        success, msg = self.bot.update_keywords(["ff", "ranked", "squad"])
        self.assertTrue(success)
        self.assertEqual(self.bot.keywords, ["ff", "ranked", "squad"])
        self.assertEqual(len(self.bot.keyword_patterns), 3)

    def test_status_formatting(self):
        text = self.bot.get_status_text()
        self.assertIn("YouTube Alert Bot", text)
        self.assertIn("@TestChannel", text)

    def test_direct_video_id_extraction(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/watch?v=O-ZJ1TZtXAU"), "O-ZJ1TZtXAU")
        self.assertEqual(extract_video_id("https://youtu.be/O-ZJ1TZtXAU"), "O-ZJ1TZtXAU")
        self.assertEqual(extract_video_id("https://www.youtube.com/live/O-ZJ1TZtXAU"), "O-ZJ1TZtXAU")
        self.assertEqual(extract_video_id("O-ZJ1TZtXAU"), "O-ZJ1TZtXAU")
        self.assertIsNone(extract_video_id("UCmyKnNRH0wH-r8I-ceP-dsg"))
        self.assertIsNone(extract_video_id("@Streamer"))

    def test_message_scanned_tracking(self):
        self.assertEqual(self.bot.messages_scanned, 0)
        self.bot._on_message("UserA", "Hello world")
        self.assertEqual(self.bot.messages_scanned, 1)
        self.bot._on_message("UserB", "Another message")
        self.assertEqual(self.bot.messages_scanned, 2)
        status = self.bot.get_status()
        self.assertEqual(status["messages_scanned"], 2)


class TestNtfyController(unittest.TestCase):
    def setUp(self):
        self.bot = LiveChatAlertBot(channel="@GamerLive", keywords=["solo", "ff"])
        self.ntfy = NtfyController(self.bot, topic="test-yt-alert-topic")

    def tearDown(self):
        if self.bot.is_running:
            self.bot.stop()

    def test_init_command_triggers_bot(self):
        # User types 'init' in the ntfy app
        res = self.ntfy.handle_command("init")
        self.assertIsNotNone(res)
        body, title, priority = res
        self.assertIn("Bot started", body)
        self.assertIn("Initial", title)
        self.assertTrue(self.bot.is_running)

    def test_status_command(self):
        res = self.ntfy.handle_command("status")
        self.assertIsNotNone(res)
        body, title, priority = res
        self.assertIn("YouTube Alert Bot", body)
        self.assertIn("@GamerLive", body)

    def test_stop_command(self):
        self.bot.start()
        self.assertTrue(self.bot.is_running)
        res = self.ntfy.handle_command("stop")
        self.assertIsNotNone(res)
        body, title, priority = res
        self.assertIn("stopped", body)
        self.assertFalse(self.bot.is_running)

    def test_channel_switch(self):
        res = self.ntfy.handle_command("channel @ProPlayer123")
        self.assertIsNotNone(res)
        body, title, priority = res
        self.assertIn("@ProPlayer123", body)
        self.assertEqual(self.bot.channel, "@ProPlayer123")

    def test_keywords_update(self):
        res = self.ntfy.handle_command("keywords custom, tournament, rush")
        self.assertIsNotNone(res)
        body, title, priority = res
        self.assertIn("Keywords updated", body)
        self.assertEqual(self.bot.keywords, ["custom", "tournament", "rush"])

    def test_loop_prevention(self):
        # Ensure outgoing bot alerts are identified and not processed as commands
        self.assertTrue(self.ntfy.is_bot_alert_message("🚨 YouTube Live Chat Alert: 'solo' by Player"))
        self.assertTrue(self.ntfy.is_bot_alert_message("🔴 Streamer is LIVE!"))
        self.assertTrue(self.ntfy.is_bot_alert_message("🟢 Bot started!"))
        # User command 'init' or 'status' is NOT a bot alert
        self.assertFalse(self.ntfy.is_bot_alert_message("init"))
        self.assertFalse(self.ntfy.is_bot_alert_message("status"))
        self.assertFalse(self.ntfy.is_bot_alert_message("channel @Streamer"))


class TestKeepAliveWebDashboard(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_get_dashboard(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"YouTube Alert Bot", resp.data)

    def test_get_status_json(self):
        resp = self.client.get("/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("state", data)
        self.assertIn("keywords", data)


if __name__ == "__main__":
    unittest.main()
