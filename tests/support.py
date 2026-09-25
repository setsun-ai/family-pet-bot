"""
Test harness: a real Database in a temp folder, a real aiogram Bot with a
recording fake session (no network), and a mocked AI. Nothing here can reach
Telegram, an AI provider or a real chat.
"""
from __future__ import annotations

import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetMe, SendMessage
from aiogram.types import Chat, Message, User

from petbot.ai import NewsDecision
from petbot.common import ChatLocks, RateLimiter
from petbot.config import Settings
from petbot.db import Database
from petbot.delivery import DeliveryService
from petbot.handlers import App
from petbot.i18n import set_language
from petbot.news import NewsService
from petbot.scheduler import Scheduler
from petbot.security import AccessControl
from petbot.sports import SportsService

TOKEN = "987654321:" + "A" * 35
ME = User(id=987654321, is_bot=True, first_name="Whiskers", username="whiskers_test_bot")
OWNER, FAMILY = 100, -1000


class RecordingSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[SendMessage] = []
        self.requests = []
        self.failure = None

    async def close(self):
        pass

    async def make_request(self, bot, method, timeout=None):
        self.requests.append(method)
        if isinstance(method, GetMe):
            return ME
        if isinstance(method, SendMessage):
            if self.failure:
                raise self.failure
            self.sent.append(method)
            chat_id = int(method.chat_id)
            return Message(message_id=len(self.sent) + 1000, date=datetime.now(UTC),
                           chat=Chat(id=chat_id, type="private" if chat_id > 0 else "supergroup"), text=method.text)
        return True

    async def stream_content(self, *args, **kwargs):
        if False:
            yield b""


class Harness:
    async def open(self, **overrides) -> Harness:
        set_language("en")
        self.tmp = tempfile.TemporaryDirectory()
        base = Settings(bot_token=TOKEN, api_key="sk-fake-test-key-not-real", bot_names=("Whiskers",),
                        database_path=Path(self.tmp.name) / "bot.sqlite3", notice_seconds=0)
        self.settings = replace(base, **overrides)
        self.db = Database(self.settings.database_path)
        await self.db.open()
        await self.db.claim_owner(OWNER)
        await self.db.set("family_chat_id", str(FAMILY))
        self.session = RecordingSession()
        self.bot = Bot(TOKEN, session=self.session)
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
        self.ai = AsyncMock()
        self.ai.chat.return_value = "Mrrr, family! <3 & *cat*"
        self.ai.news.return_value = NewsDecision(True, "Scientists helped a forest grow back. Purr!")
        self.ai.sports_comment.return_value = "Go team!"
        self.ai.check.return_value = "OK"
        self.ai.last_error = None
        self.ai.last_success = None
        self.delivery = DeliveryService(self.bot, self.db, self.settings.persona_emoji)
        self.news = NewsService(self.settings, self.db, self.ai, self.client, self.delivery)
        self.sports = SportsService(self.settings, self.client, self.ai)
        self.access = AccessControl(self.db)
        self.scheduler = Scheduler(self.settings, self.db, self.news, self.sports, self.delivery)
        self.app = App(self.settings, self.db, self.bot, ME, self.ai, self.news, self.sports,
                       self.delivery, self.access, self.scheduler, RateLimiter(), ChatLocks())
        return self

    def message(self, text="Whiskers, hi", uid=OWNER, chat=OWNER, mid=1, **kwargs) -> Message:
        value = Message(message_id=mid, date=datetime.now(UTC),
                        chat=Chat(id=chat, type="private" if chat > 0 else "supergroup", title="Family" if chat < 0 else None),
                        from_user=User(id=uid, is_bot=False, first_name=f"User{uid}"), text=text, **kwargs)
        return value.as_(self.bot)

    def last_text(self) -> str:
        return self.session.sent[-1].text

    async def close(self):
        await self.scheduler.stop()
        await self.client.aclose()
        await self.bot.session.close()
        await self.db.close()
        self.tmp.cleanup()


def rss(title="New wildlife sanctuary", date="Sat, 05 Sep 2026 09:00:00 GMT", link="https://example.org/news",
        summary="Volunteers restored a forest.") -> bytes:
    return f'''<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture</title><link>https://example.org</link>
    <description>Tests</description><item><title>{title}</title><link>{link.replace('&', '&amp;')}</link>
    <pubDate>{date}</pubDate><description>{summary}</description></item></channel></rss>'''.encode()


def event(**overrides) -> dict:
    """A TheSportsDB event, as returned by eventsnext/eventslast."""
    value = {"idEvent": "2494052", "strHomeTeam": "Riverside FC", "strAwayTeam": "Hillside United",
             "idHomeTeam": "1001", "idAwayTeam": "1002", "strLeague": "Example League",
             "strTimestamp": "2026-10-10T15:00:00", "strStatus": "NS", "intHomeScore": None, "intAwayScore": None,
             "strVenue": "Riverside Park"}
    value.update(overrides)
    return value
