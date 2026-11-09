"""Entry point for Discord TTS Bot in Python."""

import sys
import traceback
from src.app import start_app

if __name__ == "__main__":
    try:
        start_app()
    except Exception as exc:
        print(f"[Fatal] Failed to launch Discord TTS Bot: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

