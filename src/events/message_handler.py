"""Message create event handler filtering and enqueueing TTS audio jobs."""

import logging
from typing import Any, Dict, Optional

import discord

from ..storage.settings_store import SettingsStore
from ..tts.format_message import format_message_for_tts
from ..voice.serial_queue import QueueFullError
from ..voice.session_manager import VoiceSessionManager

logger = logging.getLogger(__name__)


async def handle_message_create(
    message: Any,
    context: Optional[Dict[str, Any]] = None,
    *,
    session_manager: Optional[VoiceSessionManager] = None,
    settings_store: Optional[SettingsStore] = None,
    config: Optional[Any] = None,
) -> bool:
    """Validates and processes incoming messages for TTS enqueueing.

    Returns True if the message was enqueued, False otherwise.
    """
    ctx = context or {}
    sm = session_manager or ctx.get("sessionManager", ctx.get("session_manager"))
    ss = settings_store or ctx.get("settingsStore", ctx.get("settings_store"))
    cfg = config or ctx.get("config")

    # 1. Ignore bot messages, webhooks, and system messages
    if not message:
        return False

    author = getattr(message, "author", None)
    if isinstance(message, dict):
        author = message.get("author")
        if author and author.get("bot"):
            return False
        if message.get("webhookId") or message.get("webhook_id"):
            return False
        if message.get("system"):
            return False
    else:
        if author and getattr(author, "bot", False):
            return False
        if getattr(message, "webhook_id", None) or getattr(message, "webhookId", None):
            return False
        if getattr(message, "system", False):
            return False

    # 2. Ignore Direct Messages (only server messages)
    guild = getattr(message, "guild", None)
    if isinstance(message, dict):
        guild = message.get("guild")
    if not guild:
        return False

    guild_id = str(guild.get("id") if isinstance(guild, dict) else guild.id)

    # 3. Check if bot has an active voice connection in this server
    if not sm:
        return False
    session = sm.get_session(guild_id) if hasattr(sm, "get_session") else sm.getSession(guild_id)
    if not session:
        return False

    # 4. Verify message came from the active voice channel's text chat OR the configured TTS channel
    channel = getattr(message, "channel", None)
    if isinstance(message, dict):
        channel = message.get("channel")
    channel_id = str(channel.get("id") if isinstance(channel, dict) else getattr(channel, "id", ""))

    session_channel_id = str(
        getattr(session, "channel_id", getattr(session, "channelId", "")) or ""
    )
    is_voice_channel_chat = bool(session_channel_id and channel_id == session_channel_id)

    is_configured = False
    if ss:
        is_configured = (
            ss.is_configured_channel(guild_id, channel_id)
            if hasattr(ss, "is_configured_channel")
            else ss.isConfiguredChannel(guild_id, channel_id)
        )

    if not is_voice_channel_chat and not is_configured:
        return False

    # 5. Clean, format, and truncate message text
    speak_usernames = getattr(cfg, "SPEAK_USERNAMES", True) if cfg else True
    max_chars = getattr(cfg, "MAX_MESSAGE_CHARS", 800) if cfg else 800

    if isinstance(cfg, dict):
        speak_usernames = cfg.get("SPEAK_USERNAMES", True)
        max_chars = cfg.get("MAX_MESSAGE_CHARS", 800)

    formatted = format_message_for_tts(
        message,
        speak_usernames=speak_usernames,
        max_chars=max_chars,
    )

    if not formatted:
        return False

    # 6. Resolve personal vs server speech preferences
    author_id = str(
        author.get("id") if isinstance(author, dict) else getattr(author, "id", "")
    )
    prefs = (
        (
            ss.resolve_preferences(guild_id, author_id)
            if hasattr(ss, "resolve_preferences")
            else ss.resolvePreferences(guild_id, author_id)
        )
        if ss
        else {"language": "en", "voice": "default"}
    )
    language = prefs.get("language", "en")
    voice = prefs.get("voice", "default")

    # 7. Enqueue in the guild's serial queue
    sq = getattr(session, "serial_queue", getattr(session, "serialQueue", None))
    if not sq:
        return False

    try:
        sq.enqueue(
            {
                "spokenText": formatted.spoken_text,
                "spoken_text": formatted.spoken_text,
                "text": formatted.cleaned_text,
                "authorName": formatted.author_name,
                "author_name": formatted.author_name,
                "userId": author_id,
                "user_id": author_id,
                "language": language,
                "voice": voice,
            }
        )
        return True
    except QueueFullError:
        logger.warning(
            "[MessageCreate Guild %s] Queue full (%s). Dropped message from %s.",
            guild_id,
            getattr(cfg, "MAX_QUEUE_SIZE", 50) if cfg else 50,
            formatted.author_name,
        )
        return False
    except Exception as err:
        logger.error("[MessageCreate Guild %s] Enqueue error: %s", guild_id, err)
        return False


__all__ = ["handle_message_create"]

