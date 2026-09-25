"""
Settings from the .env file, loaded explicitly (never as an import side effect).

Every option is documented in .env.example and docs/*/configuration.md.
Only BOT_TOKEN and the key of ONE AI provider are required.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FEEDS = (
    "https://www.goodnewsnetwork.org/category/news/feed/",
    "https://www.positive.news/feed/",
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
)
DEFAULT_MODELS = {"claude": "claude-haiku-4-5-20251001", "openai": "gpt-4o-mini"}
PLACEHOLDER_WORDS = ("your_", "replace", "paste_", "ваш_", "встав")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    platform: str = "telegram"  # telegram | discord
    bot_token: str = field(repr=False, default="")
    ai_provider: str = "claude"
    api_key: str = field(repr=False, default="")
    model: str = DEFAULT_MODELS["claude"]
    post_model: str = ""  # optional stronger model for automatic posts (empty = MODEL)
    owner_id: int | None = None
    language: str = "en"
    persona_file: Path = ROOT / "personas" / "cat.en.md"
    bot_names: tuple[str, ...] = ()
    persona_emoji: str = ""
    timezone: str = "UTC"
    database_path: Path = ROOT / "data" / "bot.sqlite3"
    notice_seconds: int = 8
    # news
    news_enabled: bool = True
    news_feeds: tuple[str, ...] = DEFAULT_FEEDS
    news_mode: str = "alternate"
    news_hour: int = 11
    news_minute: int = 0
    news_evening_hour: int = 18
    news_evening_minute: int = 30
    news_window_minutes: int = 120
    news_jitter_minutes: int = 20
    news_max_age_days: int = 7
    news_max_candidates: int = 3
    max_news_ai_calls_per_day: int = 6
    # sports
    sports_enabled: bool = False
    sports_team_id: str = ""
    sports_command: str = "match"
    sports_api_key: str = field(repr=False, default="3")
    sports_hour: int = 9
    sports_results: bool = True
    sports_check_minutes: int = 15
    sports_idle_hours: int = 6
    sports_teams_file: Path | None = None
    # family: weekly praise and birthdays
    family_file: Path | None = None
    praise_enabled: bool = False
    praise_weekday: int = 5
    praise_hour: int = 12
    birthday_hour: int = 9
    # quiet hours, limits, memory
    quiet_start_hour: int = 23
    quiet_end_hour: int = 8
    max_ai_calls_per_day: int = 200
    user_requests_per_minute: int = 6
    ai_timeout: int = 35
    max_input_chars: int = 4000
    history_messages: int = 8
    history_keep: int = 50
    history_days: int = 30

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def display_name(self) -> str:
        """How the pet is called in texts: the first of BOT_NAMES."""
        return self.bot_names[0] if self.bot_names else ("Кот" if self.language == "ru" else "Cat")

    @classmethod
    def load(cls, env_file: Path | None = None, *, validate_keys: bool = True) -> Settings:
        values = {k: v or "" for k, v in dotenv_values(env_file or ROOT / ".env", encoding="utf-8-sig").items()}
        values.update(os.environ)

        def text(name: str, default: str = "") -> str:
            return str(values.get(name, default)).strip()

        def number(name: str, default: int, low: int, high: int) -> int:
            try:
                result = int(text(name, str(default)) or default)
                if not low <= result <= high:
                    raise ValueError
                return result
            except ValueError:
                raise ConfigError(f"{name}: expected a whole number from {low} to {high}.") from None

        def flag(name: str, default: bool) -> bool:
            raw = text(name, str(default)).lower() or str(default).lower()
            if raw in {"true", "1", "yes", "on"}:
                return True
            if raw in {"false", "0", "no", "off"}:
                return False
            raise ConfigError(f"{name}: use true or false.")

        def items(name: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in text(name).split(",") if part.strip())

        language = text("LANGUAGE", "en").lower()[:2] or "en"
        if language not in {"en", "ru"}:
            raise ConfigError("LANGUAGE: use en or ru.")

        provider = text("AI_PROVIDER", "claude").lower()
        if provider not in {"claude", "openai"}:
            raise ConfigError("AI_PROVIDER: use claude or openai.")
        key_name = "ANTHROPIC_API_KEY" if provider == "claude" else "OPENAI_API_KEY"
        model_name = "ANTHROPIC_MODEL" if provider == "claude" else "OPENAI_MODEL"
        model = text(model_name, DEFAULT_MODELS[provider]) or DEFAULT_MODELS[provider]
        platform = text("PLATFORM", "telegram").lower() or "telegram"
        if platform not in {"telegram", "discord"}:
            raise ConfigError("PLATFORM: use telegram or discord.")
        token_name = "BOT_TOKEN" if platform == "telegram" else "DISCORD_TOKEN"
        token, key = text(token_name), text(key_name)
        if validate_keys:
            token_shape = (r"[1-9]\d{4,15}:[A-Za-z0-9_-]{30,}" if platform == "telegram"
                           else r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{20,}")
            if not re.fullmatch(token_shape, token):
                raise ConfigError(f"{token_name} is missing or malformed. Run the setup (python -m petbot setup) or edit .env.")
            if len(key) < 15 or any(word in key.lower() for word in PLACEHOLDER_WORDS):
                raise ConfigError(f"Fill in {key_name} in .env or run the setup (python -m petbot setup).")

        owner = number("OWNER_ID", 0, 1, 2**63 - 1) if text("OWNER_ID") else None

        tz = text("TIMEZONE", "UTC") or "UTC"
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConfigError(f"TIMEZONE: unknown time zone {tz!r}. Use an IANA name like Europe/Warsaw.") from None

        def path(name: str, default: str) -> Path:
            result = Path(text(name, default) or default).expanduser()
            return result if result.is_absolute() else ROOT / result

        persona = path("PERSONA_FILE", f"personas/cat.{language}.md")
        if not persona.is_file():
            raise ConfigError(f"PERSONA_FILE: file not found: {persona}")

        news_mode = text("NEWS_MODE", "alternate").lower() or "alternate"
        if news_mode not in {"alternate", "once", "twice", "every2days"}:
            raise ConfigError("NEWS_MODE: use alternate, once, twice or every2days.")
        first = number("NEWS_HOUR", 11, 0, 23) * 60 + number("NEWS_MINUTE", 0, 0, 59)
        second = number("NEWS_EVENING_HOUR", 18, 0, 23) * 60 + number("NEWS_EVENING_MINUTE", 30, 0, 59)
        if news_mode not in {"once", "every2days"} and second - first < 180:
            raise ConfigError("The evening news slot must be at least 3 hours after the first one (NEWS_EVENING_HOUR).")
        feeds = items("NEWS_FEEDS") or DEFAULT_FEEDS
        if any(not feed.startswith("https://") for feed in feeds):
            raise ConfigError("NEWS_FEEDS: every feed must be an https:// URL.")

        sports_enabled = flag("SPORTS_ENABLED", False)
        team = text("SPORTS_TEAM_ID")
        teams_file = path("SPORTS_TEAMS_FILE", "") if text("SPORTS_TEAMS_FILE") else None
        if teams_file is not None and not teams_file.is_file():
            raise ConfigError(f"SPORTS_TEAMS_FILE: file not found: {teams_file}")
        family_file = path("FAMILY_FILE", "") if text("FAMILY_FILE") else None
        if family_file is not None and not family_file.is_file():
            raise ConfigError(f"FAMILY_FILE: file not found: {family_file}")
        if sports_enabled and teams_file is None and not team.isdigit():
            raise ConfigError("SPORTS_TEAM_ID: set the numeric TheSportsDB team id (see docs: sports).")

        sports_command = (text("SPORTS_COMMAND", "match") or "match").lstrip("/").lower()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", sports_command):
            raise ConfigError("SPORTS_COMMAND: 1-32 latin letters, digits or _ (command rules of Telegram and Discord).")

        return cls(
            platform=platform, bot_token=token, ai_provider=provider, api_key=key, model=model, post_model=text("POST_MODEL"),
            owner_id=owner,
            language=language, persona_file=persona, bot_names=items("BOT_NAMES"),
            persona_emoji="".join(text("PERSONA_EMOJI").split()),
            timezone=tz, database_path=path("DATABASE_PATH", "data/bot.sqlite3"),
            notice_seconds=number("NOTICE_SECONDS", 8, 0, 3600),
            news_enabled=flag("NEWS_ENABLED", True), news_feeds=feeds, news_mode=news_mode,
            news_hour=number("NEWS_HOUR", 11, 0, 23), news_minute=number("NEWS_MINUTE", 0, 0, 59),
            news_evening_hour=number("NEWS_EVENING_HOUR", 18, 0, 23),
            news_evening_minute=number("NEWS_EVENING_MINUTE", 30, 0, 59),
            news_window_minutes=number("NEWS_WINDOW_MINUTES", 120, 1, 720),
            news_jitter_minutes=number("NEWS_JITTER_MINUTES", 20, 0, 45),
            news_max_age_days=number("NEWS_MAX_AGE_DAYS", 7, 1, 30),
            max_news_ai_calls_per_day=number("MAX_NEWS_AI_CALLS_PER_DAY", 6, 1, 100),
            sports_enabled=sports_enabled, sports_team_id=team, sports_command=sports_command,
            sports_api_key=text("SPORTS_API_KEY", "3") or "3",
            sports_hour=number("SPORTS_HOUR", 9, 0, 23), sports_results=flag("SPORTS_RESULTS", True),
            sports_check_minutes=number("SPORTS_CHECK_MINUTES", 15, 5, 120),
            sports_idle_hours=number("SPORTS_IDLE_HOURS", 6, 1, 24),
            sports_teams_file=teams_file if sports_enabled else None,
            family_file=family_file, praise_enabled=flag("PRAISE_ENABLED", False),
            praise_weekday=number("PRAISE_WEEKDAY", 5, 0, 6), praise_hour=number("PRAISE_HOUR", 12, 0, 23),
            birthday_hour=number("BIRTHDAY_HOUR", 9, 0, 23),
            quiet_start_hour=number("QUIET_START_HOUR", 23, 0, 23),
            quiet_end_hour=number("QUIET_END_HOUR", 8, 0, 23),
            max_ai_calls_per_day=number("MAX_AI_CALLS_PER_DAY", 200, 1, 10000),
            user_requests_per_minute=number("USER_REQUESTS_PER_MINUTE", 6, 1, 60),
            ai_timeout=number("AI_TIMEOUT_SECONDS", 35, 5, 120),
            history_days=number("HISTORY_DAYS", 30, 1, 365),
        )
