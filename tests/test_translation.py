"""Tests for the decoupled Hinglish translation layer."""

import asyncio
import json
import sys
from types import ModuleType
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.storage.settings_store import SettingsStore
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
    async def test_is_available_returns_true_with_deep_translator(self):
        provider = DeepTranslatorProvider(min_request_interval_sec=0.0)
        self.assertIsInstance(provider, TranslatorProvider)
        self.assertTrue(await provider.is_available())

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

        provider = DeepTranslatorProvider(timeout=5.0, min_request_interval_sec=0.0)
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

        class EmptyMyMemoryTranslator:
            def __init__(self, source="en-GB", target="hi-IN"):
                pass

            def translate(self, text):
                return ""

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = EmptyGoogleTranslator
        fake_mod.MyMemoryTranslator = EmptyMyMemoryTranslator

        fake_resp = MagicMock(status_code=200)
        fake_resp.json.return_value = [[["", None, None, 1]]]

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider = DeepTranslatorProvider(min_request_interval_sec=0.0)
        with patch.dict(sys.modules, {"deep_translator": fake_mod}):
            with patch("requests.get", return_value=fake_resp):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    with self.assertRaises(TranslationError):
                        await provider.translate_to_english_and_hindi("hello")

    async def test_caches_in_memory_and_sqlite_across_instances(self):
        calls = []

        class CountingGoogleTranslator:
            def __init__(self, source="auto", target="en"):
                self.source = source
                self.target = target

            def translate(self, text):
                calls.append((self.source, self.target, text))
                if self.target == "en":
                    return "Let's go"
                return "चलो चलते हैं"

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = CountingGoogleTranslator

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        store = SettingsStore(":memory:")
        try:
            provider1 = create_translator_provider(
                "auto", timeout=5.0, openai_api_key="", settings_store=store
            )
            self.assertIsInstance(provider1, DeepTranslatorProvider)
            provider1.min_request_interval_sec = 0.0

            with patch.dict(sys.modules, {"deep_translator": fake_mod}):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    res1 = await provider1.translate_to_english_and_hindi("Chalo   chalte hain")
                    # Second call hits in-memory cache (whitespace normalized)
                    res2 = await provider1.translate_to_english_and_hindi("Chalo chalte hain")

            self.assertEqual(len(calls), 2)
            self.assertEqual(res1.english_text, "Let's go")
            self.assertEqual(res2.hindi_text, "चलो चलते हैं")

            # New provider sharing the same SettingsStore hits SQLite cache with 0 API calls
            provider2 = create_translator_provider(
                "google", timeout=5.0, settings_store=store
            )
            with patch.dict(sys.modules, {"deep_translator": fake_mod}):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    res3 = await provider2.translate_to_english_and_hindi("Chalo chalte hain")

            self.assertEqual(len(calls), 2)
            self.assertEqual(res3.english_text, "Let's go")
            self.assertEqual(res3.hindi_text, "चलो चलते हैं")
        finally:
            store.close()

    async def test_rate_limit_429_triggers_cooldown_and_falls_back_to_gtx(self):
        web_calls = []
        gtx_calls = []

        class TooManyRequests(Exception):
            pass

        class RateLimitedGoogleTranslator:
            def __init__(self, source="auto", target="en"):
                self.source = source
                self.target = target

            def translate(self, text):
                web_calls.append((self.source, self.target, text))
                raise TooManyRequests(
                    "Server Error: You made too many requests to the server."
                    "According to google, you are allowed to make 5 requests per second"
                )

        def fake_requests_get(url, params=None, headers=None, timeout=None):
            gtx_calls.append((params["sl"], params["tl"], params["q"]))
            resp = MagicMock(status_code=200)
            if params["tl"] == "en":
                resp.json.return_value = [[["Hello friend", params["q"], None, None, 10]]]
            else:
                resp.json.return_value = [[["नमस्ते दोस्त", params["q"], None, None, 10]]]
            return resp

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = RateLimitedGoogleTranslator

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider = DeepTranslatorProvider(
            timeout=5.0, min_request_interval_sec=0.0, cooldown_sec=60.0
        )
        with patch.dict(sys.modules, {"deep_translator": fake_mod}):
            with patch("requests.get", side_effect=fake_requests_get):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    res1 = await provider.translate_to_english_and_hindi("Namaste dost")
                    # Second distinct phrase during cooldown should skip GoogleTranslator completely
                    res2 = await provider.translate_to_english_and_hindi("Kaise ho bhai")

        self.assertEqual(res1.english_text, "Hello friend")
        self.assertEqual(res1.hindi_text, "नमस्ते दोस्त")
        self.assertEqual(res2.english_text, "Hello friend")
        # Only 1 attempt hit RateLimitedGoogleTranslator before entering cooldown
        self.assertEqual(len(web_calls), 1)
        self.assertEqual(len(gtx_calls), 4)

    async def test_partial_success_does_not_rerun_step1_and_falls_back_to_mymemory(self):
        web_calls = []
        mm_calls = []

        class TooManyRequests(Exception):
            pass

        class PartialGoogleTranslator:
            def __init__(self, source="auto", target="en"):
                self.source = source
                self.target = target

            def translate(self, text):
                web_calls.append((self.source, self.target, text))
                if self.target == "en":
                    return "Meeting at five"
                raise TooManyRequests("429 Too Many Requests")

        class FakeMyMemoryTranslator:
            def __init__(self, source="en-GB", target="hi-IN"):
                self.source = source
                self.target = target

            def translate(self, text):
                mm_calls.append((self.source, self.target, text))
                return "पांच बजे मीटिंग"

        def rate_limited_gtx(url, params=None, headers=None, timeout=None):
            return MagicMock(status_code=429)

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = PartialGoogleTranslator
        fake_mod.MyMemoryTranslator = FakeMyMemoryTranslator

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider = DeepTranslatorProvider(
            timeout=5.0, min_request_interval_sec=0.0, cooldown_sec=60.0
        )
        with patch.dict(sys.modules, {"deep_translator": fake_mod}):
            with patch("requests.get", side_effect=rate_limited_gtx):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    res = await provider.translate_to_english_and_hindi("5 baje meeting hai")

        self.assertEqual(res.english_text, "Meeting at five")
        self.assertEqual(res.hindi_text, "पांच बजे मीटिंग")
        # Step 1 (auto->en) was called only ONCE and never re-run when Step 2 failed
        self.assertEqual(
            web_calls,
            [
                ("auto", "en", "5 baje meeting hai"),
                ("en", "hi", "Meeting at five"),
            ],
        )
        self.assertEqual(mm_calls, [("en-GB", "hi-IN", "Meeting at five")])

    async def test_raises_clear_error_when_all_backends_rate_limited(self):
        class TooManyRequests(Exception):
            pass

        class RateLimitedAll:
            def __init__(self, source="auto", target="en"):
                pass

            def translate(self, text):
                raise TooManyRequests("429 Too Many Requests")

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = RateLimitedAll
        fake_mod.MyMemoryTranslator = RateLimitedAll

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        provider = DeepTranslatorProvider(timeout=5.0, min_request_interval_sec=0.0)
        with patch.dict(sys.modules, {"deep_translator": fake_mod}):
            with patch("requests.get", return_value=MagicMock(status_code=429)):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    with self.assertRaises(TranslationError) as ctx:
                        await provider.translate_to_english_and_hindi("test")

        self.assertIn("rate-limited", str(ctx.exception).lower())


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
