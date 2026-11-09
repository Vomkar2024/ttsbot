"""Discord message formatting, entity sanitization, and chat intelligence for TTS.

Implements the text processing pipeline from Discord-TTS/Bot (process_msg.rs):
- Link detection & contextual descriptions
- Attachment filetype identification
- Chat acronym expansion
- Spam repeated character deduplication
- Custom emoji readability
- 60-second speaker announcement cooldown
- Forwarded message handling
"""

import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..constants import (
    ACRONYMS,
    ATTACHMENT_EXTENSIONS,
    DEFAULT_MAX_MESSAGE_CHARS,
    DEFAULT_SPEAKER_COOLDOWN_SEC,
)

# Regex patterns for Discord Markdown & Entity Sanitization
URL_REGEX = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
CUSTOM_EMOJI_CAPTURE_REGEX = re.compile(r"<(a)?:([a-zA-Z0-9_]+):\d+>")
CUSTOM_EMOJI_STRIP_REGEX = re.compile(r"<a?:[a-zA-Z0-9_]+:\d+>")
USER_MENTION_REGEX = re.compile(r"<@!?\d+>")
ROLE_MENTION_REGEX = re.compile(r"<@&\d+>")
CHANNEL_MENTION_REGEX = re.compile(r"<#\d+>")
CONTROL_CHARS_REGEX = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
WHITESPACE_REGEX = re.compile(r"\s+")


def make_emoji_readable(text: str) -> str:
    """Converts custom emojis (<:blob:123> -> 'emoji blob', <a:party:123> -> 'animated emoji party')."""
    if not text:
        return ""

    def _replace_emoji(match: re.Match) -> str:
        is_animated = bool(match.group(1))
        name = match.group(2)
        prefix = "animated emoji" if is_animated else "emoji"
        return f"{prefix} {name}"

    return CUSTOM_EMOJI_CAPTURE_REGEX.sub(_replace_emoji, text)


TRAILING_PUNCT = ".,!?:;\"'()"

def parse_acronyms(text: str) -> str:
    """Expands common internet and chat abbreviations into natural spoken English."""
    if not text:
        return ""

    words = text.split(" ")
    replaced = []
    for word in words:
        lower_raw = word.lower()
        if lower_raw in ACRONYMS:
            replaced.append(ACRONYMS[lower_raw])
            continue

        clean_word = lower_raw.strip(TRAILING_PUNCT)
        if clean_word in ACRONYMS:
            expansion = ACRONYMS[clean_word]
            # Preserve original trailing punctuation
            punct = word[len(word.rstrip(TRAILING_PUNCT)):]
            replaced.append(expansion + punct)
        else:
            replaced.append(word)

    return " ".join(replaced)


def remove_repeated_chars(text: str, limit: int = 3) -> str:
    """Truncates runs of the same character beyond `limit` to prevent TTS audio spam (e.g. looooool -> loool)."""
    if not text or limit <= 0:
        return text

    pattern = re.compile(r"(.)\1{" + str(limit) + r",}")
    return pattern.sub(r"\1" * limit, text)


def attachments_to_format(attachments: Optional[List[Any]]) -> Optional[str]:
    """Identifies file formats of message attachments for spoken context."""
    if not attachments:
        return None

    if len(attachments) >= 2:
        return "multiple files"

    att = attachments[0]
    filename = ""
    if isinstance(att, str):
        filename = att
    elif isinstance(att, dict):
        filename = att.get("filename", "")
    elif hasattr(att, "filename"):
        filename = att.filename

    if not filename or "." not in filename:
        return "a file"

    ext = filename.rsplit(".", 1)[-1].lower()
    for extensions, label in ATTACHMENT_EXTENSIONS.items():
        if ext in extensions:
            return label

    return "a file"


