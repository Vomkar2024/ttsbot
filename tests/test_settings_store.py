"""Tests for SettingsStore (SQLite)."""

import unittest
from src.storage.settings_store import SettingsStore


class TestSettingsStore(unittest.TestCase):
    def setUp(self):
        self.store = SettingsStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_falls_back_to_global_defaults(self):
        prefs = self.store.resolve_preferences("guild_1", "user_1")
        self.assertEqual(prefs["language"], "en")
        self.assertEqual(prefs["voice"], "default")

    def test_sets_and_retrieves_configured_channel(self):
        self.assertIsNone(self.store.get_configured_channel("guild_1"))
        self.assertFalse(self.store.is_configured_channel("guild_1", "chan_100"))

        self.store.set_configured_channel("guild_1", "chan_100")
        self.assertEqual(self.store.get_configured_channel("guild_1"), "chan_100")
        self.assertTrue(self.store.is_configured_channel("guild_1", "chan_100"))
        self.assertFalse(self.store.is_configured_channel("guild_1", "chan_200"))

        # Update to new channel
        self.store.set_configured_channel("guild_1", "chan_200")
        self.assertEqual(self.store.get_configured_channel("guild_1"), "chan_200")
        self.assertFalse(self.store.is_configured_channel("guild_1", "chan_100"))
        self.assertTrue(self.store.is_configured_channel("guild_1", "chan_200"))

    def test_stores_and_applies_server_defaults(self):
        self.store.set_guild_defaults("guild_1", {"language": "es", "voice": "+f1"})

        guild_settings = self.store.get_guild_settings("guild_1")
        self.assertIsNotNone(guild_settings)
        self.assertEqual(guild_settings["defaultLanguage"], "es")
        self.assertEqual(guild_settings["defaultVoice"], "+f1")

        resolved = self.store.resolve_preferences("guild_1", "user_1")
        self.assertEqual(resolved["language"], "es")
        self.assertEqual(resolved["voice"], "+f1")

    def test_enforces_field_by_field_precedence(self):
        # Guild sets Spanish and +m1
        self.store.set_guild_defaults("guild_1", {"language": "es", "voice": "+m1"})

        # User overrides only the language to French, keeping server's default voice (+m1)
        self.store.set_user_preferences("guild_1", "user_1", {"language": "fr"})

        user1_prefs = self.store.resolve_preferences("guild_1", "user_1")
        self.assertEqual(user1_prefs["language"], "fr")
        self.assertEqual(user1_prefs["voice"], "+m1")

        # Another user overrides only the voice to +f2, keeping server's default language (es)
        self.store.set_user_preferences("guild_1", "user_2", {"voice": "+f2"})

        user2_prefs = self.store.resolve_preferences("guild_1", "user_2")
        self.assertEqual(user2_prefs["language"], "es")
        self.assertEqual(user2_prefs["voice"], "+f2")

        # Third user has no overrides, gets both server defaults
        user3_prefs = self.store.resolve_preferences("guild_1", "user_3")
        self.assertEqual(user3_prefs["language"], "es")
        self.assertEqual(user3_prefs["voice"], "+m1")

    def test_reverts_back_to_server_defaults_when_cleared(self):
        self.store.set_guild_defaults("guild_1", {"language": "de", "voice": "+m2"})
        self.store.set_user_preferences("guild_1", "user_1", {"language": "it", "voice": "+f3"})

        self.assertEqual(self.store.resolve_preferences("guild_1", "user_1")["language"], "it")
        self.assertEqual(self.store.resolve_preferences("guild_1", "user_1")["voice"], "+f3")

        self.store.clear_user_preferences("guild_1", "user_1")

        self.assertEqual(self.store.resolve_preferences("guild_1", "user_1")["language"], "de")
        self.assertEqual(self.store.resolve_preferences("guild_1", "user_1")["voice"], "+m2")

    def test_caches_and_retrieves_translations(self):
        self.assertIsNone(self.store.get_cached_translation("missing"))
        self.store.set_cached_translation("k1", "Hello", "नमस्ते")
        self.assertEqual(self.store.get_cached_translation("k1"), ("Hello", "नमस्ते"))
        self.store.set_cached_translation("k1", "Hi there", "नमस्कार")
        self.assertEqual(self.store.get_cached_translation("k1"), ("Hi there", "नमस्कार"))
        self.assertIsNone(self.store.get_cached_translation("k1", max_age_ms=-1))


if __name__ == "__main__":
    unittest.main()

