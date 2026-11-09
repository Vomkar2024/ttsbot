"""Discord TTS Bot - Main Application."""

import asyncio
import logging
import signal
import sys
from typing import Optional

import discord
from discord.ext import commands

from .commands.tts_commands import TTSCommandsCog
from .config import Config, default_config
from .events.message_handler import handle_message_create
from .storage.settings_store import SettingsStore
from .tts.format_message import SpeakerCooldownTracker
from .tts.unified_provider import UnifiedTTSProvider
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
        self.tts_provider = UnifiedTTSProvider(
            default_engine=self.cfg.TTS_ENGINE,
            tts_bin=self.cfg.TTS_BIN,
        )
        self.espeak_provider = self.tts_provider  # Alias
        self.speaker_cooldown_tracker = SpeakerCooldownTracker(
            cooldown_sec=self.cfg.SPEAKER_COOLDOWN_SEC
        )
        self.session_manager = VoiceSessionManager(
            tts_provider=self.tts_provider,
            ready_timeout_ms=self.cfg.VOICE_READY_TIMEOUT_MS,
            disconnect_grace_ms=self.cfg.VOICE_DISCONNECT_GRACE_MS,
            max_queue_size=self.cfg.MAX_QUEUE_SIZE,
            ffmpeg_path=self.cfg.FFMPEG_PATH,
        )

        self.context = {
            "settingsStore": self.settings_store,
            "sessionManager": self.session_manager,
            "espeakProvider": self.tts_provider,
            "ttsProvider": self.tts_provider,
            "speakerCooldownTracker": self.speaker_cooldown_tracker,
            "config": self.cfg,
            "settings_store": self.settings_store,
            "session_manager": self.session_manager,
            "espeak_provider": self.tts_provider,
            "tts_provider": self.tts_provider,
            "speaker_cooldown_tracker": self.speaker_cooldown_tracker,
        }

    async def setup_hook(self) -> None:
        await self.add_cog(
            TTSCommandsCog(
                self,
                session_manager=self.session_manager,
                settings_store=self.settings_store,
            )
        )

        # Sync commands if token is present
        if self.cfg.DISCORD_GUILD_ID and self.cfg.DISCORD_GUILD_ID.isdigit():
            guild_obj = discord.Object(id=int(self.cfg.DISCORD_GUILD_ID))
            self.tree.copy_global_to(guild=guild_obj)
            synced = await self.tree.sync(guild=guild_obj)
            logger.info("Synced %d command(s) to development guild %s", len(synced), self.cfg.DISCORD_GUILD_ID)
        else:
            if self.cfg.DISCORD_GUILD_ID:
                logger.warning("DISCORD_GUILD_ID '%s' is not numeric; syncing globally instead.", self.cfg.DISCORD_GUILD_ID)
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
        try:
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
                    logger.info(
                        "[VoiceStateUpdate Guild %s] Bot disconnected from voice (grace: %.1fs).",
                        guild.id,
                        self.cfg.voice_disconnect_grace_sec,
                    )
                    if self.cfg.voice_disconnect_grace_sec > 0:
                        async def _delayed_cleanup() -> None:
                            await asyncio.sleep(self.cfg.voice_disconnect_grace_sec)
                            curr_session = self.session_manager.get_session(str(guild.id))
                            vc = getattr(guild, "voice_client", None)
                            if curr_session and (not vc or not vc.is_connected()):
                                logger.info(
                                    "[VoiceStateUpdate Guild %s] Disconnect grace expired without reconnection. Cleaning up session.",
                                    guild.id,
                                )
                                self.session_manager.remove_session(str(guild.id))

                        if getattr(session, "_reconnect_task", None) and not session._reconnect_task.done():
                            session._reconnect_task.cancel()
                        session._reconnect_task = asyncio.create_task(_delayed_cleanup())
                    else:
                        self.session_manager.remove_session(str(guild.id))
                    return

                # Bot reconnected or moved to a channel
                if getattr(session, "_reconnect_task", None) and not session._reconnect_task.done():
                    session._reconnect_task.cancel()
                    session._reconnect_task = None

                if before.channel and after.channel != before.channel:
                    logger.info("[VoiceStateUpdate Guild %s] Bot moved to voice channel %s.", guild.id, after.channel.id)
                    session.channel_id = str(after.channel.id)
                return

            # Case 2: Human members leaving bot's channel
            if session.channel_id:
                left_bot_channel = (
                    before.channel is not None
                    and str(before.channel.id) == session.channel_id
                    and (after.channel is None or str(after.channel.id) != session.channel_id)
                )
                if left_bot_channel:
                    channel = before.channel
                    if channel and hasattr(channel, "members"):
                        human_members = [m for m in channel.members if not getattr(m, "bot", False)]
                        if len(human_members) == 0:
                            logger.info("[VoiceStateUpdate Guild %s] Voice channel is empty. Leaving.", guild.id)
                            await self.session_manager.leave(str(guild.id))
        except Exception as err:
            logger.error("[Event:VoiceStateUpdate] Uncaught error: %s", err, exc_info=True)

    async def stop(self) -> None:
        logger.info("[TTS] Shutting down gracefully...")
        try:
            if hasattr(self.session_manager, "destroy_all_async"):
                try:
                    await asyncio.wait_for(self.session_manager.destroy_all_async(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[TTS] Voice session cleanup timed out during shutdown.")
            else:
                self.session_manager.destroy_all()
            self.settings_store.close()
            try:
                await asyncio.wait_for(self.close(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("[TTS] Bot close timed out during shutdown.")
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

    resolved_espeak = None
    try:
        provider = getattr(bot, "tts_provider", None) or getattr(bot, "espeak_provider", None)
        if provider is not None:
            resolve_fn = getattr(provider, "resolve_binary_path", None) or getattr(provider, "_resolve_binary_path", None)
            if callable(resolve_fn):
                resolved_espeak = resolve_fn()
            elif hasattr(provider, "espeak"):
                espeak_obj = getattr(provider, "espeak", None)
                sub_resolve = getattr(espeak_obj, "resolve_binary_path", None) or getattr(espeak_obj, "_resolve_binary_path", None)
                if callable(sub_resolve):
                    resolved_espeak = sub_resolve()
    except Exception as espeak_check_err:
        logger.debug("[Preflight] Exception while resolving eSpeak binary: %s", espeak_check_err)
        resolved_espeak = None

    resolved_ffmpeg = None
    try:
        resolved_ffmpeg = shutil.which(bot.cfg.FFMPEG_PATH)
    except Exception as ffmpeg_check_err:
        logger.debug("[Preflight] Exception while checking FFmpeg: %s", ffmpeg_check_err)
        resolved_ffmpeg = None

    is_gtts_primary = (getattr(bot.cfg, "TTS_ENGINE", "gtts") or "").lower() in ("gtts", "google")

    if not resolved_ffmpeg or (not resolved_espeak and not is_gtts_primary):
        missing_binaries = []
        if not resolved_espeak and not is_gtts_primary:
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
    elif not resolved_espeak and is_gtts_primary:
        logger.info(
            "eSpeak NG binary not found on PATH. Primary TTS engine is 'gTTS' (natural web voice); "
            "eSpeak offline fallback will be disabled."
        )

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


