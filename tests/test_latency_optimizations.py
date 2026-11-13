"""Unit and integration tests for low-latency streaming, caching, prefetching, and concurrency."""

import asyncio
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.hinglish.service import DELIVERY_AUTO, HinglishService
from src.translation.openai_translator_provider import OpenAITranslatorProvider
from src.tts.edge_tts_provider import EdgeTTSProvider
from src.tts.unified_provider import StreamingAudio, UnifiedTTSProvider
from src.voice.discord_voice_session import (
    DiscordVoiceSession,
    _BoundedStderrBuffer,
    _ChunkedAudioStream,
    _StreamingFFmpegPCMAudio,
    _build_ffmpeg_flags,
)
from src.voice.serial_queue import QueueJob, SerialQueue
from src.voice.session_manager import resolve_ffmpeg_path
from src.voice.tone_generator import generate_sine_wav


class TestEdgeTTSStreamingAndTimeouts(unittest.IsolatedAsyncioTestCase):
    async def test_stream_chunks_yields_incrementally_and_forwards_connect_timeout(self):
        captured_kwargs = []
        second_chunk_gate = asyncio.Event()

        class FakeCommunicate:
            def __init__(self, text, voice, rate="+0%", pitch="+0Hz", connect_timeout=10):
                captured_kwargs.append(
                    {
                        "text": text,
                        "voice": voice,
                        "rate": rate,
                        "pitch": pitch,
                        "connect_timeout": connect_timeout,
                    }
                )

            async def stream(self):
                yield {"type": "audio", "data": b"ID3chunk1"}
                await second_chunk_gate.wait()
                yield {"type": "WordBoundary", "data": b"ignored"}
                yield {"type": "audio", "data": b"chunk2"}

        provider = EdgeTTSProvider(timeout=10.0)
        with patch.dict("sys.modules", {"edge_tts": SimpleNamespace(Communicate=FakeCommunicate)}):
            aiter = provider.stream_chunks("Hello streaming", language="en").__aiter__()
            first = await aiter.__anext__()
            self.assertEqual(first, b"ID3chunk1")
            second_chunk_gate.set()
            second = await aiter.__anext__()
            self.assertEqual(second, b"chunk2")
            with self.assertRaises(StopAsyncIteration):
                await aiter.__anext__()

        self.assertEqual(len(captured_kwargs), 1)
        self.assertEqual(captured_kwargs[0]["connect_timeout"], 4)


