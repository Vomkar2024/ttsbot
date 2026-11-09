"""Configuration loader for Discord TTS Bot."""

import base64
import os
from pathlib import Path
from typing import Mapping, Optional
from dotenv import load_dotenv

from .constants import (
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
    DEFAULT_TTS_ENGINE,
    DEFAULT_SPEAKER_COOLDOWN_SEC,
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_MAX_MESSAGE_CHARS,
    DEFAULT_VOICE_READY_TIMEOUT_MS,
    DEFAULT_VOICE_DISCONNECT_GRACE_MS,
)

load_dotenv()


class Config:
    def __init__(
        self,
        discord_token: str = "",
        discord_client_id: str = "",
        discord_guild_id: str = "",
        tts_engine: str = DEFAULT_TTS_ENGINE,
        tts_bin: str = "espeak-ng",
        ffmpeg_path: str = "ffmpeg",
        database_path: str = "./data/tts.sqlite",
        speak_usernames: bool = True,
        speaker_cooldown_sec: int = DEFAULT_SPEAKER_COOLDOWN_SEC,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
        voice_ready_timeout_ms: int = DEFAULT_VOICE_READY_TIMEOUT_MS,
        voice_disconnect_grace_ms: int = DEFAULT_VOICE_DISCONNECT_GRACE_MS,
        default_language: str = DEFAULT_LANGUAGE,
        default_voice: str = DEFAULT_VOICE,
        announce_links: bool = True,
        gtts_timeout_sec: float = 5.0,
    ):
        self.DISCORD_TOKEN = discord_token
        self.DISCORD_CLIENT_ID = discord_client_id
        self.DISCORD_GUILD_ID = discord_guild_id
        self.TTS_ENGINE = tts_engine
        self.TTS_BIN = tts_bin
        self.FFMPEG_PATH = ffmpeg_path
        self.DATABASE_PATH = database_path
        self.SPEAK_USERNAMES = speak_usernames
        self.SPEAKER_COOLDOWN_SEC = speaker_cooldown_sec
        self.MAX_QUEUE_SIZE = max_queue_size
        self.MAX_MESSAGE_CHARS = max_message_chars
        self.VOICE_READY_TIMEOUT_MS = voice_ready_timeout_ms
        self.VOICE_DISCONNECT_GRACE_MS = voice_disconnect_grace_ms
        self.DEFAULT_LANGUAGE = default_language
        self.DEFAULT_VOICE = default_voice
        self.ANNOUNCE_LINKS = announce_links
        self.GTTS_TIMEOUT_SEC = gtts_timeout_sec

    @property
    def voice_ready_timeout_sec(self) -> float:
        return self.VOICE_READY_TIMEOUT_MS / 1000.0

    @property
    def voice_disconnect_grace_sec(self) -> float:
        return self.VOICE_DISCONNECT_GRACE_MS / 1000.0


def _clean_str(val: Optional[str]) -> str:
    if val is None:
        return ""
    s = str(val).strip(" \t\r\n")
    while len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
        s = s[1:-1].strip(" \t\r\n")
    return s


def _extract_app_id_from_token(token: str) -> str:
    """Extract client/app ID from the base64-encoded prefix of a Discord token."""
    parts = token.split(".")
    if not parts or not parts[0]:
        return ""
    prefix = parts[0]
    rem = len(prefix) % 4
    if rem:
        prefix += "=" * (4 - rem)
    try:
        decoded = base64.b64decode(prefix).decode("utf-8")
        if decoded.isdigit():
            return decoded
    except Exception:
        pass
    return ""


def _parse_bool(val: Optional[str], default_val: bool = True) -> bool:
    cleaned = _clean_str(val)
    if not cleaned:
        return default_val
    return cleaned.lower() not in ("false", "0", "no", "off")


def _parse_num(val: Optional[str], default_val: int) -> int:
    cleaned = _clean_str(val)
    if not cleaned:
        return default_val
    try:
        num = int(cleaned)
        return num if num > 0 else default_val
    except ValueError:
        return default_val


def _parse_float(val: Optional[str], default_val: float) -> float:
    cleaned = _clean_str(val)
    if not cleaned:
        return default_val
    try:
        num = float(cleaned)
        return num if num > 0 else default_val
    except ValueError:
        return default_val


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    source = os.environ if env is None else env

    raw_token = _clean_str(source.get("DISCORD_TOKEN"))
    if raw_token.lower().startswith("bot "):
        raw_token = raw_token[4:].strip(" \t\r\n")
    elif raw_token.lower().startswith("bearer "):
        raw_token = raw_token[7:].strip(" \t\r\n")
    raw_client_id = _clean_str(source.get("DISCORD_CLIENT_ID") or source.get("CLIENT_ID"))
    if not raw_client_id and raw_token:
        raw_client_id = _extract_app_id_from_token(raw_token)

    raw_guild_id = _clean_str(source.get("DISCORD_GUILD_ID"))

    raw_tts_engine = _clean_str(source.get("TTS_ENGINE")).lower() or DEFAULT_TTS_ENGINE
    raw_tts_bin = _clean_str(source.get("TTS_BIN")) or "espeak-ng"
    raw_ffmpeg_path = _clean_str(source.get("FFMPEG_PATH")) or "ffmpeg"

    raw_db_path = _clean_str(source.get("DATABASE_PATH")) or "./data/tts.sqlite"
    if raw_db_path == ":memory:":
        db_path = ":memory:"
    else:
        db_path = str(Path(raw_db_path).resolve())

    speak_usernames = _parse_bool(source.get("SPEAK_USERNAMES"), True)
    speaker_cooldown_sec = _parse_num(source.get("SPEAKER_COOLDOWN_SEC"), DEFAULT_SPEAKER_COOLDOWN_SEC)
    max_queue_size = _parse_num(source.get("MAX_QUEUE_SIZE"), DEFAULT_MAX_QUEUE_SIZE)
    max_message_chars = _parse_num(source.get("MAX_MESSAGE_CHARS"), DEFAULT_MAX_MESSAGE_CHARS)
    voice_ready_timeout_ms = _parse_num(source.get("VOICE_READY_TIMEOUT_MS"), DEFAULT_VOICE_READY_TIMEOUT_MS)
    voice_disconnect_grace_ms = _parse_num(source.get("VOICE_DISCONNECT_GRACE_MS"), DEFAULT_VOICE_DISCONNECT_GRACE_MS)
    announce_links = _parse_bool(source.get("ANNOUNCE_LINKS"), True)
    gtts_timeout_sec = _parse_float(source.get("GTTS_TIMEOUT_SEC"), 5.0)

    return Config(
        discord_token=raw_token,
        discord_client_id=raw_client_id,
        discord_guild_id=raw_guild_id,
        tts_engine=raw_tts_engine,
        tts_bin=raw_tts_bin,
        ffmpeg_path=raw_ffmpeg_path,
        database_path=db_path,
        speak_usernames=speak_usernames,
        speaker_cooldown_sec=speaker_cooldown_sec,
        max_queue_size=max_queue_size,
        max_message_chars=max_message_chars,
        voice_ready_timeout_ms=voice_ready_timeout_ms,
        voice_disconnect_grace_ms=voice_disconnect_grace_ms,
        default_language=DEFAULT_LANGUAGE,
        default_voice=DEFAULT_VOICE,
        announce_links=announce_links,
        gtts_timeout_sec=gtts_timeout_sec,
    )


default_config = load_config()

