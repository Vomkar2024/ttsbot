"""Configuration loader for VoiceBridge TTS Bot."""

import os
from pathlib import Path
from typing import Mapping, Optional
from dotenv import load_dotenv

from .constants import (
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
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
        tts_bin: str = "espeak-ng",
        ffmpeg_path: str = "ffmpeg",
        database_path: str = "./data/voicebridge.sqlite",
        speak_usernames: bool = True,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        max_message_chars: int = DEFAULT_MAX_MESSAGE_CHARS,
        voice_ready_timeout_ms: int = DEFAULT_VOICE_READY_TIMEOUT_MS,
        voice_disconnect_grace_ms: int = DEFAULT_VOICE_DISCONNECT_GRACE_MS,
        default_language: str = DEFAULT_LANGUAGE,
        default_voice: str = DEFAULT_VOICE,
    ):
        self.DISCORD_TOKEN = discord_token
        self.DISCORD_CLIENT_ID = discord_client_id
        self.DISCORD_GUILD_ID = discord_guild_id
        self.TTS_BIN = tts_bin
        self.FFMPEG_PATH = ffmpeg_path
        self.DATABASE_PATH = database_path
        self.SPEAK_USERNAMES = speak_usernames
        self.MAX_QUEUE_SIZE = max_queue_size
        self.MAX_MESSAGE_CHARS = max_message_chars
        self.VOICE_READY_TIMEOUT_MS = voice_ready_timeout_ms
        self.VOICE_DISCONNECT_GRACE_MS = voice_disconnect_grace_ms
        self.DEFAULT_LANGUAGE = default_language
        self.DEFAULT_VOICE = default_voice

    @property
    def voice_ready_timeout_sec(self) -> float:
        return self.VOICE_READY_TIMEOUT_MS / 1000.0

    @property
    def voice_disconnect_grace_sec(self) -> float:
        return self.VOICE_DISCONNECT_GRACE_MS / 1000.0


def _parse_bool(val: Optional[str], default_val: bool = True) -> bool:
    if val is None or str(val).strip() == "":
        return default_val
    return str(val).strip().lower() not in ("false", "0", "no", "off")


def _parse_num(val: Optional[str], default_val: int) -> int:
    if val is None:
        return default_val
    try:
        num = int(str(val).strip())
        return num if num > 0 else default_val
    except ValueError:
        return default_val


def load_config(env: Optional[Mapping[str, str]] = None) -> Config:
    source = os.environ if env is None else env

    raw_token = source.get("DISCORD_TOKEN", "").strip()
    raw_client_id = source.get("DISCORD_CLIENT_ID", "") or source.get("CLIENT_ID", "")
    raw_client_id = raw_client_id.strip()
    raw_guild_id = source.get("DISCORD_GUILD_ID", "").strip()

    raw_tts_bin = source.get("TTS_BIN", "espeak-ng").strip() or "espeak-ng"
    raw_ffmpeg_path = source.get("FFMPEG_PATH", "ffmpeg").strip() or "ffmpeg"

    raw_db_path = source.get("DATABASE_PATH", "./data/voicebridge.sqlite").strip() or "./data/voicebridge.sqlite"
    if raw_db_path == ":memory:":
        db_path = ":memory:"
    else:
        db_path = str(Path(raw_db_path).resolve())

    speak_usernames = _parse_bool(source.get("SPEAK_USERNAMES"), True)
    max_queue_size = _parse_num(source.get("MAX_QUEUE_SIZE"), DEFAULT_MAX_QUEUE_SIZE)
    max_message_chars = _parse_num(source.get("MAX_MESSAGE_CHARS"), DEFAULT_MAX_MESSAGE_CHARS)
    voice_ready_timeout_ms = _parse_num(source.get("VOICE_READY_TIMEOUT_MS"), DEFAULT_VOICE_READY_TIMEOUT_MS)
    voice_disconnect_grace_ms = _parse_num(source.get("VOICE_DISCONNECT_GRACE_MS"), DEFAULT_VOICE_DISCONNECT_GRACE_MS)

    return Config(
        discord_token=raw_token,
        discord_client_id=raw_client_id,
        discord_guild_id=raw_guild_id,
        tts_bin=raw_tts_bin,
        ffmpeg_path=raw_ffmpeg_path,
        database_path=db_path,
        speak_usernames=speak_usernames,
        max_queue_size=max_queue_size,
        max_message_chars=max_message_chars,
        voice_ready_timeout_ms=voice_ready_timeout_ms,
        voice_disconnect_grace_ms=voice_disconnect_grace_ms,
        default_language=DEFAULT_LANGUAGE,
        default_voice=DEFAULT_VOICE,
    )


default_config = load_config()