def clean_discord_text(
    text: Optional[str],
    max_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
    strip_emojis: bool = True,
) -> str:
    """Sanitizes Discord message text by stripping URLs, mentions, and control chars.

    Preserves backward compatibility with legacy caller tests.
    """
    if not text or not isinstance(text, str):
        return ""

    cleaned = URL_REGEX.sub("", text)
    if strip_emojis:
        cleaned = CUSTOM_EMOJI_STRIP_REGEX.sub("", cleaned)
    else:
        cleaned = make_emoji_readable(cleaned)
    cleaned = USER_MENTION_REGEX.sub("", cleaned)
    cleaned = ROLE_MENTION_REGEX.sub("", cleaned)
    cleaned = CHANNEL_MENTION_REGEX.sub("", cleaned)
    cleaned = CONTROL_CHARS_REGEX.sub("", cleaned)
    cleaned = WHITESPACE_REGEX.sub(" ", cleaned).strip()

    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].strip() + "..."

    return cleaned


# Alias camelCase for JS parity
cleanDiscordText = clean_discord_text


class SpeakerCooldownTracker:
    """Tracks last announced speaker per guild to implement Discord-TTS/Bot's 60-second cooldown."""

    def __init__(self, cooldown_sec: int = DEFAULT_SPEAKER_COOLDOWN_SEC):
        self.cooldown_sec = cooldown_sec
        self._guild_speakers: Dict[str, Tuple[str, float]] = {}

    def should_announce_name(self, guild_id: str, author_id: str) -> bool:
        """Returns True if the speaker's name should be announced.

        Suppresses redundant name repeats if the same user spoke within cooldown_sec.
        """
        now = time.monotonic()
        last_info = self._guild_speakers.get(guild_id)

        if last_info:
            last_author_id, last_time = last_info
            if last_author_id == author_id and (now - last_time) < self.cooldown_sec:
                # Same author within cooldown: do not announce name again
                return False

        # Different author or cooldown expired: announce name and refresh tracker
        self._guild_speakers[guild_id] = (author_id, now)
        return True

    def reset_guild(self, guild_id: str) -> None:
        self._guild_speakers.pop(str(guild_id), None)


class FormattedTTSMessage:
    def __init__(
        self,
        cleaned_text: str,
        spoken_text: str,
        author_name: str,
        contained_url: bool = False,
        attached_format: Optional[str] = None,
        is_forward: bool = False,
    ):
        self.cleaned_text = cleaned_text
        self.spoken_text = spoken_text
        self.author_name = author_name
        self.contained_url = contained_url
        self.attached_format = attached_format
        self.is_forward = is_forward

        # JS property aliases
        self.cleanedText = cleaned_text
        self.spokenText = spoken_text
        self.authorName = author_name
        self.containedUrl = contained_url
        self.attachedFormat = attached_format
        self.isForward = is_forward

    _EXCLUDED_KEYS = {"to_dict", "keys", "values", "items", "get"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cleaned_text": self.cleaned_text,
            "spoken_text": self.spoken_text,
            "author_name": self.author_name,
            "contained_url": self.contained_url,
            "attached_format": self.attached_format,
            "is_forward": self.is_forward,
            "cleanedText": self.cleaned_text,
            "spokenText": self.spoken_text,
            "authorName": self.author_name,
        }

    def __contains__(self, key: Any) -> bool:
        if not isinstance(key, str) or key.startswith("_") or key in self._EXCLUDED_KEYS:
            return False
        return hasattr(self, key) and not callable(getattr(self, key, None))

    def __getitem__(self, item: str) -> Any:
        if not isinstance(item, str) or item not in self:
            raise KeyError(item)
        return getattr(self, item)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default) if key in self else default

    def keys(self) -> List[str]:
        return list(self.to_dict().keys())

    def values(self) -> List[Any]:
        return list(self.to_dict().values())

    def items(self) -> List[Tuple[str, Any]]:
        return list(self.to_dict().items())


