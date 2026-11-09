"""Tone generator utility for testing Discord voice playback and UDP transmission.

Generates 48kHz 16-bit stereo PCM audio (standard Discord voice format) using Python's
standard library (math, struct, wave) with zero external dependencies.
"""

import io
import math
import struct
import wave
from typing import Optional

import discord


def generate_sine_pcm(
    frequency: float = 440.0,
    duration: float = 3.0,
    sample_rate: int = 48000,
    volume: float = 0.5,
) -> bytes:
    """Generates raw 16-bit signed little-endian stereo PCM bytes for a sine wave.

    Args:
        frequency: Pitch in Hertz (default 440.0 Hz - concert pitch A4).
        duration: Duration in seconds (default 3.0).
        sample_rate: Sample rate in Hz (default 48000 Hz for Discord).
        volume: Amplitude between 0.0 and 1.0 (default 0.5).

    Returns:
        Raw PCM bytes (interleaved 16-bit stereo: left sample, right sample).
    """
    total_samples = int(sample_rate * duration)
    vol = max(0.0, min(1.0, float(volume)))
    max_amplitude = 32767.0 * vol
    angular_freq = 2.0 * math.pi * float(frequency) / sample_rate

    pcm_buffer = bytearray(total_samples * 4)
    struct_pack_into = struct.pack_into

    for i in range(total_samples):
        sample_val = int(max_amplitude * math.sin(angular_freq * i))
        # Pack stereo samples (left, right) as signed 16-bit little-endian ('<hh')
        struct_pack_into("<hh", pcm_buffer, i * 4, sample_val, sample_val)

    return bytes(pcm_buffer)


def generate_sine_wav(
    frequency: float = 440.0,
    duration: float = 3.0,
    sample_rate: int = 48000,
    volume: float = 0.5,
) -> bytes:
    """Generates in-memory RIFF/WAVE file bytes of a sine wave.

    Useful for testing the FFmpeg transcoding pipeline without depending on TTS.
    """
    pcm_data = generate_sine_pcm(
        frequency=frequency,
        duration=duration,
        sample_rate=sample_rate,
        volume=volume,
    )
    wav_io = io.BytesIO()
    with wave.open(wav_io, "wb") as wav_file:
        wav_file.setnchannels(2)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)

    return wav_io.getvalue()


class ToneAudioSource(discord.AudioSource):
    """Discord AudioSource yielding 48kHz 16-bit stereo PCM for a sine tone.

    Streams directly to Discord's Opus encoder and UDP socket without invoking FFmpeg.
    """

    FRAME_SIZE = 3840  # 20ms at 48kHz 16-bit stereo: 960 samples * 2 channels * 2 bytes

    def __init__(
        self,
        frequency: float = 440.0,
        duration: float = 3.0,
        sample_rate: int = 48000,
        volume: float = 0.5,
    ):
        self.pcm_bytes = b""
        self.position = 0
        self.frequency = float(frequency)
        self.duration = float(duration)
        self.sample_rate = int(sample_rate)
        self.volume = float(volume)
        self.pcm_bytes = generate_sine_pcm(
            frequency=self.frequency,
            duration=self.duration,
            sample_rate=self.sample_rate,
            volume=self.volume,
        )

    def read(self) -> bytes:
        """Returns the next 20ms audio frame (3840 bytes), or b'' when playback ends."""
        if self.position >= len(self.pcm_bytes):
            return b""

        chunk = self.pcm_bytes[self.position : self.position + self.FRAME_SIZE]
        self.position += len(chunk)

        # Pad last frame with silence if needed to maintain 20ms frame boundary
        if len(chunk) < self.FRAME_SIZE:
            chunk = chunk + b"\x00" * (self.FRAME_SIZE - len(chunk))

        return bytes(chunk)

    def is_opus(self) -> bool:
        return False

    def cleanup(self) -> None:
        self.position = len(getattr(self, "pcm_bytes", b""))


__all__ = ["generate_sine_pcm", "generate_sine_wav", "ToneAudioSource"]
