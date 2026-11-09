"""Tests for SerialQueue (FIFO, Skip, Stop, Clear, Error Isolation)."""

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from src.tts.espeak_provider import AbortError
from src.voice.serial_queue import QueueFullError, SerialQueue


"""Tests for SerialQueue (FIFO, Skip, Stop, Clear, Error Isolation, and Loop Injection)."""

import asyncio
import sys
import threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from src.tts.espeak_provider import AbortError
from src.voice.serial_queue import QueueFullError, SerialQueue


"""Tests for SerialQueue (FIFO, Skip, Stop, Clear, Error Isolation, and Loop Injection)."""

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from src.tts.espeak_provider import AbortError
from src.voice.serial_queue import QueueFullError, SerialQueue


class MockFuture:
    def __init__(self):
        self._done = False
        self._result = None
        self._exception = None
        self._callbacks = []

    def add_done_callback(self, cb):
        self._callbacks.append(cb)

    def done(self):
        return self._done

    def set_result(self, res):
        self._done = True
        self._result = res

    def set_exception(self, exc):
        self._done = True
        self._exception = exc

    def exception(self):
        return self._exception


class MockEventLoop:
    def __init__(self):
        self._is_closed = False
        self._is_running = True
        self.scheduled = []

    def is_closed(self):
        return self._is_closed

    def is_running(self):
        return self._is_running

    def create_future(self):
        fut = MockFuture()
        fut._loop = self
        return fut

    def create_task(self, coro):
        self.scheduled.append(coro)
        return MagicMock()

    def call_soon_threadsafe(self, callback, *args):
        self.scheduled.append((callback, args))


class TestSerialQueue(unittest.TestCase):
    def setUp(self):
        self.loop = MockEventLoop()

    def test_serial_queue_loop_injection(self):
        queue = SerialQueue("guild_inj", loop=self.loop)
        self.assertIs(queue.loop, self.loop)
        self.assertIs(queue._resolve_loop(), self.loop)

    def test_enqueue_from_worker_thread_with_injected_loop(self):
        queue = SerialQueue("guild_thread", loop=self.loop)
        enqueue_result = None
        thread_error = None

        def worker():
            nonlocal enqueue_result, thread_error
            try:
                enqueue_result = queue.enqueue({"text": "Threaded message", "spokenText": "Threaded message"})
            except Exception as e:
                thread_error = e

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2.0)

        self.assertIsNone(thread_error, f"Enqueuing from worker thread failed with: {thread_error}")
        self.assertIsNotNone(enqueue_result)
        self.assertEqual(enqueue_result["jobId"], 1)
        self.assertEqual(enqueue_result["position"], 1)
        self.assertIs(enqueue_result["future"]._loop, self.loop)
        # Verify threadsafe scheduling was invoked
        self.assertTrue(len(self.loop.scheduled) > 0)

    def test_throws_queue_full_error_when_exceeding_max_size(self):
        queue = SerialQueue("guild_bound", max_size=2, loop=self.loop)

        queue.enqueue({"text": "Item 1"})
        queue.enqueue({"text": "Item 2"})

        with self.assertRaises(QueueFullError):
            queue.enqueue({"text": "Item 3"})

    def test_skip_operation_aborts_active_job(self):
        queue = SerialQueue("guild_skip", loop=self.loop)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})

        class MockCancelEvent:
            def __init__(self):
                self._is_set = False
            def is_set(self):
                return self._is_set
            def set(self):
                self._is_set = True

        queue.current_job = queue.pending_queue.pop(0)
        queue.current_cancel_event = MockCancelEvent()

        result = queue.skip()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["pending_remaining"], 1)
        self.assertTrue(queue.current_cancel_event.is_set())

    def test_stop_cancels_active_speech_and_discards_pending(self):
        queue = SerialQueue("guild_stop", loop=self.loop)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})
        j3 = queue.enqueue({"text": "Job 3"})

        class MockCancelEvent:
            def __init__(self):
                self._is_set = False
            def is_set(self):
                return self._is_set
            def set(self):
                self._is_set = True

        queue.current_job = queue.pending_queue.pop(0)
        queue.current_cancel_event = MockCancelEvent()

        result = queue.stop()
        self.assertTrue(result["stopped"])
        self.assertEqual(result["dropped_count"], 2)
        self.assertEqual(len(queue.pending_queue), 0)
        self.assertTrue(queue.current_cancel_event.is_set())

    def test_clear_discards_pending_preserving_current(self):
        queue = SerialQueue("guild_clear", loop=self.loop)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})
        j3 = queue.enqueue({"text": "Job 3"})

        class MockCancelEvent:
            def __init__(self):
                self._is_set = False
            def is_set(self):
                return self._is_set
            def set(self):
                self._is_set = True

        queue.current_job = queue.pending_queue.pop(0)
        queue.current_cancel_event = MockCancelEvent()

        result = queue.clear()
        self.assertEqual(result["cleared_count"], 2)
        self.assertTrue(result["current_playing"])
        self.assertFalse(queue.current_cancel_event.is_set())
        self.assertEqual(len(queue.pending_queue), 0)

    def test_reports_accurate_queue_status(self):
        queue = SerialQueue("guild_status", loop=self.loop)
        status_before = queue.get_status()
        self.assertFalse(status_before["isPlaying"])
        self.assertEqual(status_before["pendingCount"], 0)

        queue.enqueue({"text": "Pending item"})
        status_after = queue.get_status()
        self.assertEqual(status_after["pendingCount"], 1)
        self.assertEqual(status_after["pending"][0]["text_length"], len("Pending item"))


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
