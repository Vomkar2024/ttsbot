"""Tests for message handler filtering and TTS enqueueing."""

import unittest
from types import SimpleNamespace
from src.events.message_handler import handle_message_create


class MockSessionManager:
    def __init__(self, session=None):
        self.session = session

    def get_session(self, guild_id):
        return self.session

    getSession = get_session


class MockSettingsStore:
    def __init__(self, configured_channel="chan_tts", prefs=None):
        self.configured_channel = configured_channel
        self.prefs = prefs or {"language": "en", "voice": "default"}

    def is_configured_channel(self, guild_id, channel_id):
        return channel_id == self.configured_channel

    isConfiguredChannel = is_configured_channel

    def resolve_preferences(self, guild_id, user_id):
        return self.prefs

    resolvePreferences = resolve_preferences


class TestMessageHandler(unittest.IsolatedAsyncioTestCase):
    dummy_config = {
        "SPEAK_USERNAMES": True,
        "MAX_MESSAGE_CHARS": 800,
        "MAX_QUEUE_SIZE": 50,
    }

    async def test_ignores_bots_webhooks_and_system(self):
        context = {
            "sessionManager": MockSessionManager(session=SimpleNamespace()),
            "settingsStore": MockSettingsStore(),
            "config": self.dummy_config,
        }

        bot_msg = {"author": {"bot": True}, "guild": {"id": "g1"}, "content": "Hello"}
        self.assertFalse(await handle_message_create(bot_msg, context))

        webhook_msg = {
            "author": {"bot": False},
            "webhookId": "hook_1",
            "guild": {"id": "g1"},
            "content": "Hello",
        }
        self.assertFalse(await handle_message_create(webhook_msg, context))

        system_msg = {
            "author": {"bot": False},
            "system": True,
            "guild": {"id": "g1"},
            "content": "Hello",
        }
        self.assertFalse(await handle_message_create(system_msg, context))

    async def test_ignores_direct_messages(self):
        context = {
            "sessionManager": MockSessionManager(session=SimpleNamespace()),
            "settingsStore": MockSettingsStore(),
            "config": self.dummy_config,
        }

        dm_msg = {"author": {"bot": False}, "guild": None, "content": "Private message"}
        self.assertFalse(await handle_message_create(dm_msg, context))

    async def test_ignores_when_bot_not_connected_to_voice(self):
        context = {
            "sessionManager": MockSessionManager(session=None),  # No voice session
            "settingsStore": MockSettingsStore(),
            "config": self.dummy_config,
        }

        msg = {
            "author": {"bot": False},
            "guild": {"id": "g1"},
            "channel": {"id": "chan_tts"},
            "content": "Hello voice",
        }
        self.assertFalse(await handle_message_create(msg, context))

    async def test_ignores_from_non_configured_text_channels(self):
        context = {
            "sessionManager": MockSessionManager(session=SimpleNamespace()),
            "settingsStore": MockSettingsStore(configured_channel="configured_chan"),
            "config": self.dummy_config,
        }

        msg = {
            "author": {"bot": False},
            "guild": {"id": "g1"},
            "channel": {"id": "other_chan"},
            "content": "Should be ignored",
        }
        self.assertFalse(await handle_message_create(msg, context))

    async def test_ignores_messages_that_clean_down_to_empty(self):
        enqueued = False

        class MockQueue:
            def enqueue(self, item):
                nonlocal enqueued
                enqueued = True

        mock_session = SimpleNamespace(serial_queue=MockQueue())
        context = {
            "sessionManager": MockSessionManager(session=mock_session),
            "settingsStore": MockSettingsStore(configured_channel="tts_chan"),
            "config": self.dummy_config,
        }

        msg = {
            "author": {"bot": False},
            "guild": {"id": "g1"},
            "channel": {"id": "tts_chan"},
            "content": "https://discord.com/invite/something <#12345>",
        }

        self.assertFalse(await handle_message_create(msg, context))
        self.assertFalse(enqueued)

    async def test_enqueues_valid_message_with_speaker_attribution(self):
        queued_item = None

        class MockQueue:
            def enqueue(self, item):
                nonlocal queued_item
                queued_item = item

        mock_session = SimpleNamespace(serial_queue=MockQueue())
        context = {
            "sessionManager": MockSessionManager(session=mock_session),
            "settingsStore": MockSettingsStore(
                configured_channel="chan_tts",
                prefs={"language": "es", "voice": "+m2"},
            ),
            "config": {
                "SPEAK_USERNAMES": True,
                "MAX_MESSAGE_CHARS": 800,
                "MAX_QUEUE_SIZE": 50,
            },
        }

        msg = {
            "author": {"id": "user_42", "bot": False, "username": "testuser"},
            "member": {"displayName": "Maria"},
            "guild": {"id": "guild_99"},
            "channel": {"id": "chan_tts"},
            "content": "Buenos dias amigos!",
        }

        success = await handle_message_create(msg, context)
        self.assertTrue(success)
        self.assertIsNotNone(queued_item)
        self.assertEqual(queued_item["spokenText"], "Maria says: Buenos dias amigos!")
        self.assertEqual(queued_item["authorName"], "Maria")
        self.assertEqual(queued_item["language"], "es")
        self.assertEqual(queued_item["voice"], "+m2")
        self.assertEqual(queued_item["userId"], "user_42")


if __name__ == "__main__":
    unittest.main()

