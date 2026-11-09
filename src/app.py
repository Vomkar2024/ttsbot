"""Discord TTS Bot - Main Application."""

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
logger = logging.getLogger("TTSBot")


class TTSBot(commands.Bot):
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
                    name="/status | TTS Bot",
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
        logger.info("[TTS] Shutting down gracefully...")
        try:
            self.session_manager.destroy_all()
            self.settings_store.close()
            await self.close()
            logger.info("[TTS] Cleanup complete.")
        except Exception as err:
            logger.error("[TTS] Error during shutdown: %s", err)


def create_app(app_config: Optional[Config] = None) -> TTSBot:
    return TTSBot(app_config=app_config)


async def main_async() -> None:
    bot = create_app()

    loop = asyncio.get_running_loop()

    def _sig_handler(sig_name: str) -> None:
        logger.info("\n[Process] Caught %s. Initiating shutdown...", sig_name)
        asyncio.create_task(bot.stop())

    if sys.platform != "win32":
        for s in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(s, lambda s=s: _sig_handler(signal.Signals(s).name))

    # Pre-flight voice dependency check
    import discord.voice_client

    missing_deps = []
    if not getattr(discord.voice_client, "has_nacl", False):
        missing_deps.append("PyNaCl")
    if not getattr(discord.voice_client, "has_dave", False):
        missing_deps.append("davey")

    if missing_deps:
        dep_str = " and ".join(missing_deps)
        install_cmd = f"{sys.executable} -m pip install {' '.join(missing_deps)}"
        warn_msg = (
            "\n" + "=" * 65 + "\n"
            f" ⚠️ [VOICE DEPENDENCY WARNING] Missing: {dep_str}\n"
            "=" * 65 + "\n"
            " Discord voice and end-to-end encryption (DAVE) require these libraries.\n"
            " Without them, commands like /join will fail when connecting to voice.\n\n"
            " HOW TO FIX (run in your terminal):\n"
            f"   {install_cmd}\n"
            "=" * 65 + "\n"
        )
        print(warn_msg, file=sys.stderr, flush=True)
        logger.warning(
            "Voice dependencies missing (%s). Voice will be unavailable until installed.",
            dep_str,
        )

    # Pre-flight system binary check (eSpeak NG & FFmpeg)
    import shutil
    resolved_espeak = bot.espeak_provider.resolve_binary_path()
    resolved_ffmpeg = shutil.which(bot.cfg.FFMPEG_PATH)

    if not resolved_espeak or not resolved_ffmpeg:
        missing_binaries = []
        if not resolved_espeak:
            missing_binaries.append("eSpeak NG")
        if not resolved_ffmpeg:
            missing_binaries.append("FFmpeg")

        bin_str = " & ".join(missing_binaries)
        warn_lines = [
            "\n" + "=" * 65,
            f" ⚠️ [SYSTEM BINARY WARNING] Missing: {bin_str}",
            "=" * 65,
            " The bot requires these system binaries to synthesize and stream audio.",
        ]

        if sys.platform != "win32" and (":" in bot.cfg.TTS_BIN or "\\" in bot.cfg.TTS_BIN):
            warn_lines.append(
                f"\n ⚠️ PLATFORM MISMATCH: TTS_BIN is set to a Windows path ('{bot.cfg.TTS_BIN}') on Linux.\n"
                "    Please set TTS_BIN=espeak-ng in your hosting environment variables or .env."
            )

        warn_lines.extend([
            "\n HOW TO FIX ON BOT HOSTING / LINUX (VPS, Docker, Pterodactyl):",
            "   1. Ubuntu / Debian VPS: sudo apt update && sudo apt install -y espeak-ng ffmpeg",
            "   2. Docker: Add 'RUN apt-get update && apt-get install -y espeak-ng ffmpeg' to Dockerfile",
            "   3. Discloud: In discloud.config set APT=tools,espeak-ng,ffmpeg",
            "   4. Pterodactyl: Select a container egg with audio/apt support or contact host support.",
            "\n HOW TO FIX ON WINDOWS:",
            "   - eSpeak NG: Install from https://github.com/espeak-ng/espeak-ng/releases",
            "   - FFmpeg: winget install Gyan.FFmpeg (or add ffmpeg.exe to PATH)",
            "=" * 65 + "\n",
        ])

        full_warn = "\n".join(warn_lines)
        print(full_warn, file=sys.stderr, flush=True)
        logger.warning("System binaries missing: %s. TTS playback will fail until installed.", bin_str)

    if not bot.cfg.DISCORD_TOKEN:
        err_msg = (
            "\n" + "=" * 65 + "\n"
            " ❌ [CONFIGURATION ERROR] DISCORD_TOKEN is missing in .env\n"
            "=" * 65 + "\n"
            " HOW TO FIX:\n"
            " 1. Create or open the .env file in your bot root directory.\n"
            " 2. Add: DISCORD_TOKEN=<your_token_from_discord_developer_portal>\n"
            " 3. Restart the bot.\n"
            "=" * 65 + "\n"
        )
        print(err_msg, file=sys.stderr, flush=True)
        logger.critical(err_msg)
        sys.exit(1)

    app_id = bot.cfg.DISCORD_CLIENT_ID or "YOUR_APP_ID"

    try:
        await bot.start(bot.cfg.DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        err_msg = (
            "\n" + "=" * 65 + "\n"
            " ❌ [AUTHENTICATION FAILED] Discord refused the bot token.\n"
            "    HTTP 401 Unauthorized: Improper token has been passed.\n"
            "=" * 65 + "\n"
            " The token in your .env or hosting environment is invalid, expired, or reset.\n\n"
            " HOW TO FIX:\n"
            f" 1. Go to Discord Developer Portal:\n"
            f"    https://discord.com/developers/applications/{app_id}/bot\n"
            " 2. Click 'Reset Token' and confirm.\n"
            " 3. Copy your newly generated bot token.\n"
            " 4. Update DISCORD_TOKEN in .env (locally and in your hosting panel's File Manager):\n"
            "    DISCORD_TOKEN=your_new_token_here  (do NOT include quotes!)\n"
            " 5. Under 'Privileged Gateway Intents' on the same page, ensure\n"
            "    'MESSAGE CONTENT INTENT' is toggled ON (green check).\n"
            " 6. Restart your bot.\n"
            "=" * 65 + "\n"
        )
        print(err_msg, file=sys.stderr, flush=True)
        logger.critical(err_msg)
        sys.exit(1)
    except discord.errors.PrivilegedIntentsRequired:
        err_msg = (
            "\n" + "=" * 65 + "\n"
            " ❌ [PRIVILEGED INTENTS REQUIRED] Message Content Intent is disabled.\n"
            "=" * 65 + "\n"
            " Discord TTS Bot requires 'Message Content Intent' to read messages aloud.\n\n"
            " HOW TO FIX:\n"
            f" 1. Go to Discord Developer Portal:\n"
            f"    https://discord.com/developers/applications/{app_id}/bot\n"
            " 2. Scroll down to 'Privileged Gateway Intents'.\n"
            " 3. Toggle 'MESSAGE CONTENT INTENT' to ON (green check).\n"
            " 4. Click 'Save Changes' at the bottom of the page.\n"
            " 5. Restart your bot.\n"
            "=" * 65 + "\n"
        )
        print(err_msg, file=sys.stderr, flush=True)
        logger.critical(err_msg)
        sys.exit(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except Exception as err:
        logger.critical("[TTS] Unexpected error during bot startup: %s", err, exc_info=True)
    finally:
        await bot.stop()


def start_app() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        pass


__all__ = ["TTSBot", "create_app", "start_app", "main_async"]