def format_message_for_tts(
    message: Any,
    options: Optional[Dict[str, Any]] = None,
    *,
    speak_usernames: Optional[bool] = None,
    should_announce_name: Optional[bool] = None,
    max_chars: Optional[int] = None,
    announce_links: Optional[bool] = None,
    expand_acronyms: bool = True,
    limit_repeated_chars: int = 3,
) -> Optional[FormattedTTSMessage]:
    """Formats a Discord message into natural TTS speech using Discord-TTS/Bot rules."""
    opts = options or {}
    if max_chars is None:
        max_chars = opts.get("maxChars", opts.get("max_chars", DEFAULT_MAX_MESSAGE_CHARS))
    if speak_usernames is None:
        speak_usernames = opts.get("speakUsernames", opts.get("speak_usernames", True))
    if should_announce_name is None:
        should_announce_name = opts.get("shouldAnnounceName", opts.get("should_announce_name", speak_usernames))
    if announce_links is None:
        announce_links = opts.get("announceLinks", opts.get("announce_links", True))

    # Resolve message content
    raw_content = ""
    is_forward = False
    attachments: List[Any] = []

    if isinstance(message, dict):
        raw_content = message.get("content", "")
        attachments = message.get("attachments", []) or []
        ref = message.get("message_reference") or message.get("reference")
        if ref and (ref.get("type") == 1 or ref.get("kind") == "forward"):
            is_forward = True
    else:
        # Check for forwarded message snapshot
        ref = getattr(message, "message_reference", None)
        if ref and getattr(ref, "type", None) == 1:
            is_forward = True

        snapshots = getattr(message, "message_snapshots", None)
        if is_forward and snapshots and len(snapshots) > 0:
            snapshot = snapshots[0]
            raw_content = getattr(snapshot, "content", "")
            attachments = getattr(snapshot, "attachments", []) or []
        else:
            raw_content = getattr(message, "content", "")
            attachments = getattr(message, "attachments", []) or []

    # Detect URLs before stripping
    has_urls = bool(URL_REGEX.search(raw_content))

    # Single question mark handling (matching Discord-TTS/Bot: ? -> what)
    if raw_content.strip() == "?":
        processed_text = "what"
        has_urls = False
    else:
        # Convert custom emojis to spoken labels
        processed_text = make_emoji_readable(raw_content)

        # Strip URLs from speech stream
        processed_text = URL_REGEX.sub("", processed_text)

        # Expand chat acronyms (in English)
        if expand_acronyms:
            processed_text = parse_acronyms(processed_text)

        # Strip user, role, channel mentions & control characters
        processed_text = USER_MENTION_REGEX.sub("", processed_text)
        processed_text = ROLE_MENTION_REGEX.sub("", processed_text)
        processed_text = CHANNEL_MENTION_REGEX.sub("", processed_text)
        processed_text = CONTROL_CHARS_REGEX.sub("", processed_text)
        processed_text = WHITESPACE_REGEX.sub(" ", processed_text).strip()

        # Deduplicate repeated characters
        if limit_repeated_chars > 0:
            processed_text = remove_repeated_chars(processed_text, limit_repeated_chars)

    # Check for attached file formats
    attached_format = attachments_to_format(attachments)

    # Truncate text if exceeding limit
    if len(processed_text) > max_chars:
        processed_text = processed_text[:max_chars].strip() + "..."

    # If there is no text, no attachment, and either no URL or announce_links is disabled: return None
    if not processed_text and not attached_format and (not has_urls or not announce_links):
        return None

    # Resolve author name
    author_name = "User"
    if isinstance(message, dict):
        member = message.get("member")
        author = message.get("author")
        if isinstance(member, dict) and member.get("displayName"):
            author_name = member["displayName"]
        elif isinstance(member, dict) and member.get("display_name"):
            author_name = member["display_name"]
        elif isinstance(author, dict) and author.get("displayName"):
            author_name = author["displayName"]
        elif isinstance(author, dict) and author.get("display_name"):
            author_name = author["display_name"]
        elif isinstance(author, dict) and author.get("username"):
            author_name = author["username"]
        elif isinstance(author, dict) and author.get("name"):
            author_name = author["name"]
    else:
        member = getattr(message, "member", None)
        author = getattr(message, "author", None)
        if member and getattr(member, "display_name", None):
            author_name = member.display_name
        elif member and getattr(member, "displayName", None):
            author_name = member.displayName
        elif author and getattr(author, "display_name", None):
            author_name = author.display_name
        elif author and getattr(author, "displayName", None):
            author_name = author.displayName
        elif author and getattr(author, "name", None):
            author_name = author.name
        elif author and getattr(author, "username", None):
            author_name = author.username

    # Decide if author name should be announced
    said_name = author_name if (speak_usernames and should_announce_name) else None

    # Format speech matching Discord-TTS/Bot process_msg.rs state table
    spoken_text = _build_spoken_text(
        said_name=said_name,
        msg=processed_text,
        has_url=has_urls,
        attached_format=attached_format,
        is_forward=is_forward,
    )

    if not spoken_text.strip():
        return None

    return FormattedTTSMessage(
        cleaned_text=processed_text,
        spoken_text=spoken_text,
        author_name=author_name,
        contained_url=has_urls,
        attached_format=attached_format,
        is_forward=is_forward,
    )


