"""Tests for GTTSProvider and UnifiedTTSProvider."""

import asyncio
import unittest
from unittest.mock import patch

from src.tts.espeak_provider import AbortError
from src.tts.gtts_provider import GTTSProvider
from src.tts.unified_provider import UnifiedTTSProvider


def run_coroutine(coro):
    """Executes a coroutine synchronously without creating an asyncio loop that hangs in sandbox."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise RuntimeError("Coroutine did not finish synchronously")


class TestUnifiedAndGTTS(unittest.TestCase):
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

    def test_gtts_timeout_configuration(self):
        default_provider = GTTSProvider()
        self.assertEqual(default_provider.timeout, 5.0)

        custom_provider = GTTSProvider(timeout=2.5)
        self.assertEqual(custom_provider.timeout, 2.5)

        dict_opts_provider = GTTSProvider(options={"timeoutSec": 3.0})
        self.assertEqual(dict_opts_provider.timeout, 3.0)

    def test_gtts_mock_fallback(self):
        provider = GTTSProvider(allow_mock_fallback=True)
        wav = provider._generate_mock_wav("Hello world")
        self.assertIsInstance(wav, bytes)
        self.assertGreaterEqual(len(wav), 44)
        self.assertEqual(wav[0:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")

    def test_gtts_rejects_empty_text(self):
        provider = GTTSProvider(allow_mock_fallback=True)
        with self.assertRaises(ValueError):
            run_coroutine(provider.synthesize("   "))

    def test_gtts_cancellation(self):
        provider = GTTSProvider(allow_mock_fallback=True)
        cancel_event = asyncio.Event()
        cancel_event.set()

        with self.assertRaises(AbortError):
            run_coroutine(provider.synthesize("Will be aborted", cancel_event=cancel_event))

    def test_unified_provider_falls_back_when_gtts_fails(self):
        unified = UnifiedTTSProvider(default_engine="gtts", allow_mock_fallback=True)

        # Force gTTS to fail with error
        async def mock_fail(*args, **kwargs):
            raise RuntimeError("Google offline")

        async def mock_espeak_synth(*args, **kwargs):
            return b"RIFF....WAVE"

        with patch.object(unified.gtts, "synthesize", side_effect=mock_fail):
            with patch.object(unified.espeak, "synthesize", side_effect=mock_espeak_synth):
                res = run_coroutine(unified.synthesize("Fallback test"))
                self.assertIsInstance(res, bytes)
                self.assertEqual(res[0:4], b"RIFF")

    def test_unified_provider_falls_back_when_gtts_times_out(self):
        unified = UnifiedTTSProvider(default_engine="gtts", allow_mock_fallback=True)

        async def mock_timeout(*args, **kwargs):
            raise TimeoutError("Google TTS request timed out after 5.0s")

        async def mock_espeak_synth(*args, **kwargs):
            return b"RIFF_FROM_ESPEAK"

        with patch.object(unified.gtts, "synthesize", side_effect=mock_timeout):
            with patch.object(unified.espeak, "synthesize", side_effect=mock_espeak_synth):
                res = run_coroutine(unified.synthesize("Timeout fallback test"))
                self.assertEqual(res, b"RIFF_FROM_ESPEAK")

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

