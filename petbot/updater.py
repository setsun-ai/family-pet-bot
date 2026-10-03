"""
/update and /rollback from Telegram: the owner installs the newest GitHub release without a terminal.

Only the code package (petbot/) and requirements.txt are replaced - .env, personas, *.local.json and
data/ are never touched. Steps: download the release archive, unpack petbot/ into .update/new,
`pip install -r requirements.txt` if it changed, start the new code once (selftest: imports +
check-config), then swap the folders. The previous version stays in .update/previous for /rollback.
After the swap the bot stops polling and starts itself again (os.execv, same process for systemd).
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from . import PROJECT_URL, __version__

PACKAGE = "petbot"
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
WORK_DIR = PROJECT_DIR / ".update"
PREVIOUS_DIR = WORK_DIR / "previous"
REPO = PROJECT_URL.removeprefix("https://github.com/")
SELFTEST = f"""
import sys
sys.path.insert(0, sys.argv[1])
import {PACKAGE}, {PACKAGE}.app, {PACKAGE}.handlers
assert {PACKAGE}.__file__.startswith(sys.argv[1]), "the old code was imported"
from {PACKAGE}.__main__ import main
code = main(["check-config"])
print("OK", {PACKAGE}.__version__)
raise SystemExit(code)
"""

_restart = False


class UpdateError(Exception):
    """A step failed; nothing was changed (the message says which step)."""


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version)[:3])


def _get(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"{PACKAGE}/{__version__}",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def latest_release() -> str:
    import json

    return json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))["tag_name"]


def newer(tag: str, current: str = __version__) -> bool:
    return version_tuple(tag) > version_tuple(current)


def unpack(archive: bytes, dest: Path) -> None:
    """Only <top>/petbot/** and <top>/requirements.txt; links and paths leaving dest are refused."""
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = member.name.split("/")[1:]  # drop the "<repo>-<tag>/" top folder
            if not parts or not (parts[0] == PACKAGE or parts == ["requirements.txt"]):
                continue
            if ".." in parts or member.name.startswith("/") or not (member.isfile() or member.isdir()):
                raise UpdateError(f"unsafe archive entry: {member.name}")
            target = dest.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(member).read())
    if not (dest / PACKAGE / "__init__.py").is_file():
        raise UpdateError(f"the archive has no {PACKAGE}/")


def _run(args: list[str], timeout: int) -> tuple[bool, str]:
    try:
        result = subprocess.run(args, cwd=PROJECT_DIR, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout} s"
    return result.returncode == 0, (result.stdout + result.stderr).strip()


def install(tag: str) -> str:
    """Download, test and switch to `tag`. Returns a short log; raises UpdateError when nothing was changed."""
    new = WORK_DIR / "new"
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir(parents=True)
    try:
        unpack(_get(f"https://github.com/{REPO}/archive/refs/tags/{tag}.tar.gz", timeout=120), new)
        notes = []
        requirements = new / "requirements.txt"
        current = PROJECT_DIR / "requirements.txt"
        if requirements.is_file() and (not current.is_file() or
                                       current.read_bytes().splitlines() != requirements.read_bytes().splitlines()):
            ok, out = _run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                            "-r", str(requirements)], timeout=1800)
            if not ok:
                raise UpdateError("pip install:\n" + out[-800:])
            notes.append("pip install: OK")
        ok, out = _run([sys.executable, "-c", SELFTEST, str(new)], timeout=180)
        if not ok:
            raise UpdateError("selftest:\n" + out[-800:])
        notes.append(out.splitlines()[-1] if out else "selftest: OK")
        _swap_in(new)
        return "\n".join(notes)
    finally:
        shutil.rmtree(new, ignore_errors=True)


def _swap_in(new: Path) -> None:
    shutil.rmtree(PREVIOUS_DIR, ignore_errors=True)
    PREVIOUS_DIR.mkdir(parents=True)
    os.replace(PACKAGE_DIR, PREVIOUS_DIR / PACKAGE)
    try:
        os.replace(new / PACKAGE, PACKAGE_DIR)
    except OSError:
        os.replace(PREVIOUS_DIR / PACKAGE, PACKAGE_DIR)
        raise
    current = PROJECT_DIR / "requirements.txt"
    if current.is_file():
        shutil.copy2(current, PREVIOUS_DIR / "requirements.txt")
    if (new / "requirements.txt").is_file():
        shutil.copy2(new / "requirements.txt", current)


def previous_version() -> str | None:
    """Version kept for /rollback, or None."""
    init = PREVIOUS_DIR / PACKAGE / "__init__.py"
    if not init.is_file():
        return None
    match = re.search(r'__version__ = "([^"]+)"', init.read_text(encoding="utf-8"))
    return match.group(1) if match else "?"


def rollback() -> str | None:
    """Swap the current and the previous version (a second /rollback undoes the first). Returns the version now active."""
    version = previous_version()
    if version is None:
        return None
    swap = WORK_DIR / "swap"
    shutil.rmtree(swap, ignore_errors=True)
    os.replace(PACKAGE_DIR, swap)
    os.replace(PREVIOUS_DIR / PACKAGE, PACKAGE_DIR)
    os.replace(swap, PREVIOUS_DIR / PACKAGE)
    current, kept = PROJECT_DIR / "requirements.txt", PREVIOUS_DIR / "requirements.txt"
    if current.is_file() and kept.is_file():
        data = current.read_bytes()
        shutil.copy2(kept, current)
        kept.write_bytes(data)
    return version


def request_restart() -> None:
    global _restart
    _restart = True


def restart_requested() -> bool:
    return _restart


def restart() -> None:
    """Start the bot again in this process (the same PID, so systemd sees no stop)."""
    os.chdir(PROJECT_DIR)  # `-m` finds the package in the working directory
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            stream.flush()
    os.execv(sys.executable, [sys.executable, "-m", PACKAGE, *sys.argv[1:]])
