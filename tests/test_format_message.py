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

    def test_discord_tts_acronym_expansion(self):
        from src.tts.format_message import parse_acronyms

        self.assertEqual(parse_acronyms("iirc this is fine imo"), "if I recall correctly this is fine in my opinion")
        self.assertEqual(parse_acronyms("brb, gtg rn!"), "be right back, got to go right now!")
        self.assertEqual(parse_acronyms(":) :("), "smiley face sad face")

    def test_discord_tts_repeated_chars_truncation(self):
        from src.tts.format_message import remove_repeated_chars

        self.assertEqual(remove_repeated_chars("helloooooooo", 3), "hellooo")
        self.assertEqual(remove_repeated_chars("noooooo waaaay", 3), "nooo waaay")

    def test_discord_tts_question_mark_converts_to_what(self):
        res = format_message_for_tts({"content": "?"}, speak_usernames=False)
        self.assertIsNotNone(res)
        self.assertEqual(res.cleaned_text, "what")
        self.assertEqual(res.spoken_text, "what")

    def test_discord_tts_attachment_and_link_formatting(self):
        from src.tts.format_message import attachments_to_format

        self.assertEqual(attachments_to_format([{"filename": "screenshot.png"}]), "an image file")
        self.assertEqual(attachments_to_format([{"filename": "song.mp3"}]), "an audio file")
        self.assertEqual(attachments_to_format([{"filename": "clip.mp4"}]), "a video file")
        self.assertEqual(attachments_to_format([{"filename": "doc.pdf"}]), "a text file")
        self.assertEqual(attachments_to_format([{"filename": "a.png"}, {"filename": "b.png"}]), "multiple files")

        msg = {
            "content": "look at this https://example.com",
            "attachments": [{"filename": "photo.jpg"}],
            "member": {"displayName": "Alice"},
        }
        res = format_message_for_tts(msg, speak_usernames=True, announce_links=True)
        self.assertIsNotNone(res)
        self.assertEqual(res.spoken_text, "Alice sent a link, attached an image file, and said look at this")

    def test_speaker_cooldown_tracker(self):
        from src.tts.format_message import SpeakerCooldownTracker

        tracker = SpeakerCooldownTracker(cooldown_sec=60)
        # First message by Alice: should announce
        self.assertTrue(tracker.should_announce_name("guild1", "user_alice"))
        # Second message right after by Alice: should NOT announce
        self.assertFalse(tracker.should_announce_name("guild1", "user_alice"))
        # Message by Bob: should announce
        self.assertTrue(tracker.should_announce_name("guild1", "user_bob"))
        # Message by Alice again: should announce because Bob spoke
        self.assertTrue(tracker.should_announce_name("guild1", "user_alice"))


if __name__ == "__main__":
    unittest.main()

