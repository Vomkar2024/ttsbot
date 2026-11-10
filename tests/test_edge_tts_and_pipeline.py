"""Tests for EdgeTTSProvider, multi-engine routing, audio pipeline diagnostics/logging, and updater."""

import asyncio
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.commands.tts_commands import setup_tts_commands
from src.constants import DEFAULT_EDGE_TTS_VOICE, VOICE_VARIANT_CHOICES
from src.storage.settings_store import SettingsStore
from src.tts.edge_tts_provider import EdgeTTSProvider
from src.tts.espeak_provider import AbortError
from src.tts.unified_provider import UnifiedTTSProvider
from src.updater import maybe_update_on_startup, run_startup_update
from src.voice.discord_voice_session import (
    _BoundedStderrBuffer,
    _DiagnosticAudioSource,
    DiscordVoiceSession,
)
from src.voice.serial_queue import QueueJob
from src.voice.session_manager import VoiceSessionManager, resolve_ffmpeg_path
from src.voice.tone_generator import generate_sine_wav


class _SilencePCMSource(discord.AudioSource):
    """Test source emitting N 20ms PCM frames of all zeroes (digital silence)."""

    def __init__(self, frame_count: int = 3):
        self.remaining = frame_count

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        if self.remaining <= 0:
            return b""
        self.remaining -= 1
        return b"\x00" * 3840

    def cleanup(self) -> None:
        self.remaining = 0


class _BrokenFFmpegRawSource(discord.AudioSource):
    """Simulates FFmpegPCMAudio crashing after returning 0 PCM frames."""

    def __init__(self, err: Exception):
        self._current_error = err

    def is_opus(self) -> bool:
        return False

    def read(self) -> bytes:
        return b""

    def cleanup(self) -> None:
        pass


def _simulate_real_player_drain(voice_client):
    """Helper factory simulating discord.AudioPlayer draining source.read() until b''."""

    def _play(source, after=None):
        err = None
        try:
            while True:
                chunk = source.read()
                if not chunk:
                    break
            candidate = getattr(source, "_current_error", None)
            if isinstance(candidate, BaseException):
                err = candidate
        except Exception as exc:  # pragma: no cover
            err = exc
        if after:
            after(err)
        source.cleanup()

    return _play


