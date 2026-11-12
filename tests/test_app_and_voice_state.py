"""Tests for TTSBot lifecycle, voice state updates, and graceful shutdown."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.app import TTSBot, create_app
from src.config import Config


class TestAppAndVoiceState(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Config(
            discord_token="fake.token.123",
            database_path=":memory:",
            voice_disconnect_grace_ms=100,  # 0.1s for fast tests
        )
        self.bot = create_app(self.cfg)
        self.bot._connection.user = SimpleNamespace(id=999999, name="TTSBot")

    async def asyncTearDown(self):
        await self.bot.stop()

    async def test_create_app_initialization(self):
        self.assertIsInstance(self.bot, TTSBot)
        self.assertIsNotNone(self.bot.session_manager)
        self.assertIsNotNone(self.bot.settings_store)
        self.assertIsNotNone(self.bot.tts_provider)
        self.assertIs(self.bot.translator.settings_store, self.bot.settings_store)
        self.assertEqual(self.bot.command_prefix, "!")

    async def test_on_ready_sets_presence(self):
        with patch.object(self.bot, "change_presence", new_callable=AsyncMock) as mock_presence:
            await self.bot.on_ready()
            mock_presence.assert_awaited_once()

    async def test_on_message_delegation(self):
        msg = SimpleNamespace(
            content="Hello world",
            guild=SimpleNamespace(id=123),
            channel=SimpleNamespace(id=456),
            author=SimpleNamespace(id=789, bot=False, display_name="User"),
        )
        with patch("src.app.handle_message_create", new_callable=AsyncMock) as mock_handler:
            mock_handler.return_value = True
            await self.bot.on_message(msg)
            mock_handler.assert_awaited_once_with(msg, self.bot.context)

    async def test_voice_state_update_bot_moved_channel(self):
        guild = SimpleNamespace(id=100)
        session = SimpleNamespace(channel_id="200", _reconnect_task=None)
        self.bot.session_manager.sessions["100"] = session

        bot_member = SimpleNamespace(id=self.bot.user.id, guild=guild)
        before = SimpleNamespace(channel=SimpleNamespace(id=200))
        after = SimpleNamespace(channel=SimpleNamespace(id=300))

        await self.bot.on_voice_state_update(bot_member, before, after)
        self.assertEqual(session.channel_id, "300")

    async def test_voice_state_update_bot_disconnected_with_grace(self):
        guild = SimpleNamespace(id=100, voice_client=None)
        session = SimpleNamespace(channel_id="200", _reconnect_task=None)
        self.bot.session_manager.sessions["100"] = session

        bot_member = SimpleNamespace(id=self.bot.user.id, guild=guild)
        before = SimpleNamespace(channel=SimpleNamespace(id=200))
        after = SimpleNamespace(channel=None)

        await self.bot.on_voice_state_update(bot_member, before, after)
        self.assertIsNotNone(session._reconnect_task)
        self.assertFalse(session._reconnect_task.done())

        # Wait for grace period (0.1s) to expire
        await asyncio.sleep(0.15)
        self.assertNotIn("100", self.bot.session_manager.sessions)

    async def test_voice_state_update_bot_reconnect_cancels_grace(self):
        guild = SimpleNamespace(id=100, voice_client=SimpleNamespace(is_connected=lambda: True))
        session = SimpleNamespace(channel_id="200", _reconnect_task=None)
        self.bot.session_manager.sessions["100"] = session

        bot_member = SimpleNamespace(id=self.bot.user.id, guild=guild)
        before = SimpleNamespace(channel=SimpleNamespace(id=200))
        after = SimpleNamespace(channel=None)

        # 1. Trigger disconnect
        await self.bot.on_voice_state_update(bot_member, before, after)
        task = session._reconnect_task
        self.assertIsNotNone(task)

        # 2. Reconnect before grace timer expires
        before_recon = SimpleNamespace(channel=None)
        after_recon = SimpleNamespace(channel=SimpleNamespace(id=200))
        await self.bot.on_voice_state_update(bot_member, before_recon, after_recon)
        await asyncio.sleep(0)

        self.assertTrue(task.cancelled() or getattr(task, "cancelling", lambda: 0)() > 0)
        self.assertIn("100", self.bot.session_manager.sessions)

    async def test_voice_state_update_human_leaves_and_channel_becomes_empty(self):
        guild = SimpleNamespace(id=100)
        session = SimpleNamespace(channel_id="200")
        self.bot.session_manager.sessions["100"] = session

        voice_chan = SimpleNamespace(
            id=200,
            members=[SimpleNamespace(id=self.bot.user.id, bot=True)],  # Only bot remains
        )

        human_member = SimpleNamespace(id=555, guild=guild, bot=False)
        before = SimpleNamespace(channel=voice_chan)
        after = SimpleNamespace(channel=None)

        with patch.object(self.bot.session_manager, "leave", new_callable=AsyncMock) as mock_leave:
            await self.bot.on_voice_state_update(human_member, before, after)
            mock_leave.assert_awaited_once_with("100")

    async def test_voice_state_update_human_leaves_but_others_remain(self):
        guild = SimpleNamespace(id=100)
        session = SimpleNamespace(channel_id="200")
        self.bot.session_manager.sessions["100"] = session

        voice_chan = SimpleNamespace(
            id=200,
            members=[
                SimpleNamespace(id=self.bot.user.id, bot=True),
                SimpleNamespace(id=777, bot=False),  # Another human remains!
            ],
        )

        human_member = SimpleNamespace(id=555, guild=guild, bot=False)
        before = SimpleNamespace(channel=voice_chan)
        after = SimpleNamespace(channel=None)

        with patch.object(self.bot.session_manager, "leave", new_callable=AsyncMock) as mock_leave:
            await self.bot.on_voice_state_update(human_member, before, after)
            mock_leave.assert_not_called()

    async def test_voice_state_update_ignores_unrelated_channel_updates(self):
        guild = SimpleNamespace(id=100)
        session = SimpleNamespace(channel_id="200")
        self.bot.session_manager.sessions["100"] = session

        unrelated_chan = SimpleNamespace(id=999, members=[])
        human_member = SimpleNamespace(id=555, guild=guild, bot=False)
        before = SimpleNamespace(channel=unrelated_chan)
        after = SimpleNamespace(channel=None)

        with patch.object(self.bot.session_manager, "leave", new_callable=AsyncMock) as mock_leave:
            await self.bot.on_voice_state_update(human_member, before, after)
            mock_leave.assert_not_called()

    async def test_stop_gracefully_cleans_up(self):
        with patch.object(self.bot.session_manager, "destroy_all_async", new_callable=AsyncMock) as mock_destroy:
            with patch.object(self.bot.settings_store, "close") as mock_close_db:
                with patch.object(self.bot, "close", new_callable=AsyncMock) as mock_close_bot:
                    await self.bot.stop()
                    mock_destroy.assert_awaited_once()
                    mock_close_db.assert_called_once()
                    mock_close_bot.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

