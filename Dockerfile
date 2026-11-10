# Production Dockerfile for Discord TTS Bot
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TTS_ENGINE=edge-tts \
    TTS_BIN=espeak-ng \
    FFMPEG_PATH=ffmpeg \
    DATABASE_PATH=/app/data/tts.sqlite

# Install required system binaries: eSpeak NG, FFmpeg, Opus, libsodium, and Git
RUN apt-get update && apt-get install -y --no-install-recommends \
    espeak-ng \
    ffmpeg \
    libopus0 \
    libsodium23 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency definition and install Python libraries
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY main.py pyproject.toml README.md ./
COPY src/ ./src/

# Create data directory for SQLite persistence
RUN mkdir -p /app/data

# Run application
CMD ["python", "main.py"]