class TestEdgeTTSAndUnifiedRouting(unittest.IsolatedAsyncioTestCase):
    def test_voice_resolution_and_choices_within_discord_limit(self):
        self.assertLessEqual(len(VOICE_VARIANT_CHOICES), 25)
        choice_values = [c["value"] for c in VOICE_VARIANT_CHOICES]
        self.assertIn("edge:en-US-EmmaMultilingualNeural", choice_values)
        self.assertIn("us", choice_values)
        self.assertIn("+m1", choice_values)

        provider = EdgeTTSProvider(default_voice="en-US-AriaNeural")
        self.assertEqual(provider.resolve_voice("en", "default"), "en-US-AriaNeural")
        self.assertEqual(provider.resolve_voice("en", "edge:en-GB-RyanNeural"), "en-GB-RyanNeural")
        self.assertEqual(provider.resolve_voice("en", "en-GB-SoniaNeural"), "en-GB-SoniaNeural")
        self.assertEqual(provider.resolve_voice("hi", "default"), "hi-IN-SwaraNeural")
        self.assertEqual(provider.resolve_voice("en", "uk"), "en-GB-SoniaNeural")

    async def test_edge_tts_streams_audio_and_rejects_empty_stream(self):
        provider = EdgeTTSProvider(default_voice=DEFAULT_EDGE_TTS_VOICE, timeout=5.0)

        class _FakeCommunicate:
            def __init__(self, text, voice, rate="+0%", pitch="+0Hz"):
                self.text = text
                self.voice = voice
                self.rate = rate
                self.pitch = pitch

            async def stream(self):
                yield {"type": "audio", "data": b"ID3"}
                yield {"type": "WordBoundary", "offset": 100}
                yield {"type": "audio", "data": b"\xff\xfb\x90\x00test-mp3-frame"}

        fake_mod = SimpleNamespace(Communicate=_FakeCommunicate)
        with patch.dict("sys.modules", {"edge_tts": fake_mod}):
            audio = await provider.synthesize("Hello world", language="en", voice="default")

        self.assertEqual(audio, b"ID3\xff\xfb\x90\x00test-mp3-frame")

        class _EmptyCommunicate:
            def __init__(self, *args, **kwargs):
                pass

            async def stream(self):
                yield {"type": "WordBoundary", "offset": 0}

        with patch.dict("sys.modules", {"edge_tts": SimpleNamespace(Communicate=_EmptyCommunicate)}):
            with self.assertRaises(RuntimeError):
                await provider.synthesize("Hello world")

    async def test_unified_provider_routes_voice_packs_and_falls_back(self):
        unified = UnifiedTTSProvider(allow_mock_fallback=False)
        self.assertEqual(unified.default_engine, "edge-tts")

        # Verify selection routing
        self.assertEqual(unified.resolve_selection(voice="default")["primary_engine"], "edge-tts")
        self.assertEqual(
            unified.resolve_selection(voice="edge:en-GB-RyanNeural")["resolved_voice"],
            "en-GB-RyanNeural",
        )
        self.assertEqual(unified.resolve_selection(voice="uk")["primary_engine"], "gtts")
        self.assertEqual(unified.resolve_selection(voice="+klatt")["primary_engine"], "espeak")

        # Primary edge-tts succeeds
        with patch.object(unified.edge_tts, "synthesize", AsyncMock(return_value=b"ID3_EDGE")):
            res = await unified.synthesize_audio("Hello", language="en", voice="default")
            self.assertEqual(res.data, b"ID3_EDGE")
            self.assertEqual(res.engine, "edge-tts")
            self.assertIsNone(res.fallback_from)

        # Edge-TTS fails -> falls back to gTTS
        with patch.object(unified.edge_tts, "synthesize", AsyncMock(side_effect=RuntimeError("Edge net error"))):
            with patch.object(unified.gtts, "synthesize", AsyncMock(return_value=b"\xff\xfb_GTTS")):
                res = await unified.synthesize_audio("Fallback", language="en", voice="edge:en-US-AriaNeural")
                self.assertEqual(res.data, b"\xff\xfb_GTTS")
                self.assertEqual(res.engine, "gtts")
                self.assertEqual(res.fallback_from, "edge-tts")

        # Edge-TTS + gTTS both fail -> falls back to eSpeak
        with patch.object(unified.edge_tts, "synthesize", AsyncMock(return_value=b"")):  # empty!
            with patch.object(unified.gtts, "synthesize", AsyncMock(side_effect=TimeoutError("gTTS timeout"))):
                with patch.object(unified.espeak, "synthesize", AsyncMock(return_value=b"RIFF1234WAVE" + b"\x01" * 40)):
                    res = await unified.synthesize_audio("Deep fallback", language="en", voice="default")
                    self.assertEqual(res.engine, "espeak")
                    self.assertEqual(res.audio_format, "wav")
                    self.assertEqual(res.fallback_from, "edge-tts")


