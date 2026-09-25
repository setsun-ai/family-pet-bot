"""
One-click start: creates a private Python environment (.venv), installs the
pinned libraries, runs the setup wizard when .env isn't ready, then starts
the bot. Never downloads or executes remote scripts - only pip packages.

    python launcher.py            start (setup on first run)
    python launcher.py --setup    only (re)run the setup wizard
    python launcher.py check      test all connections and exit
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def launch() -> int:
    if not (3, 11) <= sys.version_info[:2] < (3, 15):
        print("Install Python 3.11-3.14 from https://www.python.org (3.12 or 3.13 recommended).")
        return 2
    os.chdir(ROOT)
    env = ROOT / ".venv"
    python = env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        print("Creating the local Python environment (.venv)...", flush=True)
        venv.EnvBuilder(with_pip=True).create(env)
    requirements = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = env / ".requirements-installed"
    if not stamp.exists() or stamp.read_text().strip() != fingerprint:
        print("Installing pinned libraries from PyPI...", flush=True)
        if subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)]).returncode:
            print("Installation failed: check the internet connection, the Python version and the error above.")
            return 1
        stamp.write_text(fingerprint, encoding="ascii")
    if "--setup" in sys.argv:
        return subprocess.call([str(python), "-m", "petbot", "setup"])
    check = subprocess.run([str(python), "-m", "petbot", "check-config"], capture_output=True, text=True, encoding="utf-8")
    if check.returncode:
        print(check.stderr.strip())
        if not sys.stdin.isatty():
            print("Fill in .env (see .env.example) or run the setup in a terminal: python launcher.py --setup")
            return 2
        if code := subprocess.call([str(python), "-m", "petbot", "setup"]):
            return code
    extra = [arg for arg in sys.argv[1:] if arg != "--setup"]
    return subprocess.call([str(python), "-m", "petbot", *extra])


if __name__ == "__main__":
    try:
        raise SystemExit(launch())
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    except (OSError, subprocess.SubprocessError) as error:
        print("Launcher failed: " + type(error).__name__ + ". Check the Python installation and folder permissions.")
        raise SystemExit(2) from None
