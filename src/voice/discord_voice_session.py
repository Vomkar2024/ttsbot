"""Per-guild voice playback session managing SerialQueue, FFmpegPCMAudio, and Discord VoiceClient."""

import asyncio
import io
import logging
from typing import Any, Callable, Optional

import discord

from ..constants import DEFAULT_MAX_QUEUE_SIZE, DEFAULT_VOICE_DISCONNECT_GRACE_MS
from ..tts.espeak_provider import AbortError
from ..tts.unified_provider import UnifiedTTSProvider
from .serial_queue import QueueJob, SerialQueue

logger = logging.getLogger(__name__)


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
        if configured_ffmpeg == "ffmpeg":
            try:
                from .session_manager import resolve_ffmpeg_path

                self.ffmpeg_path = resolve_ffmpeg_path(configured_ffmpeg)
            except Exception:
                self.ffmpeg_path = configured_ffmpeg
        else:
            self.ffmpeg_path = configured_ffmpeg

        vc_loop = getattr(voice_client, "loop", None)
        self.serial_queue = SerialQueue(
            guild_id=self.guild_id,
            max_size=self.max_queue_size,
            on_process_job=self._process_job,
            loop=vc_loop,
        )

        self.is_destroyed = False
        self._reconnect_task: Optional[asyncio.Task] = None

    async def _process_job(self, job: QueueJob, cancel_event: asyncio.Event) -> None:
        """Synthesizes audio via eSpeak and streams it through discord.VoiceClient."""
        if self.is_destroyed or cancel_event.is_set():
            raise AbortError("Job aborted before start")

        text_to_speak = job.spoken_text or job.text
        logger.info(
            "[VoiceSession Guild %s] Synthesizing speech: '%s' (lang=%s, voice=%s)",
            self.guild_id,
            text_to_speak,
            job.language,
            job.voice,
        )

        # 1. Synthesize audio buffer via unified TTS provider
        audio_bytes = await self.tts_provider.synthesize(
            text=text_to_speak,
            language=job.language,
            voice=job.voice,
            cancel_event=cancel_event,
        )

        if cancel_event.is_set():
            raise AbortError("Job aborted after synthesis")

        logger.info(
            "[VoiceSession Guild %s] Synthesis complete (%d bytes). Starting Discord playback...",
            self.guild_id,
            len(audio_bytes),
        )

        # 2. Play audio stream through VoiceClient
        if not self.voice_client or not self.voice_client.is_connected():
            raise AbortError("Voice client disconnected before playback")

        loop = asyncio.get_running_loop()
        play_finished = asyncio.Event()
        play_error: Optional[Exception] = None

        def _after_play(error: Optional[Exception]) -> None:
            nonlocal play_error
            if error:
                play_error = error
            loop.call_soon_threadsafe(play_finished.set)

        try:
            raw_source = discord.FFmpegPCMAudio(
                io.BytesIO(audio_bytes),
                pipe=True,
                executable=self.ffmpeg_path,
            )
            audio_source = discord.PCMVolumeTransformer(raw_source, volume=1.0)
        except Exception as e:
            logger.error(
                "[VoiceSession Guild %s] Failed to create audio source: %s",
                self.guild_id,
                e,
            )
            raise e

        try:
            # Stop any previous lingering audio on voice client
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()

            try:
                self.voice_client.play(audio_source, after=_after_play)
            except Exception as e:
                raise e

            # Wait for either completion or cancellation
            cancel_waiter = loop.create_task(cancel_event.wait())
            finish_waiter = loop.create_task(play_finished.wait())

            done, pending = await asyncio.wait(
                [cancel_waiter, finish_waiter],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()

            if cancel_event.is_set():
                try:
                    if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
                        self.voice_client.stop()
                except Exception:
                    pass
                raise AbortError("Playback cancelled by user")

            if play_error:
                raise play_error
        finally:
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
        """Plays a pure sine wave test tone through the active voice connection.

        Args:
            duration: Tone length in seconds (default 3.0).
            frequency: Frequency in Hz (default 440.0 Hz).
            volume: Amplitude from 0.0 to 1.0 (default 0.5).
            use_ffmpeg: If True, tests the FFmpeg decoding pipeline via an in-memory WAV.
                        If False, streams PCM directly via ToneAudioSource without FFmpeg.
        """
        if not self.voice_client or not self.voice_client.is_connected():
            raise AbortError("Voice client disconnected before tone playback")

        from .tone_generator import ToneAudioSource, generate_sine_wav

        if use_ffmpeg:
            wav_bytes = generate_sine_wav(
                frequency=frequency,
                duration=duration,
                volume=volume,
            )
            raw_source = discord.FFmpegPCMAudio(
                io.BytesIO(wav_bytes),
                pipe=True,
                executable=self.ffmpeg_path,
            )
            audio_source = discord.PCMVolumeTransformer(raw_source, volume=1.0)
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

        try:
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()

            self.voice_client.play(audio_source, after=_after_play)
            await play_finished.wait()

            if play_error:
                raise play_error
        finally:
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
                    # Schedule disconnect in running loop if available
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(self.voice_client.disconnect(force=True))
                    except RuntimeError:
                        pass
        except Exception:
            pass


__all__ = ["DiscordVoiceSession"]

