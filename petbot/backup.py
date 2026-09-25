"""Consistent database backups (python -m petbot backup), safe while the bot is running."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

KEEP = 10


def backup(database: Path, folder: Path | None = None) -> Path:
    """
    Copy the SQLite database with SQLite's online backup API - a consistent
    snapshot even while the bot writes (unlike copying the file, which can
    catch it half-written in WAL mode). Keeps the newest KEEP copies.
    """
    folder = folder or database.parent.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / f"{database.stem}-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
    source = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()
    for old in sorted(folder.glob(f"{database.stem}-*.sqlite3"))[:-KEEP]:
        old.unlink()
    return target
