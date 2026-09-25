import asyncio
import os
import sys

import pytest

from petbot.i18n import set_language

# The tests must never read a real .env or talk to real services.
SECRET_ENV = ("BOT_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AI_PROVIDER", "OWNER_ID", "LANGUAGE",
              "PERSONA_FILE", "BOT_NAMES", "PERSONA_EMOJI", "TIMEZONE", "NEWS_FEEDS", "SPORTS_ENABLED",
              "SPORTS_TEAM_ID", "SPORTS_COMMAND", "DATABASE_PATH", "NEWS_MODE")

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for key in SECRET_ENV:
        monkeypatch.delenv(key, raising=False)
    set_language("en")
    yield
    set_language("en")


def run(coro):
    return asyncio.run(coro)


os.environ.setdefault("PYTHONUTF8", "1")
