"""Tests for GTTSProvider and UnifiedTTSProvider."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src.tts.espeak_provider import AbortError
from src.tts.gtts_provider import GTTSProvider
from src.tts.unified_provider import UnifiedTTSProvider


class TestUnifiedAndGTTS(unittest.IsolatedAsyncioTestCase):
    def test_gtts_resolve_params(self):
        provider = GTTSProvider()

        # Standard languages
        lang, tld = provider.resolve_params("en")
        self.assertEqual(lang, "en")
        self.assertEqual(tld, "com")

        lang, tld = provider.resolve_params("en-us")
        self.assertEqual(lang, "en")
        self.assertEqual(tld, "com")

        lang, tld = provider.resolve_params("en-uk")
        self.assertEqual(lang, "en")
        self.assertEqual(tld, "co.uk")

        lang, tld = provider.resolve_params("fr")
        self.assertEqual(lang, "fr")
        self.assertEqual(tld, "fr")

        # Custom voice accents
        lang, tld = provider.resolve_params("en", "au")
        self.assertEqual(lang, "en")
        self.assertEqual(tld, "com.au")

        lang, tld = provider.resolve_params("en", "in")
        self.assertEqual(lang, "en")
        self.assertEqual(tld, "co.in")

    async def test_gtts_mock_fallback(self):
        provider = GTTSProvider(allow_mock_fallback=True)
        wav = await provider.synthesize("Hello world")
        self.assertIsInstance(wav, bytes)
        self.assertGreaterEqual(len(wav), 44)
        self.assertEqual(wav[0:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")

    async def test_gtts_rejects_empty_text(self):
        provider = GTTSProvider(allow_mock_fallback=True)
        with self.assertRaises(ValueError):
            await provider.synthesize("   ")

    async def test_gtts_cancellation(self):
        provider = GTTSProvider(allow_mock_fallback=True)
        cancel_event = asyncio.Event()
        cancel_event.set()

        with self.assertRaises(AbortError):
            await provider.synthesize("Will be aborted", cancel_event=cancel_event)

    async def test_unified_provider_routes_to_gtts_by_default(self):
        unified = UnifiedTTSProvider(default_engine="gtts", allow_mock_fallback=True)
        self.assertTrue(await unified.is_available())

        res = await unified.synthesize("Test unified speech")
        self.assertIsInstance(res, bytes)
        self.assertGreater(len(res), 0)

    async def test_unified_provider_falls_back_when_gtts_fails(self):
        unified = UnifiedTTSProvider(default_engine="gtts", allow_mock_fallback=True)

        # Force gTTS to fail
        with patch.object(unified.gtts, "synthesize", side_effect=RuntimeError("Google offline")):
            res = await unified.synthesize("Fallback test")
            self.assertIsInstance(res, bytes)
            self.assertEqual(res[0:4], b"RIFF")

    def test_unified_provider_binary_path_resolution(self):
        unified = UnifiedTTSProvider(default_engine="gtts", tts_bin="custom-bin")
        self.assertEqual(unified.tts_bin, "custom-bin")

        with patch.object(unified.espeak, "resolve_binary_path", return_value="/mock/bin/espeak"):
            self.assertEqual(unified.resolve_binary_path(), "/mock/bin/espeak")
            self.assertEqual(unified._resolve_binary_path(), "/mock/bin/espeak")

    def test_app_create_app_preflight_attributes(self):
        from src.app import create_app
        from src.config import Config

        cfg = Config(discord_token="fake.token.123", tts_engine="gtts")
        bot = create_app(app_config=cfg)
        self.assertTrue(hasattr(bot.tts_provider, "resolve_binary_path"))
        self.assertTrue(hasattr(bot.espeak_provider, "resolve_binary_path"))
        # Must execute without AttributeError
        path = bot.tts_provider.resolve_binary_path()
        self.assertTrue(path is None or isinstance(path, str))


if __name__ == "__main__":
    unittest.main()

