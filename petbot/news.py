"""
Daily good news: fresh items from RSS feeds (NEWS_FEEDS), reviewed by the AI
in the pet's voice, posted to the family chat with the source link.

Nothing is invented: if every candidate is rejected or the feeds are down,
nothing is posted. Each article is reviewed at most once (cached decision).
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

from .ai import AIService
from .common import ServiceError, fetch_bytes, safe_url
from .config import Settings
from .db import Database
from .delivery import DeliveryService
from .i18n import t
from .persona import tidy

log = logging.getLogger(__name__)

SKIP_TITLE_WORDS = ("horoscope", "astrology", "sponsored", "гороскоп", "реклама")
LIGHT_TITLE_WORDS = ("cat", "dog", "animal", "wildlife", "rescue", "funny", "bird", "volunteer",
                     "кот", "кош", "собак", "живот", "птиц", "волонт")


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    summary: str
    link: str
    published: datetime


def parse_feed(raw: bytes, now: datetime, max_age_days: int = 7) -> list[Article]:
    parsed = feedparser.parse(raw)  # bytes only: no hidden network access
    if not parsed.get("version") and not parsed.entries:
        raise ServiceError("rss_unrecognized")
    entries = []
    for entry in parsed.entries[:80]:
        link = safe_url(str(entry.get("link", "")))
        title = BeautifulSoup(str(entry.get("title", "")), "html.parser").get_text(" ", strip=True)[:300]
        stamp = dict.get(entry, "published_parsed") or dict.get(entry, "updated_parsed")
        if not link or not title or not stamp:
            continue  # undated items must not pose as fresh news
        try:
            published = datetime.fromtimestamp(calendar.timegm(stamp), UTC)
        except (TypeError, ValueError, OverflowError):
            continue
        if not now - timedelta(days=max_age_days) <= published <= now + timedelta(minutes=10):
            continue
        if any(word in title.casefold() for word in SKIP_TITLE_WORDS):
            continue
        p = urlsplit(link)
        query = [(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_") and k not in {"fbclid", "gclid"}]
        canonical = urlunsplit((p.scheme, p.netloc.lower(), p.path, urlencode(query), ""))
        summary = BeautifulSoup(str(entry.get("summary") or entry.get("description") or ""),
                                "html.parser").get_text(" ", strip=True)[:1800]
        entries.append(Article(hashlib.sha256(canonical.encode()).hexdigest(), title, summary, link, published))
    return entries


class NewsService:
    def __init__(self, settings: Settings, db: Database, ai: AIService, client: httpx.AsyncClient,
                 delivery: DeliveryService):
        self.settings, self.db, self.ai, self.client, self.delivery = settings, db, ai, client, delivery
        self.lock = asyncio.Lock()
        self.cached: list[Article] = []
        self.cached_at = -1e9
        self.last_error: str | None = None

    async def articles(self, now: datetime) -> list[Article]:
        max_age = timedelta(days=self.settings.news_max_age_days)
        if time.monotonic() - self.cached_at < 300:
            return [a for a in self.cached if now - max_age <= a.published <= now + timedelta(minutes=10)]

        async def one(url: str):
            try:
                data = await fetch_bytes(self.client, url)
                return await asyncio.to_thread(parse_feed, data, now, self.settings.news_max_age_days)
            except ServiceError:
                return None

        results = await asyncio.gather(*(one(url) for url in self.settings.news_feeds))
        if all(result is None for result in results):
            self.last_error = t("rss_all_down")
            raise ServiceError("rss_all_down")
        merged = {a.id: a for result in results if result for a in result}

        def priority(a: Article):
            # Newest day first; light stories (animals, rescues) win ties within a day.
            light = any(word in a.title.casefold() for word in LIGHT_TITLE_WORDS)
            return (a.published.date(), light, a.published)

        self.cached = sorted(merged.values(), key=priority, reverse=True)
        self.cached_at = time.monotonic()
        self.last_error = None if all(result is not None for result in results) else t("rss_some_down")
        return self.cached

    async def publish(self, chat_id: int, daily_key: str | None = None, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        async with self.lock:
            if daily_key and await self.db.has_delivery(chat_id, "daily", daily_key):
                return False
            evaluated = 0
            for article in await self.articles(now):
                if await self.db.has_delivery(chat_id, "news", article.id):
                    continue
                # "cat3:" = the review cache key used since the first version; keeps old decisions valid.
                review_id = "cat3:" + article.id
                review = await self.db.review(review_id)
                if review is None:
                    if evaluated >= self.settings.news_max_candidates:
                        break
                    evaluated += 1
                    decision = await self.ai.news(article.title, article.summary)
                    review = {"decision": "accept" if decision.accepted else "reject", "post": decision.text}
                    await self.db.save_review(review_id, review["decision"], review["post"])
                if review["decision"] != "accept":
                    continue
                date = article.published.astimezone(self.settings.tz)
                message = (f"{tidy(review['post'], self.settings.persona_emoji, 550)}\n\n"
                           + t("news_source", date=f"{date:%d.%m.%Y}", link=article.link))
                keys = [("news", article.id)]
                if daily_key:
                    keys.append(("daily", daily_key))
                return await self.delivery.send(chat_id, message, keys)
            return False
