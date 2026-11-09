"""Tests for pure tone generator, ToneAudioSource, and bundled FFmpeg resolution."""

import io
import struct
import unittest
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.voice.discord_voice_session import DiscordVoiceSession
from src.voice.session_manager import resolve_ffmpeg_path
from src.voice.tone_generator import (
    ToneAudioSource,
    generate_sine_pcm,
    generate_sine_wav,
)


class TestToneGenerator(unittest.TestCase):
    def test_generate_sine_pcm_byte_length_and_structure(self):
        duration = 0.5
        sample_rate = 48000
        pcm = generate_sine_pcm(frequency=440.0, duration=duration, sample_rate=sample_rate)

        # 48,000 samples/sec * 0.5 sec * 2 channels * 2 bytes/sample = 96,000 bytes
        expected_bytes = int(sample_rate * duration * 4)
        self.assertEqual(len(pcm), expected_bytes)

        # Verify stereo format: unpack first few samples
        # Each stereo sample is two signed 16-bit integers ('<hh')
        left, right = struct.unpack_from("<hh", pcm, 0)
        self.assertEqual(left, right)
        self.assertGreaterEqual(left, -32768)
        self.assertLessEqual(left, 32767)

    def test_generate_sine_wav_header_and_channels(self):
        duration = 0.1
        sample_rate = 48000
        wav_bytes = generate_sine_wav(frequency=440.0, duration=duration, sample_rate=sample_rate)

        # Read back with standard library wave module
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            self.assertEqual(reader.getnchannels(), 2)
            self.assertEqual(reader.getsampwidth(), 2)
            self.assertEqual(reader.getframerate(), 48000)
            self.assertEqual(reader.getnframes(), int(sample_rate * duration))

    def test_tone_audio_source_framing(self):
        duration = 0.05  # 50ms of audio -> should produce two 20ms frames + one 10ms (padded to 20ms) frame
        source = ToneAudioSource(frequency=440.0, duration=duration, sample_rate=48000)

        frames = []
        while True:
            chunk = source.read()
            if not chunk:
                break
            frames.append(chunk)

        self.assertFalse(source.is_opus())
        # Each 20ms frame at 48kHz stereo 16-bit MUST be exactly 3840 bytes
        for frame in frames:
            self.assertEqual(len(frame), ToneAudioSource.FRAME_SIZE)

        # 50ms / 20ms = 2.5 frames -> padded to 3 frames
        self.assertEqual(len(frames), 3)

        # Subsequent reads return b""
        self.assertEqual(source.read(), b"")

        # Cleanup resets position to end
        source.cleanup()
        self.assertEqual(source.read(), b"")


class TestResolveFFmpegPath(unittest.TestCase):
    @patch("shutil.which")
    def test_returns_system_ffmpeg_when_available(self, mock_which):
        mock_which.return_value = "/usr/bin/ffmpeg"
        result = resolve_ffmpeg_path("ffmpeg")
        self.assertEqual(result, "/usr/bin/ffmpeg")

    @patch("shutil.which", return_value=None)
    @patch("os.path.isfile", return_value=True)
    def test_falls_back_to_bundled_ffmpeg_when_system_missing(self, mock_isfile, mock_which):
        mock_imageio = MagicMock()
        mock_imageio.get_ffmpeg_exe.return_value = "/mock/bundled/ffmpeg"

        with patch.dict("sys.modules", {"imageio_ffmpeg": mock_imageio}):
            result = resolve_ffmpeg_path("ffmpeg")
            self.assertEqual(result, "/mock/bundled/ffmpeg")

    @patch("shutil.which", return_value=None)
    def test_falls_back_to_configured_string_when_no_ffmpeg_found(self, mock_which):
        with patch.dict("sys.modules", {"imageio_ffmpeg": None}):
            result = resolve_ffmpeg_path("custom_ffmpeg")
            self.assertEqual(result, "custom_ffmpeg")


class TestVoiceSessionPlayTone(unittest.IsolatedAsyncioTestCase):
    async def test_play_test_tone_direct_pcm(self):
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False

        # In mock play(), invoke the callback immediately
        def mock_play(source, after=None):
            if after:
                after(None)

        mock_vc.play = MagicMock(side_effect=mock_play)

        session = DiscordVoiceSession(
            guild_id="123",
            channel_id="456",
            voice_client=mock_vc,
        )

        await session.play_test_tone(duration=0.1, frequency=440.0, use_ffmpeg=False)

        mock_vc.play.assert_called_once()
        played_source = mock_vc.play.call_args[0][0]
        self.assertIsInstance(played_source, ToneAudioSource)

    async def test_play_test_tone_with_ffmpeg(self):
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False

        def mock_play(source, after=None):
            if after:
                after(None)

        mock_vc.play = MagicMock(side_effect=mock_play)

        session = DiscordVoiceSession(
            guild_id="123",
            channel_id="456",
            voice_client=mock_vc,
        )

        with patch("discord.FFmpegPCMAudio") as mock_ffmpeg_source:
            mock_raw = MagicMock(spec=discord.AudioSource)
            mock_raw.is_opus.return_value = False
            mock_ffmpeg_source.return_value = mock_raw
            await session.play_test_tone(duration=0.1, frequency=440.0, use_ffmpeg=True)
            mock_ffmpeg_source.assert_called_once()
            mock_vc.play.assert_called_once()

    async def test_play_test_tone_raises_when_disconnected(self):
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = False

        session = DiscordVoiceSession(
            guild_id="123",
            channel_id="456",
            voice_client=mock_vc,
        )

        from src.tts.espeak_provider import AbortError

        with self.assertRaises(AbortError):
            await session.play_test_tone(duration=0.1)


if __name__ == "__main__":
    unittest.main()
