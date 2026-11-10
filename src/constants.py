"""Constants and static choice lists for Discord TTS Bot."""

DEFAULT_LANGUAGE = "en"
DEFAULT_VOICE = "default"
DEFAULT_TTS_ENGINE = "edge-tts"
DEFAULT_EDGE_TTS_VOICE = "en-US-EmmaMultilingualNeural"
DEFAULT_EDGE_TTS_RATE = "+0%"
DEFAULT_EDGE_TTS_PITCH = "+0Hz"
DEFAULT_EDGE_TTS_TIMEOUT_SEC = 10.0
DEFAULT_SPEAKER_COOLDOWN_SEC = 60
DEFAULT_MAX_QUEUE_SIZE = 50
DEFAULT_MAX_MESSAGE_CHARS = 800
DEFAULT_VOICE_READY_TIMEOUT_MS = 15000
DEFAULT_VOICE_DISCONNECT_GRACE_MS = 8000

DEFAULT_TRANSLATOR_PROVIDER = "auto"
DEFAULT_TRANSLATION_TIMEOUT_SEC = 10.0
DEFAULT_TTS_TIMEOUT_SEC = 15.0
DEFAULT_DISCORD_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB standard Discord upload limit

HINGLISH_DELIVERY_CHOICES = [
    {"name": "Auto (Voice if active, else audio file)", "value": "auto"},
    {"name": "Voice channel only", "value": "voice"},
    {"name": "Attached audio file only", "value": "file"},
]

TTS_ENGINE_CHOICES = [
    {"name": "Edge Neural TTS (Custom Voice Pack, recommended)", "value": "edge-tts"},
    {"name": "Google Translate TTS (Natural)", "value": "gtts"},
    {"name": "eSpeak NG (Offline/Local)", "value": "espeak"},
]

# Common internet chat acronyms for speech expansion (matching Discord-TTS/Bot)
ACRONYMS = {
    "iirc": "if I recall correctly",
    "afaik": "as far as I know",
    "wdym": "what do you mean",
    "imo": "in my opinion",
    "brb": "be right back",
    "wym": "what you mean",
    "irl": "in real life",
    "jk": "just kidding",
    "btw": "by the way",
    ":)": "smiley face",
    ":(": "sad face",
    "gtg": "got to go",
    "rn": "right now",
    "ig": "i guess",
    "ppl": "people",
    "rly": "really",
    "cya": "see ya",
    "ik": "i know",
    "@": "at",
    "™️": "tm",
}

# Attachment extension to speech format mapping (matching Discord-TTS/Bot)
ATTACHMENT_EXTENSIONS = {
    ("bmp", "gif", "ico", "png", "psd", "svg", "jpg", "jpeg", "webp"): "an image file",
    ("mid", "midi", "mp3", "ogg", "wav", "wma", "m4a", "flac"): "an audio file",
    ("avi", "mp4", "wmv", "m4v", "mpg", "mpeg", "webm", "mov", "mkv"): "a video file",
    ("zip", "7z", "rar", "gz", "xz", "tar"): "a compressed file",
    ("doc", "docx", "txt", "odt", "rtf", "pdf", "md"): "a text file",
    ("bat", "sh", "jar", "py", "php", "js", "ts", "rs", "cpp", "c", "html", "css"): "a script file",
    ("apk", "exe", "msi", "deb", "rpm"): "a program file",
    ("dmg", "iso", "img", "ima"): "a disk image",
}

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
    {"name": "Emma US Neural (Edge-TTS)", "value": "edge:en-US-EmmaMultilingualNeural"},
    {"name": "Brian US Male (Edge-TTS)", "value": "edge:en-US-BrianMultilingualNeural"},
    {"name": "Aria US Female (Edge-TTS)", "value": "edge:en-US-AriaNeural"},
    {"name": "Sonia UK Female (Edge-TTS)", "value": "edge:en-GB-SoniaNeural"},
    {"name": "Ryan UK Male (Edge-TTS)", "value": "edge:en-GB-RyanNeural"},
    {"name": "Swara India Hindi (Edge-TTS)", "value": "edge:hi-IN-SwaraNeural"},
    {"name": "Neerja India English (Edge-TTS)", "value": "edge:en-IN-NeerjaNeural"},
    {"name": "Nanami Japanese (Edge-TTS)", "value": "edge:ja-JP-NanamiNeural"},
    {"name": "US Natural (gTTS)", "value": "us"},
    {"name": "UK Natural (gTTS)", "value": "uk"},
    {"name": "Australia Natural (gTTS)", "value": "au"},
    {"name": "India Natural (gTTS)", "value": "in"},
    {"name": "Canada Natural (gTTS)", "value": "ca"},
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

COMMAND_SETUP = "setup"
COMMAND_JOIN = "join"
COMMAND_LEAVE = "leave"
COMMAND_VOICE = "voice"
COMMAND_LANGUAGE = "language"
COMMAND_QUEUE = "queue"
COMMAND_SKIP = "skip"
COMMAND_STOP = "stop"
COMMAND_CLEAR = "clear"
COMMAND_STATUS = "status"
COMMAND_HINGLISH = "hinglish"

# Backwards compatibility aliases
SUBCOMMAND_SETUP = COMMAND_SETUP
SUBCOMMAND_JOIN = COMMAND_JOIN
SUBCOMMAND_LEAVE = COMMAND_LEAVE
SUBCOMMAND_VOICE = COMMAND_VOICE
SUBCOMMAND_LANGUAGE = COMMAND_LANGUAGE
SUBCOMMAND_QUEUE = COMMAND_QUEUE
SUBCOMMAND_SKIP = COMMAND_SKIP
SUBCOMMAND_STOP = COMMAND_STOP
SUBCOMMAND_CLEAR = COMMAND_CLEAR
SUBCOMMAND_STATUS = COMMAND_STATUS