class TestUnifiedStreamingAndCache(unittest.IsolatedAsyncioTestCase):
    async def test_lru_cache_hit_skips_second_engine_call_and_evicts_oldest(self):
        provider = UnifiedTTSProvider(
            default_engine="espeak",
            options={"cacheMaxEntries": 2, "cacheMaxItemBytes": 65536, "cacheMaxTotalBytes": 65536},
        )
        calls = []

        class _FakeEspeak:
            tts_bin = "espeak-ng"

            def resolve_voice_arg(self, lang, voice):
                return f"{lang}{voice if voice != 'default' else ''}"

            async def is_available(self):
                return True

            async def synthesize(self, text, options=None, **kwargs):
                calls.append(text)
                return b"RIFF\x24\x00\x00\x00WAVEfmt " + text.encode("utf-8")

        provider.espeak = _FakeEspeak()
        # _is_engine_patched returns False because _FakeEspeak methods live on its class, not instance __dict__
        b1 = await provider.synthesize("First")
        b2 = await provider.synthesize("First")
        self.assertEqual(b1, b2)
        self.assertEqual(calls, ["First"])

        await provider.synthesize("Second")
        await provider.synthesize("Third")
        # "First" should have been evicted when max entries = 2
        await provider.synthesize("First")
        self.assertEqual(calls, ["First", "Second", "Third", "First"])

    async def test_open_audio_stream_returns_on_first_chunk_and_populates_cache(self):
        provider = UnifiedTTSProvider(default_engine="edge-tts")
        release_rest = asyncio.Event()
        calls = 0

        class _FakeEdge:
            default_rate = "+0%"
            default_pitch = "+0Hz"

            def resolve_voice(self, lang, voice):
                return "en-US-EmmaMultilingualNeural"

            async def is_available(self):
                return True

            async def stream_chunks(self, text, options=None, **kwargs):
                nonlocal calls
                calls += 1
                yield b"ID3\x03\x00first_"
                await release_rest.wait()
                yield b"second"

        provider.edge_tts = _FakeEdge()

        stream = await provider.open_audio_stream("Stream me")
        self.assertTrue(stream.is_streaming)
        self.assertEqual(stream.first_chunk, b"ID3\x03\x00first_")
        self.assertEqual(stream.audio_format, "mp3")

        release_rest.set()
        rest = bytearray(stream.first_chunk)
        async for chunk in stream.chunk_iter:
            rest.extend(chunk)
        if stream.on_complete:
            stream.on_complete(bytes(rest))
        self.assertEqual(bytes(rest), b"ID3\x03\x00first_second")

        # Next call hits the cache immediately with non-streaming handle
        cached = await provider.open_audio_stream("Stream me")
        self.assertFalse(cached.is_streaming)
        self.assertEqual(cached.first_chunk, b"ID3\x03\x00first_second")
        self.assertEqual(calls, 1)

    async def test_open_audio_stream_falls_back_when_edge_fails_before_first_chunk(self):
        provider = UnifiedTTSProvider(default_engine="edge-tts")

        class _FailingEdge:
            default_rate = "+0%"
            default_pitch = "+0Hz"

            def resolve_voice(self, lang, voice):
                return "en-US-EmmaMultilingualNeural"

            async def is_available(self):
                return True

            async def stream_chunks(self, text, options=None, **kwargs):
                if False:
                    yield b""
                raise RuntimeError("WebSocket failed")

        class _WorkingGtts:
            def resolve_params(self, lang, voice):
                return "en", "com"

            async def is_available(self):
                return True

            async def synthesize(self, text, options=None, **kwargs):
                return b"ID3\x03\x00gtts_fallback"

        provider.edge_tts = _FailingEdge()
        provider.gtts = _WorkingGtts()

        handle = await provider.open_audio_stream("Fallback check")
        self.assertFalse(handle.is_streaming)
        self.assertEqual(handle.engine, "gtts")
        self.assertEqual(handle.fallback_from, "edge-tts")
        self.assertEqual(handle.first_chunk, b"ID3\x03\x00gtts_fallback")


