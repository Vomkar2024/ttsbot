"""Configuration loader for Discord TTS Bot."""

import base64
import os
from pathlib import Path
from typing import Mapping, Optional
from dotenv import load_dotenv

from .constants import (
    DEFAULT_DISCORD_MAX_FILE_BYTES,
    DEFAULT_EDGE_TTS_PITCH,
    DEFAULT_EDGE_TTS_RATE,
    DEFAULT_EDGE_TTS_TIMEOUT_SEC,
    DEFAULT_EDGE_TTS_VOICE,
    DEFAULT_LANGUAGE,
    DEFAULT_TRANSLATION_TIMEOUT_SEC,
    DEFAULT_TRANSLATOR_PROVIDER,
    DEFAULT_TTS_TIMEOUT_SEC,
    DEFAULT_VOICE,
    DEFAULT_TTS_ENGINE,
    DEFAULT_SPEAKER_COOLDOWN_SEC,
    DEFAULT_MAX_QUEUE_SIZE,
    DEFAULT_MAX_MESSAGE_CHARS,
    DEFAULT_VOICE_READY_TIMEOUT_MS,
    DEFAULT_VOICE_DISCONNECT_GRACE_MS,
)
from .translation.openai_translator_provider import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
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
        edge_tts_voice: str = DEFAULT_EDGE_TTS_VOICE,
        edge_tts_rate: str = DEFAULT_EDGE_TTS_RATE,
        edge_tts_pitch: str = DEFAULT_EDGE_TTS_PITCH,
        edge_tts_timeout_sec: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC,
        auto_git_pull: bool = False,
        auto_git_pull_branch: str = "main",
        auto_git_pull_pip: bool = True,
        auto_git_pull_timeout_sec: int = 60,
        translator_provider: str = DEFAULT_TRANSLATOR_PROVIDER,
        translation_timeout_sec: float = DEFAULT_TRANSLATION_TIMEOUT_SEC,
        tts_timeout_sec: float = DEFAULT_TTS_TIMEOUT_SEC,
        max_audio_file_bytes: int = DEFAULT_DISCORD_MAX_FILE_BYTES,
        openai_api_key: str = "",
        openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
        openai_model: str = DEFAULT_OPENAI_MODEL,
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
        self.EDGE_TTS_VOICE = edge_tts_voice
        self.EDGE_TTS_RATE = edge_tts_rate
        self.EDGE_TTS_PITCH = edge_tts_pitch
        self.EDGE_TTS_TIMEOUT_SEC = edge_tts_timeout_sec
        self.AUTO_GIT_PULL = auto_git_pull
        self.AUTO_GIT_PULL_BRANCH = auto_git_pull_branch
        self.AUTO_GIT_PULL_PIP = auto_git_pull_pip
        self.AUTO_GIT_PULL_TIMEOUT_SEC = auto_git_pull_timeout_sec
        self.TRANSLATOR_PROVIDER = translator_provider
        self.TRANSLATION_TIMEOUT_SEC = translation_timeout_sec
        self.TTS_TIMEOUT_SEC = tts_timeout_sec
        self.MAX_AUDIO_FILE_BYTES = max_audio_file_bytes
        self.OPENAI_API_KEY = openai_api_key
        self.OPENAI_BASE_URL = openai_base_url
        self.OPENAI_MODEL = openai_model

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


