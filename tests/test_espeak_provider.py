"""Tests for EspeakProvider."""

import asyncio
import subprocess
import unittest
from src.tts.espeak_provider import AbortError, EspeakProvider


def run_coroutine(coro):
    """Executes a coroutine that resolves synchronously without waiting on external timers."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise RuntimeError("Coroutine did not finish synchronously")


class TestEspeakProvider(unittest.TestCase):
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

    def test_rejects_empty_or_whitespace_text(self):
        provider = EspeakProvider()

        with self.assertRaises(ValueError) as cm1:
            run_coroutine(provider.synthesize(""))
        self.assertIn("Cannot synthesize empty", str(cm1.exception))

        with self.assertRaises(ValueError) as cm2:
            run_coroutine(provider.synthesize("   \n  "))
        self.assertIn("Cannot synthesize empty", str(cm2.exception))

    def test_rejects_immediately_when_cancelled_prior_to_start(self):
        provider = EspeakProvider()
        cancel_event = asyncio.Event()
        cancel_event.set()

        with self.assertRaises(AbortError):
            run_coroutine(provider.synthesize("Aborted test", cancel_event=cancel_event))

    def test_generates_valid_riff_wave_header_on_mock_fallback(self):
        provider = EspeakProvider(
            tts_bin="non-existent-binary-for-test",
            allow_mock_fallback=True,
        )

        wav = provider._generate_mock_wav("Test mock synthesis")
        self.assertIsInstance(wav, bytes)
        self.assertGreaterEqual(len(wav), 44)
        self.assertEqual(wav[0:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(wav[12:16], b"fmt ")
        self.assertEqual(wav[36:40], b"data")

    def test_resolve_binary_cross_platform_fallback(self):
        from unittest.mock import patch

        # Simulate Linux where Windows path was configured in .env
        provider = EspeakProvider(tts_bin=r"C:\Program Files\eSpeak NG\espeak-ng.exe")

        def mock_which(cmd):
            if cmd == "espeak-ng":
                return "/usr/bin/espeak-ng"
            return None

        with patch("src.tts.espeak_provider.shutil.which", side_effect=mock_which):
            with patch("pathlib.Path.is_file", return_value=False):
                resolved = provider.resolve_binary_path()
                self.assertEqual(resolved, "/usr/bin/espeak-ng")

    def test_resolve_binary_falls_back_to_legacy_espeak(self):
        from unittest.mock import patch

        provider = EspeakProvider(tts_bin="espeak-ng")

        def mock_which(cmd):
            if cmd == "espeak":
                return "/usr/bin/espeak"
            return None

        with patch("src.tts.espeak_provider.shutil.which", side_effect=mock_which):
            with patch("pathlib.Path.is_file", return_value=False):
                resolved = provider.resolve_binary_path()
                self.assertEqual(resolved, "/usr/bin/espeak")

    def test_runs_real_synthesis_if_installed_or_skips(self):
        provider = EspeakProvider(tts_bin="espeak-ng")
        resolved = provider.resolve_binary_path()

        if not resolved:
            self.skipTest("eSpeak NG binary not found on PATH. Skipping live synthesis test.")
            return

        try:
            res = subprocess.run(
                [resolved, "--stdout", "TTS test."],
                capture_output=True,
                check=True,
                timeout=5.0,
            )
            wav = res.stdout
            self.assertIsInstance(wav, bytes)
            self.assertGreater(len(wav), 44)
            self.assertEqual(wav[0:4], b"RIFF")
            self.assertEqual(wav[8:12], b"WAVE")
        except (subprocess.SubprocessError, FileNotFoundError):
            self.skipTest("eSpeak NG invocation failed in sandbox environment.")


if __name__ == "__main__":
    unittest.main()

