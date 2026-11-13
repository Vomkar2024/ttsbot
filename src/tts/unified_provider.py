"""Unified Text-to-Speech provider routing between Edge-TTS, gTTS, and eSpeak NG with graceful fallback."""

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import logging
from typing import Any, AsyncIterator, Callable, List, Optional

from ..constants import (
    DEFAULT_EDGE_TTS_PITCH,
    DEFAULT_EDGE_TTS_RATE,
    DEFAULT_EDGE_TTS_TIMEOUT_SEC,
    DEFAULT_EDGE_TTS_VOICE,
    DEFAULT_LANGUAGE,
    DEFAULT_TTS_ENGINE,
    DEFAULT_VOICE,
)
from .edge_tts_provider import EdgeTTSProvider
from .espeak_provider import AbortError, EspeakProvider
from .gtts_provider import ACCENT_TLDS, GTTSProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SynthesizedAudio:
    """Synthesized audio buffer paired with container format and synthesis provenance."""

    data: bytes
    audio_format: str
    filename_extension: str
    mime_type: str
    engine: str = ""
    voice_used: str = ""
    fallback_from: Optional[str] = None


@dataclass
class StreamingAudio:
    """Audio stream handle yielding a first chunk immediately while streaming remaining chunks."""

    first_chunk: bytes
    chunk_iter: Optional[AsyncIterator[bytes]]
    audio_format: str
    engine: str
    voice_used: str
    fallback_from: Optional[str] = None
    on_complete: Optional[Callable[[bytes], None]] = None

    @property
    def is_streaming(self) -> bool:
        return self.chunk_iter is not None


def detect_audio_format(audio_bytes: bytes) -> str:
    """Infers container format ('wav', 'ogg', or 'mp3') from audio magic bytes."""
    if len(audio_bytes) >= 12 and audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
        return "wav"
    if len(audio_bytes) >= 4 and audio_bytes[:4] == b"OggS":
        return "ogg"
    return "mp3"


def normalize_engine_name(engine: Optional[str]) -> str:
    """Normalizes configured engine aliases to canonical names ('edge-tts', 'gtts', 'espeak')."""
    raw = (engine or DEFAULT_TTS_ENGINE).strip().lower()
    if raw in ("edge", "edge-tts", "edge_tts", "edgetts", "custom"):
        return "edge-tts"
    if raw in ("gtts", "google"):
        return "gtts"
    if raw in ("espeak", "espeak-ng", "espeak_ng"):
        return "espeak"
    logger.warning("[UnifiedTTSProvider] Unknown TTS engine '%s'; defaulting to 'edge-tts'.", engine)
    return "edge-tts"


