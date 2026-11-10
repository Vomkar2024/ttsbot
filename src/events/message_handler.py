"""Message create event handler filtering and enqueueing TTS audio jobs."""

import logging
from typing import Any, Dict, Optional

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

    channel = getattr(message, "channel", None)
    if isinstance(message, dict):
        channel = message.get("channel")
    channel_id = str(channel.get("id") if isinstance(channel, dict) else getattr(channel, "id", ""))

    # 2.5. Ignore command invocations (e.g. messages starting with '!' or '/')
    content_raw = (
        getattr(message, "content", "")
        if not isinstance(message, dict)
        else message.get("content", "")
    )
    if content_raw and content_raw.strip().startswith(("!", "/")):
        return False

    # 2.6. If Hinglish mode is enabled for this guild/channel, render English text
    # in the channel and deliver corresponding Hindi audio (voice or attached file).
    hinglish_service = ctx.get("hinglish_service") or ctx.get("hinglishService")
    is_hinglish_fn = getattr(ss, "is_hinglish_enabled", None) if ss else None
    if hinglish_service and callable(is_hinglish_fn) and is_hinglish_fn(guild_id, channel_id):
        hinglish_cfg = ss.get_hinglish_mode(guild_id) if hasattr(ss, "get_hinglish_mode") else {}
        delivery_mode = hinglish_cfg.get("delivery_mode", "auto")

        # If Hinglish mode was enabled without a channel restriction, still scope to
        # the configured TTS channel or active voice chat if available.
        if not hinglish_cfg.get("channel_id"):
            active_sess = (
                sm.get_session(guild_id) if (sm and hasattr(sm, "get_session")) else None
            )
            sess_ch_id = str(getattr(active_sess, "channel_id", "") or "") if active_sess else ""
            is_vc_chat = bool(sess_ch_id and channel_id == sess_ch_id)
            is_cfg_ch = bool(
                ss and hasattr(ss, "is_configured_channel") and ss.is_configured_channel(guild_id, channel_id)
            )
            if not is_vc_chat and not is_cfg_ch:
                return False

        member_obj = message.get("member") if isinstance(message, dict) else getattr(message, "member", None)
        author_id = str(author.get("id") if isinstance(author, dict) else getattr(author, "id", ""))
        author_name = (
            getattr(member_obj, "display_name", None)
            or getattr(author, "display_name", None)
            or getattr(author, "name", None)
            or (member_obj.get("displayName") if isinstance(member_obj, dict) else None)
            or (author.get("username") if isinstance(author, dict) else None)
            or "User"
        )

        result = await hinglish_service.process_message(
            content_raw,
            guild=guild,
            member=member_obj or author,
            text_channel=channel,
            author_name=author_name,
            author_id=author_id,
            delivery_mode=delivery_mode,
        )
        if result.error_code == "EMPTY_TEXT":
            return False

        send_fn = getattr(channel, "send", None) if channel else None
        if callable(send_fn):
            await hinglish_service.send_result(send_fn, result, author_name=author_name)
        return result.ok

    # 3. Check if bot has an active voice connection in this server
    if not sm:
        return False
    session = sm.get_session(guild_id) if hasattr(sm, "get_session") else sm.getSession(guild_id)
    if not session:
        logger.info(
            "[MessageCreate Guild %s] Ignored message from %s: bot is not in a voice channel. Run /join first.",
            guild_id,
            getattr(author, "display_name", getattr(author, "name", author)),
        )
        return False

    # 4. Verify message came from the active voice channel's text chat OR the configured TTS channel
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
        logger.info(
            "[MessageCreate Guild %s] Ignored message from %s in channel #%s (ID: %s). "
            "It is neither the active voice channel text chat (%s) nor configured with /setup.",
            guild_id,
            getattr(author, "display_name", getattr(author, "name", author)),
            getattr(channel, "name", channel_id),
            channel_id,
            session_channel_id or "none",
        )
        return False

    # Resolve author ID
    author_id = str(
        author.get("id") if isinstance(author, dict) else getattr(author, "id", "")
    )

    # 4.6. Check speaker cooldown (suppress repetitive 'Alice says:' within cooldown window)
    tracker = ctx.get("speaker_cooldown_tracker") or ctx.get("speakerCooldownTracker")
    should_announce = True
    if tracker and hasattr(tracker, "should_announce_name"):
        should_announce = tracker.should_announce_name(guild_id, author_id)

    # 5. Clean, format, and truncate message text
    speak_usernames = getattr(cfg, "SPEAK_USERNAMES", True) if cfg else True
    max_chars = getattr(cfg, "MAX_MESSAGE_CHARS", 800) if cfg else 800
    announce_links = getattr(cfg, "ANNOUNCE_LINKS", True) if cfg else True

    if isinstance(cfg, dict):
        speak_usernames = cfg.get("SPEAK_USERNAMES", True)
        max_chars = cfg.get("MAX_MESSAGE_CHARS", 800)
        announce_links = cfg.get("ANNOUNCE_LINKS", True)

    formatted = format_message_for_tts(
        message,
        speak_usernames=speak_usernames,
        should_announce_name=should_announce,
        max_chars=max_chars,
        announce_links=announce_links,
    )

    if not formatted:
        content = (
            getattr(message, "content", "")
            if not isinstance(message, dict)
            else message.get("content", "")
        )
        if not content and not (
            getattr(message, "clean_content", "") if not isinstance(message, dict) else ""
        ):
            logger.warning(
                "[MessageCreate Guild %s] Message content is empty! "
                "Ensure 'Message Content Intent' is enabled in the Discord Developer Portal under Bot -> Privileged Gateway Intents.",
                guild_id,
            )
        else:
            logger.info(
                "[MessageCreate Guild %s] Message from %s cleaned down to empty text (e.g. standalone emoji/URL) and was skipped.",
                guild_id,
                getattr(author, "display_name", getattr(author, "name", author)),
            )
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
        logger.info(
            "[Stage 1/6: Text Received] [MessageCreate Guild %s] Enqueued speech "
            "(channel=#%s/%s, author=%s, chars=%d, lang=%s, voice=%s): \"%s\"",
            guild_id,
            getattr(channel, "name", channel_id),
            channel_id,
            formatted.author_name,
            len(formatted.spoken_text),
            language,
            voice,
            formatted.spoken_text,
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

