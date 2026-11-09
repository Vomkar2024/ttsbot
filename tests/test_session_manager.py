"""Tests for VoiceSessionManager."""

import asyncio
import unittest
from types import SimpleNamespace
from src.voice.session_manager import VoiceSessionError, VoiceSessionManager


class TestSessionManager(unittest.IsolatedAsyncioTestCase):
    async def test_serializes_concurrent_operations_per_guild(self):
        manager = VoiceSessionManager()
        sequence = []

        async def op1():
            await asyncio.sleep(0.025)
            sequence.append("first")

        async def op2():
            sequence.append("second")

        p1 = manager._serialize("guild_1", op1)
        p2 = manager._serialize("guild_1", op2)

        await asyncio.gather(p1, p2)
        self.assertEqual(sequence, ["first", "second"])

    async def test_allows_different_guilds_to_run_concurrently(self):
        manager = VoiceSessionManager()
        finished = []

        async def op_a():
            await asyncio.sleep(0.03)
            finished.append("A")

        async def op_b():
            finished.append("B")

        p_a = manager._serialize("guild_A", op_a)
        p_b = manager._serialize("guild_B", op_b)

        await asyncio.gather(p_a, p_b)
        self.assertEqual(finished, ["B", "A"])

    async def test_idempotently_leaves_voice_when_no_session(self):
        manager = VoiceSessionManager()
        self.assertFalse(manager.has_session("guild_none"))
        self.assertIsNone(manager.get_session("guild_none"))

        left = await manager.leave("guild_none")
        self.assertFalse(left)

    async def test_throws_when_bot_cannot_be_resolved(self):
        manager = VoiceSessionManager()
        fake_guild = SimpleNamespace(
            id="guild_test",
            me=None,
            fetch_member=None,
        )
        fake_channel = SimpleNamespace(id="vc_1")

        with self.assertRaises(VoiceSessionError) as cm:
            await manager.join(fake_guild, fake_channel)
        self.assertEqual(cm.exception.code, "BOT_NOT_RESOLVED")

    async def test_throws_when_bot_lacks_permissions(self):
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
            await manager.join(fake_guild1, fake_channel_no_connect)
        self.assertEqual(cm1.exception.code, "MISSING_CONNECT_PERM")

        # Lacks Speak
        fake_channel_no_speak = SimpleNamespace(
            id="vc_2",
            permissions_for=lambda me: SimpleNamespace(connect=True, speak=False),
        )

        with self.assertRaises(VoiceSessionError) as cm2:
            await manager.join(fake_guild1, fake_channel_no_speak)
        self.assertEqual(cm2.exception.code, "MISSING_SPEAK_PERM")

    async def test_throws_when_voice_dependencies_missing(self):
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
                await manager.join(fake_guild, fake_channel)
            self.assertEqual(cm.exception.code, "MISSING_VOICE_DEPENDENCY")
            self.assertIn("davey library needed", str(cm.exception))
        finally:
            discord.voice_client.has_dave = orig_has_dave

    async def test_reuses_healthy_session_on_same_channel(self):
        manager = VoiceSessionManager()
        fake_session = SimpleNamespace(
            channel_id="vc_same",
            is_destroyed=False,
            voice_client=SimpleNamespace(is_connected=lambda: True),
        )
        manager.sessions["guild_reuse"] = fake_session

        fake_guild = SimpleNamespace(id="guild_reuse")
        fake_channel = SimpleNamespace(id="vc_same")

        result = await manager.join(fake_guild, fake_channel)
        self.assertTrue(result["reused"])
        self.assertEqual(result["session"], fake_session)

    async def test_destroys_previous_session_when_moving_channel(self):
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

        fake_guild = SimpleNamespace(id="guild_move", me=None)
        fake_channel = SimpleNamespace(id="vc_new")

        # Joining new channel should destroy old session before failing permission/resolution
        with self.assertRaises(VoiceSessionError):
            await manager.join(fake_guild, fake_channel)

        self.assertTrue(old_destroyed)
        self.assertFalse(manager.has_session("guild_move"))

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

