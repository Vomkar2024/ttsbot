"""Sequential FIFO processing queue with bounds enforcement and job cancellation."""

import asyncio
import inspect
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from ..constants import DEFAULT_MAX_QUEUE_SIZE
from ..tts.espeak_provider import AbortError

logger = logging.getLogger(__name__)


class QueueFullError(Exception):
    """Raised when queue exceeds its maximum capacity."""
    pass


class QueueJob:
    def __init__(
        self,
        job_id: int,
        data: Dict[str, Any],
        loop: asyncio.AbstractEventLoop,
    ):
        self.id = job_id
        self.data = data
        self.created_at = int(time.time() * 1000)
        self.future: asyncio.Future = loop.create_future()
        # Suppress unhandled future exception warnings if caller does not immediately await
        self.future.add_done_callback(self._silence_unhandled)

    def _silence_unhandled(self, fut: asyncio.Future) -> None:
        try:
            fut.exception()
        except Exception:
            pass

    @property
    def text(self) -> str:
        return self.data.get("text", "")

    @property
    def spoken_text(self) -> str:
        return self.data.get("spokenText", self.data.get("spoken_text", self.text))

    @property
    def author_name(self) -> str:
        return self.data.get("authorName", self.data.get("author_name", "User"))

    @property
    def language(self) -> str:
        return self.data.get("language", "en")

    @property
    def voice(self) -> str:
        return self.data.get("voice", "default")

    @property
    def user_id(self) -> str:
        return self.data.get("userId", self.data.get("user_id", ""))

    @property
    def audio_bytes(self) -> Optional[bytes]:
        return self.data.get("audioBytes", self.data.get("audio_bytes"))

    def resolve(self) -> None:
        if not self.future.done():
            self.future.set_result(None)

    def reject(self, err: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(err)


class SerialQueue:
    def __init__(
        self,
        guild_id: str,
        max_size: int = DEFAULT_MAX_QUEUE_SIZE,
        on_process_job: Optional[Callable[[QueueJob, asyncio.Event], Any]] = None,
        options: Optional[Dict[str, Any]] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        opts = options or {}
        self.guild_id = guild_id
        self.max_size = opts.get("maxSize", opts.get("max_size", max_size))
        self.on_process_job = opts.get("onProcessJob", opts.get("on_process_job", on_process_job))
        self.loop = loop or opts.get("loop")

        self.pending_queue: List[QueueJob] = []
        self.current_job: Optional[QueueJob] = None
        self.current_cancel_event: Optional[asyncio.Event] = None
        self.is_processing = False
        self.job_sequence = 0
        self._process_task: Optional[asyncio.Task] = None

    def _resolve_loop(self) -> asyncio.AbstractEventLoop:
        """Resolves an active event loop for job futures and task scheduling."""
        if self.loop is not None and not self.loop.is_closed():
            return self.loop

        try:
            running = asyncio.get_running_loop()
            self.loop = running
            return running
        except RuntimeError:
            pass

        try:
            policy_loop = asyncio.get_event_loop_policy().get_event_loop()
            if policy_loop is not None and not policy_loop.is_closed():
                self.loop = policy_loop
                return policy_loop
        except RuntimeError:
            pass

        raise RuntimeError(
            f"SerialQueue for guild {self.guild_id} could not resolve an active event loop. "
            "Pass an explicit loop to SerialQueue(..., loop=...) when enqueuing from a worker thread."
        )

    def set_processor(self, handler: Callable[[QueueJob, asyncio.Event], Any]) -> None:
        self.on_process_job = handler

    setProcessor = set_processor

    def enqueue(self, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Enqueue a text-to-speech job into the FIFO queue."""
        if len(self.pending_queue) >= self.max_size:
            raise QueueFullError(f"Queue full: maximum {self.max_size} pending messages reached.")

        loop = self._resolve_loop()

        self.job_sequence += 1
        job_id = self.job_sequence

        job = QueueJob(job_id=job_id, data=job_data, loop=loop)
        self.pending_queue.append(job)
        position = len(self.pending_queue)

        self._ensure_processing(loop)

        return {
            "job_id": job_id,
            "jobId": job_id,
            "position": position,
            "future": job.future,
            "promise": job.future,
        }

    def skip(self) -> Dict[str, Any]:
        """Skip current playing message: abort current job and advance to next item."""
        was_playing = bool(self.current_job and self.current_cancel_event)

        if was_playing and self.current_cancel_event:
            self.current_cancel_event.set()

        return {
            "skipped": was_playing,
            "pending_remaining": len(self.pending_queue),
            "pendingRemaining": len(self.pending_queue),
        }

    def stop(self) -> Dict[str, Any]:
        """Stop current playback and drop all waiting jobs."""
        dropped_count = len(self.pending_queue)

        while self.pending_queue:
            job = self.pending_queue.pop(0)
            job.reject(AbortError("Queue stopped by user"))

        was_playing = bool(self.current_job and self.current_cancel_event)
        if was_playing and self.current_cancel_event:
            self.current_cancel_event.set()

        return {
            "stopped": was_playing or dropped_count > 0,
            "dropped_count": dropped_count,
            "droppedCount": dropped_count,
        }

    def clear(self) -> Dict[str, Any]:
        """Clear all waiting items without interrupting currently active speech."""
        cleared_count = len(self.pending_queue)

        while self.pending_queue:
            job = self.pending_queue.pop(0)
            job.reject(AbortError("Queue cleared by user"))

        return {
            "cleared_count": cleared_count,
            "clearedCount": cleared_count,
            "current_playing": bool(self.current_job),
            "currentPlaying": bool(self.current_job),
        }

    def get_status(self) -> Dict[str, Any]:
        """Get queue status snapshot."""
        current_data = None
        if self.current_job:
            current_data = {
                "id": self.current_job.id,
                "text": self.current_job.text or self.current_job.spoken_text,
                "authorName": self.current_job.author_name,
                "author_name": self.current_job.author_name,
                "language": self.current_job.language,
                "voice": self.current_job.voice,
                "createdAt": self.current_job.created_at,
                "created_at": self.current_job.created_at,
            }

        pending_items = []
        for idx, j in enumerate(self.pending_queue):
            text_str = j.spoken_text or j.text
            pending_items.append(
                {
                    "position": idx + 1,
                    "id": j.id,
                    "authorName": j.author_name,
                    "author_name": j.author_name,
                    "textLength": len(text_str) if text_str else 0,
                    "text_length": len(text_str) if text_str else 0,
                }
            )

        return {
            "guildId": self.guild_id,
            "guild_id": self.guild_id,
            "isPlaying": bool(self.current_job),
            "is_playing": bool(self.current_job),
            "currentJob": current_data,
            "current_job": current_data,
            "pendingCount": len(self.pending_queue),
            "pending_count": len(self.pending_queue),
            "pending": pending_items,
        }

    getStatus = get_status

    def _ensure_processing(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        if self.is_processing:
            return

        try:
            target_loop = loop or self._resolve_loop()
        except RuntimeError as err:
            logger.warning("[SerialQueue Guild %s] _ensure_processing loop lookup: %s", self.guild_id, err)
            return

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if current_loop is target_loop and target_loop.is_running():
            if not self._process_task or self._process_task.done():
                self._process_task = target_loop.create_task(self._process_loop())
        else:
            # Called from a worker thread or outside active running loop: schedule thread-safely
            if target_loop.is_running():
                target_loop.call_soon_threadsafe(self._schedule_process_task, target_loop)

    def _schedule_process_task(self, target_loop: asyncio.AbstractEventLoop) -> None:
        if not self.is_processing and (not self._process_task or self._process_task.done()):
            self._process_task = target_loop.create_task(self._process_loop())

    async def _process_loop(self) -> None:
        if self.is_processing:
            return
        self.is_processing = True

        try:
            while self.pending_queue:
                self.current_job = self.pending_queue.pop(0)
                self.current_cancel_event = asyncio.Event()

                try:
                    if self.on_process_job:
                        res = self.on_process_job(self.current_job, self.current_cancel_event)
                        if inspect.isawaitable(res):
                            await res
                    self.current_job.resolve()
                except Exception as err:
                    if (
                        self.current_cancel_event.is_set()
                        or isinstance(err, (AbortError, asyncio.CancelledError))
                    ):
                        abort_err = err if isinstance(err, AbortError) else AbortError("Job cancelled")
                        self.current_job.reject(abort_err)
                    else:
                        logger.error(
                            "[SerialQueue Guild %s] Job #%s failed: %s",
                            self.guild_id,
                            self.current_job.id,
                            err,
                            exc_info=True,
                        )
                        self.current_job.reject(err)
                finally:
                    self.current_job = None
                    self.current_cancel_event = None
        finally:
            self.is_processing = False


__all__ = ["SerialQueue", "QueueFullError", "QueueJob"]
