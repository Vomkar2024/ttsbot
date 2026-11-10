"""Tests for the decoupled Hinglish translation layer."""

import asyncio
import json
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.translation import (
    DeepTranslatorProvider,
    HinglishTranslation,
    OpenAITranslatorProvider,
    TranslationError,
    TranslationTimeoutError,
    TranslatorProvider,
    create_translator_provider,
)


class TestDeepTranslatorProvider(unittest.IsolatedAsyncioTestCase):
    async def test_derives_hindi_from_canonical_english_meaning(self):
        calls = []

        class FakeGoogleTranslator:
            def __init__(self, source="auto", target="en"):
                self.source = source
                self.target = target

            def translate(self, text):
                calls.append((self.source, self.target, text))
                if self.target == "en":
                    return "Hello everyone, the meeting is at 5 PM."
                if self.target == "hi":
                    return "नमस्ते सब लोग, मीटिंग शाम 5 बजे है।"
                raise AssertionError(f"Unexpected target {self.target}")

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = FakeGoogleTranslator

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider = DeepTranslatorProvider(timeout=5.0)
        with patch.dict(sys.modules, {"deep_translator": fake_mod}):
            with patch(
                "src.translation.deep_translator_provider.asyncio.to_thread",
                side_effect=inline_to_thread,
            ):
                result = await provider.translate_to_english_and_hindi(
                    "Bhai meeting 5 baje hai"
                )

        self.assertIsInstance(result, HinglishTranslation)
        self.assertIsInstance(provider, TranslatorProvider)
        self.assertEqual(result.english_text, "Hello everyone, the meeting is at 5 PM.")
        self.assertEqual(result.hindi_text, "नमस्ते सब लोग, मीटिंग शाम 5 बजे है।")
        # Hindi translation MUST receive the canonical English text, not raw input
        self.assertEqual(
            calls,
            [
                ("auto", "en", "Bhai meeting 5 baje hai"),
                ("en", "hi", "Hello everyone, the meeting is at 5 PM."),
            ],
        )

    async def test_rejects_empty_text(self):
        provider = DeepTranslatorProvider()
        with self.assertRaises(ValueError):
            await provider.translate_to_english_and_hindi("   ")

    async def test_raises_translation_timeout_error(self):
        async def slow_to_thread(*args, **kwargs):
            await asyncio.sleep(0.25)
            return ("Hello", "नमस्ते")

        provider = DeepTranslatorProvider(timeout=0.05)
        with patch("src.translation.deep_translator_provider.asyncio.to_thread", side_effect=slow_to_thread):
            with self.assertRaises(TranslationTimeoutError):
                await provider.translate_to_english_and_hindi("test message")

    async def test_raises_translation_error_on_empty_backend_response(self):
        class EmptyGoogleTranslator:
            def __init__(self, source="auto", target="en"):
                pass

            def translate(self, text):
                return ""

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = EmptyGoogleTranslator

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider = DeepTranslatorProvider()
        with patch.dict(sys.modules, {"deep_translator": fake_mod}):
            with patch(
                "src.translation.deep_translator_provider.asyncio.to_thread",
                side_effect=inline_to_thread,
            ):
                with self.assertRaises(TranslationError):
                    await provider.translate_to_english_and_hindi("hello")


class TestOpenAITranslatorProvider(unittest.IsolatedAsyncioTestCase):
    async def test_requires_api_key(self):
        provider = OpenAITranslatorProvider(api_key="")
        self.assertFalse(await provider.is_available())
        with self.assertRaises(TranslationError):
            await provider.translate_to_english_and_hindi("hello")

    async def test_parses_paired_json_response(self):
        provider = OpenAITranslatorProvider(api_key="sk-test-123")
        self.assertTrue(await provider.is_available())

        payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "english": "Let's meet tomorrow morning.",
                                "hindi": "चलो कल सुबह मिलते हैं।",
                            }
                        )
                    }
                }
            ]
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=json.dumps(payload))
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            res = await provider.translate_to_english_and_hindi("Kal subah milte hain")

        self.assertEqual(res.english_text, "Let's meet tomorrow morning.")
        self.assertEqual(res.hindi_text, "चलो कल सुबह मिलते हैं।")
        self.assertEqual(res.provider, "openai")

    async def test_handles_http_error_and_timeout(self):
        provider = OpenAITranslatorProvider(api_key="sk-test-123", timeout=0.05)

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with self.assertRaises(TranslationTimeoutError):
                await provider.translate_to_english_and_hindi("test")

        # HTTP 401/500 API error
        mock_err_resp = AsyncMock()
        mock_err_resp.status = 401
        mock_err_resp.text = AsyncMock(return_value="Invalid API key")
        mock_err_resp.__aenter__ = AsyncMock(return_value=mock_err_resp)
        mock_err_resp.__aexit__ = AsyncMock(return_value=False)
        mock_session.post = MagicMock(return_value=mock_err_resp)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            with self.assertRaises(TranslationError):
                await provider.translate_to_english_and_hindi("test")


class TestTranslatorFactory(unittest.TestCase):
    def test_auto_selects_deep_translator_without_api_key(self):
        provider = create_translator_provider("auto", openai_api_key="")
        self.assertIsInstance(provider, DeepTranslatorProvider)

    def test_auto_selects_openai_when_api_key_present(self):
        provider = create_translator_provider("auto", openai_api_key="sk-123")
        self.assertIsInstance(provider, OpenAITranslatorProvider)

    def test_rejects_unknown_provider(self):
        with self.assertRaises(ValueError):
            create_translator_provider("unknown_engine")


if __name__ == "__main__":
    unittest.main()
