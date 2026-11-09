"""Discord message formatting and entity sanitization for Text-to-Speech."""

import re
from typing import Any, Dict, Optional, Union
from ..constants import DEFAULT_MAX_MESSAGE_CHARS

# Regex patterns for Discord Markdown & Entity Sanitization
URL_REGEX = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
CUSTOM_EMOJI_REGEX = re.compile(r"<a?:[a-zA-Z0-9_]+:\d+>")
USER_MENTION_REGEX = re.compile(r"<@!?\d+>")
ROLE_MENTION_REGEX = re.compile(r"<@&\d+>")
CHANNEL_MENTION_REGEX = re.compile(r"<#\d+>")
CONTROL_CHARS_REGEX = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
WHITESPACE_REGEX = re.compile(r"\s+")


def clean_discord_text(text: Optional[str], max_chars: int = DEFAULT_MAX_MESSAGE_CHARS) -> str:
    """Sanitizes Discord message text by stripping URLs, emojis, mentions, and control chars.

    Truncates output to max_chars.
    """
    if not text or not isinstance(text, str):
        return ""

    cleaned = URL_REGEX.sub("", text)
    cleaned = CUSTOM_EMOJI_REGEX.sub("", cleaned)
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


class FormattedTTSMessage:
    def __init__(self, cleaned_text: str, spoken_text: str, author_name: str):
        self.cleaned_text = cleaned_text
        self.spoken_text = spoken_text
        self.author_name = author_name
        # JS property aliases
        self.cleanedText = cleaned_text
        self.spokenText = spoken_text
        self.authorName = author_name

    def to_dict(self) -> Dict[str, str]:
        return {
            "cleaned_text": self.cleaned_text,
            "spoken_text": self.spoken_text,
            "author_name": self.author_name,
            "cleanedText": self.cleaned_text,
            "spokenText": self.spoken_text,
            "authorName": self.author_name,
        }

    def __getitem__(self, item: str) -> str:
        return getattr(self, item)


def format_message_for_tts(
    message: Any,
    options: Optional[Dict[str, Any]] = None,
    *,
    speak_usernames: Optional[bool] = None,
    max_chars: Optional[int] = None,
) -> Optional[FormattedTTSMessage]:
    """Formats a Discord message into TTS-ready text.

    Prefixes the speaker display name unless speak_usernames is False.
    """
    opts = options or {}
    if max_chars is None:
        max_chars = opts.get("maxChars", opts.get("max_chars", DEFAULT_MAX_MESSAGE_CHARS))
    if speak_usernames is None:
        speak_usernames = opts.get("speakUsernames", opts.get("speak_usernames", True))

    content = ""
    if isinstance(message, dict):
        content = message.get("content", "")
    elif hasattr(message, "content"):
        content = message.content

    cleaned_text = clean_discord_text(content, max_chars)
    if not cleaned_text:
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
        # discord.Message or mock object
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

    spoken_text = f"{author_name} says: {cleaned_text}" if speak_usernames else cleaned_text

    return FormattedTTSMessage(
        cleaned_text=cleaned_text,
        spoken_text=spoken_text,
        author_name=author_name,
    )


# Alias camelCase for JS parity
formatMessageForTTS = format_message_for_tts

__all__ = ["clean_discord_text", "cleanDiscordText", "format_message_for_tts", "formatMessageForTTS", "FormattedTTSMessage"]

