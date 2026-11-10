"""Edge Neural Text-to-Speech (edge-tts) provider for custom high-quality voice packs."""

import asyncio
import io
import logging
import struct
from typing import Any, Optional

from ..constants import (
    DEFAULT_EDGE_TTS_PITCH,
    DEFAULT_EDGE_TTS_RATE,
    DEFAULT_EDGE_TTS_TIMEOUT_SEC,
    DEFAULT_EDGE_TTS_VOICE,
    DEFAULT_LANGUAGE,
    DEFAULT_VOICE,
)
from .espeak_provider import AbortError

logger = logging.getLogger(__name__)

# Language/locale to Microsoft Edge Neural voice mapping
LANGUAGE_TO_EDGE_VOICE = {
    "en": DEFAULT_EDGE_TTS_VOICE,
    "en-us": "en-US-EmmaMultilingualNeural",
    "en-uk": "en-GB-SoniaNeural",
    "en-gb": "en-GB-SoniaNeural",
    "en-au": "en-AU-NatashaNeural",
    "en-ca": "en-CA-ClaraNeural",
    "en-in": "en-IN-NeerjaNeural",
    "en-za": "en-ZA-LeahNeural",
    "en-ie": "en-IE-EmilyNeural",
    "es": "es-ES-ElviraNeural",
    "es-es": "es-ES-ElviraNeural",
    "es-mx": "es-MX-DaliaNeural",
    "fr": "fr-FR-DeniseNeural",
    "fr-ca": "fr-CA-SylvieNeural",
    "de": "de-DE-KatjaNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "pt-br": "pt-BR-FranciscaNeural",
    "pt-pt": "pt-PT-RaquelNeural",
    "hi": "hi-IN-SwaraNeural",
    "ja": "ja-JP-NanamiNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "zh-cn": "zh-CN-XiaoxiaoNeural",
    "zh-tw": "zh-TW-HsiaoChenNeural",
    "nl": "nl-NL-ColetteNeural",
    "pl": "pl-PL-ZofiaNeural",
    "sv": "sv-SE-SofieNeural",
    "tr": "tr-TR-EmelNeural",
    "ko": "ko-KR-SunHiNeural",
    "el": "el-GR-AthinaNeural",
    "ar": "ar-SA-ZariyahNeural",
}

ACCENT_TO_EDGE_VOICE = {
    "us": "en-US-EmmaMultilingualNeural",
    "uk": "en-GB-SoniaNeural",
    "gb": "en-GB-SoniaNeural",
    "au": "en-AU-NatashaNeural",
    "ca": "en-CA-ClaraNeural",
    "in": "en-IN-NeerjaNeural",
    "za": "en-ZA-LeahNeural",
    "ie": "en-IE-EmilyNeural",
}


