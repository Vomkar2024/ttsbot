"""Tests for VoiceSessionManager."""

import unittest
from types import SimpleNamespace
from src.voice.session_manager import VoiceSessionError, VoiceSessionManager


def run_coroutine(coro):
    """Executes a coroutine that resolves synchronously without waiting on external timers."""
    try:
        coro.send(None)
    except StopIteration as e:
        return e.value
    raise RuntimeError("Coroutine did not finish synchronously")


class TestSessionManager(unittest.TestCase):
    def test_serializes_concurrent_operations_per_guild(self):
        manager = VoiceSessionManager()
        sequence = []

        async def op1():
            sequence.append("first")

        async def op2():
            sequence.append("second")

        run_coroutine(manager._serialize("guild_1", op1))
        run_coroutine(manager._serialize("guild_1", op2))
        self.assertEqual(sequence, ["first", "second"])

    def test_allows_different_guilds_to_run_concurrently(self):
        manager = VoiceSessionManager()
        finished = []

        async def op_a():
            finished.append("A")

        async def op_b():
            finished.append("B")

        run_coroutine(manager._serialize("guild_A", op_a))
        run_coroutine(manager._serialize("guild_B", op_b))
        self.assertEqual(finished, ["A", "B"])

    def test_idempotently_leaves_voice_when_no_session(self):
        manager = VoiceSessionManager()
        self.assertFalse(manager.has_session("guild_none"))
        self.assertIsNone(manager.get_session("guild_none"))

        left = run_coroutine(manager.leave("guild_none"))
        self.assertFalse(left)

    def test_throws_when_bot_cannot_be_resolved(self):
        manager = VoiceSessionManager()
        fake_guild = SimpleNamespace(
            id="guild_test",
            me=None,
            fetch_member=None,
        )
        fake_channel = SimpleNamespace(id="vc_1")

        with self.assertRaises(VoiceSessionError) as cm:
            run_coroutine(manager.join(fake_guild, fake_channel))
        self.assertEqual(cm.exception.code, "BOT_NOT_RESOLVED")

    def test_throws_when_bot_lacks_permissions(self):
        manager = VoiceSessionManager()

        # Lacks Connect
        fake_guild1 = SimpleNamespace(
            id="guild_1",
            me=SimpleNamespace(id="bot_1"),
        )
        fake_channel_no_connect = SimpleNamespace(
            id="vc_1",
            permissions_for=lambda me: SimpleNamespace(connect=False, speak=True),
        )

        with self.assertRaises(VoiceSessionError) as cm1:
            run_coroutine(manager.join(fake_guild1, fake_channel_no_connect))
        self.assertEqual(cm1.exception.code, "MISSING_CONNECT_PERM")

        # Lacks Speak
        fake_channel_no_speak = SimpleNamespace(
            id="vc_2",
            permissions_for=lambda me: SimpleNamespace(connect=True, speak=False),
        )

        with self.assertRaises(VoiceSessionError) as cm2:
            run_coroutine(manager.join(fake_guild1, fake_channel_no_speak))
        self.assertEqual(cm2.exception.code, "MISSING_SPEAK_PERM")

    def test_throws_when_voice_dependencies_missing(self):
        manager = VoiceSessionManager()
        fake_guild = SimpleNamespace(id="g_voice", me=SimpleNamespace(id="bot_1"))
        fake_channel = SimpleNamespace(
            id="vc_voice",
            permissions_for=lambda me: SimpleNamespace(connect=True, speak=True),
        )

        import discord.voice_client
        orig_has_dave = getattr(discord.voice_client, "has_dave", True)
        try:
            discord.voice_client.has_dave = False
            with self.assertRaises(VoiceSessionError) as cm:
                run_coroutine(manager.join(fake_guild, fake_channel))
            self.assertEqual(cm.exception.code, "MISSING_VOICE_DEPENDENCY")
            self.assertIn("davey library needed", str(cm.exception))
        finally:
            discord.voice_client.has_dave = orig_has_dave

    def test_reuses_healthy_session_on_same_channel(self):
        manager = VoiceSessionManager()
        fake_session = SimpleNamespace(
            channel_id="vc_same",
            is_destroyed=False,
            voice_client=SimpleNamespace(is_connected=lambda: True),
        )
        manager.sessions["guild_reuse"] = fake_session

        fake_guild = SimpleNamespace(id="guild_reuse")
        fake_channel = SimpleNamespace(id="vc_same")

        result = run_coroutine(manager.join(fake_guild, fake_channel))
        self.assertTrue(result["reused"])
        self.assertEqual(result["session"], fake_session)

    def test_seamless_voice_channel_move_uses_move_to(self):
        manager = VoiceSessionManager()
        moved_to = None
        old_destroyed = False

        class OldQueue:
            cleared = False
            def clear(self):
                self.cleared = True

        class MockVC:
            def is_connected(self):
                return True
            def move_to(self, ch):
                nonlocal moved_to
                moved_to = ch

        class OldSession:
            channel_id = "vc_old"
            is_destroyed = False
            queue = OldQueue()
            voice_client = MockVC()

            def destroy(self):
                nonlocal old_destroyed
                old_destroyed = True

        manager.sessions["guild_move"] = OldSession()

        fake_guild = SimpleNamespace(
            id="guild_move",
            me=SimpleNamespace(id="bot_1"),
        )
        fake_channel = SimpleNamespace(
            id="vc_new",
            permissions_for=lambda me: SimpleNamespace(connect=True, speak=True),
        )

        res = run_coroutine(manager.join(fake_guild, fake_channel))
        self.assertTrue(res["reused"])
        self.assertTrue(res["moved"])
        self.assertEqual(moved_to, fake_channel)
        self.assertFalse(old_destroyed)
        self.assertEqual(res["session"].channel_id, "vc_new")
        self.assertTrue(res["session"].queue.cleared)

    def test_preserves_previous_session_when_move_lacks_permissions(self):
        manager = VoiceSessionManager()
        old_destroyed = False

        class OldSession:
            channel_id = "vc_old"
            is_destroyed = False
            voice_client = SimpleNamespace(is_connected=lambda: True)

            def destroy(self):
                nonlocal old_destroyed
                old_destroyed = True

        manager.sessions["guild_move"] = OldSession()

        fake_guild = SimpleNamespace(
            id="guild_move",
            me=SimpleNamespace(id="bot_1"),
        )
        fake_channel_forbidden = SimpleNamespace(
            id="vc_forbidden",
            permissions_for=lambda me: SimpleNamespace(connect=False, speak=False),
        )

        with self.assertRaises(VoiceSessionError) as cm:
            run_coroutine(manager.join(fake_guild, fake_channel_forbidden))

        self.assertEqual(cm.exception.code, "MISSING_CONNECT_PERM")
        self.assertFalse(old_destroyed)
        self.assertTrue(manager.has_session("guild_move"))
        self.assertEqual(manager.get_session("guild_move").channel_id, "vc_old")

    def test_destroys_stale_session_when_moving_channel(self):
        manager = VoiceSessionManager()
        old_destroyed = False

        class StaleSession:
            channel_id = "vc_old"
            is_destroyed = False
            voice_client = SimpleNamespace(is_connected=lambda: False)

            def destroy(self):
                nonlocal old_destroyed
                old_destroyed = True

        manager.sessions["guild_move"] = StaleSession()

        new_connected = False
        class MockNewVC:
            def is_connected(self):
                return True

        def mock_connect(timeout=15.0, reconnect=True):
            nonlocal new_connected
            new_connected = True
            return MockNewVC()

        fake_guild = SimpleNamespace(
            id="guild_move",
            me=SimpleNamespace(id="bot_1"),
            voice_client=None,
        )
        fake_channel = SimpleNamespace(
            id="vc_new",
            permissions_for=lambda me: SimpleNamespace(connect=True, speak=True),
            connect=mock_connect,
        )

        res = run_coroutine(manager.join(fake_guild, fake_channel))
        self.assertTrue(old_destroyed)
        self.assertTrue(new_connected)
        self.assertFalse(res["reused"])
        self.assertEqual(res["session"].channel_id, "vc_new")

    def test_removes_stale_session_cleanly(self):
        manager = VoiceSessionManager()
        destroyed = False

        class FakeSession:
            channel_id = "vc_1"

            def destroy(self):
                nonlocal destroyed
                destroyed = True

        manager.sessions["guild_stale"] = FakeSession()
        self.assertTrue(manager.has_session("guild_stale"))

        manager.remove_session("guild_stale")
        self.assertTrue(destroyed)
        self.assertFalse(manager.has_session("guild_stale"))

    def test_destroy_all_cleans_up_all_sessions(self):
        manager = VoiceSessionManager()
        destroyed_count = 0

        class FakeSession:
            def destroy(self):
                nonlocal destroyed_count
                destroyed_count += 1

        manager.sessions["g1"] = FakeSession()
        manager.sessions["g2"] = FakeSession()

        manager.destroy_all()
        self.assertEqual(destroyed_count, 2)
        self.assertEqual(len(manager.sessions), 0)


if __name__ == "__main__":
    unittest.main()

