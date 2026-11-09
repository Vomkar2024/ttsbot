# Discord TTS Bot (Python Edition)

An independent Discord text-to-speech bot written in Python, featuring dual-engine synthesis (**Google Translate TTS (gTTS)** for natural, human speech and **eSpeak NG** for offline local synthesis with automatic fallback), full Discord DAVE voice encryption support, and intelligent message processing adapted from [Discord-TTS/Bot](https://github.com/Discord-TTS/Bot). It automatically reads messages sent in the active voice channel's built-in text chat, as well as any configured text channel.

---

## What It Does

| Command | Description |
| :--- | :--- |
| `/setup` | Choose a text or voice channel to be read aloud (*Manage Server permission required*). |
| `/join` | Join the voice channel you are currently in (supports seamless channel switching). |
| `/leave` | Leave voice and discard current and pending speech. |
| `/voice` | Select natural accents (US, UK, Australia, India, etc.) or eSpeak variants for yourself, or set the server default. |
| `/language` | Select the speech language for yourself, or set the server default. |
| `/queue` | Inspect the current message and waiting queue. |
| `/skip` | Cancel only the current message; the next waiting item starts. |
| `/stop` | Cancel current speech and discard all waiting items. |
| `/clear` | Discard waiting items without interrupting current speech. |
| `/status` | Show active listening text channels, voice connection, active speech engine, and server defaults. |

### Advanced Voice & Chat Intelligence (matching Discord-TTS/Bot)
- **Natural Speech & Dual-Engine Fallback**: Default to **gTTS** for realistic, fluent voices with automatic fallback to **eSpeak NG**.
- **Speaker Announcement Cooldown**: 60-second cooldown so consecutive messages from the same user don't repetitively repeat `"Alice says:"`.
- **Link & Attachment Speech Context**: Transcodes links into `"Alice sent a link"` and file attachments into `"Alice sent an image file"`, `"an audio file"`, `"a text file"`, etc.
- **Chat Acronym Expansion**: Expands common acronyms (`iirc`, `afaik`, `btw`, `rn`, `brb`, `:)`, `:(`).
- **Spam Character Limiting**: Truncates repetitive characters (e.g. `loooooool` -> `loool`).
- **Seamless Channel Moves**: Uses Discord gateway `move_to` for smooth channel transitions without reconnect race conditions.

---

## Technology Choices

- **Python 3.10+ (tested with 3.13 & 3.14)**: Asynchronous architecture built on `asyncio`.
- **discord.py 2.5+**: Maintained Discord API library with application slash-command tree and gateway abstractions.
- **PyNaCl & davey**: Voice protocol audio encryption and Discord DAVE protocol (End-to-End Encryption) for voice connections.
- **gTTS & eSpeak NG**: Dual-engine pipeline. gTTS provides natural online voices; eSpeak NG provides zero-dependency offline local audio.
- **FFmpeg**: Transcodes local audio stream into Discord-compatible 48kHz stereo Opus frames (`discord.FFmpegPCMAudio` with `PCMVolumeTransformer`).
- **Python's built-in `sqlite3`**: Persistent, fast local settings storage without operating an external database server.

---

## Project Structure

```
tts-bot/
├── .env.example
├── .gitignore
├── requirements.txt
├── pyproject.toml
├── README.md
├── main.py
├── docs/
│   └── REFERENCE_ANALYSIS.md
├── scripts/
│   └── deploy_commands.py
├── src/
│   ├── __init__.py
│   ├── app.py
│   ├── config.py
│   ├── constants.py
│   ├── commands/
│   │   ├── __init__.py
│   │   └── tts_commands.py
│   ├── events/
│   │   ├── __init__.py
│   │   └── message_handler.py
│   ├── storage/
│   │   ├── __init__.py
│   │   └── settings_store.py
│   ├── tts/
│   │   ├── __init__.py
│   │   ├── espeak_provider.py
│   │   └── format_message.py
│   └── voice/
│       ├── __init__.py
│       ├── discord_voice_session.py
│       ├── serial_queue.py
│       └── session_manager.py
└── tests/
    ├── __init__.py
    ├── test_espeak_provider.py
    ├── test_format_message.py
    ├── test_message_handler.py
    ├── test_serial_queue.py
    ├── test_session_manager.py
    └── test_settings_store.py
```

---

## Requirements

- **Python 3.10 or newer** (Python 3.13 recommended).
- **FFmpeg** available on `PATH` (or set `FFMPEG_PATH`).
- **eSpeak NG** available on `PATH` (or set `TTS_BIN`).
- A Discord application and bot token.
- Outbound network access to Discord gateway/API and Discord voice servers. Your host/firewall must allow UDP traffic used by Discord voice.

---

## Install System Dependencies

### Ubuntu / Debian
```bash
sudo apt update
sudo apt install -y ffmpeg espeak-ng python3 python3-pip python3-venv
```

### macOS (Homebrew)
```bash
brew install ffmpeg espeak-ng python
```

### Windows (winget)
```powershell
winget install Gyan.FFmpeg
winget install eSpeak-NG.eSpeak-NG
```

After installation, verify both executables are available:
```bash
ffmpeg -version
espeak-ng --version
```

---

## Create the Discord Application and Bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and choose **New Application**.
2. Give the application a name and open its **Bot** tab.
3. Click **Add Bot** if not already created. Use **Reset Token** to generate a bot token and copy it. Treat this token like a password; never commit it or share it.
4. Under **Bot → Privileged Gateway Intents**, enable:
   - **Message Content Intent** (required to read message text).
5. Under **General Information**, copy the **Application ID** (`CLIENT_ID` / `DISCORD_CLIENT_ID`).
6. Under **OAuth2 → URL Generator**, select scopes:
   - `bot`
   - `applications.commands`
7. Select bot permissions:
   - `View Channels`
   - `Read Message History`
   - `Send Messages`
   - `Connect`
   - `Speak`
8. Open the generated invite URL and add the bot to your test server.

---

## Configure and Run

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set:
   ```env
   DISCORD_CLIENT_ID=your_application_id
   DISCORD_TOKEN=your_bot_token
   TTS_BIN=espeak-ng
   DATABASE_PATH=./data/tts.sqlite
   ```
   *(Optional: set `DISCORD_GUILD_ID=your_test_guild_id` during development so slash commands register instantly in that server).*

3. Install Python dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Register slash commands (Optional — the bot syncs automatically upon startup, but you can sync standalone):
   ```bash
   python scripts/deploy_commands.py
   ```

5. Start the bot:
   ```bash
   python main.py
   ```

The bot will automatically create the SQLite database and `data/` directory.

---

## Bot Hosting & Cloud Deployment Guide

When deploying to 24/7 bot hosting platforms, remember that **eSpeak NG** and **FFmpeg** are native system binaries that must be present in the container/server.

### 1. Docker / VPS / Cloud Containers (Recommended)
Use the included [`Dockerfile`](file:///e:/Download/Omkar/botmaker/tts/Dockerfile). It automatically installs `espeak-ng`, `ffmpeg`, and required libraries:
```bash
docker build -t discord-tts-bot .
docker run -d --name tts-bot --env-file .env -v $(pwd)/data:/app/data discord-tts-bot
```

### 2. Discloud
A [`discloud.config`](file:///e:/Download/Omkar/botmaker/tts/discloud.config) file is included with `APT=tools,espeak-ng,ffmpeg`.
1. Upload your project files (including `discloud.config` and `.env`).
2. Discloud will automatically install `espeak-ng` and `ffmpeg` during startup.

### 3. Railway / Render / Fly.io
The included [`nixpacks.toml`](file:///e:/Download/Omkar/botmaker/tts/nixpacks.toml) and [`apt.txt`](file:///e:/Download/Omkar/botmaker/tts/apt.txt) automatically declare `espeak-ng` and `ffmpeg` as system dependencies.

### 4. Shared Pterodactyl Panels (PebbleHost, BisectHosting, SparkedHost, etc.)
- In your hosting panel's **Environment Variables** or `.env`, make sure `TTS_BIN` is set to `espeak-ng` (NOT a Windows path like `C:\Program Files\...`).
- If your host's default Python egg lacks `ffmpeg` or `espeak-ng`, switch to a Python Egg that includes audio/FFmpeg tools, or ask host support to install `espeak-ng` and `ffmpeg` on your egg image.

---

### Quick Discord Usage
1. A server manager runs `/setup` in the desired text channel (or specifies one).
2. Users join a voice channel and run `/join`.
3. Any message typed into the configured text channel will be spoken in the voice channel.
4. When finished, run `/leave`.

---

## Environment Variables

| Variable | Required | Default | Purpose |
| :--- | :---: | :---: | :--- |
| `DISCORD_TOKEN` | **Yes** | — | Bot token from Developer Portal → Bot. |
| `DISCORD_CLIENT_ID` / `CLIENT_ID` | **Yes** | — | Discord application ID. |
| `DISCORD_GUILD_ID` | No | *empty* | Register commands to one development server instead of globally. |
| `TTS_BIN` | No | `espeak-ng` | eSpeak NG executable name or full binary path. |
| `FFMPEG_PATH` | No | `ffmpeg` on `PATH` | Explicit FFmpeg executable path. |
| `DATABASE_PATH` | No | `./data/tts.sqlite` | Local SQLite database file (`:memory:` for tests). |
| `SPEAK_USERNAMES` | No | `true` | Prefix each message with the sender's display name. |
| `MAX_QUEUE_SIZE` | No | `50` | Maximum waiting messages per server, excluding active item. |
| `MAX_MESSAGE_CHARS` | No | `800` | Maximum message characters sent to the local TTS engine. |
| `VOICE_READY_TIMEOUT_MS` | No | `15000` | Time to wait for a voice connection to become ready. |
| `VOICE_DISCONNECT_GRACE_MS` | No | `8000` | Grace period for temporary voice disconnects before cleanup. |

---

## Automated Test Suite

Run the full automated test suite using Python's native `unittest`:
```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

The tests cover:
- FIFO ordering and non-overlapping sequential processing.
- `skip`, `stop`, and `clear` queue control semantics.
- Queue bounds enforcement (`QueueFullError`).
- Same-channel join reuse, channel moves, and concurrent join serialization.
- Idempotent `leave` and stale disconnect cleanup.
- SQLite field-by-field preference precedence.
- Message filtering (bots, webhooks, DMs, non-configured channels).
- Discord Markdown and entity cleanup (URLs, emojis, mentions, control chars).
- Local eSpeak NG synthesis (runs live if installed, otherwise skips gracefully).
