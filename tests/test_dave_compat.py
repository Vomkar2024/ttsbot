"""Tests for DAVE 0.2.x compatibility bridge, voice diagnostics, and dependency pins."""

import asyncio
import logging
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from src.voice.dave_compat import (
    check_dependency_warnings,
    ensure_dave_compat,
    get_voice_dependency_versions,
    inspect_voice_connection_state,
)
from src.voice.discord_voice_session import DiscordVoiceSession
from src.voice.serial_queue import QueueJob


class _FakeDaveSession02:
    """Simulates davey >= 0.2.0 where keys only activate after prepare + execute."""

    def __init__(self, commit_result: bool = True, fail_commit: bool = False):
        self.ready = False
        self.can_encrypt = False
        self.status = SimpleNamespace(name="pending")
        self.commit_result = commit_result
        self.fail_commit = fail_commit
        self.prepared: list[tuple[int, int]] = []
        self.executed: list[int] = []
        self.passthrough_calls: list[tuple[bool, int]] = []

    def process_commit(self, payload: bytes) -> bool:
        if self.fail_commit:
            raise ValueError("Invalid MLS commit")
        # Deliberately keep ready=False (unlike davey 0.1.x) until prepare+execute!
        return self.commit_result

    def process_welcome(self, payload: bytes) -> None:
        if self.fail_commit:
            raise ValueError("Invalid MLS welcome")
        # Deliberately keep ready=False until prepare+execute!

    def prepare_transition(self, transition_id: int, protocol_version: int) -> None:
        self.prepared.append((transition_id, protocol_version))

    def execute_transition(self, transition_id: int) -> bool:
        self.executed.append(transition_id)
        if any(tid == transition_id and ver == 0 for tid, ver in self.prepared):
            self.ready = False
            self.can_encrypt = False
            self.status = SimpleNamespace(name="inactive")
        elif any(tid == transition_id for tid, _ in self.prepared):
            self.ready = True
            self.can_encrypt = True
            self.status = SimpleNamespace(name="active")
        return True

    def set_passthrough_mode(self, passthrough: bool, expiry: int = 10) -> None:
        self.passthrough_calls.append((passthrough, expiry))


class _FakeDaveSession01:
    """Simulates davey 0.1.x (no prepare_transition/execute_transition methods)."""

    def __init__(self):
        self.ready = False

    def process_commit(self, payload: bytes) -> None:
        self.ready = True

    def process_welcome(self, payload: bytes) -> None:
        self.ready = True


def _make_legacy_discord_classes():
    """Creates isolated FakeVoiceConnectionState & FakeVoiceWebSocket matching discord.py 2.7.1."""

    class FakeVoiceConnectionState:
        def __init__(self, dave_session=None, protocol_version: int = 1):
            self.dave_session = dave_session
            self.dave_protocol_version = protocol_version
            self.dave_pending_transitions: dict[int, int] = {}
            self.dave_downgraded = False
            self.recoveries: list[int] = []

        @property
        def can_encrypt(self) -> bool:
            return (
                self.dave_protocol_version != 0
                and self.dave_session is not None
                and self.dave_session.ready
            )

        async def _execute_transition(self, transition_id: int) -> None:
            if transition_id not in self.dave_pending_transitions:
                return
            old_version = self.dave_protocol_version
            self.dave_protocol_version = self.dave_pending_transitions.pop(transition_id)
            if old_version != self.dave_protocol_version and self.dave_protocol_version == 0:
                self.dave_downgraded = True
            elif transition_id > 0 and self.dave_downgraded:
                self.dave_downgraded = False
                if self.dave_session:
                    self.dave_session.set_passthrough_mode(True, 10)

        async def _recover_from_invalid_commit(self, transition_id: int) -> None:
            self.recoveries.append(transition_id)

    class FakeVoiceWebSocket:
        DAVE_PREPARE_TRANSITION = 21
        DAVE_EXECUTE_TRANSITION = 22
        MLS_EXTERNAL_SENDER = 25
        MLS_PROPOSALS = 27
        MLS_ANNOUNCE_COMMIT_TRANSITION = 29
        MLS_WELCOME = 30

        def __init__(self, connection):
            self._connection = connection
            self.seq_ack = -1
            self.transitions_ready: list[int] = []
            self.other_binary_ops: list[int] = []

        async def send_transition_ready(self, transition_id: int) -> None:
            self.transitions_ready.append(transition_id)

        async def received_message(self, msg: dict) -> None:
            op = msg["op"]
            data = msg["d"]
            self.seq_ack = msg.get("seq", self.seq_ack)
            state = self._connection
            if state.dave_session:
                if op == self.DAVE_PREPARE_TRANSITION:
                    state.dave_pending_transitions[data["transition_id"]] = data["protocol_version"]
                    if data["transition_id"] == 0:
                        await state._execute_transition(data["transition_id"])
                    else:
                        if data["protocol_version"] == 0 and state.dave_session:
                            state.dave_session.set_passthrough_mode(True, 120)
                        await self.send_transition_ready(data["transition_id"])
                elif op == self.DAVE_EXECUTE_TRANSITION:
                    await state._execute_transition(data["transition_id"])

        async def received_binary_message(self, msg: bytes) -> None:
            self.seq_ack = struct.unpack_from(">H", msg, 0)[0]
            op = msg[2]
            state = self._connection
            if state.dave_session is None:
                return
            if op == self.MLS_EXTERNAL_SENDER:
                self.other_binary_ops.append(op)
            elif op == self.MLS_ANNOUNCE_COMMIT_TRANSITION:
                transition_id = struct.unpack_from(">H", msg, 3)[0]
                state.dave_session.process_commit(msg[5:])
                if transition_id != 0:
                    state.dave_pending_transitions[transition_id] = state.dave_protocol_version
                    await self.send_transition_ready(transition_id)
            elif op == self.MLS_WELCOME:
                transition_id = struct.unpack_from(">H", msg, 3)[0]
                state.dave_session.process_welcome(msg[5:])
                if transition_id != 0:
                    state.dave_pending_transitions[transition_id] = state.dave_protocol_version
                    await self.send_transition_ready(transition_id)

    return FakeVoiceWebSocket, FakeVoiceConnectionState


