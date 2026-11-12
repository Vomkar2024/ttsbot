"""Factory for constructing the configured Hinglish translation provider."""

from typing import Optional

from src.storage.settings_store import SettingsStore

from .base import TranslatorProvider
from .deep_translator_provider import DeepTranslatorProvider
from .openai_translator_provider import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    OpenAITranslatorProvider,
)


def create_translator_provider(
    provider: str = "auto",
    *,
    timeout: float = 10.0,
    openai_api_key: str = "",
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL,
    openai_model: str = DEFAULT_OPENAI_MODEL,
    settings_store: Optional[SettingsStore] = None,
) -> TranslatorProvider:
    """Builds a swappable TranslatorProvider from configuration values."""
    normalized = (provider or "auto").strip().lower()

    if normalized == "auto":
        # Prefer OpenAI if configured, otherwise fallback to DeepTranslatorProvider
        if (openai_api_key or "").strip():
            return OpenAITranslatorProvider(
                api_key=openai_api_key,
                base_url=openai_base_url,
                model=openai_model,
                timeout=timeout,
            )
        return DeepTranslatorProvider(timeout=timeout, settings_store=settings_store)

    if normalized in ("openai", "llm"):
        return OpenAITranslatorProvider(
            api_key=openai_api_key,
            base_url=openai_base_url,
            model=openai_model,
            timeout=timeout,
        )

    if normalized in ("google", "deep_translator", "deep-translator"):
        return DeepTranslatorProvider(timeout=timeout, settings_store=settings_store)

    raise ValueError(
        f"Unsupported TRANSLATOR_PROVIDER '{provider}'. Expected 'auto', 'google', or 'openai'."
    )


__all__ = ["create_translator_provider"]