class TestStreamingFFmpegDecode(unittest.TestCase):
    def test_build_ffmpeg_flags_avoids_nobuffer_frame_drops(self):
        for fmt in ("mp3", "wav", "ogg"):
            before, after = _build_ffmpeg_flags(fmt)
            self.assertIn(f"-f {fmt}", before)
            self.assertIn("-analyzeduration 0", before)
            self.assertNotIn("nobuffer", before)
            self.assertEqual(after, "-flush_packets 1")

        unk_before, unk_after = _build_ffmpeg_flags("")
        self.assertIn("-probesize", unk_before)
        self.assertNotIn("nobuffer", unk_before)
        self.assertEqual(unk_after, "-flush_packets 1")

    def test_streaming_ffmpeg_emits_first_frame_while_tail_chunks_are_still_arriving(self):
        ffmpeg = resolve_ffmpeg_path()
        wav = generate_sine_wav(duration=0.6)
        mp3_bytes = subprocess.Popen(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0", "-f", "mp3", "pipe:1"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).communicate(wav)[0]
        self.assertGreater(len(mp3_bytes), 2048)

        split_at = len(mp3_bytes) // 2
        chunk_stream = _ChunkedAudioStream(mp3_bytes[:split_at])
        before, after = _build_ffmpeg_flags("mp3")
        stderr_buf = _BoundedStderrBuffer()
        source = _StreamingFFmpegPCMAudio(
            chunk_stream,
            pipe=True,
            executable=ffmpeg,
            stderr=stderr_buf,
            before_options=before,
            options=after,
        )
        setattr(source, "_source_stream", chunk_stream)

        try:
            # First PCM frame(s) should decode immediately from the first half without waiting for EOF!
            first_pcm = source.read()
            self.assertEqual(len(first_pcm), 3840)

            # Now feed the remaining chunk and finish the stream
            def _push_tail():
                chunk_stream.push(mp3_bytes[split_at:])
                chunk_stream.finish()

            t = threading.Thread(target=_push_tail, daemon=True)
            t.start()
            total_frames = 1
            while True:
                frame = source.read()
                if not frame:
                    break
                total_frames += 1
            t.join(timeout=2.0)
            self.assertGreaterEqual(total_frames, 25)
        finally:
            source.cleanup()


class TestLookaheadPrefetchAndCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_lookahead_prefetch_synthesizes_second_job_while_first_plays(self):
        loop = asyncio.get_running_loop()
        first_playing = asyncio.Event()
        release_first = threading.Event()
        second_prefetched = asyncio.Event()
        synth_calls = []

        class _PrefetchTTS:
            default_engine = "edge-tts"

            def resolve_selection(self, *, language="en", voice="default"):
                return {
                    "primary_engine": "edge-tts",
                    "engine_order": ["edge-tts"],
                    "resolved_voice": "en-US-EmmaMultilingualNeural",
                    "language": language,
                }

            async def open_audio_stream(self, text, **kwargs):
                synth_calls.append(("stream", text))
                return StreamingAudio(
                    first_chunk=generate_sine_wav(duration=0.1),
                    chunk_iter=None,
                    audio_format="wav",
                    engine="edge-tts",
                    voice_used="test",
                )

            async def synthesize_audio(self, text, **kwargs):
                synth_calls.append(("prefetch", text))
                if text == "Second message":
                    second_prefetched.set()
                return SimpleNamespace(
                    data=generate_sine_wav(duration=0.1),
                    audio_format="wav",
                    engine="edge-tts",
                    voice_used="test",
                    fallback_from=None,
                )

        vc = MagicMock()
        vc.loop = loop
        vc.is_connected.return_value = True
        vc.is_playing.return_value = False
        vc.is_paused.return_value = False

        played_count = 0

        def _play(source, after=None):
            nonlocal played_count
            played_count += 1
            current_num = played_count

            def _worker():
                while True:
                    if not source.read():
                        break
                if current_num == 1:
                    loop.call_soon_threadsafe(first_playing.set)
                    release_first.wait(timeout=5.0)
                if after:
                    after(None)
                source.cleanup()

            threading.Thread(target=_worker, daemon=True).start()

        vc.play.side_effect = _play

        with patch("discord.FFmpegPCMAudio", side_effect=lambda *a, **k: _DummyPCMSource(frames=3)):
            session = DiscordVoiceSession(
                guild_id="g_pf",
                channel_id="vc_pf",
                voice_client=vc,
                tts_provider=_PrefetchTTS(),
            )
            try:
                enq1 = session.serial_queue.enqueue({"text": "First message"})
                enq2 = session.serial_queue.enqueue({"text": "Second message"})

                await asyncio.wait_for(first_playing.wait(), timeout=2.0)
                await asyncio.wait_for(second_prefetched.wait(), timeout=2.0)
                release_first.set()
                await asyncio.wait_for(asyncio.gather(enq1["future"], enq2["future"]), timeout=3.0)

                self.assertEqual(played_count, 2)
                # First message used open_audio_stream, second message used prefetched audio (not open_audio_stream!)
                self.assertEqual(
                    synth_calls, [("stream", "First message"), ("prefetch", "Second message")]
                )
            finally:
                release_first.set()
                await session.destroy_async()

    async def test_serial_queue_clear_cancels_pending_prefetch_tasks(self):
        cancelled = []
        loop = asyncio.get_running_loop()

        async def _noop(job, cancel_event):
            await asyncio.sleep(0.05)

        q = SerialQueue(guild_id="g_cancel", on_process_job=_noop, loop=loop)
        job = QueueJob(job_id=1, data={"text": "Active"}, loop=loop)
        q.current_job = job

        pending = QueueJob(job_id=2, data={"text": "Queued"}, loop=loop)
        pending.add_cancel_hook(lambda: cancelled.append(pending.id))
        q.pending_queue.append(pending)

        res = q.clear()
        self.assertEqual(res["cleared_count"], 1)
        self.assertEqual(cancelled, [2])
        q.stop()


class _DummyPCMSource(discord.AudioSource):
    def __init__(self, frames: int = 3):
        self.remaining = frames

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self.remaining <= 0:
            return b""
        self.remaining -= 1
        return b"\x01" * 3840

    def cleanup(self) -> None:
        self.remaining = 0


class TestHinglishConcurrencyAndTranslatorSession(unittest.IsolatedAsyncioTestCase):
    async def test_hinglish_service_joins_vc_and_synthesizes_hindi_concurrently(self):
        class _FastTranslator:
            async def translate_to_english_and_hindi(self, text, *, timeout=None):
                return SimpleNamespace(
                    source_text=text,
                    english_text="Hello friends",
                    hindi_text="नमस्ते दोस्तों",
                    provider="fake",
                )

        class _DelayedTTS:
            async def synthesize(self, text, *, language=None, voice=None, **kwargs):
                await asyncio.sleep(0.15)
                return b"ID3\x03\x00hindi_audio"

        enqueued = []
        fake_session = SimpleNamespace(
            channel_id="vc_1",
            is_destroyed=False,
            voice_client=SimpleNamespace(is_connected=lambda: True),
            serial_queue=SimpleNamespace(enqueue=lambda item: enqueued.append(item)),
        )

        async def _delayed_join(guild, vc):
            await asyncio.sleep(0.15)
            return {"session": fake_session, "reused": False}

        session_manager = MagicMock()
        session_manager.get_session.return_value = None
        session_manager.join = AsyncMock(side_effect=_delayed_join)

        service = HinglishService(_FastTranslator(), _DelayedTTS(), session_manager)
        vc = SimpleNamespace(id="vc_1", name="Lounge", permissions_for=lambda me: SimpleNamespace(connect=True, speak=True))
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)
        member = SimpleNamespace(id="u_1", voice=SimpleNamespace(channel=vc))

        t0 = time.monotonic()
        res = await service.process_message(
            "Namaste dosto",
            guild=guild,
            member=member,
            delivery_mode=DELIVERY_AUTO,
        )
        elapsed = time.monotonic() - t0

        self.assertTrue(res.ok)
        self.assertEqual(res.delivered_via, "voice")
        session_manager.join.assert_awaited_once_with(guild, vc)
        self.assertEqual(len(enqueued), 1)
        # Running join (0.15s) and TTS (0.15s) sequentially would take >= 0.30s; concurrent takes ~0.15s
        self.assertLess(elapsed, 0.25)

    async def test_openai_translator_reuses_session_and_closes_cleanly(self):
        provider = OpenAITranslatorProvider(api_key="test-key")
        created_sessions = []

        class _FakeResponse:
            status = 200

            async def text(self):
                return '{"choices":[{"message":{"content":"{\\"english\\":\\"Hi\\",\\"hindi\\":\\"नमस्ते\\"}"}}]}'

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeClientSession:
            def __init__(self):
                self.closed = False
                self.post_calls = 0
                created_sessions.append(self)

            def post(self, *args, **kwargs):
                self.post_calls += 1
                return _FakeResponse()

            async def close(self):
                self.closed = True

        with patch("src.translation.openai_translator_provider.aiohttp.ClientSession", _FakeClientSession):
            await provider.translate_to_english_and_hindi("Hi")
            await provider.translate_to_english_and_hindi("Hi again")
            self.assertEqual(len(created_sessions), 1)
            self.assertEqual(created_sessions[0].post_calls, 2)

            await provider.close()
            self.assertTrue(created_sessions[0].closed)


if __name__ == "__main__":
    unittest.main()
