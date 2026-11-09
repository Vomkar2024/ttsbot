"""Standalone slash command deployment script for VoiceBridge TTS Bot."""

import asyncio
import sys

import discord
from discord.ext import commands

from src.commands.tts_commands import setup_tts_commands
from src.config import load_config
from src.storage.settings_store import SettingsStore
from src.voice.session_manager import VoiceSessionManager

config = load_config()


async def deploy() -> None:
    if not config.DISCORD_TOKEN:
        print("❌ Error: DISCORD_TOKEN is missing in environment (.env).", file=sys.stderr)
        sys.exit(1)

    print("[Deploy] Initializing Discord client for command synchronization...")

    intents = discord.Intents.default()
    bot = commands.Bot(command_prefix="!", intents=intents)

    # Initialize mock or memory context for command definitions
    settings_store = SettingsStore(":memory:")
    session_manager = VoiceSessionManager()

    setup_tts_commands(
        bot.tree,
        session_manager=session_manager,
        settings_store=settings_store,
    )

    async with bot:
        # Log in without running full event loop
        await bot.login(config.DISCORD_TOKEN)

        try:
            if config.DISCORD_GUILD_ID:
                guild_id = int(config.DISCORD_GUILD_ID)
                guild_obj = discord.Object(id=guild_id)
                print(f"[Deploy] Targeting development guild: {guild_id}")
                bot.tree.copy_global_to(guild=guild_obj)
                synced = await bot.tree.sync(guild=guild_obj)
                print(f"✅ Successfully registered {len(synced)} guild command(s). Available immediately!")
            else:
                print("[Deploy] Targeting global application commands...")
                synced = await bot.tree.sync()
                print(f"✅ Successfully registered {len(synced)} global command(s). (Global propagation may take up to an hour)")
        except Exception as err:
            print(f"❌ Command deployment failed: {err}", file=sys.stderr)
            sys.exit(1)
        finally:
            settings_store.close()
            session_manager.destroy_all()


if __name__ == "__main__":
    asyncio.run(deploy())