def _build_spoken_text(
    said_name: Optional[str],
    msg: str,
    has_url: bool,
    attached_format: Optional[str],
    is_forward: bool,
) -> str:
    """Implements the full speech synthesis formatting table from Discord-TTS/Bot."""
    msg = msg.strip()

    if said_name:
        if not msg:
            if has_url and attached_format:
                return (
                    f"{said_name} forwarded a message with a link and {attached_format}"
                    if is_forward
                    else f"{said_name} sent a link and attached {attached_format}"
                )
            if has_url:
                return (
                    f"{said_name} forwarded a message with a link"
                    if is_forward
                    else f"{said_name} sent a link"
                )
            if attached_format:
                return (
                    f"{said_name} forwarded a message with {attached_format}"
                    if is_forward
                    else f"{said_name} sent {attached_format}"
                )
            return (
                f"{said_name} forwarded a message"
                if is_forward
                else f"{said_name} sent a message"
            )
        else:
            if has_url and attached_format:
                return (
                    f"{said_name} forwarded a message with a link, {attached_format}, and that says {msg}"
                    if is_forward
                    else f"{said_name} sent a link, attached {attached_format}, and said {msg}"
                )
            if has_url:
                return (
                    f"{said_name} forwarded a message with a link that says {msg}"
                    if is_forward
                    else f"{said_name} sent a link and said {msg}"
                )
            if attached_format:
                return (
                    f"{said_name} forwarded a message with {attached_format} that says {msg}"
                    if is_forward
                    else f"{said_name} sent {attached_format} and said {msg}"
                )
            return (
                f"{said_name} forwarded a message that says {msg}"
                if is_forward
                else f"{said_name} says: {msg}"
            )
    else:
        # Speaker name suppressed or disabled
        if not msg:
            if has_url and attached_format:
                return (
                    f"forwarded message with a link and {attached_format}"
                    if is_forward
                    else f"a link and {attached_format}"
                )
            if has_url:
                return "forwarded message with a link" if is_forward else "a link"
            if attached_format:
                return (
                    f"forwarded message with {attached_format}"
                    if is_forward
                    else attached_format
                )
            return "forwarded message" if is_forward else ""
        else:
            if has_url and attached_format:
                return (
                    f"forwarded message that says {msg} with {attached_format} and a link"
                    if is_forward
                    else f"{msg} with {attached_format} and a link"
                )
            if has_url:
                return (
                    f"forwarded message that says {msg} with a link"
                    if is_forward
                    else f"{msg} with a link"
                )
            if attached_format:
                return (
                    f"forwarded message that says {msg} with {attached_format}"
                    if is_forward
                    else f"{msg} with {attached_format}"
                )
            return f"forwarded message that says {msg}" if is_forward else msg


# Alias camelCase
formatMessageForTTS = format_message_for_tts

__all__ = [
    "clean_discord_text",
    "cleanDiscordText",
    "format_message_for_tts",
    "formatMessageForTTS",
    "FormattedTTSMessage",
    "SpeakerCooldownTracker",
    "make_emoji_readable",
    "parse_acronyms",
    "remove_repeated_chars",
    "attachments_to_format",
]
