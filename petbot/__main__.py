"""
Command line:

    python -m petbot                 run the bot (Ctrl+C to stop)
    python -m petbot setup           interactive setup: token, AI key, language
    python -m petbot check           test Telegram, AI (one small paid request), RSS and sports; then exit
    python -m petbot check-config    validate .env without any network request
    python -m petbot backup          consistent copy of the database into backups/ (keeps the last 10)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

from . import __version__
from .config import ConfigError, Settings
from .i18n import set_language, t


def main(argv: list[str] | None = None) -> int:
    if os.name != "nt":
        os.umask(0o077)  # database and .env readable only by you
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="python -m petbot", description="family-pet-bot: an AI pet for your family Telegram chat")
    parser.add_argument("--version", action="version", version=f"family-pet-bot {__version__}")
    parser.add_argument("command", nargs="?", default="run", choices=["run", "setup", "check", "check-config", "backup"])
    parser.add_argument("--log-file", help="also write the log to this file (rotated at 1 MB; used by autostart)")
    args = parser.parse_args(argv)

    if args.command == "setup":
        from .wizard import run as wizard
        return wizard()

    try:
        settings = Settings.load()
    except ConfigError as error:
        print("Configuration error / Ошибка настройки: " + str(error), file=sys.stderr)
        return 2
    set_language(settings.language)

    from .app import run, setup_logging

    setup_logging(settings, args.log_file)
    if args.command == "backup":
        from .backup import backup
        print(backup(settings.database_path))
        return 0
    if args.command == "check-config":
        print(t("config_ok", provider=settings.ai_provider, model=settings.model, tz=settings.timezone,
                persona=settings.persona_file.name))
        return 0

    from filelock import FileLock, Timeout

    settings.database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with FileLock(str(settings.database_path.parent / "bot.lock"), timeout=0):
            return asyncio.run(run(settings, check=args.command == "check"))
    except Timeout:
        print(t("console_already_running"), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