class TestAudioPipelineDiagnosticsAndLogging(unittest.IsolatedAsyncioTestCase):
    def test_diagnostic_source_forwards_error_masked_by_volume_transformer(self):
        broken = _BrokenFFmpegRawSource(RuntimeError("FFmpeg exited with code 1. Stderr: bad pipe"))
        vol = discord.PCMVolumeTransformer(broken, volume=1.0)
        diag = _DiagnosticAudioSource(vol, raw_source=broken)

        self.assertIs(diag._current_error, broken._current_error)
        self.assertEqual(diag.read(), b"")
        self.assertEqual(diag.read_calls, 1)
        self.assertEqual(diag.frames_read, 0)

    def test_diagnostic_source_detects_silence_and_nonzero_pcm(self):
        silence = _SilencePCMSource(frame_count=2)
        diag_silent = _DiagnosticAudioSource(silence, raw_source=silence)
        while diag_silent.read():
            pass
        self.assertEqual(diag_silent.frames_read, 2)
        self.assertEqual(diag_silent.non_zero_frames, 0)
        self.assertEqual(diag_silent.peak_abs_sample, 0)

    async def test_pipeline_emits_all_stage_logs_with_real_ffmpeg_decode(self):
        ffmpeg_exe = resolve_ffmpeg_path("ffmpeg")
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False
        mock_vc._connection = SimpleNamespace(
            mode="aead_xchacha20_poly1305_rtpsize",
            dave_protocol_version=1,
            can_encrypt=True,
            dave_session=SimpleNamespace(ready=True, can_encrypt=True, status=SimpleNamespace(name="active")),
        )
        mock_vc.guild = SimpleNamespace(
            me=SimpleNamespace(voice=SimpleNamespace(mute=False, self_mute=False, suppress=False))
        )
        mock_vc.play = MagicMock(side_effect=_simulate_real_player_drain(mock_vc))

        wav_bytes = generate_sine_wav(frequency=440.0, duration=0.1, volume=0.5)
        tts = UnifiedTTSProvider(default_engine="edge-tts")

        session = DiscordVoiceSession(
            guild_id="999",
            channel_id="888",
            voice_client=mock_vc,
            tts_provider=tts,
            ffmpeg_path=ffmpeg_exe,
        )
        job = QueueJob(
            1,
            {
                "text": "Hello from test",
                "spokenText": "Omkar says: Hello from test",
                "language": "en",
                "voice": "default",
                "authorName": "Omkar",
            },
            asyncio.get_running_loop(),
        )

        with patch.object(tts.edge_tts, "synthesize", AsyncMock(return_value=wav_bytes)):
            with self.assertLogs("src.voice.discord_voice_session", level=logging.INFO) as cm:
                await session._process_job(job, asyncio.Event())

        joined = "\n".join(cm.output)
        self.assertIn("[Stage 1/6: Text Received]", joined)
        self.assertIn("[Stage 2/6: Voice Selected]", joined)
        self.assertIn("PrimaryEngine=edge-tts", joined)
        self.assertIn("[Stage 3/6: Audio Generated]", joined)
        self.assertIn("[Stage 4/6: FFmpeg/Source Created]", joined)
        self.assertIn("[Stage 5/6: Playback Started]", joined)
        self.assertIn("[Stage 6/6: Playback Finished]", joined)
        self.assertIn("PeakSample=", joined)
        self.assertNotIn("Frames=0", joined)

    async def test_pipeline_fails_loudly_on_corrupt_audio_with_ffmpeg_stderr(self):
        ffmpeg_exe = resolve_ffmpeg_path("ffmpeg")
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False
        mock_vc._connection = SimpleNamespace(
            mode="aead_xchacha20_poly1305_rtpsize",
            dave_protocol_version=0,
            can_encrypt=False,
            dave_session=None,
        )
        mock_vc.play = MagicMock(side_effect=_simulate_real_player_drain(mock_vc))

        mock_tts = SimpleNamespace(
            default_engine="edge-tts",
            synthesize=AsyncMock(return_value=b"NOT_A_VALID_AUDIO_STREAM_AT_ALL_0000"),
        )
        session = DiscordVoiceSession(
            guild_id="999",
            channel_id="888",
            voice_client=mock_vc,
            tts_provider=mock_tts,
            ffmpeg_path=ffmpeg_exe,
        )
        job = QueueJob(
            2,
            {"text": "bad", "spokenText": "bad", "language": "en", "voice": "default", "authorName": "U"},
            asyncio.get_running_loop(),
        )

        with self.assertLogs("src.voice.discord_voice_session", level=logging.INFO) as cm:
            with self.assertRaises(Exception):
                await session._process_job(job, asyncio.Event())

        joined = "\n".join(cm.output)
        self.assertIn("[Stage 6/6: Playback FAILED]", joined)
        self.assertIn("FFmpegStderr=", joined)

    async def test_pipeline_warns_when_server_muted_or_digital_silence(self):
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False
        mock_vc._connection = SimpleNamespace(
            mode="aead_xchacha20_poly1305_rtpsize",
            dave_protocol_version=1,
            can_encrypt=True,
            dave_session=SimpleNamespace(ready=True, can_encrypt=True, status=SimpleNamespace(name="active")),
        )
        mock_vc.guild = SimpleNamespace(
            me=SimpleNamespace(voice=SimpleNamespace(mute=True, self_mute=False, suppress=False))
        )
        mock_vc.play = MagicMock(side_effect=_simulate_real_player_drain(mock_vc))

        mock_tts = SimpleNamespace(
            default_engine="edge-tts",
            synthesize=AsyncMock(return_value=b"ID3_FAKE"),
        )
        session = DiscordVoiceSession(
            guild_id="777",
            channel_id="555",
            voice_client=mock_vc,
            tts_provider=mock_tts,
        )
        job = QueueJob(
            3,
            {"text": "hi", "spokenText": "hi", "language": "en", "voice": "default", "authorName": "U"},
            asyncio.get_running_loop(),
        )

        with patch("discord.FFmpegPCMAudio", return_value=_SilencePCMSource(frame_count=2)):
            with self.assertLogs("src.voice.discord_voice_session", level=logging.WARNING) as cm:
                await session._process_job(job, asyncio.Event())

        joined = "\n".join(cm.output)
        self.assertIn("SERVER MUTED", joined)
        self.assertIn("digital silence", joined)

    async def test_voice_and_status_commands_expose_edge_tts_voice_pack(self):
        bot = discord.ext.commands.Bot(command_prefix="!", intents=discord.Intents.default())
        store = SettingsStore(":memory:")
        tts = UnifiedTTSProvider(default_engine="edge-tts")
        sm = VoiceSessionManager(tts_provider=tts)
        setup_tts_commands(bot.tree, sm, store)

        status_cmd = bot.tree.get_command("status")
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=123),
            user=SimpleNamespace(id=456),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        await status_cmd.callback(interaction)
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        fields = {f.name: f.value for f in embed.fields}
        self.assertIn("Edge Neural TTS", fields["Speech Engine"])
        self.assertIn(DEFAULT_EDGE_TTS_VOICE, fields["Speech Engine"])

        store.close()
        sm.destroy_all()
        await bot.close()


