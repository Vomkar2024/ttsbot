"""Tests for TTSCommandsCog extension loading, command tree synchronization, and app command error handling."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

from src.app import create_app
from src.commands.tts_commands import TTSCommandsCog, setup
from src.config import Config
from src.storage.settings_store import SettingsStore
from src.voice.session_manager import VoiceSessionManager


class TestCogAndIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cfg = Config(
            discord_token="fake.token.123",
            database_path=":memory:",
            discord_guild_id="123456789",
        )
        self.bot = create_app(self.cfg)
        self.bot._connection.user = SimpleNamespace(id=999999, name="TTSBot")

    async def asyncTearDown(self):
        await self.bot.stop()

    async def test_cog_initialization_and_command_registration(self):
        cog = self.bot.get_cog("TTS")
        self.assertIsNone(cog)

        session_manager = VoiceSessionManager()
        settings_store = SettingsStore(":memory:")
        cog = TTSCommandsCog(self.bot, session_manager, settings_store)

        self.assertEqual(cog.bot, self.bot)
        self.assertEqual(len(cog.commands), 10)
        command_names = {c.name for c in cog.commands}
        expected = {"setup", "join", "leave", "voice", "language", "queue", "skip", "stop", "clear", "status"}
        self.assertEqual(command_names, expected)
        settings_store.close()

    async def test_extension_setup_function(self):
        test_bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        test_bot.session_manager = VoiceSessionManager()
        test_bot.settings_store = SettingsStore(":memory:")

        await setup(test_bot)

        cog = test_bot.get_cog("TTS")
        self.assertIsNotNone(cog)
        self.assertIsInstance(cog, TTSCommandsCog)
        test_bot.settings_store.close()
        test_bot.session_manager.destroy_all()
        await test_bot.close()

    async def test_setup_hook_loads_cog_and_syncs_guild(self):
        with patch.object(self.bot.tree, "sync", new_callable=AsyncMock) as mock_sync:
            with patch.object(self.bot.tree, "copy_global_to") as mock_copy:
                mock_sync.return_value = [SimpleNamespace(name="mock_cmd")]
                await self.bot.setup_hook()

                cog = self.bot.get_cog("TTS")
                self.assertIsNotNone(cog)
                mock_copy.assert_called_once()
                mock_sync.assert_awaited_once()

    async def test_setup_hook_syncs_globally_when_no_guild(self):
        no_guild_cfg = Config(
            discord_token="fake.token.123",
            database_path=":memory:",
            discord_guild_id="",
        )
        bot = create_app(no_guild_cfg)
        bot._connection.user = SimpleNamespace(id=999999, name="TTSBot")

        with patch.object(bot.tree, "sync", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = []
            await bot.setup_hook()

            cog = bot.get_cog("TTS")
            self.assertIsNotNone(cog)
            mock_sync.assert_awaited_once_with()

        await bot.stop()

    async def test_app_command_error_handler_response_is_not_done(self):
        with patch.object(self.bot.tree, "sync", new_callable=AsyncMock):
            await self.bot.setup_hook()

        # Retrieve tree error handler
        error_handler = self.bot.tree.on_error
        self.assertIsNotNone(error_handler)

        mock_interaction = SimpleNamespace(
            command=SimpleNamespace(name="join"),
            response=SimpleNamespace(
                is_done=lambda: False,
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(
                send=AsyncMock(),
            ),
        )

        fake_error = discord.app_commands.AppCommandError("Test command failure")
        await error_handler(mock_interaction, fake_error)

        mock_interaction.response.send_message.assert_awaited_once()
        self.assertIn("Test command failure", mock_interaction.response.send_message.call_args[0][0])

    async def test_app_command_error_handler_response_is_done(self):
        with patch.object(self.bot.tree, "sync", new_callable=AsyncMock):
            await self.bot.setup_hook()

        error_handler = self.bot.tree.on_error
        self.assertIsNotNone(error_handler)

        mock_interaction = SimpleNamespace(
            command=SimpleNamespace(name="join"),
            response=SimpleNamespace(
                is_done=lambda: True,
                send_message=AsyncMock(),
            ),
            followup=SimpleNamespace(
                send=AsyncMock(),
            ),
        )

        fake_error = discord.app_commands.AppCommandError("Deferred failure")
        await error_handler(mock_interaction, fake_error)

        mock_interaction.followup.send.assert_awaited_once()
        self.assertIn("Deferred failure", mock_interaction.followup.send.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
