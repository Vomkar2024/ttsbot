"""Default zero-API-key translator using deep-translator (Google Translate)."""

import asyncio
import logging
from typing import Optional

from .base import (
    HinglishTranslation,
    TranslationError,
    TranslationTimeoutError,
)

logger = logging.getLogger(__name__)


class DeepTranslatorProvider:
    """Translates incoming text into synced English + Hindi using deep-translator.

    To guarantee the spoken Hindi matches the English text shown in Discord,
    this provider produces the canonical English text first (`auto -> en`) and
    then translates that exact English text into Hindi (`en -> hi`).
    """

    provider_name = "deep-translator"

    def __init__(self, timeout: float = 10.0):
        self.timeout = float(timeout)

    async def is_available(self) -> bool:
        try:
            import deep_translator  # noqa: F401
            return True
        except ImportError:
            return False

    def _translate_sync(self, source_text: str) -> tuple[str, str]:
        try:
            from deep_translator import GoogleTranslator
        except ImportError as exc:
            raise TranslationError(
                "deep-translator is not installed. Run: pip install deep-translator"
            ) from exc

        try:
            english_raw = GoogleTranslator(source="auto", target="en").translate(source_text)
        except Exception as exc:
            raise TranslationError(f"Failed to translate text to English: {exc}") from exc

        english_text = (english_raw or "").strip()
        if not english_text:
            raise TranslationError("Translation service returned empty English text.")

        # Derive Hindi directly from the canonical English output so both
        # channels express the same meaning and stay strictly in sync.
        try:
            hindi_raw = GoogleTranslator(source="en", target="hi").translate(english_text)
        except Exception as exc:
            raise TranslationError(f"Failed to translate English text to Hindi: {exc}") from exc

        hindi_text = (hindi_raw or "").strip()
        if not hindi_text:
            raise TranslationError("Translation service returned empty Hindi text.")

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

