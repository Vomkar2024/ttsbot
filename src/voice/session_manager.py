"""Voice session manager handling per-guild concurrency serialization, channel moves, and lifecycle."""
import asyncio
import inspect
import logging
import os
import shutil
import time
from typing import Any, Dict, Optional

import discord

from ..constants import (
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_VOICE_DISCONNECT_GRACE_MS,
    DEFAULT_VOICE_READY_TIMEOUT_MS,
)
from ..tts.unified_provider import UnifiedTTSProvider
from .dave_compat import (
    ensure_dave_compat,
    ensure_opus_loaded,
    inspect_voice_connection_state,
)
from .discord_voice_session import DiscordVoiceSession

logger = logging.getLogger(__name__)


def resolve_ffmpeg_path(configured: str = "ffmpeg") -> str:
    """Resolves a usable FFmpeg executable with system PATH and imageio-ffmpeg fallbacks."""
    target = (configured or "ffmpeg").strip() or "ffmpeg"

    # First try resolving configured command on PATH
    system_ffmpeg = shutil.which(target)
    if system_ffmpeg:
        return system_ffmpeg

    # Check explicit path on disk if path separators were given
    if ("/" in target or "\\" in target) and os.path.isfile(target):
        return target

    # Cross-platform safety: if configured path was e.g. a Windows path on Linux, try PATH 'ffmpeg'
    if target != "ffmpeg":
        default_path_ffmpeg = shutil.which("ffmpeg")
        if default_path_ffmpeg:
            logger.warning(
                "[FFmpeg] Configured path '%s' was not found; falling back to PATH ffmpeg: %s",
                target,
                default_path_ffmpeg,
            )
            return default_path_ffmpeg

    # Fall back to bundled FFmpeg from imageio-ffmpeg wheel
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            logger.info("[FFmpeg] Using bundled imageio-ffmpeg executable: %s", bundled)
            return bundled
    except Exception as err:
        logger.debug("[FFmpeg] Could not load bundled imageio-ffmpeg: %s", err)

    logger.error("[FFmpeg] FFmpeg executable not found (configured='%s')!", target)
    return target

class VoiceSessionError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


