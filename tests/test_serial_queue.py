"""Tests for SerialQueue (FIFO, Skip, Stop, Clear, Error Isolation)."""

import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import unittest
from src.tts.espeak_provider import AbortError
from src.voice.serial_queue import QueueFullError, SerialQueue


class TestSerialQueue(unittest.IsolatedAsyncioTestCase):
    async def test_executes_jobs_strictly_fifo_without_overlap(self):
        executed_order = []
        concurrent_count = 0
        max_concurrency = 0

        async def process_job(job, cancel_event):
            nonlocal concurrent_count, max_concurrency
            concurrent_count += 1
            max_concurrency = max(max_concurrency, concurrent_count)
            await asyncio.sleep(0.02)
            executed_order.append(job.id)
            concurrent_count -= 1

        queue = SerialQueue("guild_fifo", on_process_job=process_job)

        j1 = queue.enqueue({"text": "Item 1"})
        j2 = queue.enqueue({"text": "Item 2"})
        j3 = queue.enqueue({"text": "Item 3"})

        await asyncio.gather(j1["future"], j2["future"], j3["future"])

        self.assertEqual(executed_order, [1, 2, 3])
        self.assertEqual(max_concurrency, 1, "Processing must be strictly non-overlapping")

    async def test_throws_queue_full_error_when_exceeding_max_size(self):
        queue = SerialQueue("guild_bound", max_size=2)

        # Hang the processor so jobs accumulate in pending queue
        hang_event = asyncio.Event()

        async def hung_processor(job, cancel_event):
            await hang_event.wait()

        queue.set_processor(hung_processor)

        queue.enqueue({"text": "Active 1"})  # Becomes active job
        # Give event loop a tick to start processing active job
        await asyncio.sleep(0.01)

        queue.enqueue({"text": "Pending 1"})  # Waiting item 1
        queue.enqueue({"text": "Pending 2"})  # Waiting item 2 (max_size reached)

        with self.assertRaises(QueueFullError):
            queue.enqueue({"text": "Pending 3"})  # Should exceed max_size

        queue.stop()
        hang_event.set()

    async def test_skip_cancels_active_speech_and_advances_to_next(self):
        events = []
        job1_started = asyncio.Event()

        async def process_job(job, cancel_event):
            events.append(f"start_{job.id}")
            if job.id == 1:
                job1_started.set()
                await cancel_event.wait()
                raise AbortError("aborted")
            else:
                events.append(f"finish_{job.id}")

        queue = SerialQueue("guild_skip", on_process_job=process_job)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})

        await job1_started.wait()

        result = queue.skip()
        self.assertTrue(result["skipped"])
        self.assertEqual(result["pending_remaining"], 1)

        with self.assertRaises(AbortError):
            await j1["future"]

        await j2["future"]
        self.assertEqual(events, ["start_1", "start_2", "finish_2"])

    async def test_stop_cancels_active_speech_and_discards_pending(self):
        job1_started = asyncio.Event()

        async def process_job(job, cancel_event):
            job1_started.set()
            await cancel_event.wait()
            raise AbortError("aborted")

        queue = SerialQueue("guild_stop", on_process_job=process_job)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})
        j3 = queue.enqueue({"text": "Job 3"})

        await job1_started.wait()

        result = queue.stop()
        self.assertTrue(result["stopped"])
        self.assertEqual(result["dropped_count"], 2)

        with self.assertRaises(AbortError):
            await j1["future"]
        with self.assertRaises(AbortError):
            await j2["future"]
        with self.assertRaises(AbortError):
            await j3["future"]

        self.assertEqual(len(queue.pending_queue), 0)

    async def test_clear_discards_pending_preserving_current(self):
        job1_started = asyncio.Event()
        job1_finished = False

        async def process_job(job, cancel_event):
            nonlocal job1_finished
            job1_started.set()
            await asyncio.sleep(0.04)
            job1_finished = True

        queue = SerialQueue("guild_clear", on_process_job=process_job)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})
        j3 = queue.enqueue({"text": "Job 3"})

        await job1_started.wait()

        result = queue.clear()
        self.assertEqual(result["cleared_count"], 2)
        self.assertTrue(result["current_playing"])

        await j1["future"]
        self.assertTrue(job1_finished)

        with self.assertRaises(AbortError):
            await j2["future"]
        with self.assertRaises(AbortError):
            await j3["future"]

        self.assertEqual(len(queue.pending_queue), 0)

    async def test_isolates_errors_without_halting_subsequent_jobs(self):
        completed = []

        async def process_job(job, cancel_event):
            if job.id == 2:
                raise RuntimeError("Synthetic audio synthesis failure")
            completed.append(job.id)

        queue = SerialQueue("guild_isolate", on_process_job=process_job)

        j1 = queue.enqueue({"text": "Job 1"})
        j2 = queue.enqueue({"text": "Job 2"})
        j3 = queue.enqueue({"text": "Job 3"})

        await j1["future"]
        with self.assertRaises(RuntimeError) as cm:
            await j2["future"]
        self.assertEqual(str(cm.exception), "Synthetic audio synthesis failure")

        await j3["future"]
        self.assertEqual(completed, [1, 3])

    def test_reports_accurate_queue_status(self):
        queue = SerialQueue("guild_status")
        status_before = queue.get_status()
        self.assertFalse(status_before["isPlaying"])
        self.assertEqual(status_before["pendingCount"], 0)


if __name__ == "__main__":
    unittest.main()
