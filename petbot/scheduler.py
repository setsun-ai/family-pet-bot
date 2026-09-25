"""
Background jobs: daily news slots, match-day announcements and results, cleanup.

Nothing is posted during quiet hours, nothing is posted twice (deliveries are
de-duplicated in SQLite), and old events are never posted late as "new".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .common import ServiceError
from .config import Settings
from .db import Database
from .delivery import DeliveryService
from .i18n import t
from .news import NewsService
from .sports import Match, SportsService

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NewsSlot:
    key: str
    start: datetime
    end: datetime


def quiet_time(now: datetime, settings: Settings) -> bool:
    hour = now.astimezone(settings.tz).hour
    start, end = settings.quiet_start_hour, settings.quiet_end_hour
    if start == end:
        return False
    return (hour >= start or hour < end) if start > end else start <= hour < end


def news_count(day: date, settings: Settings) -> int:
    if settings.news_mode == "once":
        return 1
    if settings.news_mode == "twice":
        return 2
    # A steady 2 / 1 / 2 / 1 rhythm - not "even day of month", which breaks at month ends.
    return 2 if (day - date(2026, 1, 1)).days % 2 == 0 else 1


def news_plan(now: datetime, settings: Settings, chat_id: int = 0) -> list[NewsSlot]:
    local = now.astimezone(settings.tz)
    day = local.date().isoformat()
    specs = [(day, settings.news_hour, settings.news_minute)]
    if news_count(local.date(), settings) == 2:
        specs.append((day + ":evening", settings.news_evening_hour, settings.news_evening_minute))
    result = []
    for key, hour, minute in specs:
        # A stable per-chat jitter, so posts don't arrive at the same second every day.
        digest = hashlib.sha256(f"{chat_id}:{key}:news-slot-v1".encode()).hexdigest()
        jitter = int(digest[:8], 16) % (settings.news_jitter_minutes + 1)
        start = local.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(minutes=jitter)
        end = min(start + timedelta(minutes=settings.news_window_minutes),
                  (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0))
        result.append(NewsSlot(key, start, end))
    return result


def morning_due(match: Match | None, now: datetime, settings: Settings) -> bool:
    """Announce between SPORTS_HOUR and +3 h on match day, and only before kick-off."""
    if match is None or match.status != "upcoming" or match.kickoff is None:
        return False
    local = now.astimezone(settings.tz)
    if match.kickoff.astimezone(settings.tz).date() != local.date():
        return False
    start = local.replace(hour=settings.sports_hour, minute=0, second=0, microsecond=0)
    return start <= local < start + timedelta(hours=3) and now < match.kickoff


def result_due(match: Match | None, now: datetime) -> bool:
    """A confirmed final result of a match that started within the last 18 hours."""
    if match is None or match.status != "finished" or not match.kickoff:
        return False
    return timedelta(0) <= now - match.kickoff <= timedelta(hours=18)


class Scheduler:
    def __init__(self, settings: Settings, db: Database, news: NewsService, sports: SportsService,
                 delivery: DeliveryService):
        self.settings, self.db, self.news, self.sports, self.delivery = settings, db, news, sports, delivery
        self.tasks: list[asyncio.Task] = []
        self.last_errors: dict[str, str] = {}
        self._sports_fetched: datetime | None = None
        self._sports_attempt: datetime | None = None

    def start(self) -> None:
        if self.tasks:
            return
        jobs = [("cleanup", self.cleanup_tick, 3600)]
        if self.settings.news_enabled:
            jobs.append(("news", self.news_tick, 60))
        if self.settings.sports_enabled:
            jobs.append(("sports", self.sports_tick, 60))  # local tick; HTTP polling is adaptive
        for name, function, interval in jobs:
            self.tasks.append(asyncio.create_task(self._loop(name, function, interval), name="scheduler-" + name))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    async def _loop(self, name, function, interval):
        while True:
            try:
                await function()
                self.last_errors.pop(name, None)
            except ServiceError as error:
                self.last_errors[name] = str(error)
                log.warning("Scheduled %s: %s", name, error)
            except Exception as error:
                self.last_errors[name] = t("internal_error_type", kind=type(error).__name__)
                log.error("Scheduled %s failed (%s)", name, type(error).__name__)
            await asyncio.sleep(interval)

    async def news_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.settings.tz)
        chat = await self.db.family()
        if not chat or quiet_time(now, self.settings):
            return
        for slot in news_plan(now, self.settings, chat):
            if not slot.start <= now < slot.end or await self.db.has_delivery(chat, "daily", slot.key):
                continue
            # A manual /news and an automatic post don't arrive back to back.
            last = await self.db.last_delivery_at(chat, ("news",))
            if last and now - last < timedelta(hours=2):
                continue
            sports_last = await self.db.last_delivery_at(chat, ("sports-morning", "sports-result"))
            if sports_last and now - sports_last < timedelta(minutes=30):
                continue
            if not await self.db.claim_scheduled_attempt(chat, "news:" + slot.key, now):
                continue
            await self.news.publish(chat, slot.key, now=now)
            return

    def _poll_interval(self, now: datetime) -> timedelta:
        upcoming, last = self.sports.cached
        today = now.astimezone(self.settings.tz).date()
        match_day = any(m and m.kickoff and m.kickoff.astimezone(self.settings.tz).date() == today
                        for m in (upcoming, last))
        return timedelta(minutes=self.settings.sports_check_minutes if match_day else self.settings.sports_idle_hours * 60)

    async def sports_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.settings.tz)
        chat = await self.db.family()
        if not chat:
            return
        interval = self._poll_interval(now)
        stale = self._sports_fetched is None or now - self._sports_fetched >= interval
        morning = now.astimezone(self.settings.tz).replace(hour=self.settings.sports_hour, minute=0, second=0, microsecond=0)
        if self._sports_fetched and self._sports_fetched < morning <= now:
            stale = True  # always refresh once when the announcement window opens
        if stale:
            # During an outage, don't hit the API on every one-minute tick.
            if self._sports_attempt and now - self._sports_attempt < timedelta(minutes=5):
                return
            self._sports_attempt = now
            await self.sports.fetch()
            self._sports_fetched = now
        if quiet_time(now, self.settings):
            return
        upcoming, last = self.sports.cached
        if morning_due(upcoming, now, self.settings) and not await self.db.has_delivery(chat, "sports-morning", upcoming.id):
            await self.delivery.send(chat, await self.sports.morning_message(upcoming), [("sports-morning", upcoming.id)])
        if (self.settings.sports_results and result_due(last, now)
                and not await self.db.has_delivery(chat, "sports-result", last.id)):
            await self.delivery.send(chat, await self.sports.result_message(last), [("sports-result", last.id)])

    async def cleanup_tick(self) -> None:
        await self.db.cleanup(self.settings.history_days)
