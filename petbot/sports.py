"""
Your team's matches from TheSportsDB (free public API, any sport, any league).

SPORTS_TEAM_ID is the numeric team id: search your team on thesportsdb.com,
the id is in the page address (e.g. .../team/133604-arsenal -> 133604).
The free key "3" gives the next and the last match - enough for a match-day
announcement and a result post. Facts come only from the API; the AI adds one
in-character line and may never change them.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from .common import ServiceError, fetch_bytes
from .config import Settings
from .i18n import t

API = "https://www.thesportsdb.com/api/v1/json/{key}/{endpoint}.php?id={team}"
FINISHED = {"FT", "AET", "PEN", "AOT", "MATCH FINISHED", "FINISHED", "AFTER PENALTIES", "AFTER EXTRA TIME"}
POSTPONED = {"PST", "POSTPONED", "CANC", "CANCELLED", "CANCELED", "ABD", "ABANDONED", "SUSP", "SUSPENDED", "INT"}
NOT_STARTED = {"", "NS", "NOT STARTED", "TBD", "SCHEDULED"}


@dataclass(frozen=True)
class Match:
    id: str
    home: str
    away: str
    league: str
    status: str  # upcoming | live | finished | postponed
    kickoff: datetime | None
    score: str | None
    venue: str
    team_is_home: bool

    @property
    def url(self) -> str:
        return f"https://www.thesportsdb.com/event/{self.id}"

    def outcome(self) -> str | None:
        """win / draw / loss from our team's point of view, only for a finished match."""
        if self.status != "finished" or not self.score:
            return None
        home, away = (int(v) for v in self.score.split(":"))
        ours, theirs = (home, away) if self.team_is_home else (away, home)
        return "win" if ours > theirs else "loss" if ours < theirs else "draw"


def parse_event(event: dict, team_id: str) -> Match | None:
    event_id = str(event.get("idEvent") or "").strip()
    home, away = str(event.get("strHomeTeam") or "").strip(), str(event.get("strAwayTeam") or "").strip()
    if not event_id.isdigit() or not home or not away:
        return None
    kickoff = None
    stamp = str(event.get("strTimestamp") or "").strip()
    if stamp:
        try:
            kickoff = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=UTC)  # TheSportsDB timestamps are UTC
        except ValueError:
            kickoff = None
    raw_status = str(event.get("strStatus") or "").strip().upper()
    home_score, away_score = event.get("intHomeScore"), event.get("intAwayScore")
    has_score = str(home_score).isdigit() and str(away_score).isdigit()
    if raw_status in FINISHED and has_score:
        status = "finished"
    elif raw_status in POSTPONED:
        status = "postponed"
    elif raw_status in NOT_STARTED and not has_score:
        status = "upcoming"
    else:
        status = "live"  # 1H, HT, 2H, ET... - never announced as final
    return Match(
        id=event_id, home=home[:100], away=away[:100], league=str(event.get("strLeague") or "")[:100],
        status=status, kickoff=kickoff, score=f"{home_score}:{away_score}" if has_score else None,
        venue=str(event.get("strVenue") or "")[:200],
        team_is_home=str(event.get("idHomeTeam") or "") == team_id,
    )


def parse_response(raw: bytes, key: str, team_id: str) -> list[Match]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise ServiceError("sports_bad_response") from None
    if not isinstance(data, dict):
        raise ServiceError("sports_bad_response")
    events = data.get(key) or []
    return [m for m in (parse_event(e, team_id) for e in events if isinstance(e, dict)) if m]


def describe(match: Match, tz: ZoneInfo) -> str:
    """Plain factual lines - the same in announcements, results and /match."""
    title = {"finished": "sports_result", "live": "sports_live", "postponed": "sports_postponed"}.get(
        match.status, "sports_next")
    lines = [f"🏆 {match.league} — {t(title)}" if match.league else t(title),
             f"{match.home} {match.score or '—'} {match.away}"]
    if match.kickoff:
        local = match.kickoff.astimezone(tz)
        lines.append(f"📅 {local:%d.%m.%Y, %H:%M} ({tz.key})")
    if match.venue:
        lines.append("🏟 " + match.venue)
    return "\n".join(lines)


class SportsService:
    def __init__(self, settings: Settings, client: httpx.AsyncClient, ai=None):
        self.settings, self.client, self.ai = settings, client, ai
        self.lock = asyncio.Lock()
        self.cached: tuple[Match | None, Match | None] = (None, None)
        self.cached_at = -1e9
        self.last_error: str | None = None
        self.last_success: str | None = None

    def _url(self, endpoint: str) -> str:
        s = self.settings
        return API.format(key=s.sports_api_key, endpoint=endpoint, team=s.sports_team_id)

    async def fetch(self) -> tuple[Match | None, Match | None]:
        """(next match, last match) of the team; cached for 5 minutes."""
        async with self.lock:
            if time.monotonic() - self.cached_at < 300:
                return self.cached
            try:
                next_raw, last_raw = await asyncio.gather(fetch_bytes(self.client, self._url("eventsnext")),
                                                          fetch_bytes(self.client, self._url("eventslast")))
                upcoming = parse_response(next_raw, "events", self.settings.sports_team_id)
                results = parse_response(last_raw, "results", self.settings.sports_team_id)
            except ServiceError as error:
                self.last_error = str(error)
                raise
            future = sorted((m for m in upcoming if m.kickoff), key=lambda m: m.kickoff)
            past = sorted((m for m in results if m.kickoff), key=lambda m: m.kickoff)
            self.cached = (future[0] if future else None, past[-1] if past else None)
            self.cached_at = time.monotonic()
            self.last_error = None
            self.last_success = datetime.now(self.settings.tz).isoformat(timespec="seconds")
            return self.cached

    async def summary(self) -> str:
        upcoming, last = await self.fetch()
        parts = [describe(m, self.settings.tz) for m in (upcoming, last) if m]
        return "\n\n".join(parts) if parts else t("sports_nothing")

    async def _comment(self, match: Match, moment: str) -> str:
        """One in-character line; the post still goes out (without it) if the AI fails."""
        if self.ai is None:
            return ""
        our = match.home if match.team_is_home else match.away
        facts = {"moment": moment, "our_team": our, "home": match.home, "away": match.away,
                 "league": match.league, "score": match.score, "result_for_our_team": match.outcome()}
        try:
            return await self.ai.sports_comment(facts)
        except ServiceError:
            return ""

    async def morning_message(self, match: Match) -> str:
        comment = await self._comment(match, "match_today")
        return "\n\n".join(p for p in (t("sports_today"), describe(match, self.settings.tz), comment) if p)

    async def result_message(self, match: Match) -> str:
        if match.status != "finished" or not match.score:
            raise ValueError("only confirmed final results may be announced")
        comment = await self._comment(match, "final_result")
        return "\n\n".join(p for p in (describe(match, self.settings.tz), comment) if p)
