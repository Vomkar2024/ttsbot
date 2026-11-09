"""Entry point for Discord TTS Bot in Python."""

import sys
from src.app import start_app

if __name__ == "__main__":
    try:
        start_app()
    except Exception as exc:
        print(f"[Fatal] Failed to launch Discord TTS Bot: {exc}", file=sys.stderr)
        sys.exit(1)