class EdgeTTSProvider:
    """Online neural TTS provider utilizing Microsoft Edge TTS custom voice packs."""

    def __init__(
        self,
        default_voice: str = DEFAULT_EDGE_TTS_VOICE,
        default_rate: str = DEFAULT_EDGE_TTS_RATE,
        default_pitch: str = DEFAULT_EDGE_TTS_PITCH,
        timeout: float = DEFAULT_EDGE_TTS_TIMEOUT_SEC,
        allow_mock_fallback: bool = False,
        options: Optional[dict] = None,
    ):
        opts = options or {}
        self.default_voice = (
            opts.get("edgeTtsVoice", opts.get("edge_tts_voice", default_voice))
            or DEFAULT_EDGE_TTS_VOICE
        )
        self.default_rate = (
            opts.get("edgeTtsRate", opts.get("edge_tts_rate", default_rate))
            or DEFAULT_EDGE_TTS_RATE
        )
        self.default_pitch = (
            opts.get("edgeTtsPitch", opts.get("edge_tts_pitch", default_pitch))
            or DEFAULT_EDGE_TTS_PITCH
        )
        self.timeout = float(
            opts.get(
                "edgeTtsTimeoutSec",
                opts.get("edge_tts_timeout_sec", opts.get("timeout", opts.get("timeoutSec", timeout))),
            )
        )
        self.allow_mock_fallback = bool(
            opts.get("allowMockFallback", opts.get("allow_mock_fallback", allow_mock_fallback))
        )

    def resolve_voice(
        self,
        language: Optional[str] = DEFAULT_LANGUAGE,
        voice: Optional[str] = DEFAULT_VOICE,
    ) -> str:
        """Resolves a target Edge-TTS neural voice short name from language and voice selection."""
        voice_input = (voice or "").strip()
        lang_input = (language or DEFAULT_LANGUAGE).strip().lower()

        # 1. Explicit Edge-TTS prefix (e.g. "edge:en-US-AriaNeural")
        if voice_input.lower().startswith("edge:"):
            explicit = voice_input[5:].strip()
            if explicit:
                return explicit

        # 2. Direct Edge-TTS ShortName (e.g. "en-US-EmmaMultilingualNeural")
        if voice_input.lower().endswith("neural") and "-" in voice_input:
            return voice_input

        # 3. Accent override when language is English or unset
        voice_lower = voice_input.lower()
        if voice_lower in ACCENT_TO_EDGE_VOICE and (not lang_input or lang_input.startswith("en")):
            return ACCENT_TO_EDGE_VOICE[voice_lower]

        # 4. Language mapping (honoring custom default_voice for base "en")
        if lang_input in ("", "en"):
            return self.default_voice or DEFAULT_EDGE_TTS_VOICE
        if lang_input in LANGUAGE_TO_EDGE_VOICE:
            return LANGUAGE_TO_EDGE_VOICE[lang_input]
        if "-" in lang_input:
            base = lang_input.split("-")[0]
            if base in LANGUAGE_TO_EDGE_VOICE:
                return LANGUAGE_TO_EDGE_VOICE[base]

        return self.default_voice or DEFAULT_EDGE_TTS_VOICE

    def _resolve_rate(self, speed: Optional[Any], opts: dict) -> str:
        raw_rate = opts.get("rate")
        if isinstance(raw_rate, str) and raw_rate.strip().endswith("%"):
            cleaned = raw_rate.strip()
            if cleaned[0] not in ("+", "-"):
                cleaned = f"+{cleaned}"
            return cleaned
        if isinstance(speed, (int, float)):
            # Convert eSpeak-style WPM (175 baseline) to Edge percentage delta
            pct = int(round(((float(speed) - 175.0) / 175.0) * 100.0))
            pct = max(-50, min(100, pct))
            return f"{pct:+d}%"
        return self.default_rate or DEFAULT_EDGE_TTS_RATE

    def _resolve_pitch(self, pitch: Optional[Any], opts: dict) -> str:
        raw_pitch = opts.get("pitch")
        if isinstance(raw_pitch, str) and raw_pitch.strip().lower().endswith("hz"):
            cleaned = raw_pitch.strip()
            if cleaned[0] not in ("+", "-"):
                cleaned = f"+{cleaned}"
            return cleaned
        if isinstance(pitch, (int, float)):
            # Convert eSpeak-style pitch (50 baseline) to Hz delta
            hz = int(round(float(pitch) - 50.0))
            hz = max(-50, min(50, hz))
            return f"{hz:+d}Hz"
        return self.default_pitch or DEFAULT_EDGE_TTS_PITCH

    async def is_available(self) -> bool:
        """Checks if edge-tts library can be imported."""
        try:
            import edge_tts  # noqa: F401
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
        """Synthesizes text to MP3 bytes via edge-tts streaming."""
        opts = options or {}
        if language is None:
            language = opts.get("language")
        if voice is None:
            voice = opts.get("voice")
        if speed is None:
            speed = opts.get("speed")
        if pitch is None:
            pitch = opts.get("pitch")
        if cancel_event is None:
            cancel_event = opts.get("cancel_event", opts.get("cancelEvent"))

        sig = signal or opts.get("signal")
        if (sig and getattr(sig, "aborted", False)) or (cancel_event and cancel_event.is_set()):
            raise AbortError("Synthesis aborted prior to execution")

        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Cannot synthesize empty or whitespace-only text")

        resolved_voice = self.resolve_voice(language, voice)
        rate_str = self._resolve_rate(speed, opts)
        pitch_str = self._resolve_pitch(pitch, opts)
        timeout_sec = float(opts.get("timeout", opts.get("timeoutSec", self.timeout)))

        async def _stream_audio() -> bytes:
            import edge_tts

            communicator = edge_tts.Communicate(
                text=text,
                voice=resolved_voice,
                rate=rate_str,
                pitch=pitch_str,
            )
            audio_buf = bytearray()
            async for chunk in communicator.stream():
                if (sig and getattr(sig, "aborted", False)) or (
                    cancel_event and cancel_event.is_set()
                ):
                    raise AbortError("Synthesis cancelled during Edge-TTS streaming")
                if isinstance(chunk, dict) and chunk.get("type") == "audio":
                    data = chunk.get("data")
                    if data:
                        audio_buf.extend(data)
            return bytes(audio_buf)

        try:
            audio_bytes = await asyncio.wait_for(_stream_audio(), timeout=timeout_sec)
        except AbortError:
            raise
        except asyncio.TimeoutError as te:
            logger.warning(
                "[EdgeTTSProvider] Edge-TTS timed out after %.1fs for voice=%s (lang=%s)",
                timeout_sec,
                resolved_voice,
                language,
            )
            if cancel_event and cancel_event.is_set():
                raise AbortError("Synthesis cancelled during timeout") from te
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise TimeoutError(
                f"Edge-TTS request timed out after {timeout_sec:.1f}s (voice={resolved_voice})"
            ) from te
        except Exception as err:
            if cancel_event and cancel_event.is_set():
                raise AbortError("Synthesis cancelled during Edge-TTS failure") from err
            logger.warning(
                "[EdgeTTSProvider] Edge-TTS failed for voice=%s (lang=%s): %s",
                resolved_voice,
                language,
                err,
            )
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise err

        if (sig and getattr(sig, "aborted", False)) or (cancel_event and cancel_event.is_set()):
            raise AbortError("Synthesis cancelled after completion")

        if not audio_bytes:
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise RuntimeError(
                f"Edge-TTS produced empty audio stream for voice={resolved_voice}"
            )

        return audio_bytes

    def _generate_mock_wav(self, text: str) -> bytes:
        """Minimal mock WAV generator for offline fallback testing."""
        sample_rate = 24000
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


__all__ = [
    "EdgeTTSProvider",
    "LANGUAGE_TO_EDGE_VOICE",
    "ACCENT_TO_EDGE_VOICE",
]