def _pack_binary(seq: int, op: int, transition_id: int, payload: bytes = b"mls-data") -> bytes:
    return struct.pack(">HBH", seq, op, transition_id) + payload


class TestDaveCompat(unittest.IsolatedAsyncioTestCase):
    async def test_noop_for_davey_01x(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession01, __version__="0.1.7")
        res = ensure_dave_compat(
            voice_ws_cls=ws_cls,
            voice_state_cls=state_cls,
            davey_mod=fake_mod,
        )
        self.assertFalse(res["applied"])
        self.assertFalse(res["davey_02_detected"])

    async def test_patches_davey_02_and_is_idempotent(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")

        res1 = ensure_dave_compat(
            voice_ws_cls=ws_cls,
            voice_state_cls=state_cls,
            davey_mod=fake_mod,
        )
        self.assertTrue(res1["applied"])
        self.assertFalse(res1["already_patched"])
        self.assertTrue(res1["davey_02_detected"])

        res2 = ensure_dave_compat(
            voice_ws_cls=ws_cls,
            voice_state_cls=state_cls,
            davey_mod=fake_mod,
        )
        self.assertFalse(res2["applied"])
        self.assertTrue(res2["already_patched"])

    async def test_welcome_transition_zero_executes_immediately(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02()
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        self.assertFalse(state.can_encrypt)
        await ws.received_binary_message(_pack_binary(1, ws_cls.MLS_WELCOME, 0))

        self.assertEqual(dave.prepared, [(0, 1)])
        self.assertEqual(dave.executed, [0])
        self.assertEqual(ws.transitions_ready, [])
        self.assertTrue(dave.ready)
        self.assertTrue(state.can_encrypt)

    async def test_welcome_and_commit_nonzero_wait_for_execute_opcode(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02()
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        await ws.received_binary_message(_pack_binary(10, ws_cls.MLS_WELCOME, 42))
        self.assertEqual(dave.prepared, [(42, 1)])
        self.assertEqual(dave.executed, [])
        self.assertEqual(ws.transitions_ready, [42])
        self.assertFalse(state.can_encrypt)

        await ws.received_message({"op": ws_cls.DAVE_EXECUTE_TRANSITION, "d": {"transition_id": 42}})
        self.assertEqual(dave.executed, [42])
        self.assertTrue(state.can_encrypt)

    async def test_commit_returns_false_skips_prepare_and_ready(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02(commit_result=False)
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        await ws.received_binary_message(_pack_binary(5, ws_cls.MLS_ANNOUNCE_COMMIT_TRANSITION, 7))
        self.assertEqual(dave.prepared, [])
        self.assertEqual(dave.executed, [])
        self.assertEqual(ws.transitions_ready, [])

    async def test_commit_returns_true_transition_zero_activates_keys(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02(commit_result=True)
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        await ws.received_binary_message(_pack_binary(6, ws_cls.MLS_ANNOUNCE_COMMIT_TRANSITION, 0))
        self.assertEqual(dave.prepared, [(0, 1)])
        self.assertEqual(dave.executed, [0])
        self.assertTrue(state.can_encrypt)

    async def test_invalid_commit_triggers_recovery(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02(fail_commit=True)
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        await ws.received_binary_message(_pack_binary(7, ws_cls.MLS_ANNOUNCE_COMMIT_TRANSITION, 13))
        self.assertEqual(state.recoveries, [13])

    async def test_op21_prepare_transition_downgrade_prepares_and_executes(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02()
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        # Simulate initial active session
        await ws.received_binary_message(_pack_binary(1, ws_cls.MLS_WELCOME, 0))
        self.assertTrue(state.can_encrypt)

        # Downgrade via op 21 -> op 22
        await ws.received_message(
            {"op": ws_cls.DAVE_PREPARE_TRANSITION, "d": {"transition_id": 9, "protocol_version": 0}}
        )
        self.assertIn((9, 0), dave.prepared)
        self.assertEqual(ws.transitions_ready, [9])

        await ws.received_message({"op": ws_cls.DAVE_EXECUTE_TRANSITION, "d": {"transition_id": 9}})
        self.assertIn(9, dave.executed)
        self.assertFalse(dave.ready)
        self.assertEqual(state.dave_protocol_version, 0)

    async def test_other_binary_opcodes_pass_through(self):
        ws_cls, state_cls = _make_legacy_discord_classes()
        fake_mod = SimpleNamespace(DaveSession=_FakeDaveSession02, __version__="0.2.0")
        ensure_dave_compat(voice_ws_cls=ws_cls, voice_state_cls=state_cls, davey_mod=fake_mod)

        dave = _FakeDaveSession02()
        state = state_cls(dave_session=dave, protocol_version=1)
        ws = ws_cls(state)

        await ws.received_binary_message(_pack_binary(3, ws_cls.MLS_EXTERNAL_SENDER, 0))
        self.assertEqual(ws.other_binary_ops, [ws_cls.MLS_EXTERNAL_SENDER])


class TestVoiceDiagnosticsAndPins(unittest.IsolatedAsyncioTestCase):
    def test_inspect_voice_connection_state_handles_mocks_and_real_attrs(self):
        self.assertIsNone(inspect_voice_connection_state(None))
        self.assertIsNone(inspect_voice_connection_state(MagicMock(spec=discord.VoiceClient)))

        mock_vc = MagicMock()
        mock_vc._connection = SimpleNamespace(
            mode="aead_xchacha20_poly1305_rtpsize",
            dave_protocol_version=1,
            can_encrypt=False,
            dave_session=SimpleNamespace(ready=False, can_encrypt=False, status=SimpleNamespace(name="pending")),
        )
        diag = inspect_voice_connection_state(mock_vc)
        self.assertEqual(
            diag,
            {
                "mode": "aead_xchacha20_poly1305_rtpsize",
                "dave_protocol_version": 1,
                "dave_session_present": True,
                "dave_ready": False,
                "dave_session_can_encrypt": False,
                "can_encrypt": False,
                "dave_status": "pending",
            },
        )

    async def test_voice_session_warns_when_dave_not_ready(self):
        mock_vc = MagicMock(spec=discord.VoiceClient)
        mock_vc.is_connected.return_value = True
        mock_vc.is_playing.return_value = False
        mock_vc.is_paused.return_value = False
        mock_vc._connection = SimpleNamespace(
            mode="aead_xchacha20_poly1305_rtpsize",
            dave_protocol_version=1,
            can_encrypt=False,
            dave_session=SimpleNamespace(ready=False, can_encrypt=False, status=SimpleNamespace(name="pending")),
        )

        def mock_play(source, after=None):
            if after:
                after(None)

        mock_vc.play = MagicMock(side_effect=mock_play)
        session = DiscordVoiceSession(guild_id="123", channel_id="456", voice_client=mock_vc)

        with self.assertLogs("src.voice.discord_voice_session", level=logging.WARNING) as cm:
            await session.play_test_tone(duration=0.05, use_ffmpeg=False)

        self.assertTrue(any("encryption is NOT ready" in msg for msg in cm.output))

    async def test_voice_session_no_warning_when_dave_ready_and_cleans_up_on_play_failure(self):
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

        # Case 1: play() succeeds -> INFO logs state, no WARNING, cleanup delegated to AudioPlayer
        mock_raw = MagicMock(spec=discord.AudioSource)
        mock_raw.is_opus.return_value = False
        mock_vc.play = MagicMock(side_effect=lambda src, after=None: after(None) if after else None)

        mock_tts = SimpleNamespace(synthesize=AsyncMock(return_value=b"fake-mp3"))
        session = DiscordVoiceSession(
            guild_id="123",
            channel_id="456",
            voice_client=mock_vc,
            tts_provider=mock_tts,
        )
        job = QueueJob(
            1,
            {
                "text": "hi",
                "spokenText": "hi",
                "language": "en",
                "voice": "default",
                "authorName": "User",
            },
            asyncio.get_running_loop(),
        )

        with patch("discord.FFmpegPCMAudio", return_value=mock_raw):
            with self.assertLogs("src.voice.discord_voice_session", level=logging.INFO) as cm:
                await session._process_job(job, asyncio.Event())

        self.assertFalse(any(r.levelno >= logging.WARNING for r in cm.records))
        self.assertTrue(any("can_encrypt=True" in msg for msg in cm.output))
        mock_raw.cleanup.assert_not_called()

        # Case 2: play() raises before AudioPlayer starts -> cleanup() IS called
        mock_raw2 = MagicMock(spec=discord.AudioSource)
        mock_raw2.is_opus.return_value = False
        mock_vc.play = MagicMock(side_effect=RuntimeError("Opus failure"))

        with patch("discord.FFmpegPCMAudio", return_value=mock_raw2):
            with self.assertRaises(RuntimeError):
                await session._process_job(job, asyncio.Event())

        mock_raw2.cleanup.assert_called_once()

    def test_dependency_warnings_detect_out_of_range_versions(self):
        warns_old = check_dependency_warnings({"discord_py": "2.6.4", "pynacl": "1.5.0"})
        self.assertTrue(any("older than 2.7.0" in w for w in warns_old))

        warns_nacl = check_dependency_warnings({"discord_py": "2.7.1", "pynacl": "1.6.2"})
        self.assertTrue(any("PyNaCl 1.6.2" in w for w in warns_nacl))

        self.assertEqual(check_dependency_warnings({"discord_py": "2.7.1", "pynacl": "1.5.0"}), [])

    async def test_live_runtime_compat_and_dependency_pin_files(self):
        root = Path(__file__).resolve().parent.parent
        reqs = (root / "requirements.txt").read_text(encoding="utf-8")
        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

        for content in (reqs, pyproject):
            self.assertIn("discord.py[voice]>=2.7.0,<2.8", content)
            self.assertIn("davey>=0.1.7,<0.2.0", content)
            self.assertIn("PyNaCl>=1.5.0,<1.6", content)
            self.assertIn("imageio-ffmpeg>=0.6.0", content)

        # Verify ensure_dave_compat runs safely on real installed discord.py
        import discord.gateway
        import discord.voice_state

        res = ensure_dave_compat()
        versions = get_voice_dependency_versions()
        self.assertIn("discord_py", versions)
        if versions.get("davey_02_api") and not res.get("native_support"):
            self.assertTrue(res.get("applied") or res.get("already_patched"))
            self.assertTrue(versions.get("compat_patched"))

            # Verify the patched real discord.py classes drive FakeDaveSession02 to ready=True
            state = object.__new__(discord.voice_state.VoiceConnectionState)
            state.dave_session = _FakeDaveSession02()
            state.dave_protocol_version = 1
            state.dave_pending_transitions = {}
            state.dave_downgraded = False
            state._recover_from_invalid_commit = AsyncMock()

            ws = object.__new__(discord.gateway.DiscordVoiceWebSocket)
            ws._connection = state
            ws.seq_ack = -1
            ws._hook = AsyncMock()
            ws.send_transition_ready = AsyncMock()

            await ws.received_binary_message(
                _pack_binary(1, discord.gateway.DiscordVoiceWebSocket.MLS_WELCOME, 15)
            )
            self.assertEqual(state.dave_session.prepared, [(15, 1)])
            ws.send_transition_ready.assert_awaited_once_with(15)
            self.assertFalse(state.can_encrypt)

            await ws.received_message(
                {"op": discord.gateway.DiscordVoiceWebSocket.DAVE_EXECUTE_TRANSITION, "d": {"transition_id": 15}}
            )
            self.assertEqual(state.dave_session.executed, [15])
            self.assertTrue(state.can_encrypt)


if __name__ == "__main__":
    unittest.main()
