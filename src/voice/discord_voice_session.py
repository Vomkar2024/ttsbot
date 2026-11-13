"""Per-guild voice playback session managing SerialQueue, FFmpegPCMAudio, diagnostics, and Discord VoiceClient."""

import asyncio
import io
import logging
import queue
import struct
import time
from typing import Any, Callable, Optional

import discord

try:
    import audioop  # audioop-lts on Python 3.13+
except ImportError:  # pragma: no cover
    audioop = None  # type: ignore

from ..constants import DEFAULT_MAX_QUEUE_SIZE, DEFAULT_VOICE_DISCONNECT_GRACE_MS
from ..tts.espeak_provider import AbortError
from ..tts.unified_provider import StreamingAudio, UnifiedTTSProvider, detect_audio_format
from .dave_compat import ensure_opus_loaded, inspect_voice_connection_state
from .serial_queue import QueueJob, SerialQueue

logger = logging.getLogger(__name__)

# 48,000 Hz * 2 channels * 2 bytes/sample (16-bit stereo PCM expected by Discord)
_PCM_BYTES_PER_SECOND = 48000 * 2 * 2
_ORIG_FFMPEG_PCM_AUDIO = discord.FFmpegPCMAudio
_LOOKAHEAD_PREFETCH_LIMIT = 2


class _BoundedStderrBuffer:
    """Thread-safe, memory-bounded stderr collector compatible with discord.FFmpegAudio."""

    def __init__(self, max_bytes: int = 16384):
        self._max_bytes = max_bytes
        self._buf = bytearray()

    def write(self, data: bytes) -> int:
        if not data:
            return 0
        remaining = self._max_bytes - len(self._buf)
        if remaining > 0:
            self._buf.extend(data[:remaining])
        return len(data)

    def get_text(self) -> str:
        return bytes(self._buf).decode("utf-8", errors="replace").strip()


class _ChunkedAudioStream(io.RawIOBase):
    """Thread-safe readable byte stream fed incrementally by an async TTS chunk producer."""

    def __init__(self, first_chunk: bytes = b""):
        self._q: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._pending = bytearray(first_chunk) if first_chunk else bytearray()
        self._eof = False
        self._closed = False
        self.total_bytes = len(first_chunk)

    def push(self, chunk: bytes) -> None:
        if chunk and not self._closed:
            self.total_bytes += len(chunk)
            self._q.put(chunk)

    def finish(self) -> None:
        self._q.put(None)

    def close_stream(self) -> None:
        self._closed = True
        self._q.put(None)

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            return b""
        if size == 0:
            return b""
        if not self._pending and not self._eof:
            item = self._q.get()
            if item is None:
                self._eof = True
                return b""
            self._pending.extend(item)
        if not self._pending:
            return b""
        if size is None or size < 0:
            out = bytes(self._pending)
            self._pending.clear()
            return out
        n = min(size, len(self._pending))
        out = bytes(self._pending[:n])
        del self._pending[:n]
        return out

    def readinto(self, b: bytearray) -> int:
        data = self.read(len(b))
        if not data:
            return 0
        b[: len(data)] = data
        return len(data)


class _StreamingFFmpegPCMAudio(discord.FFmpegPCMAudio):
    """FFmpegPCMAudio subclass that flushes stdin after each chunk for minimal streaming latency."""

    def _pipe_writer(self, source: io.BufferedIOBase) -> None:
        while self._process:
            data = source.read(self.BLOCKSIZE)
            if not data:
                if self._stdin is not None:
                    try:
                        self._stdin.close()
                    except Exception:
                        pass
                return
            try:
                if self._stdin is not None:
                    self._stdin.write(data)
                    self._stdin.flush()
            except Exception:
                try:
                    self._process.terminate()
                except Exception:
                    pass
                return

    def cleanup(self) -> None:
        try:
            source = getattr(self, "_source_stream", None)
            if source is not None and hasattr(source, "close_stream"):
                source.close_stream()
        except Exception:
            pass
        super().cleanup()


def _build_ffmpeg_flags(audio_format: str) -> tuple[str, str]:
    """Builds low-latency FFmpeg input/output flags without dropping short inputs."""
    fmt = (audio_format or "").strip().lower()
    if fmt in ("mp3", "wav", "ogg"):
        before_options = f"-f {fmt} -probesize 1024 -analyzeduration 0"
    else:
        before_options = "-probesize 4096 -analyzeduration 0"
    options = "-flush_packets 1"
    return before_options, options


