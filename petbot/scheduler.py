"""
Background jobs: good news, match previews and results, weekly praise, birthdays, cleanup.

Nothing is posted during quiet hours, nothing twice (deliveries are
de-duplicated in SQLite), old events are never posted late as "new", and
automatic posts keep a polite distance from each other.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .common import ServiceError
from .config import Settings
from .db import Database
from .delivery import DeliveryService
from .family import FamilyService, praise_due, week_key
from .i18n import t
from .news import NewsService
from .sports import Match, SportsService, Team

log = logging.getLogger(__name__)
POST_KINDS = ("news", "sports-preview", "sports-result", "praise", "birthday")


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
    parity = (day - date(2026, 1, 1)).days % 2 == 0
    return {"once": 1, "twice": 2, "every2days": 1 if parity else 0}.get(settings.news_mode, 2 if parity else 1)


def news_plan(now: datetime, settings: Settings, chat_id: int = 0) -> list[NewsSlot]:
    local = now.astimezone(settings.tz)
    day = local.date().isoformat()
    count = news_count(local.date(), settings)
    specs = [(day, settings.news_hour, settings.news_minute)][:count]
    if count == 2:
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


def morning_due(match: Match | None, now: datetime, settings: Settings, hour: int | None = None) -> bool:
    """Preview between the team's hour and +3 h on match day, and only before kick-off."""
    if match is None or match.status != "upcoming" or match.kickoff is None:
        return False
    local = now.astimezone(settings.tz)
    if match.kickoff.astimezone(settings.tz).date() != local.date():
        return False
    start = local.replace(hour=settings.sports_hour if hour is None else hour, minute=0, second=0, microsecond=0)
    return start <= local < start + timedelta(hours=3) and now < match.kickoff


def result_due(match: Match | None, now: datetime) -> bool:
    """A confirmed final result of a match that started within the last 18 hours."""
    if match is None or match.status != "finished" or not match.kickoff:
        return False
    return timedelta(0) <= now - match.kickoff <= timedelta(hours=18)


class Scheduler:
    def __init__(self, settings: Settings, db: Database, news: NewsService, sports: SportsService,
                 delivery: DeliveryService, family: FamilyService | None = None):
        self.settings, self.db, self.news, self.sports, self.delivery = settings, db, news, sports, delivery
        self.family = family
        self.tasks: list[asyncio.Task] = []
        self.last_errors: dict[str, str] = {}

    def start(self) -> None:
        if self.tasks:
            return
        jobs = [("cleanup", self.cleanup_tick, 3600)]
        if self.settings.news_enabled:
            jobs.append(("news", self.news_tick, 60))
        if self.sports.teams:
            jobs.append(("sports", self.sports_tick, 60))  # local tick; HTTP polling is adaptive
        if self.family and self.family.members:
            jobs.append(("family", self.family_tick, 60))
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

    async def _recent_post(self, chat: int, now: datetime, minutes: int, kinds: tuple[str, ...] = POST_KINDS) -> bool:
        last = await self.db.last_delivery_at(chat, kinds)
        return bool(last and now - last < timedelta(minutes=minutes))

    # --- news ---
    async def news_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.settings.tz)
        chat = await self.db.family()
        if not chat or quiet_time(now, self.settings):
            return
        for slot in news_plan(now, self.settings, chat):
            if not slot.start <= now < slot.end or await self.db.has_delivery(chat, "daily", slot.key):
                continue
            # A manual /news and an automatic post don't come back to back; other posts get 30 min of air.
            if await self._recent_post(chat, now, 120, ("news",)) or await self._recent_post(chat, now, 30):
                continue
            if not await self.db.claim_scheduled_attempt(chat, "news:" + slot.key, now):
                continue
            await self.news.publish(chat, slot.key, now=now)
            return

    # --- sports ---
    def _poll_interval(self, team: Team, now: datetime) -> timedelta:
        state = self.sports.state[team.key]
        today = now.astimezone(self.settings.tz).date()
        match_day = any(m and m.kickoff and m.kickoff.astimezone(self.settings.tz).date() in (today, today - timedelta(days=1))
                        for m in (state.upcoming, state.last))
        return timedelta(minutes=self.settings.sports_check_minutes if match_day else self.settings.sports_idle_hours * 60)

    async def sports_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.settings.tz)
        chat = await self.db.family()
        if not chat:
            return
        for team in self.sports.teams:
            try:
                await self._team_tick(team, chat, now)
            except ServiceError as error:
                self.last_errors["sports:" + team.key] = str(error)

    async def _team_tick(self, team: Team, chat: int, now: datetime) -> None:
        state = self.sports.state[team.key]
        stale = state.fetched is None or now - state.fetched >= self._poll_interval(team, now)
        window = now.astimezone(self.settings.tz).replace(hour=team.hour, minute=0, second=0, microsecond=0)
        if state.fetched and state.fetched < window <= now:
            stale = True  # refresh once when the preview window opens
        if stale:
            if state.attempt and now - state.attempt < timedelta(minutes=5):
                return  # during an outage, don't hit the source every minute
            state.attempt = now
            await self.sports.fetch(team, force=True)
            state.fetched = now
        if quiet_time(now, self.settings) or await self._recent_post(chat, now, 10):
            return
        upcoming, last = state.upcoming, state.last
        if morning_due(upcoming, now, self.settings, team.hour):
            key = f"{team.key}:{upcoming.id}"
            if not await self.db.has_delivery(chat, "sports-preview", key):
                await self.delivery.send(chat, await self.sports.preview_message(team, upcoming), [("sports-preview", key)],
                                         sound="first")
                return
        if team.results and result_due(last, now):
            key = f"{team.key}:{last.id}"
            if not await self.db.has_delivery(chat, "sports-result", key):
                await self.delivery.send(chat, await self.sports.result_message(team, last, upcoming), [("sports-result", key)],
                                         sound="none")  # results arrive quietly

    # --- family ---
    async def family_tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.settings.tz)
        chat = await self.db.family()
        if not chat or quiet_time(now, self.settings) or await self._recent_post(chat, now, 30):
            return
        local = now.astimezone(self.settings.tz)
        if local.hour >= self.settings.birthday_hour:
            for member in self.family.birthdays(local):
                key = f"{local.year}:{member.name}"
                if not await self.db.has_delivery(chat, "birthday", key) and \
                        await self.db.claim_scheduled_attempt(chat, "birthday:" + key, now):
                    await self.delivery.send(chat, await self.family.birthday_text(member, local), [("birthday", key)])
                    return
        s = self.settings
        if s.praise_enabled and praise_due(local, s.praise_weekday, s.praise_hour):
            week = week_key(local)
            if await self.db.has_delivery(chat, "praise", week) or \
                    not await self.db.claim_scheduled_attempt(chat, "praise:" + week, now):
                return
            member, rotation = await self.family.choose()
            if member is None:
                return
            text = await self.family.praise_text(member, local)
            if await self.delivery.send(chat, text, [("praise", week)]):
                await self.db.set("praise_rotation", json.dumps(rotation, ensure_ascii=False))
                await self.family.remember_praise(member, text)

    async def cleanup_tick(self) -> None:
        await self.db.cleanup(self.settings.history_days)