def _parse_num(val: Optional[str], default_val: int, min_val: int = 1) -> int:
    cleaned = _clean_str(val)
    if not cleaned:
        return default_val
    try:
        num = int(cleaned)
        return num if num >= min_val else default_val
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
    if raw_guild_id and not raw_guild_id.isdigit():
        raw_guild_id = ""

    raw_tts_engine = _clean_str(source.get("TTS_ENGINE")).lower() or DEFAULT_TTS_ENGINE
    raw_tts_bin = _clean_str(source.get("TTS_BIN")) or "espeak-ng"
    raw_ffmpeg_path = _clean_str(source.get("FFMPEG_PATH")) or "ffmpeg"

    raw_db_path = _clean_str(source.get("DATABASE_PATH")) or "./data/tts.sqlite"
    if raw_db_path == ":memory:":
        db_path = ":memory:"
    else:
        db_path = str(Path(raw_db_path).resolve())

    speak_usernames = _parse_bool(source.get("SPEAK_USERNAMES"), True)
    speaker_cooldown_sec = _parse_num(
        source.get("SPEAKER_COOLDOWN_SEC"),
        DEFAULT_SPEAKER_COOLDOWN_SEC,
        min_val=0,
    )
    max_queue_size = _parse_num(source.get("MAX_QUEUE_SIZE"), DEFAULT_MAX_QUEUE_SIZE, min_val=1)
    max_message_chars = _parse_num(source.get("MAX_MESSAGE_CHARS"), DEFAULT_MAX_MESSAGE_CHARS, min_val=1)
    voice_ready_timeout_ms = _parse_num(
        source.get("VOICE_READY_TIMEOUT_MS"),
        DEFAULT_VOICE_READY_TIMEOUT_MS,
        min_val=100,
    )
    voice_disconnect_grace_ms = _parse_num(
        source.get("VOICE_DISCONNECT_GRACE_MS"),
        DEFAULT_VOICE_DISCONNECT_GRACE_MS,
        min_val=0,
    )
    announce_links = _parse_bool(source.get("ANNOUNCE_LINKS"), True)
    gtts_timeout_sec = _parse_float(source.get("GTTS_TIMEOUT_SEC"), 5.0)
    edge_tts_voice = _clean_str(source.get("EDGE_TTS_VOICE")) or DEFAULT_EDGE_TTS_VOICE
    edge_tts_rate = _clean_str(source.get("EDGE_TTS_RATE")) or DEFAULT_EDGE_TTS_RATE
    edge_tts_pitch = _clean_str(source.get("EDGE_TTS_PITCH")) or DEFAULT_EDGE_TTS_PITCH
    edge_tts_timeout_sec = _parse_float(
        source.get("EDGE_TTS_TIMEOUT_SEC"), DEFAULT_EDGE_TTS_TIMEOUT_SEC
    )
    auto_git_pull = _parse_bool(source.get("AUTO_GIT_PULL"), False)
    auto_git_pull_branch = _clean_str(source.get("AUTO_GIT_PULL_BRANCH")) or "main"
    auto_git_pull_pip = _parse_bool(source.get("AUTO_GIT_PULL_PIP"), True)
    auto_git_pull_timeout_sec = _parse_num(
        source.get("AUTO_GIT_PULL_TIMEOUT_SEC"), 60, min_val=5
    )
    translator_provider = (
        _clean_str(source.get("TRANSLATOR_PROVIDER")).lower() or DEFAULT_TRANSLATOR_PROVIDER
    )
    translation_timeout_sec = _parse_float(
        source.get("TRANSLATION_TIMEOUT_SEC"), DEFAULT_TRANSLATION_TIMEOUT_SEC
    )
    tts_timeout_sec = _parse_float(source.get("TTS_TIMEOUT_SEC"), DEFAULT_TTS_TIMEOUT_SEC)
    max_audio_file_bytes = _parse_num(
        source.get("MAX_AUDIO_FILE_BYTES"),
        DEFAULT_DISCORD_MAX_FILE_BYTES,
        min_val=1024,
    )
    openai_api_key = _clean_str(source.get("OPENAI_API_KEY"))
    openai_base_url = _clean_str(source.get("OPENAI_BASE_URL")) or DEFAULT_OPENAI_BASE_URL
    openai_model = _clean_str(source.get("OPENAI_MODEL")) or DEFAULT_OPENAI_MODEL

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
        edge_tts_voice=edge_tts_voice,
        edge_tts_rate=edge_tts_rate,
        edge_tts_pitch=edge_tts_pitch,
        edge_tts_timeout_sec=edge_tts_timeout_sec,
        auto_git_pull=auto_git_pull,
        auto_git_pull_branch=auto_git_pull_branch,
        auto_git_pull_pip=auto_git_pull_pip,
        auto_git_pull_timeout_sec=auto_git_pull_timeout_sec,
        translator_provider=translator_provider,
        translation_timeout_sec=translation_timeout_sec,
        tts_timeout_sec=tts_timeout_sec,
        max_audio_file_bytes=max_audio_file_bytes,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        openai_model=openai_model,
    )


default_config = load_config()

