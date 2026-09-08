"""
Automated Test Suite for YouTube Alert Bot & Controllers
--------------------------------------------------------
Validates:
1. Keyword matching regex with word boundaries (zero false positives).
2. LiveChatAlertBot thread management, state transitions, dynamic updates.
3. @handle resolution, friendly display string formatting, and channel ID conversion.
4. NtfyController command parsing ('init', 'status', 'stop', 'channel', 'keywords') & loop prevention.
5. Keep-alive web server endpoints (GET / and GET /status).
"""

import sys
import unittest
from unittest import mock
import json

# Ensure UTF-8 output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from yt_live_chat_alert import (
    LiveChatAlertBot,
    compile_keyword_patterns,
    matches_keyword,
    extract_video_id,
    resolve_channel_details,
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
        self.assertEqual(status["target_display"], "@TestChannel")
        self.assertIn("solo", status["keywords"])

    def test_target_display_formatting(self):
        self.bot.channel_handle = "@iamkokkikumar"
        self.bot.channel_title = "KOKKI KUMAR YT"
        self.assertEqual(self.bot.get_target_display(), "@iamkokkikumar (KOKKI KUMAR YT)")

        # Same title as handle
        self.bot.channel_title = "iamkokkikumar"
        self.assertEqual(self.bot.get_target_display(), "@iamkokkikumar")

        # No title
        self.bot.channel_title = None
        self.assertEqual(self.bot.get_target_display(), "@iamkokkikumar")

    def test_update_channel(self):
        success, msg = self.bot.update_channel("@NewStreamer")
        self.assertTrue(success)
        self.assertEqual(self.bot.channel, "@NewStreamer")
        self.assertIn("@NewStreamer", msg)

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

    def test_dynamic_keyword_updates_and_spam_reset(self):
        self.assertEqual(self.bot.keywords, ["solo", "custom"])
        kw_func = lambda: self.bot.keyword_patterns
        self.assertEqual(matches_keyword("playing solo today", patterns=kw_func()), "solo")
        self.assertIsNone(matches_keyword("playing squad today", patterns=kw_func()))

        self.bot.update_keywords(["squad", "clutch"])
        self.assertEqual(matches_keyword("playing squad today", patterns=kw_func()), "squad")
        self.assertIsNone(matches_keyword("playing solo today", patterns=kw_func()))

    def test_channel_ownership_verification_logic(self):
        from yt_live_chat_alert import is_video_from_channel
        self.assertFalse(is_video_from_channel("", "UC123"))
        self.assertFalse(is_video_from_channel("vid123", ""))

    def test_state_persistence(self):
        self.bot.update_channel("@PersistChannel")
        self.bot.update_keywords(["persist_kw"])
        self.assertEqual(self.bot.channel, "@PersistChannel")
        self.assertEqual(self.bot.keywords, ["persist_kw"])

    def test_format_author(self):
        from yt_live_chat_alert import format_author
        self.assertEqual(format_author("NAVEEN-PNTk"), "@NAVEEN-PNTk")
        self.assertEqual(format_author("@NAVEEN-PNTk"), "@NAVEEN-PNTk")
        self.assertEqual(format_author("@@NAVEEN-PNTk"), "@NAVEEN-PNTk")
        self.assertEqual(format_author(""), "@Anonymous")
        self.assertEqual(format_author(None), "@Anonymous")

    def test_spam_detection_allows_different_messages_and_blocks_repeated(self):
        from yt_live_chat_alert import should_send_alert, reset_anti_spam_cache
        reset_anti_spam_cache()

        # 1. User sends 'solo' -> allowed
        self.assertTrue(should_send_alert("User1", "solo", "solo"))

        # 2. Same user sends 'solo' again immediately -> blocked as duplicate spam
        self.assertFalse(should_send_alert("User1", "solo", "solo"))
        self.assertFalse(should_send_alert("User1", "solo", "solo!"))

        # 3. Same user sends 'solo pola' -> allowed (different message)
        self.assertTrue(should_send_alert("User1", "solo", "solo pola"))

        # 4. Same user sends 'solo va' -> allowed (different message)
        self.assertTrue(should_send_alert("User1", "solo", "solo va"))

        # 5. Same user repeats 'solo va' -> blocked as duplicate spam
        self.assertFalse(should_send_alert("User1", "solo", "solo va"))

        # Reset cache test
        reset_anti_spam_cache()
        self.assertTrue(should_send_alert("User1", "solo", "solo"))


class TestNtfyController(unittest.TestCase):
    def setUp(self):
        self.bot = LiveChatAlertBot(channel="@GamerLive", keywords=["solo", "ff"], idle_check_interval=1)
        self.bot.channel_id = "UCdummy1234567890123456"
        self.ntfy = NtfyController(self.bot, topic="test-yt-alert-topic")

    def tearDown(self):
        if self.bot.is_running:
            self.bot.stop()

    def test_init_command_triggers_bot(self):
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

    def test_channel_command_shows_handle(self):
        res = self.ntfy.handle_command("channel")
        self.assertIsNotNone(res)
        body, title, priority = res
        self.assertIn("Current target: @GamerLive", body)

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
        self.assertTrue(self.ntfy.is_bot_alert_message("🚨 YouTube Live Chat Alert: 'solo' by Player"))
        self.assertTrue(self.ntfy.is_bot_alert_message("🔴 Streamer is LIVE!"))
        self.assertTrue(self.ntfy.is_bot_alert_message("🟢 Bot started!"))
        self.assertFalse(self.ntfy.is_bot_alert_message("init"))
        self.assertFalse(self.ntfy.is_bot_alert_message("status"))
        self.assertFalse(self.ntfy.is_bot_alert_message("channel @Streamer"))

    def test_slash_command_support(self):
        res = self.ntfy.handle_command("/status")
        self.assertIsNotNone(res)
        self.assertIn("YouTube Alert Bot", res[0])

        res_start = self.ntfy.handle_command("/start")
        self.assertIsNotNone(res_start)
        self.assertIn("Bot started", res_start[0])

        res_stop = self.ntfy.handle_command("/stop")
        self.assertIsNotNone(res_stop)
        self.assertIn("stopped", res_stop[0])

    def test_publish_response_mocked(self):
        with mock.patch("requests.post") as mock_post:
            mock_resp = mock.MagicMock()
            mock_resp.ok = True
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            success = self.ntfy.publish_response(
                message="Test status",
                title="📊 Bot Status Report",
                priority=3,
            )
            self.assertTrue(success)
            self.assertTrue(mock_post.called)
            # Verify first call was to ntfy server with root JSON payload
            first_call_args = mock_post.call_args_list[0]
            first_kwargs = first_call_args[1]
            self.assertEqual(first_kwargs["json"]["title"], "📊 Bot Status Report")
            self.assertEqual(first_kwargs["json"]["topic"], "test-yt-alert-topic")


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
