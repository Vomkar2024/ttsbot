"""VoiceBridge TTS Bot - Main Application."""

import asyncio
import logging
import signal
import sys
from typing import Optional

import discord
from discord.ext import commands

from .commands.tts_commands import setup_tts_commands
from .config import Config, default_config
from .events.message_handler import handle_message_create
from .storage.settings_store import SettingsStore
from .tts.espeak_provider import EspeakProvider
from .voice.session_manager import VoiceSessionManager

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
)
logger = logging.getLogger("VoiceBridge")


class VoiceBridgeBot(commands.Bot):
    def __init__(self, app_config: Optional[Config] = None):
        self.cfg = app_config or default_config

        intents = discord.Intents.default()
        intents.guilds = True
        intents.voice_states = True
        intents.guild_messages = True
        intents.message_content = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
        )

        self.settings_store = SettingsStore(self.cfg.DATABASE_PATH)
        self.espeak_provider = EspeakProvider(tts_bin=self.cfg.TTS_BIN)
        self.session_manager = VoiceSessionManager(
            espeak_provider=self.espeak_provider,
            ready_timeout_ms=self.cfg.VOICE_READY_TIMEOUT_MS,
            disconnect_grace_ms=self.cfg.VOICE_DISCONNECT_GRACE_MS,
            max_queue_size=self.cfg.MAX_QUEUE_SIZE,
            ffmpeg_path=self.cfg.FFMPEG_PATH,
        )

        self.context = {
            "settingsStore": self.settings_store,
            "sessionManager": self.session_manager,
            "espeakProvider": self.espeak_provider,
            "config": self.cfg,
            "settings_store": self.settings_store,
            "session_manager": self.session_manager,
            "espeak_provider": self.espeak_provider,
        }

    async def setup_hook(self) -> None:
        setup_tts_commands(
            self.tree,
            session_manager=self.session_manager,
            settings_store=self.settings_store,
        )

        # Sync commands if token is present
        if self.cfg.DISCORD_GUILD_ID:
            guild_obj = discord.Object(id=int(self.cfg.DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            logger.info("Synced %d command(s) to development guild %s", len(synced), self.cfg.DISCORD_GUILD_ID)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %d global application command(s)", len(synced))

    async def on_ready(self) -> None:
        user = self.user
        if user:
            logger.info("Logged in as %s (ID: %s)", user, user.id)
            logger.info("Active in %d server(s).", len(self.guilds))
            try:
                activity = discord.Activity(
                    type=discord.ActivityType.listening,
                    name="/tts help | VoiceBridge",
                )
                await self.change_presence(activity=activity)
            except Exception:
                pass

    async def on_message(self, message: discord.Message) -> None:
        try:
            await handle_message_create(message, self.context)
        except Exception as err:
            logger.error("[Event:MessageCreate] Uncaught error: %s", err, exc_info=True)

    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        guild = member.guild
        if not guild or not self.user:
            return

        bot_id = self.user.id
        session = self.session_manager.get_session(str(guild.id))
        if not session:
            return

        # Case 1: Bot disconnect or move
        if member.id == bot_id:
            if not after.channel:
                logger.info("[VoiceStateUpdate Guild %s] Bot disconnected from voice. Cleaning up session.", guild.id)
                self.session_manager.remove_session(str(guild.id))
                return
            if before.channel and after.channel != before.channel:
                logger.info("[VoiceStateUpdate Guild %s] Bot moved to voice channel %s.", guild.id, after.channel.id)
                session.channel_id = str(after.channel.id)
            return

        # Case 2: Human members leaving bot's channel
        if session.channel_id:
            channel = guild.get_channel(int(session.channel_id))
            if channel and isinstance(channel, discord.VoiceChannel):
                human_members = [m for m in channel.members if not m.bot]
                if len(human_members) == 0:
                    logger.info("[VoiceStateUpdate Guild %s] Voice channel is empty. Leaving.", guild.id)
                    await self.session_manager.leave(str(guild.id))

    async def stop(self) -> None:
        logger.info("[VoiceBridge] Shutting down gracefully...")
        try:
            self.session_manager.destroy_all()
            self.settings_store.close()
            await self.close()
            logger.info("[VoiceBridge] Cleanup complete.")
        except Exception as err:
            logger.error("[VoiceBridge] Error during shutdown: %s", err)


def create_app(app_config: Optional[Config] = None) -> VoiceBridgeBot:
    return VoiceBridgeBot(app_config=app_config)


async def main_async() -> None:
    bot = create_app()

    loop = asyncio.get_running_loop()

    def _sig_handler(sig_name: str) -> None:
        logger.info("\n[Process] Caught %s. Initiating shutdown...", sig_name)
        asyncio.create_task(bot.stop())

    if sys.platform != "win32":
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda s=s: _sig_handler(signal.Signals(s).name))

    if not bot.cfg.DISCORD_TOKEN:
        logger.warning("[VoiceBridge] DISCORD_TOKEN is missing in .env. Bot cannot connect to Discord.")
        return

    try:
        await bot.start(bot.cfg.DISCORD_TOKEN)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await bot.stop()


def start_app() -> None:
    try:
        asyncio.run(main_async())
    except (KeyboardInterrupt, SystemExit):
        pass


__all__ = ["VoiceBridgeBot", "create_app", "start_app", "main_async"]

