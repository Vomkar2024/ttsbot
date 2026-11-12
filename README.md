# TTS Bot (Hinglish Edition)

A voice-chat focused Discord bot that listens to English text in chat and speaks natural Hindi audio. Supports on-demand Hinglish commands and automatic channel mode for seamless English-to-Hindi translation with voice playback.

## What It Does

| Command | Description |
| :--- | :--- |
| `/setup` | Choose text channel to read aloud (Manage Server required). |
| `/join` | Join voice channel you are in. |
| `/leave` | Leave voice and discard current/pending speech. |
| `/voice` | Select voice style (US, UK, India, etc.) for yourself or server. |
| `/language` | Set speech language (en, hi) for yourself or server. |
| `/queue` | View current and waiting speech queue. |
| `/skip` | Cancel current message only. |
| `/stop` | Cancel current and all waiting messages. |
| `/clear` | Discard waiting messages without interrupting current speech. |
| `/status` | Show voice connection, active channel, and your settings. |
| `/hinglish say` | Convert text to English display + Hindi audio (voice or file). |
| `/hinglish mode` | Enable automatic Hinglish for a channel (Manage Server required). |
| `/hinglish status` | Check Hinglish configuration and translator backend. |

## Hinglish Behavior
- **Input**: English text (or Hinglish mix) in configured channel.
- **Output**: English text displayed in chat + Hindi audio spoken in voice channel.
- **Fallback**: If no voice channel active, attaches Hindi MP3 file to message.
- **Translation**: Uses OpenAI-compatible LLM or Google DeepL for accurate English→Hindi conversion.

## Requirements
- Python 3.10+ (3.13 recommended)
- FFmpeg (system binary `ffmpeg` on PATH)
- eSpeak NG (system binary `espeak-ng` on PATH)
- Discord bot token
- Discord application with Message Content Intent enabled

## System Dependencies (Linux)
```bash
sudo apt update
sudo apt install -y ffmpeg espeak-ng python3 python3-pip python3-venv libopus0
```

## System Dependencies (macOS)
```bash
brew install ffmpeg espeak-ng python3
```

## System Dependencies (Windows)
```powershell
winget install Gyan.FFmpeg
winget install eSpeak-NG.eSpeak-NG
```

After installing, verify:
```bash
ffmpeg -version
espeak-ng --version
```

## Create Discord Application
1. Go to Discord Developer Portal → New Application.
2. Create Bot, enable Message Content Intent under Bot → Privileged Gateway Intents.
3. Copy bot token and application ID.
4. Under OAuth2, select scopes: bot, applications.commands.
5. Grant permissions: View Channels, Read Message History, Send Messages, Attach Files, Connect, Speak.
6. Invite bot to your server.

## Configuration
Copy `.env.example` to `.env` and set:
```env
DISCORD_TOKEN=your_bot_token
DISCORD_CLIENT_ID=your_application_id
TTS_ENGINE=edge-tts  # edge-tts | gtts | espeak
TTS_BIN=espeak-ng
FFMPEG_PATH=ffmpeg
DATABASE_PATH=./data/tts.sqlite
```

Optional: `DISCORD_GUILD_ID` for development command sync.

## Install Python Dependencies
```bash
pip install -r requirements.txt
```

## Run
```bash
python main.py
```

The bot creates `data/` and SQLite database automatically.

## Deployment
- Docker: Use included Dockerfile (installs eSpeak/FFmpeg).
- Discloud: Include `discloud.config` with `APT=tools,espeak-ng,ffmpeg`.
- Railway/Render: Use `nixpacks.toml` for system dependencies.

## Environment Variables
| Variable | Required | Default | Description |
| :--- | :---: | :---: | :--- |
| DISCORD_TOKEN | Yes | — | Bot token |
| DISCORD_CLIENT_ID | Yes | — | Application ID |
| TTS_ENGINE | No | edge-tts | Synthesis engine |
| TTS_BIN | No | espeak-ng | eSpeak executable |
| FFMPEG_PATH | No | ffmpeg | FFmpeg path |
| DATABASE_PATH | No | ./data/tts.sqlite | SQLite storage |
| TRANSLATOR_PROVIDER | No | auto | Translation backend (auto/google/openai) |
| OPENAI_API_KEY | No | — | For OpenAI translation |
| MAX_QUEUE_SIZE | No | 50 | Max waiting messages |
| VOICE_READY_TIMEOUT_MS | No | 15000 | Voice connection timeout |
| TTS_TIMEOUT_SEC | No | 15.0 | Hindi synthesis timeout |

## Voice Channel Flow
1. User joins voice channel.
2. Run `/join` (or auto-join if configured).
3. Messages in configured text channel are translated to Hindi and spoken.
4. Queue ensures one speech at a time.

## Hinglish Commands
- `/hinglish say text:"Bhai meeting 5 baje hai"` → Shows English + speaks Hindi.
- `/hinglish mode enabled:true channel:#chat` → Auto-process channel messages.
- `/hinglish status` → Shows current configuration.

## Testing
Run full test suite:
```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

Tests cover queue logic, voice session management, translation, and audio synthesis.

## License
MIT License. Source code in this repository.