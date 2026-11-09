"""Tests for Discord slash commands registration and parameter configuration."""

import unittest
import discord
from discord.ext import commands

from src.commands.tts_commands import setup_tts_commands
from src.constants import (
    COMMAND_CLEAR,
    COMMAND_JOIN,
    COMMAND_LANGUAGE,
    COMMAND_LEAVE,
    COMMAND_QUEUE,
    COMMAND_SETUP,
    COMMAND_SKIP,
    COMMAND_STATUS,
    COMMAND_STOP,
    COMMAND_VOICE,
    LANGUAGE_CHOICES,
    VOICE_VARIANT_CHOICES,
)
from src.storage.settings_store import SettingsStore
from src.voice.session_manager import VoiceSessionManager


class TestCommandsRegistration(unittest.TestCase):
    def setUp(self):
        self.bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
        self.settings_store = SettingsStore(":memory:")
        self.session_manager = VoiceSessionManager()
        self.registered = setup_tts_commands(
            self.bot.tree,
            session_manager=self.session_manager,
            settings_store=self.settings_store,
        )

    def tearDown(self):
        self.settings_store.close()
        self.session_manager.destroy_all()

    def test_registers_exactly_ten_top_level_commands(self):
        cmd_names = [c.name for c in self.bot.tree.get_commands()]
        expected_names = [
            COMMAND_SETUP,
            COMMAND_JOIN,
            COMMAND_LEAVE,
            COMMAND_VOICE,
            COMMAND_LANGUAGE,
            COMMAND_QUEUE,
            COMMAND_SKIP,
            COMMAND_STOP,
            COMMAND_CLEAR,
            COMMAND_STATUS,
        ]
        self.assertEqual(len(cmd_names), 10)
        self.assertCountEqual(cmd_names, expected_names)

    def test_no_command_named_or_grouped_under_tts(self):
        commands_list = self.bot.tree.get_commands()
        for cmd in commands_list:
            self.assertNotEqual(cmd.name, "tts", "No command should be named 'tts'")
            self.assertIsNone(cmd.root_parent, f"Command {cmd.name} should be top-level without parent group")

    def test_setup_command_parameters(self):
        setup_cmd = self.bot.tree.get_command(COMMAND_SETUP)
        self.assertIsNotNone(setup_cmd)
        param_names = [p.name for p in setup_cmd.parameters]
        self.assertIn("channel", param_names)

    def test_voice_command_parameters_and_choices(self):
        voice_cmd = self.bot.tree.get_command(COMMAND_VOICE)
        self.assertIsNotNone(voice_cmd)
        param_names = [p.name for p in voice_cmd.parameters]
        self.assertIn("variant", param_names)
        self.assertIn("server", param_names)

        variant_param = next(p for p in voice_cmd.parameters if p.name == "variant")
        choice_values = [c.value for c in variant_param.choices]
        expected_values = [c["value"] for c in VOICE_VARIANT_CHOICES]
        self.assertEqual(choice_values, expected_values)

    def test_language_command_parameters_and_choices(self):
        lang_cmd = self.bot.tree.get_command(COMMAND_LANGUAGE)
        self.assertIsNotNone(lang_cmd)
        param_names = [p.name for p in lang_cmd.parameters]
        self.assertIn("language", param_names)
        self.assertIn("server", param_names)

        lang_param = next(p for p in lang_cmd.parameters if p.name == "language")
        choice_values = [c.value for c in lang_param.choices]
        expected_values = [c["value"] for c in LANGUAGE_CHOICES]
        self.assertEqual(choice_values, expected_values)

    def test_parameterless_commands(self):
        for name in [
            COMMAND_JOIN,
            COMMAND_LEAVE,
            COMMAND_QUEUE,
            COMMAND_SKIP,
            COMMAND_STOP,
            COMMAND_CLEAR,
            COMMAND_STATUS,
        ]:
            cmd = self.bot.tree.get_command(name)
            self.assertIsNotNone(cmd, f"Command {name} must exist")
            self.assertEqual(len(cmd.parameters), 0, f"Command {name} should have no arguments")


if __name__ == "__main__":
    unittest.main()

