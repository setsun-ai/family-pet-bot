"""
Interactive setup (python -m petbot setup). Standard library only, so it
works before the configuration is valid. Secrets are typed hidden and never
printed. Re-running keeps the current values as defaults.
"""
from __future__ import annotations

import getpass
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .config import ROOT
from .i18n import set_language, t

ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def read_env() -> str:
    if ENV.exists():
        return ENV.read_text(encoding="utf-8-sig")
    return EXAMPLE.read_text(encoding="utf-8") if EXAMPLE.exists() else ""


def current(text: str, key: str) -> str:
    found = re.search(r"^" + re.escape(key) + r"=(.*)$", text, flags=re.M)
    return found[1].split(" #")[0].strip() if found else ""


def set_value(text: str, key: str, value: str) -> str:
    """Replace KEY=... (also a commented-out '# KEY=' line) in place, or append it."""
    line = f"{key}={value}"
    pattern = r"^#?\s*" + re.escape(key) + r"=.*$"
    if re.search(pattern, text, flags=re.M):
        return re.sub(pattern, lambda _: line, text, count=1, flags=re.M)
    return text.rstrip("\n") + "\n" + line + "\n"


def write_env(text: str) -> None:
    tmp = ROOT / ".env.tmp"
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, ENV)
    if os.name != "nt":
        os.chmod(ENV, 0o600)


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def yes(prompt: str, default: bool) -> bool:
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer[0] in {"y", "д", "t"}


def search_teams(name: str) -> list[dict]:
    url = "https://www.thesportsdb.com/api/v1/json/3/searchteams.php?t=" + urllib.parse.quote(name)
    request = urllib.request.Request(url, headers={"User-Agent": "family-pet-bot setup"})
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310 - fixed https URL
        return (json.loads(response.read()) or {}).get("teams") or []


def run() -> int:
    try:
        return _run()
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled / Отменено.")
        return 2
    except ValueError as error:
        print("Error / Ошибка: " + str(error))
        return 2


def _run() -> int:
    text = read_env()
    lang = ask("Language / Язык (en/ru)", current(text, "LANGUAGE") or "en").lower()[:2]
    if lang not in {"en", "ru"}:
        lang = "en"
    set_language(lang)
    text = set_value(text, "LANGUAGE", lang)
    print("\n" + t("wiz_intro") + "\n")

    # Telegram or Discord
    platform = current(text, "PLATFORM") or "telegram"
    platform = "discord" if ask(t("wiz_platform"), "2" if platform == "discord" else "1") == "2" else "telegram"
    text = set_value(text, "PLATFORM", platform)
    token_name = "BOT_TOKEN" if platform == "telegram" else "DISCORD_TOKEN"
    token_shape = (r"[1-9]\d{4,15}:[A-Za-z0-9_-]{30,}" if platform == "telegram"
                   else r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}")
    if not current(text, token_name) or yes(t("wiz_replace_token"), False):
        print(t("wiz_token_help" if platform == "telegram" else "wiz_token_help_discord"))
        token = getpass.getpass(token_name + ": ").strip()
        if not re.fullmatch(token_shape, token):
            raise ValueError(t("wiz_bad_token"))
        text = set_value(text, token_name, token)

    # AI provider
    provider = current(text, "AI_PROVIDER") or "claude"
    key_name = "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
    if not current(text, key_name) or yes(t("wiz_replace_ai"), False):
        choice = ask(t("wiz_provider"), "1" if provider == "claude" else "2")
        provider = "openai" if choice == "2" else "claude"
        key_name = "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
        print(t("wiz_key_help_" + provider))
        key = getpass.getpass(key_name + ": ").strip()
        if len(key) < 15 or not re.fullmatch(r"[A-Za-z0-9_-]+", key):
            raise ValueError(t("wiz_bad_key"))
        text = set_value(text, "AI_PROVIDER", provider)
        text = set_value(text, key_name, key)

    # The pet
    print("\n" + t("wiz_pet_help"))
    names = ask(t("wiz_names"), current(text, "BOT_NAMES") or ("Мурзик" if lang == "ru" else "Whiskers"))
    text = set_value(text, "BOT_NAMES", ", ".join(n.strip() for n in names.split(",") if n.strip()))
    persona = ask(t("wiz_persona"), current(text, "PERSONA_FILE") or f"personas/cat.{lang}.md")
    if not (ROOT / persona).is_file() and not Path(persona).is_file():
        raise ValueError(t("wiz_no_persona", path=persona))
    text = set_value(text, "PERSONA_FILE", persona)
    text = set_value(text, "TIMEZONE", ask(t("wiz_timezone"), current(text, "TIMEZONE") or "UTC"))

    # News and sports
    text = set_value(text, "NEWS_ENABLED", "true" if yes(t("wiz_news"), current(text, "NEWS_ENABLED") != "false") else "false")
    if yes(t("wiz_sports"), current(text, "SPORTS_ENABLED") == "true"):
        team = ask(t("wiz_team_search"))
        try:
            found = search_teams(team) if team else []
        except OSError:
            found = []
            print(t("wiz_team_offline"))
        for i, item in enumerate(found[:8], 1):
            print(f"  {i}. {item.get('strTeam')} — {item.get('strLeague')} ({item.get('strCountry')}) id={item.get('idTeam')}")
        pick = ask(t("wiz_team_pick"), "1" if found else "")
        team_id = found[int(pick) - 1]["idTeam"] if pick.isdigit() and 0 < int(pick) <= len(found[:8]) else pick
        if not str(team_id).isdigit():
            raise ValueError(t("wiz_team_bad"))
        text = set_value(text, "SPORTS_ENABLED", "true")
        text = set_value(text, "SPORTS_TEAM_ID", str(team_id))
    else:
        text = set_value(text, "SPORTS_ENABLED", "false")

    write_env(text)
    print("\n" + t("wiz_done"))
    return 0
