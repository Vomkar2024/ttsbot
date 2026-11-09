"""Constants and static choice lists for Discord TTS Bot."""

DEFAULT_LANGUAGE = "en"
DEFAULT_VOICE = "default"
DEFAULT_TTS_ENGINE = "gtts"
DEFAULT_SPEAKER_COOLDOWN_SEC = 60
DEFAULT_MAX_QUEUE_SIZE = 50
DEFAULT_MAX_MESSAGE_CHARS = 800
DEFAULT_VOICE_READY_TIMEOUT_MS = 15000
DEFAULT_VOICE_DISCONNECT_GRACE_MS = 8000

TTS_ENGINE_CHOICES = [
    {"name": "Google Translate TTS (Natural, recommended)", "value": "gtts"},
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

