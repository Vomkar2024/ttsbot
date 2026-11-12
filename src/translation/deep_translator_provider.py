"""Default zero-API-key translator using deep-translator with caching, pacing, and fallbacks."""

import asyncio
from collections import OrderedDict
import logging
import threading
import time
from typing import List, Optional

from .base import (
    HinglishTranslation,
    TranslationError,
    TranslationTimeoutError,
)
from src.storage.settings_store import SettingsStore

logger = logging.getLogger(__name__)

_GTX_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
_GTX_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Detects HTTP 429 / TooManyRequests errors from translator libraries or HTTP responses."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return (
        "toomanyrequests" in name
        or "too many requests" in msg
        or "429" in msg
        or "rate limit" in msg
    )


def _chunk_text(text: str, max_chars: int) -> List[str]:
    """Splits text into chunks no longer than `max_chars` along whitespace boundaries."""
    cleaned = (text or "").strip()
    if len(cleaned) <= max_chars:
        return [cleaned] if cleaned else []

    chunks: List[str] = []
    remaining = cleaned
    while len(remaining) > max_chars:
        split_at = remaining.rfind(" ", 0, max_chars + 1)
        if split_at <= 0:
            split_at = max_chars
        part = remaining[:split_at].strip()
        if part:
            chunks.append(part)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