class TestUpdater(unittest.TestCase):
    def test_maybe_update_disabled_by_default(self):
        res = maybe_update_on_startup(env={"AUTO_GIT_PULL": "false"})
        self.assertFalse(res.attempted)
        self.assertEqual(res.skipped_reason, "disabled")

    def test_skips_when_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = run_startup_update(repo_dir=Path(tmp))
            self.assertFalse(res.attempted)
            self.assertEqual(res.skipped_reason, "not_git_repo")

    def test_pulls_updates_and_reinstalls_changed_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            req = root / "requirements.txt"
            req.write_text("discord.py>=2.7.0\n", encoding="utf-8")

            calls = []

            def fake_run(cmd, cwd, capture_output, text, timeout, check):
                calls.append(cmd)
                if cmd[-3:] == ["rev-parse", "--short", "HEAD"]:
                    # Return different short SHAs before vs after pull
                    sha = "aaaa111" if len(calls) == 1 else "bbbb222"
                    return subprocess.CompletedProcess(cmd, 0, stdout=sha + "\n", stderr="")
                if "pull" in cmd:
                    req.write_text("discord.py>=2.7.0\nedge-tts>=7.0.0\n", encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, stdout="Updating aaaa111..bbbb222\n", stderr="")
                return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

            with patch("shutil.which", return_value="git"):
                with patch("subprocess.run", side_effect=fake_run):
                    res = run_startup_update(repo_dir=root, branch="main", install_deps=True)

            self.assertTrue(res.attempted)
            self.assertTrue(res.updated)
            self.assertTrue(res.pip_ran)
            self.assertTrue(res.pip_succeeded)
            self.assertEqual(res.before_commit, "aaaa111")
            self.assertEqual(res.after_commit, "bbbb222")
            self.assertTrue(any(sys.executable in c and "pip" in c for c in calls))


if __name__ == "__main__":
    unittest.main()
