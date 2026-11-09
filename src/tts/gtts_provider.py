"""Google Translate Text-to-Speech (gTTS) provider for natural voice synthesis."""

import asyncio
import io
import logging
from typing import Any, Optional

from ..constants import DEFAULT_LANGUAGE
from .espeak_provider import AbortError

logger = logging.getLogger(__name__)

# Map common language/locale codes to gTTS language and TLD accent
LOCALE_TO_GTTS = {
    "en": ("en", "com"),
    "en-us": ("en", "com"),
    "en-uk": ("en", "co.uk"),
    "en-gb": ("en", "co.uk"),
    "en-au": ("en", "com.au"),
    "en-ca": ("en", "ca"),
    "en-in": ("en", "co.in"),
    "en-za": ("en", "co.za"),
    "en-ie": ("en", "ie"),
    "es": ("es", "com"),
    "es-es": ("es", "es"),
    "es-mx": ("es", "com.mx"),
    "fr": ("fr", "fr"),
    "fr-ca": ("fr", "ca"),
    "de": ("de", "de"),
    "it": ("it", "it"),
    "pt": ("pt", "com"),
    "pt-br": ("pt", "com.br"),
    "pt-pt": ("pt", "pt"),
    "hi": ("hi", "co.in"),
    "ja": ("ja", "co.jp"),
    "ko": ("ko", "co.kr"),
    "zh": ("zh-CN", "com"),
    "zh-cn": ("zh-CN", "com"),
    "zh-tw": ("zh-TW", "com"),
    "ru": ("ru", "com"),
    "nl": ("nl", "nl"),
    "pl": ("pl", "pl"),
    "sv": ("sv", "se"),
    "tr": ("tr", "com.tr"),
    "el": ("el", "gr"),
    "ar": ("ar", "com"),
}

ACCENT_TLDS = {
    "us": "com",
    "uk": "co.uk",
    "gb": "co.uk",
    "au": "com.au",
    "ca": "ca",
    "in": "co.in",
    "za": "co.za",
    "ie": "ie",
}


class GTTSProvider:
    """Online TTS provider utilizing Google Translate TTS for natural-sounding speech."""

    def __init__(
        self,
        allow_mock_fallback: bool = False,
        options: Optional[dict] = None,
        timeout: float = 5.0,
    ):
        opts = options or {}
        self.allow_mock_fallback = opts.get(
            "allowMockFallback", opts.get("allow_mock_fallback", allow_mock_fallback)
        )
        self.timeout = float(opts.get("timeout", opts.get("timeoutSec", timeout)))

    def resolve_params(
        self,
        language: Optional[str] = DEFAULT_LANGUAGE,
        voice: Optional[str] = None,
    ) -> tuple[str, str]:
        """Resolves (lang, tld) parameters for gTTS from language code and voice variant."""
        lang_input = (language or DEFAULT_LANGUAGE).strip().lower()
        voice_input = (voice or "").strip().lower()

        # Check locale mapping
        if lang_input in LOCALE_TO_GTTS:
            gtts_lang, gtts_tld = LOCALE_TO_GTTS[lang_input]
        elif "-" in lang_input:
            base_lang = lang_input.split("-")[0]
            gtts_lang = base_lang
            gtts_tld = "com"
        else:
            gtts_lang = lang_input
            gtts_tld = "com"

        # Check voice for accent override
        if voice_input in ACCENT_TLDS:
            gtts_tld = ACCENT_TLDS[voice_input]
        elif voice_input.startswith("tld:"):
            gtts_tld = voice_input[4:]

        return gtts_lang, gtts_tld

    async def is_available(self) -> bool:
        """Checks if gTTS library is imported successfully."""
        try:
            import gtts  # noqa: F401
            return True
        except ImportError:
            return False

    isAvailable = is_available

    async def synthesize(
        self,
        text: str,
        options: Optional[dict] = None,
        *,
        language: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[int] = None,
        pitch: Optional[int] = None,
        cancel_event: Optional[asyncio.Event] = None,
        signal: Any = None,
    ) -> bytes:
        """Synthesizes text to MP3 bytes using Google Translate TTS.

        Runs network fetching in a separate thread so it never blocks the asyncio event loop.
        """
        opts = options or {}
        if language is None:
            language = opts.get("language")
        if voice is None:
            voice = opts.get("voice")
        if cancel_event is None:
            cancel_event = opts.get("cancel_event", opts.get("cancelEvent"))

        # Cancellation check
        sig = signal or opts.get("signal")
        if (sig and getattr(sig, "aborted", False)) or (cancel_event and cancel_event.is_set()):
            raise AbortError("Synthesis aborted prior to execution")

        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Cannot synthesize empty or whitespace-only text")

        gtts_lang, gtts_tld = self.resolve_params(language, voice)
        timeout_sec = float(opts.get("timeout", opts.get("timeoutSec", self.timeout)))

        # Flag for slow speech if requested
        slow = bool(speed is not None and speed < 120)

        def _fetch_audio() -> bytes:
            try:
                from gtts import gTTS

                tts = gTTS(text=text, lang=gtts_lang, tld=gtts_tld, slow=slow, timeout=timeout_sec)
                fp = io.BytesIO()
                tts.write_to_fp(fp)
                return fp.getvalue()
            except Exception as err:
                logger.warning(
                    "[GTTSProvider] Google TTS request failed for lang=%s tld=%s: %s",
                    gtts_lang,
                    gtts_tld,
                    err,
                )
                raise err

        try:
            audio_bytes = await asyncio.wait_for(
                asyncio.to_thread(_fetch_audio),
                timeout=timeout_sec + 1.0,
            )
        except asyncio.TimeoutError as te:
            logger.warning(
                "[GTTSProvider] Google TTS request timed out after %.1fs for lang=%s tld=%s",
                timeout_sec,
                gtts_lang,
                gtts_tld,
            )
            if cancel_event and cancel_event.is_set():
                raise AbortError("Synthesis cancelled during execution") from te
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise TimeoutError(f"Google TTS request timed out after {timeout_sec}s") from te
        except Exception as e:
            if cancel_event and cancel_event.is_set():
                raise AbortError("Synthesis cancelled during execution") from e
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise e

        if (sig and getattr(sig, "aborted", False)) or (cancel_event and cancel_event.is_set()):
            raise AbortError("Synthesis cancelled after completion")

        return audio_bytes

    def _generate_mock_wav(self, text: str) -> bytes:
        """Minimal mock WAV generator for offline fallback testing."""
        import struct

        sample_rate = 22050
        num_channels = 1
        bits_per_sample = 16
        duration_sec = max(0.2, min(1.0, len(text) * 0.02))
        num_samples = int(sample_rate * duration_sec)
        data_size = num_samples * num_channels * (bits_per_sample // 8)

        out = io.BytesIO()
        out.write(b"RIFF")
        out.write(struct.pack("<I", 36 + data_size))
        out.write(b"WAVE")
        out.write(b"fmt ")
        out.write(struct.pack("<I", 16))
        out.write(struct.pack("<H", 1))
        out.write(struct.pack("<H", num_channels))
        out.write(struct.pack("<I", sample_rate))
        byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
        out.write(struct.pack("<I", byte_rate))
        block_align = num_channels * (bits_per_sample // 8)
        out.write(struct.pack("<H", block_align))
        out.write(struct.pack("<H", bits_per_sample))
        out.write(b"data")
        out.write(struct.pack("<I", data_size))
        out.write(b"\x00" * data_size)
        return out.getvalue()


__all__ = ["GTTSProvider", "LOCALE_TO_GTTS", "ACCENT_TLDS"]