class DeepTranslatorProvider:
    """Translates incoming text into synced English + Hindi without requiring an API key.

    To guarantee the spoken Hindi matches the English text shown in Discord,
    this provider produces the canonical English text first (`auto -> en`) and
    then translates that exact English text into Hindi (`en -> hi`).

    Mitigations against Google Translate rate-limiting (HTTP 429):
    1. Two-tier caching (in-memory LRU + persistent SQLite via `SettingsStore`).
    2. Serialized request pacing (`min_request_interval_sec`) to stay under 5 req/sec.
    3. Decoupled step execution so a failure in `en -> hi` never re-runs `auto -> en`.
    4. Cooldown circuit breaker + automatic fallback chain:
       - Primary: `deep_translator.GoogleTranslator` (`translate.google.com/m`)
       - Fallback 1: Google `translate.googleapis.com/translate_a/single?client=gtx`
       - Fallback 2: `deep_translator.MyMemoryTranslator`
    """

    provider_name = "deep-translator"

    def __init__(
        self,
        timeout: float = 10.0,
        settings_store: Optional[SettingsStore] = None,
        *,
        min_request_interval_sec: float = 0.25,
        cooldown_sec: float = 60.0,
        max_memory_cache: int = 512,
    ):
        self.timeout = float(timeout)
        self.settings_store = settings_store
        self.min_request_interval_sec = max(0.0, float(min_request_interval_sec))
        self.cooldown_sec = max(1.0, float(cooldown_sec))
        self.max_memory_cache = max(1, int(max_memory_cache))

        self._cache_lock = threading.Lock()
        self._memory_cache: "OrderedDict[str, tuple[str, str]]" = OrderedDict()

        self._request_lock = threading.Lock()
        self._last_request_ts = 0.0
        self._google_web_cooldown_until = 0.0
        self._gtx_cooldown_until = 0.0
        self._mymemory_cooldown_until = 0.0

        # Ensure cache table exists if a SettingsStore was passed directly
        if self.settings_store and hasattr(self.settings_store, "conn"):
            lock = getattr(self.settings_store, "_lock", None)
            if lock is not None:
                with lock:
                    self.settings_store.conn.execute(
                        "CREATE TABLE IF NOT EXISTS translation_cache "
                        "(cache_key TEXT PRIMARY KEY, english_text TEXT NOT NULL, "
                        "hindi_text TEXT NOT NULL, created_at INTEGER)"
                    )
            else:
                self.settings_store.conn.execute(
                    "CREATE TABLE IF NOT EXISTS translation_cache "
                    "(cache_key TEXT PRIMARY KEY, english_text TEXT NOT NULL, "
                    "hindi_text TEXT NOT NULL, created_at INTEGER)"
                )

    async def is_available(self) -> bool:
        """Returns True if deep_translator or requests is importable."""
        try:
            import deep_translator  # noqa: F401

            return True
        except ImportError:
            try:
                import requests  # noqa: F401

                return True
            except ImportError:
                return False

    @staticmethod
    def _normalize_cache_key(text: str) -> str:
        return " ".join((text or "").split())

    def _get_translation_cache(self, cache_key: str) -> Optional[tuple[str, str]]:
        """Retrieve cached translation from memory or SQLite if available."""
        normalized_key = self._normalize_cache_key(cache_key)
        if not normalized_key:
            return None

        with self._cache_lock:
            cached = self._memory_cache.get(normalized_key)
            if cached is not None:
                self._memory_cache.move_to_end(normalized_key)
                return cached

        if not self.settings_store:
            return None

        if hasattr(self.settings_store, "get_cached_translation"):
            row = self.settings_store.get_cached_translation(normalized_key)
        else:
            with self.settings_store._lock:
                cursor = self.settings_store.conn.cursor()
                cursor.execute(
                    "SELECT english_text, hindi_text FROM translation_cache WHERE cache_key = ?",
                    (normalized_key,),
                )
                fetched = cursor.fetchone()
                row = (fetched[0], fetched[1]) if fetched else None

        if row:
            with self._cache_lock:
                self._memory_cache[normalized_key] = row
                self._memory_cache.move_to_end(normalized_key)
                while len(self._memory_cache) > self.max_memory_cache:
                    self._memory_cache.popitem(last=False)
            return row
        return None

    def _set_translation_cache(self, cache_key: str, english_text: str, hindi_text: str) -> None:
        """Store translation in in-memory LRU and SQLite cache."""
        normalized_key = self._normalize_cache_key(cache_key)
        if not normalized_key or not english_text or not hindi_text:
            return

        with self._cache_lock:
            self._memory_cache[normalized_key] = (english_text, hindi_text)
            self._memory_cache.move_to_end(normalized_key)
            while len(self._memory_cache) > self.max_memory_cache:
                self._memory_cache.popitem(last=False)

        if not self.settings_store:
            return

        if hasattr(self.settings_store, "set_cached_translation"):
            self.settings_store.set_cached_translation(normalized_key, english_text, hindi_text)
        else:
            now = int(time.time() * 1000)
            with self.settings_store._lock:
                self.settings_store.conn.execute(
                    "INSERT INTO translation_cache (cache_key, english_text, hindi_text, created_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(cache_key) DO UPDATE SET "
                    "english_text = excluded.english_text, hindi_text = excluded.hindi_text, "
                    "created_at = excluded.created_at",
                    (normalized_key, english_text, hindi_text, now),
                )

    def _pace_request(self, deadline: Optional[float] = None) -> None:
        """Enforces minimum spacing between outgoing translation requests."""
        if self.min_request_interval_sec <= 0:
            return
        with self._request_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_ts
            wait_sec = self.min_request_interval_sec - elapsed
            if wait_sec > 0:
                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0:
                        return
                    wait_sec = min(wait_sec, max(0.0, remaining * 0.5))
                if wait_sec > 0:
                    time.sleep(wait_sec)
            self._last_request_ts = time.monotonic()

    def _translate_via_google_web(
        self,
        text: str,
        source: str,
        target: str,
        *,
        deadline: Optional[float] = None,
    ) -> tuple[Optional[str], Optional[Exception]]:
        """Primary backend: deep_translator.GoogleTranslator (translate.google.com/m)."""
        if time.monotonic() < self._google_web_cooldown_until:
            return None, None

        try:
            from deep_translator import GoogleTranslator
        except ImportError as exc:
            return None, exc

        last_exc: Optional[Exception] = None
        for attempt in range(2):
            if deadline is not None and time.monotonic() >= deadline:
                break
            self._pace_request(deadline=deadline)
            try:
                translated = GoogleTranslator(source=source, target=target).translate(text)
                cleaned = (translated or "").strip()
                if cleaned:
                    return cleaned, None
                last_exc = TranslationError(
                    f"GoogleTranslator returned empty text for {source}->{target}."
                )
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit_error(exc):
                    self._google_web_cooldown_until = time.monotonic() + self.cooldown_sec
                    logger.warning(
                        "[DeepTranslatorProvider] GoogleTranslator hit 429 rate limit; "
                        "entering %.0fs cooldown and switching to fallback: %s",
                        self.cooldown_sec,
                        exc,
                    )
                    return None, exc

            if attempt == 0:
                delay = 0.25
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.35:
                        break
                    delay = min(delay, remaining * 0.3)
                time.sleep(delay)

        return None, last_exc

    def _translate_via_google_gtx(
        self,
        text: str,
        source: str,
        target: str,
        *,
        deadline: Optional[float] = None,
    ) -> tuple[Optional[str], Optional[Exception]]:
        """Fallback 1: Google translate.googleapis.com/translate_a/single (client=gtx)."""
        if time.monotonic() < self._gtx_cooldown_until:
            return None, None

        try:
            import requests
        except ImportError as exc:
            return None, exc

        chunks = _chunk_text(text, 4500)
        if not chunks:
            return None, None

        translated_parts: List[str] = []
        last_exc: Optional[Exception] = None

        for chunk in chunks:
            part_text: Optional[str] = None
            for attempt in range(2):
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    break
                self._pace_request(deadline=deadline)
                req_timeout = 5.0
                if deadline is not None:
                    req_timeout = max(0.5, min(5.0, deadline - time.monotonic()))
                try:
                    resp = requests.get(
                        _GTX_ENDPOINT,
                        params={
                            "client": "gtx",
                            "sl": source,
                            "tl": target,
                            "dt": "t",
                            "q": chunk,
                        },
                        headers={"User-Agent": _GTX_USER_AGENT},
                        timeout=req_timeout,
                    )
                    if resp.status_code == 429:
                        self._gtx_cooldown_until = time.monotonic() + self.cooldown_sec
                        err = TranslationError("Google GTX endpoint returned HTTP 429 rate limit.")
                        logger.warning(
                            "[DeepTranslatorProvider] Google GTX hit 429 rate limit; "
                            "entering %.0fs cooldown.",
                            self.cooldown_sec,
                        )
                        return None, err
                    if resp.status_code >= 400:
                        last_exc = TranslationError(
                            f"Google GTX endpoint returned HTTP {resp.status_code}"
                        )
                    else:
                        data = resp.json()
                        segments = data[0] if isinstance(data, list) and data else []
                        joined = "".join(
                            str(seg[0])
                            for seg in segments
                            if isinstance(seg, list) and seg and seg[0]
                        ).strip()
                        if joined:
                            part_text = joined
                            break
                        last_exc = TranslationError("Google GTX endpoint returned empty text.")
                except Exception as exc:
                    last_exc = exc
                    if _is_rate_limit_error(exc):
                        self._gtx_cooldown_until = time.monotonic() + self.cooldown_sec
                        return None, exc

                if attempt == 0:
                    delay = 0.25
                    if deadline is not None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0.35:
                            break
                        delay = min(delay, remaining * 0.3)
                    time.sleep(delay)

            if not part_text:
                return None, last_exc
            translated_parts.append(part_text)

        joined_all = " ".join(translated_parts).strip()
        return (joined_all, None) if joined_all else (None, last_exc)

    def _translate_via_mymemory(
        self,
        text: str,
        source: str,
        target: str,
        *,
        deadline: Optional[float] = None,
    ) -> tuple[Optional[str], Optional[Exception]]:
        """Fallback 2: deep_translator.MyMemoryTranslator (explicit language pairs)."""
        if time.monotonic() < self._mymemory_cooldown_until:
            return None, None

        try:
            from deep_translator import MyMemoryTranslator
        except ImportError as exc:
            return None, exc

        lang_map = {
            "auto": "hi-IN",
            "en": "en-GB",
            "hi": "hi-IN",
        }
        mm_source = lang_map.get(source.lower(), source)
        mm_target = lang_map.get(target.lower(), target)
        if mm_source == mm_target:
            return text.strip(), None

        chunks = _chunk_text(text, 450)
        if not chunks:
            return None, None

        translated_parts: List[str] = []
        for chunk in chunks:
            if deadline is not None and time.monotonic() >= deadline:
                return None, TranslationTimeoutError("Translation request timed out during fallback.")
            self._pace_request(deadline=deadline)
            try:
                translated = MyMemoryTranslator(source=mm_source, target=mm_target).translate(chunk)
                cleaned = (translated or "").strip()
                if not cleaned:
                    return None, TranslationError("MyMemoryTranslator returned empty text.")
                translated_parts.append(cleaned)
            except Exception as exc:
                if _is_rate_limit_error(exc):
                    self._mymemory_cooldown_until = time.monotonic() + self.cooldown_sec
                return None, exc

        joined_all = " ".join(translated_parts).strip()
        return (joined_all, None) if joined_all else (None, None)

    def _translate_step(
        self,
        text: str,
        *,
        source: str,
        target: str,
        step_label: str,
        deadline: Optional[float] = None,
    ) -> str:
        """Translates one directional step using primary + automatic fallbacks."""
        errors: List[Exception] = []

        # 1. Primary: deep_translator.GoogleTranslator (skipped during 429 cooldown)
        res, err = self._translate_via_google_web(
            text, source, target, deadline=deadline
        )
        if res:
            return res
        if err is not None:
            errors.append(err)

        # 2. Fallback 1: Google translate_a/single?client=gtx
        res, err = self._translate_via_google_gtx(
            text, source, target, deadline=deadline
        )
        if res:
            return res
        if err is not None:
            errors.append(err)

        # 3. Fallback 2: MyMemoryTranslator
        res, err = self._translate_via_mymemory(
            text, source, target, deadline=deadline
        )
        if res:
            return res
        if err is not None:
            errors.append(err)

        if errors:
            last_err = errors[-1]
            if all(_is_rate_limit_error(e) for e in errors):
                raise TranslationError(
                    f"Translation rate-limited across all free backends while translating {step_label}. "
                    "Please wait a few seconds or configure OPENAI_API_KEY."
                ) from last_err
            raise TranslationError(
                f"Failed to translate {step_label}: {last_err}"
            ) from last_err

        raise TranslationError(f"Translation service returned empty output for {step_label}.")

    def _translate_sync(
        self,
        source_text: str,
        *,
        timeout_sec: Optional[float] = None,
    ) -> tuple[str, str]:
        """Translate source text to English then Hindi with caching and fallbacks."""
        cache_key = self._normalize_cache_key(source_text)
        cached = self._get_translation_cache(cache_key)
        if cached:
            english_text, hindi_text = cached
            logger.debug("[DeepTranslatorProvider] Cache hit for: %s", cache_key[:50])
            return english_text, hindi_text

        effective_timeout = float(timeout_sec if timeout_sec is not None else self.timeout)
        deadline = time.monotonic() + max(1.0, effective_timeout - 0.25)

        # Step 1: Canonical English rendering (never re-run if Step 2 needs a fallback)
        english_text = self._translate_step(
            source_text,
            source="auto",
            target="en",
            step_label="text to English",
            deadline=deadline,
        )

        # Step 2: Spoken Hindi derived from the exact canonical English output
        hindi_text = self._translate_step(
            english_text,
            source="en",
            target="hi",
            step_label="English text to Hindi",
            deadline=deadline,
        )

        self._set_translation_cache(cache_key, english_text, hindi_text)
        return english_text, hindi_text

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
                asyncio.to_thread(
                    self._translate_sync,
                    cleaned_source,
                    timeout_sec=effective_timeout,
                ),
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
