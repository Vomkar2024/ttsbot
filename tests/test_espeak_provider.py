"""Tests for EspeakProvider."""

import asyncio
import unittest
from src.tts.espeak_provider import AbortError, EspeakProvider


class TestEspeakProvider(unittest.IsolatedAsyncioTestCase):
    def test_resolve_voice_arg(self):
        provider = EspeakProvider()

        # Default voice
        self.assertEqual(provider.resolve_voice_arg("en", "default"), "en")
        self.assertEqual(provider.resolve_voice_arg("es", ""), "es")
        self.assertEqual(provider.resolve_voice_arg(None, None), "en")

        # Modifiers (+m1, +f2)
        self.assertEqual(provider.resolve_voice_arg("en", "+m1"), "en+m1")
        self.assertEqual(provider.resolve_voice_arg("fr", "+f3"), "fr+f3")

        # Explicit voice identifier
        self.assertEqual(provider.resolve_voice_arg("en", "en-us+m3"), "en-us+m3")

    async def test_rejects_empty_or_whitespace_text(self):
        provider = EspeakProvider()

        with self.assertRaises(ValueError) as cm1:
            await provider.synthesize("")
        self.assertIn("Cannot synthesize empty", str(cm1.exception))

        with self.assertRaises(ValueError) as cm2:
            await provider.synthesize("   \n  ")
        self.assertIn("Cannot synthesize empty", str(cm2.exception))

    async def test_rejects_immediately_when_cancelled_prior_to_start(self):
        provider = EspeakProvider()
        cancel_event = asyncio.Event()
        cancel_event.set()

        with self.assertRaises(AbortError):
            await provider.synthesize("Aborted test", cancel_event=cancel_event)

    async def test_generates_valid_riff_wave_header_on_mock_fallback(self):
        provider = EspeakProvider(
            tts_bin="non-existent-binary-for-test",
            allow_mock_fallback=True,
        )

        wav = await provider.synthesize("Test mock synthesis")
        self.assertIsInstance(wav, bytes)
        self.assertGreaterEqual(len(wav), 44)
        self.assertEqual(wav[0:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(wav[12:16], b"fmt ")
        self.assertEqual(wav[36:40], b"data")

    async def test_runs_real_synthesis_if_installed_or_skips(self):
        provider = EspeakProvider(tts_bin="espeak-ng")
        available = await provider.is_available()

        if not available:
            self.skipTest("eSpeak NG binary not found on PATH. Skipping live synthesis test.")
            return

        wav = await provider.synthesize("VoiceBridge test.")
        self.assertIsInstance(wav, bytes)
        self.assertGreater(len(wav), 44)
        self.assertEqual(wav[0:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")


if __name__ == "__main__":
    unittest.main()