class VoiceSessionManager:
    def __init__(
        self,
        tts_provider: Optional[Any] = None,
        espeak_provider: Optional[Any] = None,
        ready_timeout_ms: int = DEFAULT_VOICE_READY_TIMEOUT_MS,
        disconnect_grace_ms: int = DEFAULT_VOICE_DISCONNECT_GRACE_MS,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        ffmpeg_path: str = "ffmpeg",
        options: Optional[dict] = None,
    ):
        opts = options or {}
        self.tts_provider = tts_provider or espeak_provider or UnifiedTTSProvider()
        self.espeak_provider = self.tts_provider  # Backward-compatibility alias
        self.ready_timeout_ms = opts.get(
            "readyTimeoutMs", opts.get("ready_timeout_ms", ready_timeout_ms)
        )
        self.ready_timeout_sec = self.ready_timeout_ms / 1000.0
        self.disconnect_grace_ms = opts.get(
            "disconnectGraceMs", opts.get("disconnect_grace_ms", disconnect_grace_ms)
        )
        self.max_queue_size = opts.get(
            "maxQueueSize", opts.get("max_queue_size", max_queue_size)
                )
        configured_ffmpeg = opts.get(
            "ffmpegPath",
            opts.get("ffmpeg_path", ffmpeg_path)
        )

        self.ffmpeg_path = resolve_ffmpeg_path(configured_ffmpeg)

        self.sessions: Dict[str, DiscordVoiceSession] = {}
        self._guild_locks: Dict[str, asyncio.Lock] = {}

    def _get_guild_lock(self, guild_id: str) -> asyncio.Lock:
        if guild_id not in self._guild_locks:
            self._guild_locks[guild_id] = asyncio.Lock()
        return self._guild_locks[guild_id]

    async def _serialize(self, guild_id: str, coro_func):
        """Helper to run a coroutine with guild serialization."""
        lock = self._get_guild_lock(guild_id)
        async with lock:
            res = coro_func()
            if inspect.isawaitable(res):
                return await res
            return res

    async def join(
        self,
        guild: discord.Guild,
        voice_channel: discord.VoiceChannel,
    ) -> Dict[str, Any]:
        """Joins a voice channel, serializing execution per guild.

        Reuses healthy connections in the same channel, replaces sessions on channel switch.
        """
        guild_id = str(guild.id)
        lock = self._get_guild_lock(guild_id)

        async with lock:
            existing = self.sessions.get(guild_id)

            # Same-channel check
            if existing:
                if str(existing.channel_id) == str(voice_channel.id):
                    vc = getattr(existing, "voice_client", None)
                    # Support mock objects in tests where is_connected may be method or bool or attr
                    is_conn = True
                    if vc is not None:
                        if hasattr(vc, "is_connected"):
                            is_conn = vc.is_connected() if callable(vc.is_connected) else bool(vc.is_connected)
                        elif hasattr(vc, "state"):
                            status = getattr(vc.state, "status", "")
                            is_conn = status != "destroyed"

                    if is_conn and not getattr(existing, "is_destroyed", False):
                        return {"session": existing, "reused": True}

            # Check bot permissions in target voice channel
            me = getattr(guild, "me", None)
            if not me and hasattr(guild, "members"):
                me = getattr(guild.members, "me", None)
            if not me and hasattr(guild, "fetch_member"):
                try:
                    client_user = getattr(guild, "_state", None)
                    if client_user and hasattr(client_user, "user"):
                        me = await guild.fetch_member(client_user.user.id)
                except Exception:
                    pass

            # Check fetchMe fallback for mock test objects
            if not me and hasattr(guild, "members") and hasattr(guild.members, "fetchMe"):
                try:
                    res = guild.members.fetchMe()
                    if inspect.isawaitable(res):
                        me = await res
                    else:
                        me = res
                except Exception:
                    pass

            if not me:
                raise VoiceSessionError(
                    "Bot member could not be resolved in this server.",
                    "BOT_NOT_RESOLVED",
                )

            permissions = None
            if hasattr(voice_channel, "permissions_for"):
                permissions = voice_channel.permissions_for(me)

            if permissions is not None:
                has_connect = getattr(permissions, "connect", True)
                has_speak = getattr(permissions, "speak", True)
                # If permissions object has `has()` method (for test mocks)
                if hasattr(permissions, "has") and callable(permissions.has):
                    has_connect = permissions.has("connect") or permissions.has(1)
                    has_speak = permissions.has("speak") or permissions.has(2)

                if not has_connect:
                    raise VoiceSessionError(
                        "Bot lacks permission to 'Connect' to the voice channel.",
                        "MISSING_CONNECT_PERM",
                    )
                if not has_speak:
                    raise VoiceSessionError(
                        "Bot lacks permission to 'Speak' in the voice channel.",
                        "MISSING_SPEAK_PERM",
                    )

            # Check voice dependencies prior to connecting
            try:
                import discord.voice_client
                if not getattr(discord.voice_client, "has_nacl", False):
                    raise VoiceSessionError(
                        "PyNaCl library needed in order to use voice. Install it via 'pip install \"PyNaCl>=1.5.0,<1.6\"' and restart the bot.",
                        "MISSING_VOICE_DEPENDENCY",
                    )
                if not getattr(discord.voice_client, "has_dave", False):
                    raise VoiceSessionError(
                        "davey library needed in order to use voice. Install it via 'pip install \"davey>=0.1.7,<0.2.0\"' and restart the bot.",
                        "MISSING_VOICE_DEPENDENCY",
                    )
                compat_res = ensure_dave_compat()
                logger.debug("[VoiceSessionManager Guild %s] DAVE compat check: %s", guild_id, compat_res)
                if not ensure_opus_loaded():
                    logger.error(
                        "[VoiceSessionManager Guild %s] Native libopus library is NOT loaded! "
                        "Playback will fail unless libopus0 (Linux) or bundled Opus DLL (Windows) is present.",
                        guild_id,
                    )
            except VoiceSessionError:
                raise
            except Exception as dep_err:
                logger.warning(
                    "[VoiceSessionManager Guild %s] Voice dependency/DAVE check raised warning: %s",
                    guild_id,
                    dep_err,
                    exc_info=True,
                )

            # Determine whether we can seamlessly move an active voice connection
            existing_vc = getattr(existing, "voice_client", None) if existing else getattr(guild, "voice_client", None)
            is_vc_conn = False
            if existing_vc is not None:
                if hasattr(existing_vc, "is_connected"):
                    is_vc_conn = existing_vc.is_connected() if callable(existing_vc.is_connected) else bool(existing_vc.is_connected)
                elif hasattr(existing_vc, "state"):
                    status = getattr(existing_vc.state, "status", "")
                    is_vc_conn = status != "destroyed"

            if existing_vc and is_vc_conn and not getattr(existing, "is_destroyed", False):
                # Seamless channel move via Discord Gateway move_to
                try:
                    if hasattr(existing_vc, "move_to"):
                        res = existing_vc.move_to(voice_channel)
                        if inspect.isawaitable(res):
                            await res
                    if existing:
                        existing.channel_id = str(voice_channel.id)
                        if hasattr(existing, "serial_queue") and hasattr(existing.serial_queue, "clear"):
                            existing.serial_queue.clear()
                        elif hasattr(existing, "queue") and hasattr(existing.queue, "clear"):
                            existing.queue.clear()
                        logger.info(
                            "[VoiceSessionManager Guild %s] Moved active voice session to channel %s.",
                            guild_id,
                            voice_channel.id,
                        )
                        return {"session": existing, "reused": True, "moved": True}
                    voice_client = existing_vc
                except Exception as err:
                    logger.warning(
                        "[VoiceSessionManager Guild %s] Seamless move_to failed: %s. Reconnecting.",
                        guild_id,
                        err,
                    )
                    if hasattr(existing, "destroy_async"):
                        await existing.destroy_async()
                    elif hasattr(existing, "destroy"):
                        existing.destroy()
                    self.sessions.pop(guild_id, None)
                    voice_client = None
            else:
                if existing:
                    if hasattr(existing, "destroy_async"):
                        await existing.destroy_async()
                    elif hasattr(existing, "destroy"):
                        existing.destroy()
                    self.sessions.pop(guild_id, None)
                voice_client = None

            if voice_client is None:
                connect_t0 = time.monotonic()
                try:
                    conn_res = voice_channel.connect(
                        timeout=self.ready_timeout_sec,
                        reconnect=True,
                    )
                    if inspect.isawaitable(conn_res):
                        voice_client = await conn_res
                    else:
                        voice_client = conn_res
                except Exception as err:
                    logger.error(
                        "[VoiceSessionManager Guild %s] Voice connect to channel %s failed: %s",
                        guild_id,
                        voice_channel.id,
                        err,
                        exc_info=True,
                    )
                    raise VoiceSessionError(
                        f"Failed to establish voice connection within {self.ready_timeout_ms}ms: {err}",
                        "CONNECTION_TIMEOUT",
                    ) from err

                conn_ms = (time.monotonic() - connect_t0) * 1000.0
                # Wait briefly for DAVE MLS handshake if running against a live Discord voice connection
                raw_conn = getattr(voice_client, "_connection", None)
                if raw_conn is not None and hasattr(raw_conn, "ws"):
                    wait_deadline = time.monotonic() + 1.5
                    while time.monotonic() < wait_deadline:
                        diag_now = inspect_voice_connection_state(voice_client)
                        if not diag_now:
                            break
                        dave_ver = diag_now.get("dave_protocol_version") or 0
                        if dave_ver == 0 or diag_now.get("can_encrypt") is True:
                            break
                        await asyncio.sleep(0.05)

                diag_after = inspect_voice_connection_state(voice_client)
                logger.info(
                    "[VoiceSessionManager Guild %s] Connected to voice channel %s in %.0fms | FFmpeg='%s' | Transport=%s",
                    guild_id,
                    voice_channel.id,
                    conn_ms,
                    self.ffmpeg_path,
                    diag_after,
                )

            session = DiscordVoiceSession(
                guild_id=guild_id,
                channel_id=str(voice_channel.id),
                voice_client=voice_client,
                tts_provider=self.tts_provider,
                max_queue_size=self.max_queue_size,
                disconnect_grace_ms=self.disconnect_grace_ms,
                ffmpeg_path=self.ffmpeg_path,
                on_disconnect=lambda: self.remove_session(guild_id),
            )

            self.sessions[guild_id] = session
            return {"session": session, "reused": False}

    async def leave(self, guild_id: str) -> bool:
        """Leaves voice channel for a guild, stops player, and purges queue."""
        guild_id_str = str(guild_id)
        lock = self._get_guild_lock(guild_id_str)

        async with lock:
            session = self.sessions.get(guild_id_str)
            if not session:
                return False

            if hasattr(session, "destroy_async"):
                await session.destroy_async()
            elif hasattr(session, "destroy"):
                session.destroy()

            self.sessions.pop(guild_id_str, None)
            return True

    def get_session(self, guild_id: str) -> Optional[DiscordVoiceSession]:
        return self.sessions.get(str(guild_id))

    getSession = get_session

    def has_session(self, guild_id: str) -> bool:
        return str(guild_id) in self.sessions

    hasSession = has_session

    def remove_session(self, guild_id: str) -> None:
        session = self.sessions.pop(str(guild_id), None)
        if session:
            try:
                session.destroy()
            except Exception:
                pass

    removeSession = remove_session

    def destroy_all(self) -> None:
        for session in list(self.sessions.values()):
            try:
                session.destroy()
            except Exception:
                pass
        self.sessions.clear()

    destroyAll = destroy_all

    async def destroy_all_async(self) -> None:
        """Asynchronously cleans up and disconnects all active voice sessions."""
        sessions_to_destroy = list(self.sessions.values())
        self.sessions.clear()
        for session in sessions_to_destroy:
            try:
                if hasattr(session, "destroy_async"):
                    await session.destroy_async()
                elif hasattr(session, "destroy"):
                    session.destroy()
            except Exception as err:
                logger.warning("[VoiceSessionManager] Error during session destroy: %s", err)

    destroyAllAsync = destroy_all_async


__all__ = ["VoiceSessionManager", "VoiceSessionError", "resolve_ffmpeg_path"]
