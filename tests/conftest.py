"""Pytest configuration and sandbox/platform compatibility fixtures."""

import signal
import sys
from unittest.mock import MagicMock


def _ensure_windows_asyncio_self_pipe() -> None:
    """Ensures asyncio ProactorEventLoop does not hang in sandboxed Windows environments.

    In restricted/sandboxed environments on Windows, outbound socket connections to 127.0.0.1
    are blocked by local firewall policies (WinError 10013), causing Python's default
    socket.socketpair() to hang in accept() during loop self-pipe initialization.
    This replaces the loop self-pipe with a safe mock during test execution on Windows.
    """
    if sys.platform != "win32":
        return

    import asyncio.proactor_events

    def _dummy_make_self_pipe(self):
        self._ssock = MagicMock()
        self._csock = MagicMock()
        self._internal_fds += 1

    asyncio.proactor_events.BaseProactorEventLoop._make_self_pipe = _dummy_make_self_pipe
    asyncio.proactor_events.BaseProactorEventLoop._close_self_pipe = lambda self: None
    asyncio.proactor_events.BaseProactorEventLoop._loop_self_reading = lambda self, f=None: None

    # Replace set_wakeup_fd to avoid Windows kernel STATUS_INVALID_HANDLE (0xc0000008)
    signal.set_wakeup_fd = lambda fd, *, warn_on_full_buffer=True: -1


_ensure_windows_asyncio_self_pipe()
