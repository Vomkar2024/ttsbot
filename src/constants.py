"""Constants and static choice lists for VoiceBridge TTS Bot."""

DEFAULT_LANGUAGE = "en"
DEFAULT_VOICE = "default"
DEFAULT_MAX_QUEUE_SIZE = 50
DEFAULT_MAX_MESSAGE_CHARS = 800
DEFAULT_VOICE_READY_TIMEOUT_MS = 15000
DEFAULT_VOICE_DISCONNECT_GRACE_MS = 8000

LANGUAGE_CHOICES = [
    {"name": "English (en)", "value": "en"},
    {"name": "English (US) (en-us)", "value": "en-us"},
    {"name": "Spanish (es)", "value": "es"},
    {"name": "French (fr)", "value": "fr"},
    {"name": "German (de)", "value": "de"},
    {"name": "Italian (it)", "value": "it"},
    {"name": "Portuguese (pt)", "value": "pt"},
    {"name": "Hindi (hi)", "value": "hi"},
    {"name": "Japanese (ja)", "value": "ja"},
    {"name": "Russian (ru)", "value": "ru"},
    {"name": "Chinese (zh)", "value": "zh"},
    {"name": "Dutch (nl)", "value": "nl"},
    {"name": "Polish (pl)", "value": "pl"},
    {"name": "Swedish (sv)", "value": "sv"},
    {"name": "Turkish (tr)", "value": "tr"},
    {"name": "Korean (ko)", "value": "ko"},
    {"name": "Greek (el)", "value": "el"},
    {"name": "Arabic (ar)", "value": "ar"},
]

VOICE_VARIANT_CHOICES = [
    {"name": "Default voice", "value": "default"},
    {"name": "Male 1 (+m1)", "value": "+m1"},
    {"name": "Male 2 (+m2)", "value": "+m2"},
    {"name": "Male 3 (+m3)", "value": "+m3"},
    {"name": "Male 4 (+m4)", "value": "+m4"},
    {"name": "Female 1 (+f1)", "value": "+f1"},
    {"name": "Female 2 (+f2)", "value": "+f2"},
    {"name": "Female 3 (+f3)", "value": "+f3"},
    {"name": "Female 4 (+f4)", "value": "+f4"},
    {"name": "Whisper (+whisper)", "value": "+whisper"},
    {"name": "Croak (+croak)", "value": "+croak"},
    {"name": "Robot (+klatt)", "value": "+klatt"},
]

COMMAND_TTS = "tts"

SUBCOMMAND_SETUP = "setup"
SUBCOMMAND_JOIN = "join"
SUBCOMMAND_LEAVE = "leave"
SUBCOMMAND_VOICE = "voice"
SUBCOMMAND_LANGUAGE = "language"
SUBCOMMAND_QUEUE = "queue"
SUBCOMMAND_SKIP = "skip"
SUBCOMMAND_STOP = "stop"
SUBCOMMAND_CLEAR = "clear"
SUBCOMMAND_STATUS = "status"

