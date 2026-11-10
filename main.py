"""Entry point for Discord TTS Bot in Python."""

import sys
import traceback

from src.updater import maybe_update_on_startup

if __name__ == "__main__":
    force_update = "--update" in sys.argv or "--update-only" in sys.argv
    update_only = "--update-only" in sys.argv
    force_pip = "--reinstall-deps" in sys.argv

    try:
        maybe_update_on_startup(force=force_update, force_pip=force_pip)
        if update_only:
            sys.exit(0)

        from src.app import start_app

        start_app()
    except Exception as exc:
        print(f"[Fatal] Failed to launch Discord TTS Bot: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
