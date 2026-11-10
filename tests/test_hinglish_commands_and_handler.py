"""Tests for Hinglish commands, SQLite persistence, and message handler routing."""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord
from discord.ext import commands

from src.commands.hinglish_commands import HinglishCommandsCog, setup_hinglish_commands
from src.events.message_handler import handle_message_create
from src.hinglish.service import HinglishResult
from src.storage.settings_store import SettingsStore


class TestHinglishSettingsAndCommands(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        self.store = SettingsStore(":memory:")
        self.hinglish_service = MagicMock()
        self.hinglish_service.translator = SimpleNamespace(provider_name="deep-translator")
        self.session_manager = MagicMock()
        self.session_manager.get_session.return_value = None

    async def asyncTearDown(self):
        self.store.close()
        await self.bot.close()

    def test_settings_store_hinglish_persistence(self):
        default_cfg = self.store.get_hinglish_mode("guild_1")
        self.assertFalse(default_cfg["enabled"])
        self.assertFalse(self.store.is_hinglish_enabled("guild_1", "chan_1"))

        self.store.set_hinglish_mode("guild_1", True, channel_id="chan_1", delivery_mode="voice")
        cfg = self.store.get_hinglish_mode("guild_1")
        self.assertTrue(cfg["enabled"])
        self.assertEqual(cfg["channel_id"], "chan_1")
        self.assertEqual(cfg["delivery_mode"], "voice")
        self.assertTrue(self.store.is_hinglish_enabled("guild_1", "chan_1"))
        self.assertFalse(self.store.is_hinglish_enabled("guild_1", "chan_other"))

        self.store.set_hinglish_mode("guild_1", False)
        self.assertFalse(self.store.is_hinglish_enabled("guild_1", "chan_1"))

    def test_registers_hinglish_group_and_subcommands(self):
        groups = setup_hinglish_commands(
            self.bot.tree,
            hinglish_service=self.hinglish_service,
            settings_store=self.store,
            session_manager=self.session_manager,
        )
        self.assertEqual(len(groups), 1)
        group = self.bot.tree.get_command("hinglish")
        self.assertIsNotNone(group)
        sub_names = {c.name for c in group.commands}
        self.assertEqual(sub_names, {"say", "mode", "status"})

    async def test_hinglish_say_command_delegates_to_service(self):
        cog = HinglishCommandsCog(
            self.bot,
            hinglish_service=self.hinglish_service,
            settings_store=self.store,
            session_manager=self.session_manager,
        )
        group = cog.commands[0]
        say_cmd = group.get_command("say")

        fake_result = HinglishResult(
            source_text="Hello",
            english_text="Hello!",
            hindi_text="नमस्ते!",
            delivered_via="file",
        )
        self.hinglish_service.process_message = AsyncMock(return_value=fake_result)
        self.hinglish_service.send_result = AsyncMock(return_value=fake_result)

        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=111, get_member=lambda uid: None),
            channel=SimpleNamespace(id=222),
            user=SimpleNamespace(id=333, display_name="Omkar", name="omkar"),
            response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )

        await say_cmd.callback(interaction, "Hello", "auto")
        interaction.response.defer.assert_awaited_once()
        self.hinglish_service.process_message.assert_awaited_once()
        self.hinglish_service.send_result.assert_awaited_once()

    async def test_hinglish_mode_requires_manage_guild(self):
        cog = HinglishCommandsCog(
            self.bot,
            hinglish_service=self.hinglish_service,
            settings_store=self.store,
            session_manager=self.session_manager,
        )
        mode_cmd = cog.commands[0].get_command("mode")

        interaction_no_perm = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=222),
            user=SimpleNamespace(id=333, guild_permissions=SimpleNamespace(manage_guild=False)),
            permissions=SimpleNamespace(manage_guild=False),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await mode_cmd.callback(interaction_no_perm, True, None, "auto")
        self.assertIn("Manage Server", interaction_no_perm.response.send_message.call_args[0][0])
        self.assertFalse(self.store.get_hinglish_mode("111")["enabled"])

        interaction_admin = SimpleNamespace(
            guild=SimpleNamespace(id=111),
            channel=SimpleNamespace(id=222),
            user=SimpleNamespace(id=333, guild_permissions=SimpleNamespace(manage_guild=True)),
            permissions=SimpleNamespace(manage_guild=True),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await mode_cmd.callback(interaction_admin, True, None, "auto")
        self.assertTrue(self.store.is_hinglish_enabled("111", "222"))

    async def test_message_handler_routes_hinglish_enabled_messages_without_active_voice(self):
        self.store.set_hinglish_mode("g_1", True, channel_id="c_1", delivery_mode="auto")

        fake_result = HinglishResult(
            source_text="Kal milte hai",
            english_text="See you tomorrow",
            hindi_text="कल मिलते हैं",
            delivered_via="file",
        )
        self.hinglish_service.process_message = AsyncMock(return_value=fake_result)
        self.hinglish_service.send_result = AsyncMock(return_value=fake_result)

        channel_send = AsyncMock()
        msg = SimpleNamespace(
            author=SimpleNamespace(id=42, bot=False, display_name="Riya", name="riya"),
            member=SimpleNamespace(display_name="Riya"),
            guild=SimpleNamespace(id="g_1"),
            channel=SimpleNamespace(id="c_1", send=channel_send),
            content="Kal milte hai",
            webhook_id=None,
            system=False,
        )
        ctx = {
            "settings_store": self.store,
            "session_manager": self.session_manager,
            "hinglish_service": self.hinglish_service,
        }

        handled = await handle_message_create(msg, ctx)
        self.assertTrue(handled)
        self.hinglish_service.process_message.assert_awaited_once()
        self.hinglish_service.send_result.assert_awaited_once_with(
            channel_send, fake_result, author_name="Riya"
        )


if __name__ == "__main__":
    unittest.main()

