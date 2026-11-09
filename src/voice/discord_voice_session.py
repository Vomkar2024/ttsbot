"""Per-guild voice playback session managing SerialQueue, FFmpegPCMAudio, and Discord VoiceClient."""

import asyncio
import io
import logging
from typing import Any, Callable, Optional

import discord

from ..constants import DEFAULT_MAX_QUEUE_SIZE, DEFAULT_VOICE_DISCONNECT_GRACE_MS
from ..tts.espeak_provider import AbortError, EspeakProvider
from .serial_queue import QueueJob, SerialQueue

logger = logging.getLogger(__name__)


class DiscordVoiceSession:
    def __init__(
        self,
        guild_id: str,
        channel_id: str,
        voice_client: discord.VoiceClient,
        espeak_provider: Optional[EspeakProvider] = None,
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
        self.espeak_provider = espeak_provider or EspeakProvider()
        self.on_disconnect = on_disconnect or (lambda: None)
        self.max_queue_size = opts.get("maxQueueSize", opts.get("max_queue_size", max_queue_size))
        self.disconnect_grace_ms = opts.get(
            "disconnectGraceMs", opts.get("disconnect_grace_ms", disconnect_grace_ms)
        )
        self.disconnect_grace_sec = self.disconnect_grace_ms / 1000.0
        self.ffmpeg_path = opts.get("ffmpegPath", opts.get("ffmpeg_path", ffmpeg_path))

        self.serial_queue = SerialQueue(
            guild_id=self.guild_id,
            max_size=self.max_queue_size,
            on_process_job=self._process_job,
        )

        self.is_destroyed = False
        self._reconnect_task: Optional[asyncio.Task] = None

    async def _process_job(self, job: QueueJob, cancel_event: asyncio.Event) -> None:
        """Synthesizes audio via eSpeak and streams it through discord.VoiceClient."""
        if self.is_destroyed or cancel_event.is_set():
            raise AbortError("Job aborted before start")

        text_to_speak = job.spoken_text or job.text

        # 1. Synthesize audio buffer via eSpeak NG
        wav_bytes = await self.espeak_provider.synthesize(
            text=text_to_speak,
            language=job.language,
            voice=job.voice,
            cancel_event=cancel_event,
        )

        if cancel_event.is_set():
            raise AbortError("Job aborted after synthesis")

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
            audio_source = discord.FFmpegPCMAudio(
                io.BytesIO(wav_bytes),
                pipe=True,
                executable=self.ffmpeg_path,
            )
        except Exception as e:
            logger.error(
                "[VoiceSession Guild %s] Failed to create FFmpegPCMAudio: %s",
                self.guild_id,
                e,
            )
            raise e

        # Stop any previous lingering audio on voice client
        if self.voice_client.is_playing() or self.voice_client.is_paused():
            self.voice_client.stop()

        try:
            self.voice_client.play(audio_source, after=_after_play)
        except Exception as e:
            try:
                audio_source.cleanup()
            except Exception:
                pass
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
                if self.voice_client and self.voice_client.is_playing():
                    self.voice_client.stop()
            except Exception:
                pass
            raise AbortError("Playback cancelled by user")

        if play_error:
            raise play_error

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

