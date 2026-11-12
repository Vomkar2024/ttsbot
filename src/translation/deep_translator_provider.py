"""Default zero-API-key translator using deep-translator (Google Translate)."""

import asyncio
import logging
import time
from typing import Optional

from .base import (
    HinglishTranslation,
    TranslationError,
    TranslationTimeoutError,
)

from src.storage.settings_store import SettingsStore

logger = logging.getLogger(__name__)


class DeepTranslatorProvider:
    """Translates incoming text into synced English + Hindi using deep-translator.

    To guarantee the spoken Hindi matches the English text shown in Discord,
    this provider produces the canonical English text first (`auto -> en`) and
    then translates that exact English text into Hindi (`en -> hi`).
    """

    provider_name = "deep-translator"

    def __init__(self, timeout: float = 10.0, settings_store: Optional[SettingsStore] = None):
        self.timeout = float(timeout)
        self.settings_store = settings_store
        # Initialize cache table if using SQLite storage
        if self.settings_store:
            self.settings_store.conn.execute(
                "CREATE TABLE IF NOT EXISTS translation_cache (cache_key TEXT PRIMARY KEY, english_text TEXT NOT NULL, hindi_text TEXT NOT NULL, created_at INTEGER)"
            )

    def _get_translation_cache(self, cache_key: str) -> Optional[tuple[str, str]]:
        """Retrieve cached translation from SQLite if available."""
        if not self.settings_store:
            return None
        with self.settings_store._lock:
            cursor = self.settings_store.conn.cursor()
            cursor.execute(
                "SELECT english_text, hindi_text FROM translation_cache WHERE cache_key = ?",
                (cache_key,)
            )
            row = cursor.fetchone()
            if row:
                return (row[0], row[1])
        return None

    def _set_translation_cache(self, cache_key: str, english_text: str, hindi_text: str) -> None:
        """Store translation in cache.

        Uses UPSERT to replace existing entries and keep newest results.
        """
        if not self.settings_store:
            return
        now = int(time.time() * 1000)
        with self.settings_store._lock:
            self.settings_store.conn.execute(
                "INSERT INTO translation_cache (cache_key, english_text, hindi_text, created_at) VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET english_text = excluded.english_text, hindi_text = excluded.hindi_text, created_at = excluded.created_at",
                (cache_key, english_text, hindi_text, now)
            )

    def _translate_sync(self, source_text: str) -> tuple[str, str]:
        """Translate source text to English then Hindi.

        First checks translation cache, then falls back to deep-translator API with retry backoff.
        """
        # Check cache before API call
        cache_key = source_text.strip()
        cached = self._get_translation_cache(cache_key)
        if cached:
            english_text, hindi_text = cached
            logger.debug("[DeepTranslatorProvider] Cache hit for: %s", cache_key[:50])
            return english_text, hindi_text

        # Cache miss - proceed with API call with exponential backoff retry
        max_attempts = 3
        base_delay = 1.0  # seconds
        max_delay = 10.0  # seconds
        last_exception = None
        for attempt in range(max_attempts):
            try:
                from deep_translator import GoogleTranslator
            except ImportError as exc:
                raise TranslationError(
                    "deep-translator is not installed. Run: pip install deep-translator"
                ) from exc

            try:
                english_raw = GoogleTranslator(source="auto", target="en").translate(source_text)
            except Exception as exc:
                last_exception = exc
                if attempt < max_attempts - 1:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "[DeepTranslatorProvider] Retry %d/%d after %.1fs due to: %s",
                        attempt + 1,
                        max_attempts,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                raise TranslationError(f"Failed to translate text to English after retries: {exc}") from exc

            english_text = (english_raw or "").strip()
            if not english_text:
                last_exception = TranslationError("Translation service returned empty English text.")
                if attempt < max_attempts - 1:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "[DeepTranslatorProvider] Retry %d/%d after %.1fs due to empty response",
                        attempt + 1,
                        max_attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise TranslationError("Translation service returned empty English text after retries.")

            # Derive Hindi directly from the canonical English output
            try:
                hindi_raw = GoogleTranslator(source="en", target="hi").translate(english_text)
            except Exception as exc:
                last_exception = exc
                if attempt < max_attempts - 1:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "[DeepTranslatorProvider] Retry %d/%d after %.1fs Hindi translation: %s",
                        attempt + 1,
                        max_attempts,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                    continue
                raise TranslationError(f"Failed to translate English text to Hindi after retries: {exc}") from exc

            hindi_text = (hindi_raw or "").strip()
            if not hindi_text:
                last_exception = TranslationError("Translation service returned empty Hindi text.")
                if attempt < max_attempts - 1:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    logger.warning(
                        "[DeepTranslatorProvider] Retry %d/%d after %.1fs due to empty Hindi response",
                        attempt + 1,
                        max_attempts,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                raise TranslationError("Translation service returned empty Hindi text after retries.")

            # Cache the successful translation
            self._set_translation_cache(cache_key, english_text, hindi_text)

            return english_text, hindi_text
        # Should never reach here if max_attempts >= 1
        raise TranslationError(f"Translation failed after {max_attempts} attempts. Last error: {last_exception}") if last_exception else TranslationError("Translation failed after retries")

    async def translate_to_english_and_hindi(
        self,
        text: str,
        *,
        timeout: Optional[float] = None,
    ) -> HinglishTranslation:
        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Cannot translate empty or whitespace-only text.")

        cleaned_source = text.strip()
        effective_timeout = float(timeout if timeout is not None else self.timeout)

        try:
            english_text, hindi_text = await asyncio.wait_for(
                asyncio.to_thread(self._translate_sync, cleaned_source),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError as exc:
            logger.warning(
                "[DeepTranslatorProvider] Translation timed out after %.1fs",
                effective_timeout,
            )
            raise TranslationTimeoutError(
                f"Translation request timed out after {effective_timeout:.1f}s"
            ) from exc
        except TranslationError:
            raise
        except Exception as exc:
            raise TranslationError(f"Unexpected translation failure: {exc}") from exc

        return HinglishTranslation(
            source_text=cleaned_source,
            english_text=english_text,
            hindi_text=hindi_text,
            provider=self.provider_name,
        )


__all__ = ["DeepTranslatorProvider"]