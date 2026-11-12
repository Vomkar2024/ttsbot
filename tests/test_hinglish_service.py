"""Tests for HinglishService orchestration, audio delivery paths, and failure cases."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock

import discord

from src.hinglish.service import (
    DELIVERY_AUTO,
    DELIVERY_FILE,
    DELIVERY_VOICE,
    HinglishService,
)
from src.translation.base import HinglishTranslation, TranslationTimeoutError
from src.voice.session_manager import VoiceSessionError


class FakeTranslator:
    provider_name = "fake"

    def __init__(self, english="Hello everyone!", hindi="नमस्ते सब लोग!", error=None):
        self.english = english
        self.hindi = hindi
        self.error = error
        self.calls = []

    async def is_available(self):
        return True

    async def translate_to_english_and_hindi(self, text, *, timeout=None):
        self.calls.append(text)
        if self.error:
            raise self.error
        return HinglishTranslation(
            source_text=text,
            english_text=self.english,
            hindi_text=self.hindi,
            provider=self.provider_name,
        )


class FakeTTSProvider:
    def __init__(self, audio_bytes=b"ID3\x03\x00mock_mp3", error=None, delay=0.0):
        self.audio_bytes = audio_bytes
        self.error = error
        self.delay = delay
        self.calls = []

    async def synthesize(self, text, *, language=None, voice=None, **kwargs):
        self.calls.append({"text": text, "language": language, "voice": voice})
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.audio_bytes


class TestHinglishService(unittest.IsolatedAsyncioTestCase):
    def _make_voice_channel(self, ch_id="vc_1", name="General Voice", connect=True, speak=True):
        return SimpleNamespace(
            id=ch_id,
            name=name,
            permissions_for=lambda me: SimpleNamespace(connect=connect, speak=speak),
        )

    def _make_text_channel(self, ch_id="tc_1", attach_files=True, send_messages=True):
        return SimpleNamespace(
            id=ch_id,
            name="hinglish-chat",
            permissions_for=lambda me: SimpleNamespace(
                attach_files=attach_files,
                send_messages=send_messages,
            ),
        )

    async def test_voice_delivery_joins_user_vc_and_enqueues_hindi_audio(self):
        translator = FakeTranslator(
            english="The server restarts in five minutes.",
            hindi="सर्वर पांच मिनट में फिर से शुरू होगा।",
        )
        tts = FakeTTSProvider(audio_bytes=b"RIFF\x24\x00\x00\x00WAVEfmt ")

        enqueued_jobs = []
        fake_session = SimpleNamespace(
            channel_id="vc_1",
            is_destroyed=False,
            voice_client=SimpleNamespace(is_connected=lambda: True),
            serial_queue=SimpleNamespace(enqueue=lambda item: enqueued_jobs.append(item)),
        )
        session_manager = MagicMock()
        session_manager.get_session.return_value = None
        session_manager.join = AsyncMock(return_value={"session": fake_session, "reused": False})

        service = HinglishService(translator, tts, session_manager)

        vc = self._make_voice_channel(ch_id="vc_1", name="Lounge")
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)
        member = SimpleNamespace(id="u_1", voice=SimpleNamespace(channel=vc))
        tc = self._make_text_channel()

        result = await service.process_message(
            "Bhai server 5 min me restart hoga",
            guild=guild,
            member=member,
            text_channel=tc,
            author_name="Omkar",
            author_id="u_1",
            delivery_mode=DELIVERY_AUTO,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.delivered_via, DELIVERY_VOICE)
        self.assertEqual(result.english_text, "The server restarts in five minutes.")
        self.assertEqual(result.hindi_text, "सर्वर पांच मिनट में फिर से शुरू होगा।")
        self.assertEqual(tts.calls[0]["text"], "सर्वर पांच मिनट में फिर से शुरू होगा।")
        self.assertEqual(tts.calls[0]["language"], "hi")
        session_manager.join.assert_awaited_once_with(guild, vc)
        self.assertEqual(len(enqueued_jobs), 1)
        self.assertEqual(enqueued_jobs[0]["spoken_text"], "सर्वर पांच मिनट में फिर से शुरू होगा।")
        self.assertEqual(enqueued_jobs[0]["text"], "The server restarts in five minutes.")
        self.assertEqual(enqueued_jobs[0]["language"], "hi")
        self.assertEqual(enqueued_jobs[0]["audio_bytes"], b"RIFF\x24\x00\x00\x00WAVEfmt ")

    async def test_auto_falls_back_to_audio_attachment_when_no_voice_active(self):
        translator = FakeTranslator(english="Good morning!", hindi="सुप्रभात!")
        tts = FakeTTSProvider(audio_bytes=b"ID3_FAKE_MP3_BYTES")
        session_manager = MagicMock()
        session_manager.get_session.return_value = None

        service = HinglishService(translator, tts, session_manager)
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)
        member = SimpleNamespace(id="u_1", voice=None)  # Not in VC
        tc = self._make_text_channel(attach_files=True)

        result = await service.process_message(
            "Good morning!",
            guild=guild,
            member=member,
            text_channel=tc,
            delivery_mode=DELIVERY_AUTO,
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.delivered_via, DELIVERY_FILE)
        self.assertEqual(result.english_text, "Good morning!")
        self.assertIsNotNone(result.audio)
        self.assertEqual(result.audio.filename_extension, "mp3")
        self.assertIn("No active voice channel", result.status_note)

        sender = AsyncMock()
        await service.send_result(sender, result, author_name="Alice")
        sender.assert_awaited_once()
        kwargs = sender.call_args.kwargs
        self.assertIn("embed", kwargs)
        self.assertIn("file", kwargs)
        self.assertEqual(kwargs["embed"].description, "Good morning!")
        self.assertEqual(kwargs["file"].filename, "hinglish_hi.mp3")

    async def test_explicit_voice_mode_fails_when_user_not_in_voice_channel(self):
        translator = FakeTranslator()
        tts = FakeTTSProvider()
        session_manager = MagicMock()
        session_manager.get_session.return_value = None

        service = HinglishService(translator, tts, session_manager)
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"))
        member = SimpleNamespace(id="u_1", voice=None)

        result = await service.process_message(
            "Hello",
            guild=guild,
            member=member,
            delivery_mode=DELIVERY_VOICE,
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.delivered_via, "text_only")
        self.assertEqual(result.error_code, "USER_NOT_IN_VOICE")
        self.assertIn("not in a voice channel", result.status_note)
        self.assertEqual(len(tts.calls), 0)

    async def test_handles_tts_failure_and_tts_timeout_gracefully(self):
        translator = FakeTranslator(english="Meeting starts now.", hindi="मीटिंग अभी शुरू होती है।")
        session_manager = MagicMock()
        session_manager.get_session.return_value = None
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"))
        member = SimpleNamespace(id="u_1", voice=None)

        # Case 1: TTS timeout
        slow_tts = FakeTTSProvider(delay=0.3)
        service_timeout = HinglishService(translator, slow_tts, session_manager, tts_timeout_sec=0.05)
        res_timeout = await service_timeout.process_message(
            "Meeting starts now.", guild=guild, member=member
        )
        self.assertFalse(res_timeout.ok)
        self.assertEqual(res_timeout.delivered_via, "text_only")
        self.assertEqual(res_timeout.error_code, "TTS_TIMEOUT")
        self.assertEqual(res_timeout.english_text, "Meeting starts now.")

        # Case 2: TTS service error
        failing_tts = FakeTTSProvider(error=RuntimeError("gTTS 503 Service Unavailable"))
        service_fail = HinglishService(translator, failing_tts, session_manager)
        res_fail = await service_fail.process_message(
            "Meeting starts now.", guild=guild, member=member
        )
        self.assertFalse(res_fail.ok)
        self.assertEqual(res_fail.delivered_via, "text_only")
        self.assertEqual(res_fail.error_code, "TTS_FAILED")
        self.assertEqual(res_fail.english_text, "Meeting starts now.")

    async def test_handles_audio_file_exceeding_discord_limit(self):
        translator = FakeTranslator(english="Long text", hindi="लंबा संदेश")
        big_audio = b"A" * 2048
        tts = FakeTTSProvider(audio_bytes=big_audio)
        session_manager = MagicMock()
        session_manager.get_session.return_value = None

        service = HinglishService(translator, tts, session_manager, max_audio_file_bytes=1024)
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)
        member = SimpleNamespace(id="u_1", voice=None)
        tc = self._make_text_channel(attach_files=True)

        result = await service.process_message(
            "Long text", guild=guild, member=member, text_channel=tc, delivery_mode=DELIVERY_FILE
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.delivered_via, "text_only")
        self.assertEqual(result.error_code, "FILE_TOO_LARGE")
        self.assertIn("exceeds Discord's upload limit", result.status_note)

        # Runtime HTTP 413 Payload Too Large fallback during send_result
        service_small = HinglishService(translator, tts, session_manager, max_audio_file_bytes=10_000)
        res_ok = await service_small.process_message(
            "Long text", guild=guild, member=member, text_channel=tc, delivery_mode=DELIVERY_FILE
        )
        self.assertEqual(res_ok.delivered_via, DELIVERY_FILE)

        resp_413 = MagicMock(status=413, reason="Payload Too Large")
        http_413 = discord.HTTPException(resp_413, {"message": "Request entity too large", "code": 40005})
        sender = AsyncMock(side_effect=[http_413, None])
        sent = await service_small.send_result(sender, res_ok, author_name="Alice")
        self.assertEqual(sent.delivered_via, "text_only")
        self.assertEqual(sent.error_code, "FILE_TOO_LARGE")
        self.assertEqual(sender.await_count, 2)

    async def test_handles_missing_connect_and_speak_permissions(self):
        translator = FakeTranslator(english="Hi", hindi="नमस्ते")
        tts = FakeTTSProvider()
        session_manager = MagicMock()
        session_manager.get_session.return_value = None
        session_manager.join = AsyncMock()

        service = HinglishService(translator, tts, session_manager)
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)

        # Missing Connect in explicit voice mode
        vc_no_connect = self._make_voice_channel(connect=False, speak=True)
        member_no_conn = SimpleNamespace(id="u_1", voice=SimpleNamespace(channel=vc_no_connect))
        res_conn = await service.process_message(
            "Hi", guild=guild, member=member_no_conn, delivery_mode=DELIVERY_VOICE
        )
        self.assertEqual(res_conn.error_code, "MISSING_CONNECT_PERM")
        self.assertIn("Connect", res_conn.status_note)
        session_manager.join.assert_not_called()

        # Missing Speak in auto mode -> surfaces Speak error AND falls back to file attachment
        vc_no_speak = self._make_voice_channel(connect=True, speak=False)
        member_no_speak = SimpleNamespace(id="u_1", voice=SimpleNamespace(channel=vc_no_speak))
        tc = self._make_text_channel(attach_files=True)
        res_speak = await service.process_message(
            "Hi", guild=guild, member=member_no_speak, text_channel=tc, delivery_mode=DELIVERY_AUTO
        )
        self.assertEqual(res_speak.voice_error_code, "MISSING_SPEAK_PERM")
        self.assertEqual(res_speak.delivered_via, DELIVERY_FILE)
        self.assertIn("Speak", res_speak.status_note)

        # VoiceSessionError propagated from session_manager.join
        vc_ok = self._make_voice_channel(connect=True, speak=True)
        member_ok = SimpleNamespace(id="u_1", voice=SimpleNamespace(channel=vc_ok))
        session_manager.join = AsyncMock(
            side_effect=VoiceSessionError("Missing Speak", "MISSING_SPEAK_PERM")
        )
        res_err = await service.process_message(
            "Hi", guild=guild, member=member_ok, delivery_mode=DELIVERY_VOICE
        )
        self.assertEqual(res_err.error_code, "MISSING_SPEAK_PERM")

    async def test_handles_missing_attach_files_permission_preflight_and_runtime(self):
        translator = FakeTranslator(english="Hi", hindi="नमस्ते")
        tts = FakeTTSProvider()
        session_manager = MagicMock()
        session_manager.get_session.return_value = None

        service = HinglishService(translator, tts, session_manager)
        guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)
        member = SimpleNamespace(id="u_1", voice=None)

        # 1. Pre-flight check catches attach_files=False
        tc_no_attach = self._make_text_channel(attach_files=False)
        res = await service.process_message(
            "Hi", guild=guild, member=member, text_channel=tc_no_attach, delivery_mode=DELIVERY_FILE
        )
        self.assertFalse(res.ok)
        self.assertEqual(res.delivered_via, "text_only")
        self.assertEqual(res.error_code, "MISSING_ATTACH_FILES_PERM")
        self.assertIn("Attach Files", res.status_note)

        # 2. Runtime discord.Forbidden fallback during send_result
        tc_allowed = self._make_text_channel(attach_files=True)
        res_ok = await service.process_message(
            "Hi", guild=guild, member=member, text_channel=tc_allowed, delivery_mode=DELIVERY_FILE
        )
        self.assertEqual(res_ok.delivered_via, DELIVERY_FILE)

        resp_mock = MagicMock(status=403, reason="Forbidden")
        forbidden_err = discord.Forbidden(resp_mock, "Missing Permissions")
        sender = AsyncMock(side_effect=[forbidden_err, None])
        sent = await service.send_result(sender, res_ok, author_name="Alice")
        self.assertEqual(sent.delivered_via, "text_only")
        self.assertEqual(sent.error_code, "MISSING_ATTACH_FILES_PERM")
        self.assertEqual(sender.await_count, 2)

    async def test_handles_translation_timeout_cleanly(self):
        translator = FakeTranslator(error=TranslationTimeoutError("Timed out"))
        tts = FakeTTSProvider()
        service = HinglishService(translator, tts, MagicMock())
        res = await service.process_message("hello")
        self.assertFalse(res.ok)
        self.assertEqual(res.delivered_via, "none")
        self.assertEqual(res.error_code, "TRANSLATION_TIMEOUT")
        self.assertEqual(len(tts.calls), 0)

    async def test_cached_translation_still_synthesizes_and_delivers_audio(self):
        import sys
        from types import ModuleType
        from unittest.mock import patch
        from src.storage.settings_store import SettingsStore
        from src.translation import DeepTranslatorProvider

        calls = []

        class FakeGoogleTranslator:
            def __init__(self, source="auto", target="en"):
                self.target = target

            def translate(self, text):
                calls.append(text)
                return "Good night!" if self.target == "en" else "शुभ रात्रि!"

        fake_mod = ModuleType("deep_translator")
        fake_mod.GoogleTranslator = FakeGoogleTranslator

        async def inline_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        store = SettingsStore(":memory:")
        try:
            translator = DeepTranslatorProvider(settings_store=store, min_request_interval_sec=0.0)
            tts = FakeTTSProvider(audio_bytes=b"ID3_FAKE_MP3")
            session_manager = MagicMock()
            session_manager.get_session.return_value = None
            service = HinglishService(translator, tts, session_manager)

            guild = SimpleNamespace(id="g_1", me=SimpleNamespace(id="bot_1"), filesize_limit=10_485_760)
            member = SimpleNamespace(id="u_1", voice=None)
            tc = self._make_text_channel(attach_files=True)

            with patch.dict(sys.modules, {"deep_translator": fake_mod}):
                with patch(
                    "src.translation.deep_translator_provider.asyncio.to_thread",
                    side_effect=inline_to_thread,
                ):
                    res1 = await service.process_message(
                        "Shubh ratri", guild=guild, member=member, text_channel=tc, delivery_mode=DELIVERY_AUTO
                    )
                    res2 = await service.process_message(
                        "Shubh ratri", guild=guild, member=member, text_channel=tc, delivery_mode=DELIVERY_AUTO
                    )

            self.assertEqual(len(calls), 2)
            self.assertTrue(res1.ok)
            self.assertTrue(res2.ok)
            self.assertEqual(res2.delivered_via, DELIVERY_FILE)
            self.assertEqual(res2.english_text, "Good night!")
            self.assertEqual(len(tts.calls), 2)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
