"""eSpeak NG offline text-to-speech provider."""

import asyncio
import io
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from ..constants import DEFAULT_LANGUAGE, DEFAULT_VOICE


class AbortError(Exception):
    """Raised when synthesis or playback is cancelled or aborted."""
    pass


class EspeakProvider:
    def __init__(
        self,
        tts_bin: str = "espeak-ng",
        allow_mock_fallback: bool = False,
        options: Optional[dict] = None,
    ):
        opts = options or {}
        self.tts_bin = opts.get("ttsBin", opts.get("tts_bin", tts_bin))
        self.allow_mock_fallback = opts.get(
            "allowMockFallback", opts.get("allow_mock_fallback", allow_mock_fallback)
        )

    def resolve_voice_arg(
        self,
        language: Optional[str] = DEFAULT_LANGUAGE,
        voice: Optional[str] = DEFAULT_VOICE,
    ) -> str:
        lang = language.strip() if (language and language.strip()) else DEFAULT_LANGUAGE
        v = voice.strip() if (voice and voice.strip()) else DEFAULT_VOICE

        if not v or v == "default":
            return lang

        if v.startswith("+"):
            return f"{lang}{v}"

        return v

    # Alias camelCase for JS parity
    resolveVoiceArg = resolve_voice_arg

    def _resolve_binary_path(self) -> Optional[str]:
        if Path(self.tts_bin).is_file():
            return self.tts_bin
        found = shutil.which(self.tts_bin)
        if found:
            return found
        if sys.platform == "win32":
            candidates = [
                r"C:\Program Files\eSpeak NG\espeak-ng.exe",
                r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
            ]
            for candidate in candidates:
                if Path(candidate).is_file():
                    return candidate
        return None

    async def is_available(self) -> bool:
        """Tests if the eSpeak binary is available on the system."""
        resolved = self._resolve_binary_path()
        if not resolved:
            return False
        try:
            proc = await asyncio.create_subprocess_exec(
                resolved,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            return proc.returncode == 0
        except (FileNotFoundError, OSError):
            return False

    isAvailable = is_available

    async def synthesize(
        self,
        text: str,
        options: Optional[dict] = None,
        *,
        language: Optional[str] = None,
        voice: Optional[str] = None,
        speed: Optional[int] = None,
        pitch: Optional[int] = None,
        cancel_event: Optional[asyncio.Event] = None,
        signal: Any = None,
    ) -> bytes:
        """Synthesizes text to a WAV bytes buffer using eSpeak NG.

        Streams text into eSpeak's stdin and returns stdout as bytes.
        """
        opts = options or {}
        if language is None:
            language = opts.get("language")
        if voice is None:
            voice = opts.get("voice")
        if speed is None:
            speed = opts.get("speed")
        if pitch is None:
            pitch = opts.get("pitch")
        if cancel_event is None:
            cancel_event = opts.get("cancel_event", opts.get("cancelEvent"))

        # Check for AbortSignal-like or cancel_event abort
        sig = signal or opts.get("signal")
        if (sig and getattr(sig, "aborted", False)) or (cancel_event and cancel_event.is_set()):
            raise AbortError("Synthesis aborted prior to execution")

        if not text or not isinstance(text, str) or not text.strip():
            raise ValueError("Cannot synthesize empty or whitespace-only text")

        resolved_bin = self._resolve_binary_path()
        if not resolved_bin:
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise RuntimeError(
                f"eSpeak process error ({self.tts_bin}): binary not found. Ensure eSpeak NG is installed and on PATH or TTS_BIN is set."
            )

        voice_arg = self.resolve_voice_arg(language, voice)
        args = [resolved_bin, "-v", voice_arg, "--stdin", "--stdout"]

        if speed is not None and isinstance(speed, (int, float)):
            args.extend(["-s", str(int(speed))])
        if pitch is not None and isinstance(pitch, (int, float)):
            args.extend(["-p", str(int(pitch))])

        proc: Optional[asyncio.subprocess.Process] = None

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, OSError) as err:
            if self.allow_mock_fallback:
                return self._generate_mock_wav(text)
            raise RuntimeError(
                f"eSpeak process error ({self.tts_bin}): {err}. Ensure eSpeak NG is installed and on PATH or TTS_BIN is set."
            ) from err

        async def _feed_input():
            try:
                stdout_data, stderr_data = await proc.communicate(text.encode("utf-8"))
                return stdout_data, stderr_data
            except Exception as e:
                raise e

        # Monitor cancellation
        communicate_task = asyncio.create_task(_feed_input())

        if cancel_event is not None or sig is not None:
            async def _watch_cancel():
                while not communicate_task.done():
                    if (cancel_event and cancel_event.is_set()) or (sig and getattr(sig, "aborted", False)):
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        communicate_task.cancel()
                        break
                    await asyncio.sleep(0.01)

            watcher_task = asyncio.create_task(_watch_cancel())
        else:
            watcher_task = None

        try:
            stdout_data, stderr_data = await communicate_task
        except asyncio.CancelledError:
            try:
                proc.terminate()
            except Exception:
                pass
            raise AbortError("Synthesis aborted by signal")
        finally:
            if watcher_task and not watcher_task.done():
                watcher_task.cancel()

        if proc.returncode != 0:
            err_msg = stderr_data.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"eSpeak exited with code {proc.returncode}: {err_msg}")

        if not stdout_data:
            raise RuntimeError("eSpeak produced empty audio stream")

        return stdout_data

    def synthesize_sync(self, text: str, **kwargs) -> bytes:
        """Synchronous synthesis helper for quick tests."""
        return asyncio.run(self.synthesize(text, **kwargs))

    def _generate_mock_wav(self, text: str) -> bytes:
        """Generates a minimal valid RIFF WAVE PCM audio buffer for testing."""
        sample_rate = 22050
        num_channels = 1
        bits_per_sample = 16
        duration_sec = max(0.2, min(1.0, len(text) * 0.02))
        num_samples = int(sample_rate * duration_sec)
        data_size = num_samples * num_channels * (bits_per_sample // 8)

        # Build WAV in memory
        out = io.BytesIO()
        # RIFF header
        out.write(b"RIFF")
        out.write(struct.pack("<I", 36 + data_size))
        out.write(b"WAVE")
        # fmt chunk
        out.write(b"fmt ")
        out.write(struct.pack("<I", 16))  # chunk size
        out.write(struct.pack("<H", 1))   # audio format PCM = 1
        out.write(struct.pack("<H", num_channels))
        out.write(struct.pack("<I", sample_rate))
        byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
        out.write(struct.pack("<I", byte_rate))
        block_align = num_channels * (bits_per_sample // 8)
        out.write(struct.pack("<H", block_align))
        out.write(struct.pack("<H", bits_per_sample))
        # data chunk
        out.write(b"data")
        out.write(struct.pack("<I", data_size))
        # Silence payload
        out.write(b"\x00" * data_size)

        return out.getvalue()


__all__ = ["EspeakProvider", "AbortError"]