class UnifiedTTSProvider:
    """Multi-engine TTS provider managing Edge-TTS custom voice pack, gTTS, and eSpeak NG with auto-fallback."""

    def __init__(
        self,
        default_engine: str = DEFAULT_TTS_ENGINE,
        tts_bin: str = "espeak-ng",
        allow_mock_fallback: bool = False,
        options: Optional[dict] = None,
    ):
        opts = options or {}
        raw_default = opts.get("defaultEngine", opts.get("default_engine", default_engine))
        self.default_engine = normalize_engine_name(raw_default)
        self.allow_mock_fallback = bool(
            opts.get("allowMockFallback", opts.get("allow_mock_fallback", allow_mock_fallback))
        )

        self.edge_tts = EdgeTTSProvider(
            default_voice=str(
                opts.get("edgeTtsVoice", opts.get("edge_tts_voice", DEFAULT_EDGE_TTS_VOICE))
                or DEFAULT_EDGE_TTS_VOICE
            ),
            default_rate=str(
                opts.get("edgeTtsRate", opts.get("edge_tts_rate", DEFAULT_EDGE_TTS_RATE))
                or DEFAULT_EDGE_TTS_RATE
            ),
            default_pitch=str(
                opts.get("edgeTtsPitch", opts.get("edge_tts_pitch", DEFAULT_EDGE_TTS_PITCH))
                or DEFAULT_EDGE_TTS_PITCH
            ),
            timeout=float(
                opts.get(
                    "edgeTtsTimeoutSec",
                    opts.get("edge_tts_timeout_sec", DEFAULT_EDGE_TTS_TIMEOUT_SEC),
                )
            ),
            allow_mock_fallback=self.allow_mock_fallback,
        )
        self.gtts = GTTSProvider(
            allow_mock_fallback=self.allow_mock_fallback,
            timeout=float(opts.get("gtts_timeout_sec", opts.get("timeout", 5.0))),
        )
        self.espeak = EspeakProvider(
            tts_bin=tts_bin,
            allow_mock_fallback=self.allow_mock_fallback,
        )

        # Bounded in-memory LRU audio cache for instant playback of repeated messages
        self._cache_enabled = bool(opts.get("enableCache", opts.get("enable_cache", True)))
        self._cache_max_entries = max(0, int(opts.get("cacheMaxEntries", 128)))
        self._cache_max_total_bytes = max(0, int(opts.get("cacheMaxTotalBytes", 4 * 1024 * 1024)))
        self._cache_max_item_bytes = max(0, int(opts.get("cacheMaxItemBytes", 256 * 1024)))
        self._audio_cache: "OrderedDict[tuple, tuple[bytes, str, str, str]]" = OrderedDict()
        self._audio_cache_bytes = 0

    @property
    def tts_bin(self) -> str:
        """Configured eSpeak binary executable path."""
        espeak_obj = getattr(self, "espeak", None)
        return getattr(espeak_obj, "tts_bin", "espeak-ng") if espeak_obj else "espeak-ng"

    def resolve_binary_path(self) -> Optional[str]:
        """Resolves the underlying eSpeak binary path if available."""
        if hasattr(self, "espeak") and hasattr(self.espeak, "resolve_binary_path"):
            return self.espeak.resolve_binary_path()
        return None

    _resolve_binary_path = resolve_binary_path

    def has_binary(self) -> bool:
        """Check if the underlying eSpeak binary can be located."""
        return self.resolve_binary_path() is not None

    def resolve_voice_arg(
        self,
        language: Optional[str] = DEFAULT_LANGUAGE,
        voice: Optional[str] = DEFAULT_VOICE,
    ) -> str:
        """Compatibility wrapper for eSpeak voice resolution."""
        return self.espeak.resolve_voice_arg(language, voice)

    # Alias camelCase
    resolveVoiceArg = resolve_voice_arg

    def resolve_selection(
        self,
        *,
        engine: Optional[str] = None,
        language: Optional[str] = DEFAULT_LANGUAGE,
        voice: Optional[str] = DEFAULT_VOICE,
    ) -> dict:
        """Determines primary engine, ordered fallbacks, and resolved voice identifier."""
        lang_clean = (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
        voice_clean = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
        voice_lower = voice_clean.lower()

        if engine:
            primary = normalize_engine_name(engine)
        elif voice_lower.startswith("edge:") or (voice_lower.endswith("neural") and "-" in voice_clean):
            primary = "edge-tts"
        elif voice_clean.startswith("+"):
            primary = "espeak"
        elif voice_lower in ACCENT_TLDS or voice_lower.startswith("tld:"):
            primary = "gtts"
        else:
            primary = self.default_engine

        if primary == "edge-tts":
            order = ["edge-tts", "gtts", "espeak"]
            resolved_voice = self.edge_tts.resolve_voice(lang_clean, voice_clean)
        elif primary == "gtts":
            order = ["gtts", "espeak", "edge-tts"]
            g_lang, g_tld = self.gtts.resolve_params(lang_clean, voice_clean)
            resolved_voice = f"{g_lang} (tld={g_tld})"
        else:
            order = ["espeak", "gtts", "edge-tts"]
            resolved_voice = self.espeak.resolve_voice_arg(lang_clean, voice_clean)

        return {
            "primary_engine": primary,
            "engine_order": order,
            "resolved_voice": resolved_voice,
            "language": lang_clean,
            "voice_preference": voice_clean,
        }

    def _describe_voice_for_engine(
        self,
        engine_name: str,
        language: Optional[str],
        voice: Optional[str],
    ) -> str:
        lang_clean = (language or DEFAULT_LANGUAGE).strip() or DEFAULT_LANGUAGE
        voice_clean = (voice or DEFAULT_VOICE).strip() or DEFAULT_VOICE
        if engine_name == "edge-tts":
            return self.edge_tts.resolve_voice(lang_clean, voice_clean)
        if engine_name == "gtts":
            g_lang, g_tld = self.gtts.resolve_params(lang_clean, voice_clean)
            return f"{g_lang} (tld={g_tld})"
        return self.espeak.resolve_voice_arg(lang_clean, voice_clean)

    async def is_available(self) -> bool:
        """Returns True if at least one TTS engine is operational."""
        edge_ok = await self.edge_tts.is_available()
        gtts_ok = await self.gtts.is_available()
        espeak_ok = await self.espeak.is_available()
        return edge_ok or gtts_ok or espeak_ok

    isAvailable = is_available

    async def _synthesize_with_engine(
        self,
        engine_name: str,
        text: str,
        options: Optional[dict],
        *,
        language: Optional[str],
        voice: Optional[str],
        speed: Optional[int],
        pitch: Optional[int],
        cancel_event: Optional[asyncio.Event],
        signal: Any,
    ) -> bytes:
        if engine_name == "edge-tts":
            return await self.edge_tts.synthesize(
                text,
                options,
                language=language,
                voice=voice,
                speed=speed,
                pitch=pitch,
                cancel_event=cancel_event,
                signal=signal,
            )
        if engine_name == "gtts":
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

    def _is_engine_patched(self, engine_name: str) -> bool:
        if engine_name == "edge-tts":
            obj = getattr(self, "edge_tts", None)
        elif engine_name == "gtts":
            obj = getattr(self, "gtts", None)
        else:
            obj = getattr(self, "espeak", None)
        if obj is None:
            return False
        d = getattr(obj, "__dict__", {})
        return "synthesize" in d or "stream_chunks" in d

    def _make_cache_key(
        self,
        *,
        primary_engine: str,
        resolved_voice: str,
        language: Optional[str],
        speed: Optional[int],
        pitch: Optional[int],
        text: str,
    ) -> tuple:
        lang_norm = (language or DEFAULT_LANGUAGE).strip().lower() or DEFAULT_LANGUAGE
        text_norm = " ".join((text or "").split())
        rate_str = getattr(self.edge_tts, "default_rate", DEFAULT_EDGE_TTS_RATE)
        pitch_str = getattr(self.edge_tts, "default_pitch", DEFAULT_EDGE_TTS_PITCH)
        return (primary_engine, resolved_voice, lang_norm, speed, pitch, rate_str, pitch_str, text_norm)

    def _get_cached_audio(self, cache_key: tuple) -> Optional[tuple[bytes, str, str, str]]:
        if not self._cache_enabled or self._cache_max_entries <= 0:
            return None
        entry = self._audio_cache.get(cache_key)
        if entry is not None:
            self._audio_cache.move_to_end(cache_key)
        return entry

    def _store_cached_audio(
        self,
        cache_key: tuple,
        audio_bytes: bytes,
        engine_used: str,
        voice_used: str,
        audio_format: str,
    ) -> None:
        if (
            not self._cache_enabled
            or self._cache_max_entries <= 0
            or not audio_bytes
            or len(audio_bytes) > self._cache_max_item_bytes
            or len(audio_bytes) > self._cache_max_total_bytes
        ):
            return
        old = self._audio_cache.pop(cache_key, None)
        if old is not None:
            self._audio_cache_bytes -= len(old[0])
        self._audio_cache[cache_key] = (audio_bytes, engine_used, voice_used, audio_format)
        self._audio_cache_bytes += len(audio_bytes)
        while (
            len(self._audio_cache) > self._cache_max_entries
            or self._audio_cache_bytes > self._cache_max_total_bytes
        ):
            _, evicted = self._audio_cache.popitem(last=False)
            self._audio_cache_bytes -= len(evicted[0])

    async def _synthesize_internal(
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
    ) -> tuple[bytes, str, str, Optional[str]]:
        opts = options or {}
        explicit_engine = engine or opts.get("engine")
        selection = self.resolve_selection(
            engine=explicit_engine,
            language=language,
            voice=voice,
        )
        engine_order: List[str] = selection["engine_order"]
        primary_engine = selection["primary_engine"]
        any_patched = any(self._is_engine_patched(eng) for eng in engine_order)

        cache_key = self._make_cache_key(
            primary_engine=primary_engine,
            resolved_voice=selection["resolved_voice"],
            language=language,
            speed=speed,
            pitch=pitch,
            text=text,
        )
        if not options and not any_patched:
            cached = self._get_cached_audio(cache_key)
            if cached is not None:
                data, used_eng, used_voice, _ = cached
                return data, used_eng, used_voice, None

        last_err: Optional[Exception] = None
        for idx, candidate in enumerate(engine_order):
            # When falling back away from an engine-specific voice tag (e.g. "+klatt" or "edge:..."),
            # keep language cleanly and pass voice="default" on fallback engines if incompatible.
            eff_voice = voice
            if idx > 0 and voice:
                v_str = voice.strip()
                if candidate != "espeak" and v_str.startswith("+"):
                    eff_voice = "default"
                elif candidate != "edge-tts" and (
                    v_str.lower().startswith("edge:") or v_str.lower().endswith("neural")
                ):
                    eff_voice = "default"

            try:
                audio_bytes = await self._synthesize_with_engine(
                    candidate,
                    text,
                    options,
                    language=language,
                    voice=eff_voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                )
                if not audio_bytes:
                    raise RuntimeError(f"Engine '{candidate}' returned empty audio buffer (0 bytes)")

                voice_used = self._describe_voice_for_engine(candidate, language, eff_voice)
                fallback_from = primary_engine if idx > 0 else None
                if not options and not any_patched and fallback_from is None:
                    self._store_cached_audio(
                        cache_key,
                        audio_bytes,
                        candidate,
                        voice_used,
                        detect_audio_format(audio_bytes),
                    )
                return audio_bytes, candidate, voice_used, fallback_from
            except AbortError:
                raise
            except ValueError:
                # Input validation error (e.g. blank text) should fail immediately, not loop fallbacks
                raise
            except Exception as err:
                last_err = err
                if idx + 1 < len(engine_order):
                    next_eng = engine_order[idx + 1]
                    logger.warning(
                        "[UnifiedTTSProvider] %s synthesis failed (%s). Falling back to %s...",
                        candidate,
                        err,
                        next_eng,
                    )

        assert last_err is not None
        raise last_err

    async def open_audio_stream(
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
    ) -> StreamingAudio:
        """Opens a low-latency audio stream, returning as soon as the first chunk or cached buffer is ready."""
        opts = options or {}
        explicit_engine = engine or opts.get("engine")
        selection = self.resolve_selection(
            engine=explicit_engine,
            language=language,
            voice=voice,
        )
        engine_order: List[str] = selection["engine_order"]
        primary_engine = selection["primary_engine"]
        any_patched = any(self._is_engine_patched(eng) for eng in engine_order)

        cache_key = self._make_cache_key(
            primary_engine=primary_engine,
            resolved_voice=selection["resolved_voice"],
            language=language,
            speed=speed,
            pitch=pitch,
            text=text,
        )
        if not options and not any_patched:
            cached = self._get_cached_audio(cache_key)
            if cached is not None:
                data, used_eng, used_voice, fmt = cached
                return StreamingAudio(
                    first_chunk=data,
                    chunk_iter=None,
                    audio_format=fmt,
                    engine=used_eng,
                    voice_used=used_voice,
                    fallback_from=None,
                )

        last_err: Optional[Exception] = None
        for idx, candidate in enumerate(engine_order):
            eff_voice = voice
            if idx > 0 and voice:
                v_str = voice.strip()
                if candidate != "espeak" and v_str.startswith("+"):
                    eff_voice = "default"
                elif candidate != "edge-tts" and (
                    v_str.lower().startswith("edge:") or v_str.lower().endswith("neural")
                ):
                    eff_voice = "default"

            if (
                candidate == "edge-tts"
                and not self._is_engine_patched("edge-tts")
                and hasattr(self.edge_tts, "stream_chunks")
            ):
                aiter = self.edge_tts.stream_chunks(
                    text,
                    options,
                    language=language,
                    voice=eff_voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                ).__aiter__()
                try:
                    first_chunk = b""
                    while not first_chunk:
                        first_chunk = await aiter.__anext__()
                    voice_used = self._describe_voice_for_engine(candidate, language, eff_voice)
                    fallback_from = primary_engine if idx > 0 else None
                    fmt = detect_audio_format(first_chunk)

                    def _on_complete(full_bytes: bytes, _ck=cache_key, _eng=candidate, _vu=voice_used, _fmt=fmt) -> None:
                        if not options and not any_patched and fallback_from is None:
                            self._store_cached_audio(_ck, full_bytes, _eng, _vu, _fmt)

                    return StreamingAudio(
                        first_chunk=first_chunk,
                        chunk_iter=aiter,
                        audio_format=fmt,
                        engine=candidate,
                        voice_used=voice_used,
                        fallback_from=fallback_from,
                        on_complete=_on_complete,
                    )
                except AbortError:
                    aclose = getattr(aiter, "aclose", None)
                    if callable(aclose):
                        try:
                            await aclose()
                        except Exception:
                            pass
                    raise
                except ValueError:
                    aclose = getattr(aiter, "aclose", None)
                    if callable(aclose):
                        try:
                            await aclose()
                        except Exception:
                            pass
                    raise
                except StopAsyncIteration:
                    err = RuntimeError("Edge-TTS produced empty audio stream")
                    last_err = err
                    if idx + 1 < len(engine_order):
                        logger.warning(
                            "[UnifiedTTSProvider] %s synthesis failed (%s). Falling back to %s...",
                            candidate,
                            err,
                            engine_order[idx + 1],
                        )
                    continue
                except Exception as err:
                    aclose = getattr(aiter, "aclose", None)
                    if callable(aclose):
                        try:
                            await aclose()
                        except Exception:
                            pass
                    last_err = err
                    if idx + 1 < len(engine_order):
                        logger.warning(
                            "[UnifiedTTSProvider] %s synthesis failed (%s). Falling back to %s...",
                            candidate,
                            err,
                            engine_order[idx + 1],
                        )
                    continue

            try:
                audio_bytes = await self._synthesize_with_engine(
                    candidate,
                    text,
                    options,
                    language=language,
                    voice=eff_voice,
                    speed=speed,
                    pitch=pitch,
                    cancel_event=cancel_event,
                    signal=signal,
                )
                if not audio_bytes:
                    raise RuntimeError(f"Engine '{candidate}' returned empty audio buffer (0 bytes)")

                voice_used = self._describe_voice_for_engine(candidate, language, eff_voice)
                fallback_from = primary_engine if idx > 0 else None
                fmt = detect_audio_format(audio_bytes)
                if not options and not any_patched and fallback_from is None:
                    self._store_cached_audio(cache_key, audio_bytes, candidate, voice_used, fmt)
                return StreamingAudio(
                    first_chunk=audio_bytes,
                    chunk_iter=None,
                    audio_format=fmt,
                    engine=candidate,
                    voice_used=voice_used,
                    fallback_from=fallback_from,
                )
            except AbortError:
                raise
            except ValueError:
                raise
            except Exception as err:
                last_err = err
                if idx + 1 < len(engine_order):
                    logger.warning(
                        "[UnifiedTTSProvider] %s synthesis failed (%s). Falling back to %s...",
                        candidate,
                        err,
                        engine_order[idx + 1],
                    )

        assert last_err is not None
        raise last_err

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
        audio_bytes, _, _, _ = await self._synthesize_internal(
            text,
            options,
            engine=engine,
            language=language,
            voice=voice,
            speed=speed,
            pitch=pitch,
            cancel_event=cancel_event,
            signal=signal,
        )
        return audio_bytes

    async def synthesize_audio(
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
    ) -> SynthesizedAudio:
        """Synthesizes text and returns audio bytes with detected container and provenance metadata."""
        audio_bytes, used_engine, used_voice, fallback_from = await self._synthesize_internal(
            text,
            options,
            engine=engine,
            language=language,
            voice=voice,
            speed=speed,
            pitch=pitch,
            cancel_event=cancel_event,
            signal=signal,
        )
        fmt = detect_audio_format(audio_bytes)
        mime_map = {
            "wav": "audio/wav",
            "ogg": "audio/ogg",
            "mp3": "audio/mpeg",
        }
        return SynthesizedAudio(
            data=audio_bytes,
            audio_format=fmt,
            filename_extension=fmt,
            mime_type=mime_map.get(fmt, "audio/mpeg"),
            engine=used_engine,
            voice_used=used_voice,
            fallback_from=fallback_from,
        )


__all__ = [
    "UnifiedTTSProvider",
    "EdgeTTSProvider",
    "GTTSProvider",
    "EspeakProvider",
    "AbortError",
    "SynthesizedAudio",
    "StreamingAudio",
    "detect_audio_format",
    "normalize_engine_name",
]
