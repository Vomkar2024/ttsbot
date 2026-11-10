"""Runtime smoke check verifying TTSBot instantiation, configuration, and shutdown."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.app import TTSBot, create_app
from src.config import Config


class TestRuntimeStartup(unittest.IsolatedAsyncioTestCase):
    async def test_full_runtime_initialization_and_command_loading(self):
        cfg = Config(
            discord_token="fake.token.123",
            database_path=":memory:",
            discord_guild_id="999888777",
            tts_engine="gtts",
        )

        bot = create_app(cfg)
        self.assertIsInstance(bot, TTSBot)
        bot._connection.user = SimpleNamespace(id=111222, name="TTSBot")

        # Mock sync to avoid real Discord HTTP calls during setup_hook
        with patch.object(bot.tree, "sync", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = []
            await bot.setup_hook()

        # Verify Cogs are loaded
        cog = bot.get_cog("TTS")
        self.assertIsNotNone(cog)
        hinglish_cog = bot.get_cog("Hinglish")
        self.assertIsNotNone(hinglish_cog)

        # Verify all 11 top-level application commands (10 TTS + /hinglish group) are loaded
        commands_list = bot.tree.get_commands()
        self.assertEqual(len(commands_list), 11)
        command_names = {c.name for c in commands_list}
        self.assertIn("join", command_names)
        self.assertIn("leave", command_names)
        self.assertIn("setup", command_names)
        self.assertIn("status", command_names)
        self.assertIn("queue", command_names)
        self.assertIn("skip", command_names)
        self.assertIn("stop", command_names)
        self.assertIn("clear", command_names)
        self.assertIn("voice", command_names)
        self.assertIn("language", command_names)
        self.assertIn("hinglish", command_names)

        # Verify graceful stop executes cleanly without blocking
        await bot.stop()
        self.assertTrue(bot.is_closed())


if __name__ == "__main__":
    unittest.main()

