"""Voice session manager handling per-guild concurrency serialization, channel moves, and lifecycle."""

import asyncio
import logging
from typing import Any, Dict, Optional, Tuple

import discord

from ..constants import (
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_VOICE_DISCONNECT_GRACE_MS,
    DEFAULT_VOICE_READY_TIMEOUT_MS,
)
from ..tts.espeak_provider import EspeakProvider
from .discord_voice_session import DiscordVoiceSession

logger = logging.getLogger(__name__)


class VoiceSessionError(Exception):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.message = message
        self.code = code


class VoiceSessionManager:
    def __init__(
        self,
        espeak_provider: Optional[EspeakProvider] = None,
        ready_timeout_ms: int = DEFAULT_VOICE_READY_TIMEOUT_MS,
        disconnect_grace_ms: int = DEFAULT_VOICE_DISCONNECT_GRACE_MS,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        ffmpeg_path: str = "ffmpeg",
        options: Optional[dict] = None,
    ):
        opts = options or {}
        self.espeak_provider = espeak_provider or EspeakProvider()
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
        self.ffmpeg_path = opts.get("ffmpegPath", opts.get("ffmpeg_path", ffmpeg_path))

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
            if asyncio.iscoroutine(res):
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

                # Channel move or stale connection: destroy old session
                if hasattr(existing, "destroy_async"):
                    await existing.destroy_async()
                elif hasattr(existing, "destroy"):
                    existing.destroy()
                self.sessions.pop(guild_id, None)

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
                    if asyncio.iscoroutine(res):
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

            # Connect to voice channel
            try:
                voice_client = await voice_channel.connect(
                    timeout=self.ready_timeout_sec,
                    reconnect=True,
                )
            except Exception as err:
                raise VoiceSessionError(
                    f"Failed to establish voice connection within {self.ready_timeout_ms}ms: {err}",
                    "CONNECTION_TIMEOUT",
                ) from err

            session = DiscordVoiceSession(
                guild_id=guild_id,
                channel_id=str(voice_channel.id),
                voice_client=voice_client,
                espeak_provider=self.espeak_provider,
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


__all__ = ["VoiceSessionManager", "VoiceSessionError"]
