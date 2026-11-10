from .hinglish_commands import HinglishCommandsCog, setup_hinglish_commands
from .tts_commands import TTSCommandsCog, TtsCommandGroup, setup_tts_commands

__all__ = [
    "setup_tts_commands",
    "TtsCommandGroup",
    "TTSCommandsCog",
    "setup_hinglish_commands",
    "HinglishCommandsCog",
]

