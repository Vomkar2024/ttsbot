"""Base types and provider interface for the decoupled Hinglish translation layer."""

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


class TranslationError(Exception):
    """Raised when the translation service fails to produce aligned output."""


class TranslationTimeoutError(TranslationError, TimeoutError):
    """Raised when the translation service exceeds its configured timeout."""


@dataclass(frozen=True)
class HinglishTranslation:
    """Paired English (display) and Hindi (spoken) text derived from the same meaning."""

    source_text: str
    english_text: str
    hindi_text: str
    provider: str = "unknown"


@runtime_checkable
class TranslatorProvider(Protocol):
    """Swappable async interface for translating incoming messages into synced English + Hindi."""

    provider_name: str

    async def is_available(self) -> bool:
        """Returns True if the translation provider is configured and ready."""
        ...

    async def translate_to_english_and_hindi(
        self,
        text: str,
        *,
        timeout: Optional[float] = None,
    ) -> HinglishTranslation:
        """Produces aligned English text and corresponding Hindi text from incoming content."""
        ...


__all__ = [
    "HinglishTranslation",
    "TranslationError",
    "TranslationTimeoutError",
    "TranslatorProvider",
]

