"""Unified Text-to-Speech provider routing between gTTS and eSpeak NG with graceful fallback."""

import asyncio
import logging
from typing import Any, Optional

from ..constants import DEFAULT_LANGUAGE, DEFAULT_TTS_ENGINE, DEFAULT_VOICE
from .espeak_provider import AbortError, EspeakProvider
from .gtts_provider import GTTSProvider

logger = logging.getLogger(__name__)


class UnifiedTTSProvider:
    """Multi-engine TTS provider managing gTTS and eSpeak NG with auto-fallback."""

    def __init__(
        self,
        default_engine: str = DEFAULT_TTS_ENGINE,
        tts_bin: str = "espeak-ng",
        allow_mock_fallback: bool = False,
        options: Optional[dict] = None,
    ):
        opts = options or {}
        self.default_engine = opts.get("defaultEngine", opts.get("default_engine", default_engine)).lower()
        self.allow_mock_fallback = opts.get(
            "allowMockFallback", opts.get("allow_mock_fallback", allow_mock_fallback)
        )

        self.gtts = GTTSProvider(allow_mock_fallback=self.allow_mock_fallback)
        self.espeak = EspeakProvider(
            tts_bin=tts_bin,
            allow_mock_fallback=self.allow_mock_fallback,
        )

    def resolve_voice_arg(
        self,
        language: Optional[str] = DEFAULT_LANGUAGE,
        voice: Optional[str] = DEFAULT_VOICE,
    ) -> str:
        """Compatibility wrapper for eSpeak voice resolution."""
        return self.espeak.resolve_voice_arg(language, voice)

    # Alias camelCase
    resolveVoiceArg = resolve_voice_arg

    async def is_available(self) -> bool:
        """Returns True if at least one TTS engine is operational."""
        gtts_ok = await self.gtts.is_available()
        espeak_ok = await self.espeak.is_available()
        return gtts_ok or espeak_ok

    isAvailable = is_available

    async def synthesize(
        self,
        text: str,
        options: Optional[dict] = None,
        *,
        engine: Optional[str] = None,
        language: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[int] = None,
        pitch: Optional[int] = None,
        cancel_event: Optional[asyncio.Event] = None,
        signal: Any = None,
    ) -> bytes:
        """Synthesizes text using the designated engine, falling back automatically if an error occurs."""
        opts = options or {}
        target_engine = (engine or opts.get("engine") or self.default_engine).lower()

        if target_engine in ("gtts", "google"):
            try:
                return await self.gtts.synthesize(
                    text,
                    options,
                    language=language,
                    voice=voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                )
            except AbortError:
                raise
            except Exception as err:
                logger.warning(
                    "[UnifiedTTSProvider] gTTS synthesis failed (%s). Falling back to eSpeak NG...",
                    err,
                )
                return await self.espeak.synthesize(
                    text,
                    options,
                    language=language,
                    voice=voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                )
        else:
            # eSpeak NG primary
            try:
                return await self.espeak.synthesize(
                    text,
                    options,
                    language=language,
                    voice=voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                )
            except AbortError:
                raise
            except Exception as err:
                logger.warning(
                    "[UnifiedTTSProvider] eSpeak synthesis failed (%s). Falling back to gTTS...",
                    err,
                )
                return await self.gtts.synthesize(
                    text,
                    options,
                    language=language,
                    voice=voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                )


__all__ = ["UnifiedTTSProvider", "GTTSProvider", "EspeakProvider", "AbortError"]

