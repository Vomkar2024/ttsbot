"""Tests for Discord message formatting and entity sanitization."""

import unittest
from src.tts.format_message import clean_discord_text, format_message_for_tts


class TestFormatMessage(unittest.TestCase):
    def test_strips_urls_mentions_emojis_and_collapses_spaces(self):
        input_text = "Hey <@123456> check this: https://github.com/discordjs <:smile:999888> <#101010>   looks   awesome!   "
        cleaned = clean_discord_text(input_text)
        self.assertEqual(cleaned, "Hey check this: looks awesome!")

    def test_strips_animated_emojis_and_role_mentions(self):
        input_text = "Alert <@&98765> look at <a:fire:55555> right now!"
        cleaned = clean_discord_text(input_text)
        self.assertEqual(cleaned, "Alert look at right now!")

    def test_removes_non_printable_ascii_control_characters(self):
        input_text = "Clean\x00this\x07text\x1Fplease"
        cleaned = clean_discord_text(input_text)
        self.assertEqual(cleaned, "Cleanthistextplease")

    def test_truncates_messages_exceeding_max_characters(self):
        long_string = "a" * 200
        cleaned = clean_discord_text(long_string, max_chars=50)
        self.assertEqual(len(cleaned), 53)  # 50 chars + '...'
        self.assertTrue(cleaned.endswith("..."))

    def test_returns_none_for_empty_or_stripped_messages(self):
        self.assertEqual(clean_discord_text(""), "")
        self.assertEqual(clean_discord_text("   "), "")
        self.assertEqual(clean_discord_text("https://only-a-link.com"), "")
        self.assertIsNone(format_message_for_tts({"content": "   "}))
        self.assertIsNone(format_message_for_tts({"content": "https://only-a-link.com"}))

    def test_prefixes_author_display_name_when_speak_usernames_is_true(self):
        message = {
            "content": "Hello everyone!",
            "member": {"displayName": "Alice"},
        }
        result = format_message_for_tts(message, speak_usernames=True)
        self.assertIsNotNone(result)
        self.assertEqual(result.cleaned_text, "Hello everyone!")
        self.assertEqual(result.author_name, "Alice")
        self.assertEqual(result.spoken_text, "Alice says: Hello everyone!")

    def test_omits_author_display_name_when_speak_usernames_is_false(self):
        message = {
            "content": "Just the speech content.",
            "member": {"displayName": "Bob"},
        }
        result = format_message_for_tts(message, speak_usernames=False)
        self.assertIsNotNone(result)
        self.assertEqual(result.cleaned_text, "Just the speech content.")
        self.assertEqual(result.spoken_text, "Just the speech content.")

    def test_falls_back_to_author_display_name_or_username(self):
        msg_with_author = {
            "content": "Direct test",
            "author": {"displayName": "Charlie", "username": "charlie123"},
        }
        res = format_message_for_tts(msg_with_author, speak_usernames=True)
        self.assertIsNotNone(res)
        self.assertEqual(res.spoken_text, "Charlie says: Direct test")

        msg_with_username_only = {
            "content": "Username test",
            "author": {"username": "dave_dev"},
        }
        res2 = format_message_for_tts(msg_with_username_only, speak_usernames=True)
        self.assertIsNotNone(res2)
        self.assertEqual(res2.spoken_text, "dave_dev says: Username test")


if __name__ == "__main__":
    unittest.main()

