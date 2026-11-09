"""Unit tests for configuration loading and validation."""

import unittest
from src.config import (
    Config,
    _clean_str,
    _extract_app_id_from_token,
    _parse_bool,
    _parse_num,
    load_config,
)
from src.constants import (
    DEFAULT_MAX_MESSAGE_CHARS,
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_SPEAKER_COOLDOWN_SEC,
    DEFAULT_TTS_ENGINE,
    DEFAULT_VOICE_DISCONNECT_GRACE_MS,
    DEFAULT_VOICE_READY_TIMEOUT_MS,
)


class TestConfig(unittest.TestCase):
    def test_clean_str(self):
        self.assertEqual(_clean_str(None), "")
        self.assertEqual(_clean_str(""), "")
        self.assertEqual(_clean_str("  hello  "), "hello")
        self.assertEqual(_clean_str('"quoted"'), "quoted")
        self.assertEqual(_clean_str("'single'"), "single")
        self.assertEqual(_clean_str('""double wrapped""'), "double wrapped")

    def test_parse_bool(self):
        # Defaults
        self.assertTrue(_parse_bool(None, True))
        self.assertFalse(_parse_bool(None, False))
        self.assertTrue(_parse_bool("", True))
        self.assertFalse(_parse_bool("", False))

        # Truthy
        self.assertTrue(_parse_bool("true"))
        self.assertTrue(_parse_bool("1"))
        self.assertTrue(_parse_bool("yes"))
        self.assertTrue(_parse_bool("on"))
        self.assertTrue(_parse_bool("TRUE"))

        # Falsy
        self.assertFalse(_parse_bool("false"))
        self.assertFalse(_parse_bool("0"))
        self.assertFalse(_parse_bool("no"))
        self.assertFalse(_parse_bool("off"))
        self.assertFalse(_parse_bool("FALSE"))

    def test_parse_num(self):
        # Default fallback
        self.assertEqual(_parse_num(None, 42), 42)
        self.assertEqual(_parse_num("", 42), 42)
        self.assertEqual(_parse_num("invalid", 42), 42)

        # Basic parsing
        self.assertEqual(_parse_num("10", 42), 10)

        # Zero handling with min_val=0 vs min_val=1
        self.assertEqual(_parse_num("0", 60, min_val=0), 0)
        self.assertEqual(_parse_num("0", 60, min_val=1), 60)
        self.assertEqual(_parse_num("-5", 60, min_val=0), 60)

    def test_extract_app_id_from_token(self):
        # Token starts with base64 user ID
        # "123456789" in base64 is "MTIzNDU2Nzg5"
        token = "MTIzNDU2Nzg5.xxxx.yyyy"
        app_id = _extract_app_id_from_token(token)
        self.assertEqual(app_id, "123456789")

        # Invalid token
        self.assertEqual(_extract_app_id_from_token(""), "")
        self.assertEqual(_extract_app_id_from_token("not_base_64!"), "")

    def test_load_config_defaults(self):
        cfg = load_config({})
        self.assertEqual(cfg.DISCORD_TOKEN, "")
        self.assertEqual(cfg.TTS_ENGINE, DEFAULT_TTS_ENGINE)
        self.assertEqual(cfg.SPEAKER_COOLDOWN_SEC, DEFAULT_SPEAKER_COOLDOWN_SEC)
        self.assertEqual(cfg.MAX_QUEUE_SIZE, DEFAULT_MAX_QUEUE_SIZE)
        self.assertEqual(cfg.MAX_MESSAGE_CHARS, DEFAULT_MAX_MESSAGE_CHARS)
        self.assertEqual(cfg.VOICE_READY_TIMEOUT_MS, DEFAULT_VOICE_READY_TIMEOUT_MS)
        self.assertEqual(cfg.VOICE_DISCONNECT_GRACE_MS, DEFAULT_VOICE_DISCONNECT_GRACE_MS)
        self.assertTrue(cfg.SPEAK_USERNAMES)
        self.assertTrue(cfg.ANNOUNCE_LINKS)

    def test_load_config_custom_env(self):
        env = {
            "DISCORD_TOKEN": "Bot MTIzNDU2Nzg5.xxxx.yyyy",
            "DISCORD_GUILD_ID": "9876543210",
            "TTS_ENGINE": "ESPEAK",
            "SPEAKER_COOLDOWN_SEC": "0",  # Test zero cooldown
            "MAX_QUEUE_SIZE": "25",
            "MAX_MESSAGE_CHARS": "500",
            "VOICE_DISCONNECT_GRACE_MS": "0",  # Test zero grace
            "SPEAK_USERNAMES": "false",
            "ANNOUNCE_LINKS": "no",
            "GTTS_TIMEOUT_SEC": "8.5",
        }
        cfg = load_config(env)
        self.assertEqual(cfg.DISCORD_TOKEN, "MTIzNDU2Nzg5.xxxx.yyyy")
        self.assertEqual(cfg.DISCORD_CLIENT_ID, "123456789")
        self.assertEqual(cfg.DISCORD_GUILD_ID, "9876543210")
        self.assertEqual(cfg.TTS_ENGINE, "espeak")
        self.assertEqual(cfg.SPEAKER_COOLDOWN_SEC, 0)
        self.assertEqual(cfg.MAX_QUEUE_SIZE, 25)
        self.assertEqual(cfg.MAX_MESSAGE_CHARS, 500)
        self.assertEqual(cfg.VOICE_DISCONNECT_GRACE_MS, 0)
        self.assertFalse(cfg.SPEAK_USERNAMES)
        self.assertFalse(cfg.ANNOUNCE_LINKS)
        self.assertEqual(cfg.GTTS_TIMEOUT_SEC, 8.5)
        self.assertEqual(cfg.voice_disconnect_grace_sec, 0.0)

    def test_load_config_non_numeric_guild_id(self):
        env = {"DISCORD_GUILD_ID": "not_a_number"}
        cfg = load_config(env)
        self.assertEqual(cfg.DISCORD_GUILD_ID, "")


if __name__ == "__main__":
    unittest.main()