class _DiagnosticAudioSource(discord.AudioSource):
    """Wraps an AudioSource to track PCM frames/amplitude and forward underlying FFmpeg errors."""

    def __init__(self, inner_source: discord.AudioSource, raw_source: Any = None):
        self.inner_source = inner_source
        self.raw_source = (
            raw_source if raw_source is not None else getattr(inner_source, "original", inner_source)
        )
        self.read_calls = 0
        self.frames_read = 0
        self.pcm_bytes = 0
        self.non_zero_frames = 0
        self.peak_abs_sample = 0

    def is_opus(self) -> bool:
        return bool(self.inner_source.is_opus())

    @property
    def _current_error(self) -> Optional[BaseException]:
        # Forward underlying FFmpegProcessError masked by PCMVolumeTransformer
        err = getattr(self.inner_source, "_current_error", None)
        if isinstance(err, BaseException):
            return err
        raw_err = getattr(self.raw_source, "_current_error", None)
        if isinstance(raw_err, BaseException):
            return raw_err
        return None

    def read(self) -> bytes:
        self.read_calls += 1
        data = self.inner_source.read()
        if data:
            self.frames_read += 1
            self.pcm_bytes += len(data)
            if audioop is not None:
                try:
                    peak = audioop.max(data, 2)
                    if peak > 0:
                        self.non_zero_frames += 1
                        if peak > self.peak_abs_sample:
                            self.peak_abs_sample = peak
                    return data
                except Exception:
                    pass
            if any(data):
                self.non_zero_frames += 1
        return data

    def cleanup(self) -> None:
        self.inner_source.cleanup()


def _estimate_audio_duration_sec(audio_bytes: bytes, fmt: str) -> float:
    """Estimates audio duration in seconds from WAV headers or typical MP3 bitrate."""
    if not audio_bytes:
        return 0.0
    if fmt == "wav" and len(audio_bytes) >= 44 and audio_bytes[:4] == b"RIFF":
        try:
            byte_rate = struct.unpack_from("<I", audio_bytes, 28)[0]
            payload_len = max(0, len(audio_bytes) - 44)
            if byte_rate > 0:
                return payload_len / float(byte_rate)
        except Exception:
            pass
    # Typical Edge-TTS (audio-24khz-48kbitrate-mono-mp3) / gTTS bitrate (~32-48 kbps)
    return (len(audio_bytes) * 8.0) / 48000.0


def _validate_audio_bytes(audio_bytes: bytes, audio_format: str) -> None:
    """Validates synthesized audio buffer is non-empty and contains sample payload."""
    if not audio_bytes or len(audio_bytes) == 0:
        raise RuntimeError("TTS engine generated 0 bytes of audio.")
    if audio_format == "wav" and len(audio_bytes) <= 44:
        raise RuntimeError(
            f"Generated WAV buffer is incomplete ({len(audio_bytes)} bytes, header only, 0 PCM sample bytes)."
        )


