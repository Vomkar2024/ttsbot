"""Persistent SQLite storage for Discord server and user TTS preferences."""

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ..constants import DEFAULT_LANGUAGE, DEFAULT_VOICE


class SettingsStore:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._lock = threading.Lock()

        if db_path != ":memory:":
            db_dir = Path(db_path).parent
            db_dir.mkdir(parents=True, exist_ok=True)

        self.conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,  # autocommit mode
        )
        self.conn.row_factory = sqlite3.Row
        if db_path != ":memory:":
            try:
                self.conn.execute("PRAGMA journal_mode = WAL;")
                self.conn.execute("PRAGMA synchronous = NORMAL;")
                self.conn.execute("PRAGMA busy_timeout = 5000;")
            except Exception:
                pass
        self._init_tables()

    def _init_tables(self) -> None:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.executescript(
                """
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    channel_id TEXT,
                    default_language TEXT,
                    default_voice TEXT,
                    updated_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    guild_id TEXT,
                    user_id TEXT,
                    language TEXT,
                    voice TEXT,
                    updated_at INTEGER,
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS hinglish_settings (
                    guild_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    channel_id TEXT,
                    delivery_mode TEXT NOT NULL DEFAULT 'auto',
                    updated_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS translation_cache (
                    cache_key TEXT PRIMARY KEY,
                    english_text TEXT NOT NULL,
                    hindi_text TEXT NOT NULL,
                    created_at INTEGER
                );
                """
            )

    def set_configured_channel(self, guild_id: str, channel_id: Optional[str]) -> None:
        now = int(time.time() * 1000)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO guild_settings (guild_id, channel_id, default_language, default_voice, updated_at)
                VALUES (?, ?, NULL, NULL, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    channel_id = excluded.channel_id,
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), str(channel_id) if channel_id else None, now),
            )

    # Alias camelCase for JS compatibility if needed
    setConfiguredChannel = set_configured_channel

    def get_configured_channel(self, guild_id: str) -> Optional[str]:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT channel_id FROM guild_settings WHERE guild_id = ?",
                (str(guild_id),),
            )
            row = cursor.fetchone()
            if row and row["channel_id"]:
                return str(row["channel_id"])
            return None

    getConfiguredChannel = get_configured_channel

    def is_configured_channel(self, guild_id: str, channel_id: str) -> bool:
        if not guild_id or not channel_id:
            return False
        configured = self.get_configured_channel(guild_id)
        return bool(configured and configured == str(channel_id))

    isConfiguredChannel = is_configured_channel

    def set_guild_defaults(
        self,
        guild_id: str,
        defaults: Optional[Dict[str, Optional[str]]] = None,
        *,
        language: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> None:
        if defaults:
            if "language" in defaults:
                language = defaults["language"]
            if "voice" in defaults:
                voice = defaults["voice"]

        now = int(time.time() * 1000)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO guild_settings (guild_id, channel_id, default_language, default_voice, updated_at)
                VALUES (?, NULL, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    default_language = coalesce(excluded.default_language, guild_settings.default_language),
                    default_voice = coalesce(excluded.default_voice, guild_settings.default_voice),
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), language, voice, now),
            )

    setGuildDefaults = set_guild_defaults

    def get_guild_settings(self, guild_id: str) -> Optional[Dict[str, Optional[str]]]:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT channel_id, default_language, default_voice FROM guild_settings WHERE guild_id = ?",
                (str(guild_id),),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "channelId": row["channel_id"],
                "defaultLanguage": row["default_language"],
                "defaultVoice": row["default_voice"],
                "channel_id": row["channel_id"],
                "default_language": row["default_language"],
                "default_voice": row["default_voice"],
            }

    getGuildSettings = get_guild_settings

    def set_user_preferences(
        self,
        guild_id: str,
        user_id: str,
        prefs: Optional[Dict[str, Optional[str]]] = None,
        *,
        language: Optional[str] = None,
        voice: Optional[str] = None,
    ) -> None:
        if prefs:
            if "language" in prefs:
                language = prefs["language"]
            if "voice" in prefs:
                voice = prefs["voice"]

        now = int(time.time() * 1000)
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO user_settings (guild_id, user_id, language, voice, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    language = coalesce(excluded.language, user_settings.language),
                    voice = coalesce(excluded.voice, user_settings.voice),
                    updated_at = excluded.updated_at
                """,
                (str(guild_id), str(user_id), language, voice, now),
            )

    setUserPreferences = set_user_preferences

    def get_user_preferences(self, guild_id: str, user_id: str) -> Optional[Dict[str, Optional[str]]]:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT language, voice FROM user_settings WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {
                "language": row["language"],
                "voice": row["voice"],
            }

    getUserPreferences = get_user_preferences

    def clear_user_preferences(self, guild_id: str, user_id: str) -> None:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "DELETE FROM user_settings WHERE guild_id = ? AND user_id = ?",
                (str(guild_id), str(user_id)),
            )

    clearUserPreferences = clear_user_preferences

    def resolve_preferences(self, guild_id: str, user_id: str) -> Dict[str, str]:
        user = self.get_user_preferences(guild_id, user_id)
        guild = self.get_guild_settings(guild_id)

        user_lang = user.get("language") if user else None
        guild_lang = guild.get("defaultLanguage") if guild else None
        language = user_lang or guild_lang or DEFAULT_LANGUAGE

        user_voice = user.get("voice") if user else None
        guild_voice = guild.get("defaultVoice") if guild else None
        voice = user_voice or guild_voice or DEFAULT_VOICE

        return {"language": language, "voice": voice}

    resolvePreferences = resolve_preferences

    def set_hinglish_mode(
        self,
        guild_id: str,
        enabled: bool,
        *,
        channel_id: Optional[str] = None,
        delivery_mode: str = "auto",
    ) -> None:
        now = int(time.time() * 1000)
        mode_clean = (delivery_mode or "auto").strip().lower()
        if mode_clean not in ("auto", "voice", "file"):
            mode_clean = "auto"
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                """
                INSERT INTO hinglish_settings (guild_id, enabled, channel_id, delivery_mode, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    channel_id = coalesce(excluded.channel_id, hinglish_settings.channel_id),
                    delivery_mode = excluded.delivery_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    str(guild_id),
                    1 if enabled else 0,
                    str(channel_id) if channel_id else None,
                    mode_clean,
                    now,
                ),
            )

    def get_hinglish_mode(self, guild_id: str) -> Dict[str, Any]:
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT enabled, channel_id, delivery_mode FROM hinglish_settings WHERE guild_id = ?",
                (str(guild_id),),
            )
            row = cursor.fetchone()
            if not row:
                return {"enabled": False, "channel_id": None, "delivery_mode": "auto"}
            return {
                "enabled": bool(row["enabled"]),
                "channel_id": str(row["channel_id"]) if row["channel_id"] else None,
                "delivery_mode": str(row["delivery_mode"] or "auto"),
            }

    def is_hinglish_enabled(self, guild_id: str, channel_id: Optional[str] = None) -> bool:
        settings = self.get_hinglish_mode(guild_id)
        if not settings["enabled"]:
            return False
        configured_channel_id = settings["channel_id"]
        if configured_channel_id and channel_id is not None:
            return configured_channel_id == str(channel_id)
        return True

    def get_cached_translation(
        self,
        cache_key: str,
        *,
        max_age_ms: Optional[int] = None,
    ) -> Optional[tuple[str, str]]:
        if not cache_key:
            return None
        with self._lock:
            cursor = self.conn.cursor()
            cursor.execute(
                "SELECT english_text, hindi_text, created_at FROM translation_cache WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            if max_age_ms is not None and row["created_at"] is not None:
                now = int(time.time() * 1000)
                if now - int(row["created_at"]) > max_age_ms:
                    return None
            return (str(row["english_text"]), str(row["hindi_text"]))

    def set_cached_translation(
        self,
        cache_key: str,
        english_text: str,
        hindi_text: str,
    ) -> None:
        if not cache_key or not english_text or not hindi_text:
            return
        now = int(time.time() * 1000)
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO translation_cache (cache_key, english_text, hindi_text, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    english_text = excluded.english_text,
                    hindi_text = excluded.hindi_text,
                    created_at = excluded.created_at
                """,
                (cache_key, english_text, hindi_text, now),
            )

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.close()
            except Exception:
                pass


__all__ = ["SettingsStore"]