class DiscordVoiceSession:
    def __init__(
        self,
        guild_id: str,
        channel_id: str,
        voice_client: discord.VoiceClient,
        tts_provider: Optional[Any] = None,
        espeak_provider: Optional[Any] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        disconnect_grace_ms: int = DEFAULT_VOICE_DISCONNECT_GRACE_MS,
        ffmpeg_path: str = "ffmpeg",
        options: Optional[dict] = None,
    ):
        opts = options or {}
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.voice_client = voice_client
        self.tts_provider = tts_provider or espeak_provider or UnifiedTTSProvider()
        self.espeak_provider = self.tts_provider
        self.on_disconnect = on_disconnect or (lambda: None)
        self.max_queue_size = opts.get("maxQueueSize", opts.get("max_queue_size", max_queue_size))
        self.disconnect_grace_ms = opts.get(
            "disconnectGraceMs", opts.get("disconnect_grace_ms", disconnect_grace_ms)
        )
        self.disconnect_grace_sec = self.disconnect_grace_ms / 1000.0
        configured_ffmpeg = opts.get("ffmpegPath", opts.get("ffmpeg_path", ffmpeg_path))
        try:
            from .session_manager import resolve_ffmpeg_path

            self.ffmpeg_path = resolve_ffmpeg_path(configured_ffmpeg)
        except Exception:
            self.ffmpeg_path = configured_ffmpeg

        vc_loop = getattr(voice_client, "loop", None)
        self.serial_queue = SerialQueue(
            guild_id=self.guild_id,
            max_size=self.max_queue_size,
            on_process_job=self._process_job,
            on_enqueue=self._on_job_enqueued,
            loop=vc_loop,
        )

        self.is_destroyed = False
        self._reconnect_task: Optional[asyncio.Task] = None
        self._last_voice_diag: Optional[tuple] = None

    def _log_voice_transport_state(self) -> Optional[dict]:
        diag = inspect_voice_connection_state(self.voice_client)
        if diag is None:
            return None

        state_key = (
            diag.get("mode"),
            diag.get("dave_protocol_version"),
            diag.get("can_encrypt"),
            diag.get("dave_ready"),
            diag.get("dave_status"),
        )
        if state_key != self._last_voice_diag:
            self._last_voice_diag = state_key
            logger.info(
                "[VoiceSession Guild %s] Voice transport state: mode=%s, dave_v=%s, "
                "can_encrypt=%s, dave_ready=%s, dave_status=%s",
                self.guild_id,
                diag.get("mode"),
                diag.get("dave_protocol_version"),
                diag.get("can_encrypt"),
                diag.get("dave_ready"),
                diag.get("dave_status"),
            )

        dave_ver = diag.get("dave_protocol_version") or 0
        if dave_ver > 0 and diag.get("can_encrypt") is not True:
            logger.warning(
                "[VoiceSession Guild %s] DAVE E2EE v%s is negotiated, but encryption is NOT ready "
                "(can_encrypt=%s, dave_ready=%s, dave_status=%s). Audio packets will be sent "
                "unencrypted and dropped by Discord clients. Ensure davey<0.2.0 or DAVE compat bridge is active.",
                self.guild_id,
                dave_ver,
                diag.get("can_encrypt"),
                diag.get("dave_ready"),
                diag.get("dave_status"),
            )
        return diag

    async def _wait_for_dave_ready(
        self,
        cancel_event: Optional[asyncio.Event] = None,
        max_wait_sec: float = 2.0,
    ) -> Optional[dict]:
        """Waits briefly for active DAVE MLS key transition when connected to live Discord voice."""
        conn = getattr(self.voice_client, "_connection", None)
        if conn is None or not hasattr(conn, "ws"):
            return inspect_voice_connection_state(self.voice_client)

        deadline = time.monotonic() + max_wait_sec
        while time.monotonic() < deadline:
            if self.is_destroyed or (cancel_event and cancel_event.is_set()):
                break
            if not self.voice_client or not self.voice_client.is_connected():
                break
            diag = inspect_voice_connection_state(self.voice_client)
            if not diag:
                break
            dave_ver = diag.get("dave_protocol_version") or 0
            if dave_ver == 0 or diag.get("can_encrypt") is True:
                return diag
            await asyncio.sleep(0.01)

        return inspect_voice_connection_state(self.voice_client)

    def _on_job_enqueued(self, job: QueueJob) -> None:
        """Starts background pre-synthesis for queued jobs when another job is already active."""
        if self.is_destroyed or job.audio_bytes is not None:
            return
        if self.serial_queue.current_job is None and len(self.serial_queue.pending_queue) <= 1:
            return
        if len(self.serial_queue.pending_queue) > _LOOKAHEAD_PREFETCH_LIMIT:
            return
        self._start_prefetch_for_job(job)

    def _schedule_lookahead_prefetch(self) -> None:
        """Ensures up to _LOOKAHEAD_PREFETCH_LIMIT pending jobs are pre-synthesizing while audio plays."""
        if self.is_destroyed:
            return
        for pending_job in self.serial_queue.pending_queue[:_LOOKAHEAD_PREFETCH_LIMIT]:
            self._start_prefetch_for_job(pending_job)

    def _start_prefetch_for_job(self, job: QueueJob) -> None:
        if (
            self.is_destroyed
            or job.audio_bytes is not None
            or job.data.get("_prefetch_task") is not None
        ):
            return
        text_to_speak = job.spoken_text or job.text
        if not text_to_speak:
            return

        try:
            loop = self.serial_queue._resolve_loop()
        except Exception:
            return
        if not loop.is_running():
            return

        def _spawn() -> None:
            if (
                self.is_destroyed
                or job.audio_bytes is not None
                or job.data.get("_prefetch_task") is not None
            ):
                return
            prefetch_cancel = asyncio.Event()
            job.data["_prefetch_cancel"] = prefetch_cancel

            async def _run_prefetch() -> dict:
                audio_bytes, audio_format, used_engine, used_voice, fallback_note = (
                    await self._synthesize_buffered(
                        text_to_speak=text_to_speak,
                        language=job.language,
                        voice=job.voice,
                        cancel_event=prefetch_cancel,
                    )
                )
                return {
                    "audio_bytes": audio_bytes,
                    "audio_format": audio_format,
                    "used_engine": used_engine,
                    "used_voice": used_voice,
                    "fallback_note": fallback_note,
                }

            task = loop.create_task(_run_prefetch())
            job.data["_prefetch_task"] = task

            def _cancel_prefetch() -> None:
                prefetch_cancel.set()
                if not task.done():
                    task.cancel()

            job.add_cancel_hook(_cancel_prefetch)

        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop is loop:
            _spawn()
        else:
            loop.call_soon_threadsafe(_spawn)

    async def _synthesize_buffered(
        self,
        *,
        text_to_speak: str,
        language: str,
        voice: str,
        cancel_event: asyncio.Event,
    ) -> tuple[bytes, str, str, str, str]:
        synth_audio_fn = getattr(self.tts_provider, "synthesize_audio", None)
        if callable(synth_audio_fn):
            synth_res = await synth_audio_fn(
                text=text_to_speak,
                language=language,
                voice=voice,
                cancel_event=cancel_event,
            )
            audio_bytes = synth_res.data
            audio_format = synth_res.audio_format or detect_audio_format(audio_bytes)
            used_engine = synth_res.engine or getattr(self.tts_provider, "default_engine", "tts")
            used_voice = synth_res.voice_used or voice
            fallback_note = (
                f" (fallback from {synth_res.fallback_from})"
                if getattr(synth_res, "fallback_from", None)
                else ""
            )
            return audio_bytes, audio_format, used_engine, used_voice, fallback_note

        audio_bytes = await self.tts_provider.synthesize(
            text=text_to_speak,
            language=language,
            voice=voice,
            cancel_event=cancel_event,
        )
        audio_format = detect_audio_format(audio_bytes)
        used_engine = getattr(self.tts_provider, "default_engine", "tts")
        used_voice = voice
        return audio_bytes, audio_format, used_engine, used_voice, ""

    def _check_voice_output_health(self) -> None:
        """Checks Discord server mute / self mute / Stage suppress flags safely."""
        guild = getattr(self.voice_client, "guild", None)
        me = getattr(guild, "me", None) if guild is not None else None
        voice_state = getattr(me, "voice", None) if me is not None else None
        if voice_state is None:
            return

        if getattr(voice_state, "mute", None) is True:
            logger.warning(
                "[VoiceSession Guild %s] Bot is SERVER MUTED in the voice channel! "
                "Unmute the bot in Discord Server/Voice Channel settings or no audio will be heard.",
                self.guild_id,
            )
        if getattr(voice_state, "self_mute", None) is True:
            logger.warning(
                "[VoiceSession Guild %s] Bot voice state reports self_mute=True.",
                self.guild_id,
            )
        if getattr(voice_state, "suppress", None) is True:
            logger.warning(
                "[VoiceSession Guild %s] Bot is SUPPRESSED (e.g., Stage channel listener). "
                "Promote the bot to Speaker so users can hear TTS audio.",
                self.guild_id,
            )

    async def _process_job(self, job: QueueJob, cancel_event: asyncio.Event) -> None:
        """Synthesizes TTS speech and streams it through Discord VoiceClient with stage logging."""
        if self.is_destroyed or cancel_event.is_set():
            raise AbortError("Job aborted before start")

        text_to_speak = job.spoken_text or job.text
        preview = text_to_speak if len(text_to_speak) <= 80 else f"{text_to_speak[:77]}..."

        # Stage 1: Text Received
        logger.info(
            "[AudioPipeline Guild %s Job #%s] [Stage 1/6: Text Received] "
            "Author=%s | Chars=%d | Lang=%s | VoicePref=%s | Text=\"%s\"",
            self.guild_id,
            job.id,
            job.author_name,
            len(text_to_speak),
            job.language,
            job.voice,
            preview,
        )

        # Stage 2 & 3: Voice Pack Selection & Audio Synthesis (or Live Stream Open)
        stream_handle: Optional[StreamingAudio] = None
        audio_bytes: Optional[bytes] = None
        audio_format: str = "mp3"
        used_engine: str = ""
        used_voice: str = job.voice or "default"
        fallback_note: str = ""

        if job.audio_bytes is not None:
            audio_bytes = job.audio_bytes
            audio_format = detect_audio_format(audio_bytes)
            used_engine = "pre-synthesized"
            used_voice = job.voice or "default"
            fallback_note = ""
            logger.info(
                "[AudioPipeline Guild %s Job #%s] [Stage 2/6: Voice Selected] "
                "Engine=pre-synthesized | VoicePack=%s | Lang=%s",
                self.guild_id,
                job.id,
                used_voice,
                job.language,
            )
        else:
            resolve_fn = getattr(self.tts_provider, "resolve_selection", None)
            if callable(resolve_fn):
                sel = resolve_fn(language=job.language, voice=job.voice)
                logger.info(
                    "[AudioPipeline Guild %s Job #%s] [Stage 2/6: Voice Selected] "
                    "PrimaryEngine=%s | VoicePack=%s | Lang=%s | FallbackOrder=%s",
                    self.guild_id,
                    job.id,
                    sel.get("primary_engine"),
                    sel.get("resolved_voice"),
                    sel.get("language"),
                    "->".join(sel.get("engine_order", [])),
                )
            else:
                logger.info(
                    "[AudioPipeline Guild %s Job #%s] [Stage 2/6: Voice Selected] "
                    "PrimaryEngine=custom | VoicePack=%s | Lang=%s",
                    self.guild_id,
                    job.id,
                    job.voice,
                    job.language,
                )

            # Check if a lookahead pre-synthesis task was already started while previous audio played
            prefetch_task: Optional[asyncio.Task] = job.data.get("_prefetch_task")
            prefetch_cancel: Optional[asyncio.Event] = job.data.get("_prefetch_cancel")
            if prefetch_task is not None:
                cancel_waiter_pf = asyncio.create_task(cancel_event.wait())
                try:
                    done_pf, _ = await asyncio.wait(
                        [prefetch_task, cancel_waiter_pf],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if cancel_event.is_set():
                        if prefetch_cancel is not None:
                            prefetch_cancel.set()
                        if not prefetch_task.done():
                            prefetch_task.cancel()
                        raise AbortError("Job cancelled while awaiting pre-synthesized audio")
                    if prefetch_task in done_pf and not prefetch_task.cancelled():
                        pf_exc = prefetch_task.exception()
                        if pf_exc is None:
                            pf_res = prefetch_task.result()
                            audio_bytes = pf_res["audio_bytes"]
                            audio_format = pf_res["audio_format"]
                            used_engine = pf_res["used_engine"]
                            used_voice = pf_res["used_voice"]
                            fallback_note = pf_res["fallback_note"]
                        elif isinstance(pf_exc, AbortError):
                            raise pf_exc
                        else:
                            logger.debug(
                                "[AudioPipeline Guild %s Job #%s] Prefetch failed (%s); falling back to direct synthesis.",
                                self.guild_id,
                                job.id,
                                pf_exc,
                            )
                finally:
                    cancel_waiter_pf.cancel()

            if audio_bytes is None:
                try:
                    open_stream_fn = getattr(self.tts_provider, "open_audio_stream", None)
                    if callable(open_stream_fn):
                        stream_handle = await open_stream_fn(
                            text=text_to_speak,
                            language=job.language,
                            voice=job.voice,
                            cancel_event=cancel_event,
                        )
                        audio_format = stream_handle.audio_format
                        used_engine = stream_handle.engine
                        used_voice = stream_handle.voice_used or job.voice
                        fallback_note = (
                            f" (fallback from {stream_handle.fallback_from})"
                            if stream_handle.fallback_from
                            else ""
                        )
                        if not stream_handle.is_streaming:
                            audio_bytes = stream_handle.first_chunk
                            stream_handle = None
                    else:
                        (
                            audio_bytes,
                            audio_format,
                            used_engine,
                            used_voice,
                            fallback_note,
                        ) = await self._synthesize_buffered(
                            text_to_speak=text_to_speak,
                            language=job.language,
                            voice=job.voice,
                            cancel_event=cancel_event,
                        )
                except AbortError:
                    logger.info(
                        "[AudioPipeline Guild %s Job #%s] [Stage 3/6: Synthesis Cancelled] Aborted by user.",
                        self.guild_id,
                        job.id,
                    )
                    raise
                except Exception as synth_err:
                    logger.error(
                        "[AudioPipeline Guild %s Job #%s] [Stage 3/6: Audio FAILED] TTS synthesis failed: %s",
                        self.guild_id,
                        job.id,
                        synth_err,
                        exc_info=True,
                    )
                    raise

        if cancel_event.is_set():
            if stream_handle is not None and stream_handle.chunk_iter is not None:
                aclose = getattr(stream_handle.chunk_iter, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
            raise AbortError("Job aborted after synthesis")

        if stream_handle is not None:
            logger.info(
                "[AudioPipeline Guild %s Job #%s] [Stage 3/6: Audio Stream Ready] "
                "Engine=%s%s | VoiceUsed=%s | Format=%s | FirstChunk=%d bytes (live streaming)",
                self.guild_id,
                job.id,
                used_engine,
                fallback_note,
                used_voice,
                audio_format,
                len(stream_handle.first_chunk),
            )
            input_desc = f"live-stream ({len(stream_handle.first_chunk)}+ bytes {audio_format})"
        else:
            assert audio_bytes is not None
            try:
                _validate_audio_bytes(audio_bytes, audio_format)
            except Exception as val_err:
                logger.error(
                    "[AudioPipeline Guild %s Job #%s] [Stage 3/6: Audio FAILED] Invalid audio output: %s",
                    self.guild_id,
                    job.id,
                    val_err,
                )
                raise

            est_duration_sec = _estimate_audio_duration_sec(audio_bytes, audio_format)
            logger.info(
                "[AudioPipeline Guild %s Job #%s] [Stage 3/6: Audio Generated] "
                "Engine=%s%s | VoiceUsed=%s | Format=%s | Size=%d bytes | EstDuration=%.2fs",
                self.guild_id,
                job.id,
                used_engine,
                fallback_note,
                used_voice,
                audio_format,
                len(audio_bytes),
                est_duration_sec,
            )
            input_desc = f"{len(audio_bytes)} bytes ({audio_format})"

        # Verify voice client before creating FFmpeg subprocess
        if not self.voice_client or not self.voice_client.is_connected():
            if stream_handle is not None and stream_handle.chunk_iter is not None:
                aclose = getattr(stream_handle.chunk_iter, "aclose", None)
                if callable(aclose):
                    try:
                        await aclose()
                    except Exception:
                        pass
            logger.error(
                "[AudioPipeline Guild %s Job #%s] [Stage 4/6: FFmpeg/Source FAILED] "
                "Voice client disconnected before source creation.",
                self.guild_id,
                job.id,
            )
            raise AbortError("Voice client disconnected before playback")

        # Stage 4: FFmpeg / AudioSource Creation
        ffmpeg_stderr_buf = _BoundedStderrBuffer()
        chunk_stream: Optional[_ChunkedAudioStream] = None
        pump_task: Optional[asyncio.Task] = None
        stream_error: Optional[BaseException] = None
        before_opts, after_opts = _build_ffmpeg_flags(audio_format)

        try:
            if stream_handle is not None:
                chunk_stream = _ChunkedAudioStream(stream_handle.first_chunk)
                use_flushing_cls = discord.FFmpegPCMAudio is _ORIG_FFMPEG_PCM_AUDIO
                ffmpeg_cls = _StreamingFFmpegPCMAudio if use_flushing_cls else discord.FFmpegPCMAudio
                raw_source = ffmpeg_cls(
                    chunk_stream,
                    pipe=True,
                    executable=self.ffmpeg_path,
                    stderr=ffmpeg_stderr_buf,
                    before_options=before_opts,
                    options=after_opts,
                )
                if use_flushing_cls:
                    setattr(raw_source, "_source_stream", chunk_stream)

                async def _pump_chunks() -> None:
                    nonlocal stream_error
                    collected = bytearray(stream_handle.first_chunk)
                    completed_cleanly = False
                    try:
                        assert stream_handle.chunk_iter is not None
                        async for next_chunk in stream_handle.chunk_iter:
                            if self.is_destroyed or cancel_event.is_set():
                                break
                            if next_chunk:
                                collected.extend(next_chunk)
                                chunk_stream.push(next_chunk)
                        else:
                            completed_cleanly = not self.is_destroyed and not cancel_event.is_set()
                    except AbortError:
                        pass
                    except Exception as pump_exc:
                        stream_error = pump_exc
                        logger.warning(
                            "[AudioPipeline Guild %s Job #%s] Live TTS chunk stream error: %s",
                            self.guild_id,
                            job.id,
                            pump_exc,
                        )
                    finally:
                        chunk_stream.finish()
                        aclose = getattr(stream_handle.chunk_iter, "aclose", None)
                        if callable(aclose):
                            try:
                                await aclose()
                            except Exception:
                                pass
                        if completed_cleanly and callable(stream_handle.on_complete):
                            try:
                                stream_handle.on_complete(bytes(collected))
                            except Exception:
                                pass

                pump_task = asyncio.create_task(_pump_chunks())
            else:
                assert audio_bytes is not None
                raw_source = discord.FFmpegPCMAudio(
                    io.BytesIO(audio_bytes),
                    pipe=True,
                    executable=self.ffmpeg_path,
                    stderr=ffmpeg_stderr_buf,
                    before_options=before_opts,
                    options=after_opts,
                )

            vol_source = discord.PCMVolumeTransformer(raw_source, volume=1.0)
            audio_source = _DiagnosticAudioSource(vol_source, raw_source=raw_source)
            logger.info(
                "[AudioPipeline Guild %s Job #%s] [Stage 4/6: FFmpeg/Source Created] "
                "Executable=\"%s\" | Input=%s | TargetPCM=48kHz-stereo-s16le",
                self.guild_id,
                job.id,
                self.ffmpeg_path,
                input_desc,
            )
        except Exception as e:
            if pump_task is not None and not pump_task.done():
                pump_task.cancel()
            if chunk_stream is not None:
                chunk_stream.close_stream()
            logger.error(
                "[AudioPipeline Guild %s Job #%s] [Stage 4/6: FFmpeg/Source FAILED] "
                "Failed creating FFmpegPCMAudio (executable=\"%s\"): %s",
                self.guild_id,
                job.id,
                self.ffmpeg_path,
                e,
                exc_info=True,
            )
            raise

        # Stage 5: Transport Checks & Playback Start
        await self._wait_for_dave_ready(cancel_event=cancel_event, max_wait_sec=2.0)
        if cancel_event.is_set():
            if pump_task is not None and not pump_task.done():
                pump_task.cancel()
            if chunk_stream is not None:
                chunk_stream.close_stream()
            try:
                audio_source.cleanup()
            except Exception:
                pass
            raise AbortError("Playback cancelled while waiting for DAVE encryption")

        diag = self._log_voice_transport_state()
        self._check_voice_output_health()
        opus_ready = ensure_opus_loaded()

        loop = asyncio.get_running_loop()
        play_finished = asyncio.Event()
        play_error: Optional[BaseException] = None

        def _after_play(error: Optional[Exception]) -> None:
            nonlocal play_error
            if error:
                play_error = error
            loop.call_soon_threadsafe(play_finished.set)

        play_started = False
        play_t0 = time.monotonic()
        try:
            # Stop any lingering player and yield briefly so SpeakingState.none doesn't race SpeakingState.voice
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
                await asyncio.sleep(0.01)

            self.voice_client.play(audio_source, after=_after_play)
            play_started = True

            # Pre-synthesize upcoming queued items while this audio is playing
            self._schedule_lookahead_prefetch()

            logger.info(
                "[AudioPipeline Guild %s Job #%s] [Stage 5/6: Playback Started] "
                "Channel=%s | OpusLoaded=%s | can_encrypt=%s",
                self.guild_id,
                job.id,
                self.channel_id,
                opus_ready,
                diag.get("can_encrypt") if diag else "unknown",
            )

            # Wait for completion or cancellation
            cancel_waiter = loop.create_task(cancel_event.wait())
            finish_waiter = loop.create_task(play_finished.wait())

            done, pending = await asyncio.wait(
                [cancel_waiter, finish_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

            if cancel_event.is_set():
                if pump_task is not None and not pump_task.done():
                    pump_task.cancel()
                if chunk_stream is not None:
                    chunk_stream.close_stream()
                try:
                    if self.voice_client and (
                        self.voice_client.is_playing() or self.voice_client.is_paused()
                    ):
                        self.voice_client.stop()
                except Exception:
                    pass
                logger.info(
                    "[AudioPipeline Guild %s Job #%s] [Stage 6/6: Playback Cancelled] "
                    "Stopped after %d PCM frames (%d bytes).",
                    self.guild_id,
                    job.id,
                    audio_source.frames_read,
                    audio_source.pcm_bytes,
                )
                raise AbortError("Playback cancelled by user")

            if pump_task is not None and not pump_task.done():
                await asyncio.gather(pump_task, return_exceptions=True)

            # Flush FFmpeg stderr pipe thread briefly if still alive
            reader_thread = getattr(raw_source, "_pipe_reader_thread", None)
            if reader_thread is not None and hasattr(reader_thread, "join"):
                try:
                    if not hasattr(reader_thread, "is_alive") or reader_thread.is_alive():
                        reader_thread.join(timeout=0.01)
                except Exception:
                    pass

            proc = getattr(raw_source, "_process", None)
            raw_rc = getattr(proc, "returncode", None) if proc is not None else None
            ffmpeg_rc = raw_rc if (isinstance(raw_rc, int) and not isinstance(raw_rc, bool)) else None
            ffmpeg_stderr = ffmpeg_stderr_buf.get_text()
            if len(ffmpeg_stderr) > 800:
                ffmpeg_stderr = "..." + ffmpeg_stderr[-800:]

            elapsed_sec = time.monotonic() - play_t0
            pcm_duration_sec = audio_source.pcm_bytes / float(_PCM_BYTES_PER_SECOND)
            source_err = audio_source._current_error

            effective_error: Optional[BaseException] = play_error or source_err or stream_error
            if effective_error is None and ffmpeg_rc not in (None, 0):
                effective_error = RuntimeError(
                    f"FFmpeg exited with status {ffmpeg_rc}"
                    + (f": {ffmpeg_stderr}" if ffmpeg_stderr else "")
                )
            elif (
                effective_error is None
                and audio_source.read_calls > 0
                and audio_source.frames_read == 0
            ):
                effective_error = RuntimeError(
                    "FFmpeg produced 0 PCM audio frames (stream empty or failed to decode)"
                    + (f"; FFmpeg stderr: {ffmpeg_stderr}" if ffmpeg_stderr else "")
                )

            if effective_error is not None:
                logger.error(
                    "[AudioPipeline Guild %s Job #%s] [Stage 6/6: Playback FAILED] "
                    "Elapsed=%.2fs | Frames=%d | PCMBytes=%d | FFmpegExit=%s | Error=%s | FFmpegStderr=\"%s\"",
                    self.guild_id,
                    job.id,
                    elapsed_sec,
                    audio_source.frames_read,
                    audio_source.pcm_bytes,
                    ffmpeg_rc,
                    effective_error,
                    ffmpeg_stderr or "<none>",
                )
                raise effective_error

            logger.info(
                "[AudioPipeline Guild %s Job #%s] [Stage 6/6: Playback Finished] "
                "Elapsed=%.2fs | PCMDuration=%.2fs | Frames=%d (%d non-zero) | PCMBytes=%d | PeakSample=%d",
                self.guild_id,
                job.id,
                elapsed_sec,
                pcm_duration_sec,
                audio_source.frames_read,
                audio_source.non_zero_frames,
                audio_source.pcm_bytes,
                audio_source.peak_abs_sample,
            )

            if audio_source.frames_read > 0 and audio_source.non_zero_frames == 0:
                logger.warning(
                    "[AudioPipeline Guild %s Job #%s] Transmitted %d PCM frames (%d bytes), "
                    "but ALL samples were digital silence (PeakSample=0)!",
                    self.guild_id,
                    job.id,
                    audio_source.frames_read,
                    audio_source.pcm_bytes,
                )

            if ffmpeg_stderr:
                logger.debug(
                    "[AudioPipeline Guild %s Job #%s] FFmpeg stderr (exit=%s): %s",
                    self.guild_id,
                    job.id,
                    ffmpeg_rc,
                    ffmpeg_stderr,
                )
        except Exception as exc:
            if not play_started and not isinstance(exc, AbortError):
                logger.error(
                    "[AudioPipeline Guild %s Job #%s] [Stage 5/6: Playback FAILED to Start] %s",
                    self.guild_id,
                    job.id,
                    exc,
                    exc_info=True,
                )
            raise
        finally:
            if pump_task is not None and not pump_task.done():
                pump_task.cancel()
            if not play_started:
                if chunk_stream is not None:
                    chunk_stream.close_stream()
                try:
                    audio_source.cleanup()
                except Exception:
                    pass

    async def play_test_tone(
        self,
        duration: float = 3.0,
        frequency: float = 440.0,
        volume: float = 0.5,
        use_ffmpeg: bool = False,
    ) -> None:
        """Plays a pure sine wave test tone through the active voice connection."""
        if not self.voice_client or not self.voice_client.is_connected():
            raise AbortError("Voice client disconnected before tone playback")

        self._log_voice_transport_state()

        from .tone_generator import ToneAudioSource, generate_sine_wav

        if use_ffmpeg:
            wav_bytes = generate_sine_wav(
                frequency=frequency,
                duration=duration,
                volume=volume,
            )
            stderr_buf = _BoundedStderrBuffer()
            before_opts, after_opts = _build_ffmpeg_flags("wav")
            raw_source = discord.FFmpegPCMAudio(
                io.BytesIO(wav_bytes),
                pipe=True,
                executable=self.ffmpeg_path,
                stderr=stderr_buf,
                before_options=before_opts,
                options=after_opts,
            )
            audio_source: discord.AudioSource = _DiagnosticAudioSource(
                discord.PCMVolumeTransformer(raw_source, volume=1.0),
                raw_source=raw_source,
            )
        else:
            audio_source = ToneAudioSource(
                frequency=frequency,
                duration=duration,
                volume=volume,
            )

        loop = asyncio.get_running_loop()
        play_finished = asyncio.Event()
        play_error: Optional[Exception] = None

        def _after_play(error: Optional[Exception]) -> None:
            nonlocal play_error
            if error:
                play_error = error
            loop.call_soon_threadsafe(play_finished.set)

        play_started = False
        try:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
                await asyncio.sleep(0.01)

            self.voice_client.play(audio_source, after=_after_play)
            play_started = True
            await play_finished.wait()

            if play_error:
                raise play_error
        finally:
            if not play_started:
                try:
                    audio_source.cleanup()
                except Exception:
                    pass

    async def destroy_async(self) -> None:
        """Closes player, drops queue, and disconnects the voice client."""
        if self.is_destroyed:
            return
        self.is_destroyed = True

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()

        try:
            self.serial_queue.stop()
        except Exception:
            pass

        try:
            if self.voice_client:
                if self.voice_client.is_playing():
                    self.voice_client.stop()
                if self.voice_client.is_connected():
                    await self.voice_client.disconnect(force=True)
        except Exception:
            pass

    def destroy(self) -> None:
        """Synchronous wrapper to trigger cleanup."""
        if self.is_destroyed:
            return
        self.is_destroyed = True

        try:
            self.serial_queue.stop()
        except Exception:
            pass

        try:
            if self.voice_client:
                if self.voice_client.is_playing():
                    self.voice_client.stop()
                if self.voice_client.is_connected():
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.voice_client.disconnect(force=True))
                    except RuntimeError:
                        pass
        except Exception:
            pass


__all__ = [
    "DiscordVoiceSession",
    "_DiagnosticAudioSource",
    "_BoundedStderrBuffer",
    "_ChunkedAudioStream",
    "_StreamingFFmpegPCMAudio",
    "_build_ffmpeg_flags",
]
