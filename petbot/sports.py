"""
Your teams' matches: preview on match day, full result after the final whistle.

Several teams can be followed at once (teams.json, see docs). Each team has a
data SOURCE, chosen for what it reliably knows:

  thesportsdb   any sport, any league: next/last match and whether it's final.
                Free key "3". Optional "upl": true adds the full Ukrainian
                Premier League table and goal scorers from upl.ua.
  espn          football leagues on ESPN (e.g. "uefa.champions", "eng.1"):
                fixtures, results, scorers with minutes, full table.

Facts (teams, score, scorers, table) come only from these sources and are
checked for consistency (e.g. goals must add up to the score, otherwise the
scorers are left out). The AI turns facts into a few short messages in the
pet's voice and may never change them.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from .common import ServiceError, fetch_bytes
from .i18n import t

log = logging.getLogger(__name__)

TSDB = "https://www.thesportsdb.com/api/v1/json/{key}/{endpoint}.php?id={team}"
ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer/{league}"
ESPN_STANDINGS = "https://site.api.espn.com/apis/v2/sports/soccer/{league}/standings"
UPL_CALENDAR = "https://upl.ua/ua/tournaments/games"
UPL_HOME = "https://upl.ua/ua"

FINISHED = {"FT", "AET", "PEN", "AOT", "MATCH FINISHED", "FINISHED", "AFTER PENALTIES", "AFTER EXTRA TIME",
            "STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_FINAL_AET", "STATUS_FINAL_PEN"}
POSTPONED = {"PST", "POSTPONED", "CANC", "CANCELLED", "CANCELED", "ABD", "ABANDONED", "SUSP", "SUSPENDED", "INT",
             "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_ABANDONED", "STATUS_SUSPENDED"}
NOT_STARTED = {"", "NS", "NOT STARTED", "TBD", "SCHEDULED", "STATUS_SCHEDULED"}


# --- data model ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Goal:
    minute: str
    player: str
    for_home: bool          # which team the goal counts for
    kind: str = "goal"      # goal | penalty | own_goal
    assist: str = ""


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
    url: str = ""
    goals: tuple[Goal, ...] | None = None  # None = unknown (never guessed)

    def outcome(self) -> str | None:
        """win / draw / loss from our team's point of view, only for a finished match."""
        if self.status != "finished" or not self.score:
            return None
        home, away = (int(v) for v in self.score.split(":"))
        ours, theirs = (home, away) if self.team_is_home else (away, home)
        return "win" if ours > theirs else "loss" if ours < theirs else "draw"

    @property
    def opponent(self) -> str:
        return self.away if self.team_is_home else self.home


@dataclass(frozen=True)
class Standing:
    name: str
    position: int
    played: int
    points: int


@dataclass(frozen=True)
class Team:
    """One followed team (from teams.json or the single-team SPORTS_* settings)."""
    key: str                 # stable id used in delivery keys, e.g. "polissya"
    name: str                # how the family calls it
    command: str             # Telegram command without "/"
    source: str              # thesportsdb | espn
    team_id: str
    league: str = ""         # espn league slug
    upl: bool = False        # enrich from upl.ua (Ukrainian Premier League)
    upl_name: str = ""       # the team's name on upl.ua, e.g. "Полісся"
    competitions: tuple[str, ...] = ()  # only announce these competitions (empty = all)
    hour: int = 9            # match-day preview window starts at this hour
    results: bool = True
    analysis: bool = True    # ask the AI for a short preview/summary


def teams_from_settings(settings) -> tuple[Team, ...]:
    """teams.json (SPORTS_TEAMS_FILE) or the single team from SPORTS_TEAM_ID / SPORTS_COMMAND."""
    if not settings.sports_enabled:
        return ()
    single = None if settings.sports_teams_file else Team(
        key=settings.sports_command, name=settings.sports_command.capitalize(), command=settings.sports_command,
        source="thesportsdb", team_id=settings.sports_team_id, hour=settings.sports_hour,
        results=settings.sports_results)
    return load_teams(settings.sports_teams_file, single)


def load_teams(path: Path | None, single: Team | None) -> tuple[Team, ...]:
    if path is None:
        return (single,) if single else ()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ServiceError("sports_teams_file", error=type(error).__name__) from None
    teams = []
    for item in raw if isinstance(raw, list) else []:
        command = str(item.get("command", "")).lstrip("/").lower()
        source = str(item.get("source", "thesportsdb")).lower()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", command) or source not in {"thesportsdb", "espn"} or \
                not str(item.get("team_id", "")).strip() or (source == "espn" and not item.get("league")):
            raise ServiceError("sports_teams_file", error=f"command/source/team_id/league of {item.get('name')!r}")
        teams.append(Team(
            key=command, name=str(item.get("name") or command), command=command, source=source,
            team_id=str(item.get("team_id", "")), league=str(item.get("league", "")),
            upl=bool(item.get("upl", False)), upl_name=str(item.get("upl_name", "")),
            competitions=tuple(item.get("competitions") or ()), hour=int(item.get("hour", 9)),
            results=bool(item.get("results", True)), analysis=bool(item.get("analysis", True)),
        ))
    return tuple(teams)


# --- TheSportsDB ------------------------------------------------------------------------------

def _status(raw: str, has_score: bool) -> str:
    raw = raw.strip().upper()
    if raw in FINISHED and has_score:
        return "finished"
    if raw in POSTPONED:
        return "postponed"
    if raw in NOT_STARTED and not has_score:
        return "upcoming"
    return "live"  # 1H, HT, 2H, ET... - never announced as final


def parse_tsdb_event(event: dict, team_id: str) -> Match | None:
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
    hs, as_ = event.get("intHomeScore"), event.get("intAwayScore")
    has_score = str(hs).isdigit() and str(as_).isdigit()
    return Match(
        id="tsdb:" + event_id, home=home[:100], away=away[:100], league=str(event.get("strLeague") or "")[:100],
        status=_status(str(event.get("strStatus") or ""), has_score), kickoff=kickoff,
        score=f"{hs}:{as_}" if has_score else None, venue=str(event.get("strVenue") or "")[:200],
        team_is_home=str(event.get("idHomeTeam") or "") == team_id,
        url=f"https://www.thesportsdb.com/event/{event_id}",
    )


def parse_tsdb(raw: bytes, key: str, team_id: str) -> list[Match]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise ServiceError("sports_bad_response") from None
    events = (data or {}).get(key) or [] if isinstance(data, dict) else []
    return [m for m in (parse_tsdb_event(e, team_id) for e in events if isinstance(e, dict)) if m]


# --- ESPN ----------------------------------------------------------------------------------------

def parse_espn_event(event: dict, team_id: str) -> Match | None:
    try:
        comp = event["competitions"][0]
        sides = {c["homeAway"]: c for c in comp["competitors"]}
        home, away = sides["home"], sides["away"]
    except (KeyError, IndexError, TypeError):
        return None

    def score(side: dict) -> str | None:
        value = side.get("score")
        value = value.get("displayValue") if isinstance(value, dict) else value
        return str(value) if str(value or "").isdigit() else None

    hs, as_ = score(home), score(away)
    has_score = hs is not None and as_ is not None
    status_type = (comp.get("status") or event.get("status") or {}).get("type", {})
    raw_status = str(status_type.get("name", ""))
    if status_type.get("completed") and has_score:
        raw_status = "STATUS_FULL_TIME"
    try:
        kickoff = datetime.fromisoformat(str(event.get("date", "")).replace("Z", "+00:00"))
    except ValueError:
        kickoff = None
    return Match(
        id="espn:" + str(event.get("id")), home=home["team"]["displayName"][:100], away=away["team"]["displayName"][:100],
        league=str((event.get("league") or {}).get("name") or event.get("season", {}).get("displayName") or "")[:100],
        status=_status(raw_status, has_score), kickoff=kickoff, score=f"{hs}:{as_}" if has_score else None,
        venue="",  # ESPN venues of future matches are sometimes placeholders - better none than a wrong one
        team_is_home=str(home["team"].get("id")) == team_id,
        url=f"https://www.espn.com/soccer/match/_/gameId/{event.get('id')}",
    )


def parse_espn_goals(summary: dict, match: Match) -> tuple[Goal, ...] | None:
    goals = []
    for item in summary.get("keyEvents") or []:
        if not item.get("scoringPlay"):
            continue
        people = [p.get("athlete", {}).get("displayName", "") for p in item.get("participants") or []]
        text = str((item.get("type") or {}).get("text", "")).lower()
        kind = "own_goal" if "own" in text else "penalty" if "penalty" in text else "goal"
        for_home = str((item.get("team") or {}).get("displayName", "")) == match.home
        goals.append(Goal(str((item.get("clock") or {}).get("displayValue", "")), people[0] if people else "?",
                          for_home, kind, people[1] if len(people) > 1 and kind == "goal" else ""))
    # Whether an own goal is listed under the scorer's team or the benefiting team isn't
    # documented - accept whichever reading adds up to the final score.
    flipped = [replace(g, for_home=not g.for_home) if g.kind == "own_goal" else g for g in goals]
    return _checked(goals, match) or (_checked(flipped, match) if flipped != goals else None)


def parse_espn_standings(data: dict) -> dict[str, Standing]:
    table = {}
    for group in data.get("children") or [data]:
        for entry in (group.get("standings") or {}).get("entries") or []:
            stats = {s.get("name"): s.get("value") for s in entry.get("stats") or []}
            name = entry["team"]["displayName"]
            try:
                table[team_key(name)] = Standing(name, int(stats["rank"]), int(stats.get("gamesPlayed", 0)),
                                                 int(stats.get("points", 0)))
            except (KeyError, TypeError, ValueError):
                continue
    return table


# --- upl.ua (Ukrainian Premier League) -----------------------------------------------------------

def team_key(name: str) -> str:
    quoted = re.search(r"[«\"]([^»\"]+)[»\"]", name)
    if quoted:
        name = quoted[1]
    return re.sub(r"\([^)]*\)", "", name).casefold().strip()


LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z", "и": "y",
    "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ь": "", "ю": "iu",
    "я": "ia", "ы": "y", "э": "e", "ё": "e", "ъ": "", "'": "", "ʼ": "", "’": ""})


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", team_key(name).translate(LATIN))


def find_standing(table: dict[str, Standing], name: str) -> Standing | None:
    """
    A team in a table, also across alphabets: the UPL table is in Ukrainian,
    TheSportsDB/ESPN names are in English ("Полісся" ~ "Polissya Zhytomyr").
    """
    key = team_key(name)
    if not key:
        return None
    if key in table:
        return table[key]
    found = next((st for k, st in table.items() if key in k or k in key), None)
    if found:
        return found
    best, score = None, 0.0
    words = _words(name)
    for k, st in table.items():
        first = _words(k)[:1]
        for word in words if first else ():
            ratio = SequenceMatcher(None, first[0], word).ratio()
            if ratio > score:
                best, score = st, ratio
    return best if score >= 0.8 else None


def upl_standings_url(html: bytes | str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.select('a[href*="/tournaments/championship/"]'):
        label = " ".join(a.stripped_strings).casefold()
        if not ("прем" in label and "ліга" in label) or re.search(r"(?:u\s*19|ліга\s*[-–]?\s*2|молод|юнац)", label):
            continue
        href = str(a.get("href", ""))
        href = "https://upl.ua" + href if href.startswith("/") else href
        if re.fullmatch(r"https://upl\.ua/ua/tournaments/championship/\d+/?", href):
            return href
    return None


def parse_upl_standings(html: bytes | str) -> dict[str, Standing]:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [x.get_text(" ", strip=True).casefold() for x in rows[0].find_all(["th", "td"])]

        def index(options: set[str], header=header) -> int:
            return next((i for i, h in enumerate(header) if h in options), -1)

        idx = (index({"місце", "м", "#"}), index({"команда", "клуб"}), index({"і", "и", "ігри"}), index({"о", "очки"}))
        if min(idx) < 0:
            continue
        parsed: dict[str, Standing] = {}
        for row in rows[1:]:
            cells = [x.get_text(" ", strip=True) for x in row.find_all("td")]
            if not cells:
                continue
            try:
                rank, games, points = int(cells[idx[0]]), int(cells[idx[2]]), int(cells[idx[3]])
                parsed[team_key(cells[idx[1]])] = Standing(cells[idx[1]][:100], rank, games, points)
            except (ValueError, IndexError):
                break
        if 8 <= len(parsed) <= 32 and sorted(s.position for s in parsed.values()) == list(range(1, len(parsed) + 1)):
            return parsed
    raise ServiceError("sports_table_unrecognized")


def upl_report_link(calendar_html: bytes | str, team: str, day: date) -> str | None:
    """The match report of `team` on `day` (main league only)."""
    soup = BeautifulSoup(calendar_html, "html.parser")
    current: date | None = None
    for el in soup.select(".tournaments-games .tour-date, .tournaments-games .tour-match"):
        if "tour-date" in el.get("class", []):
            found = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", el.get_text(" ", strip=True))
            current = date(int(found[3]), int(found[2]), int(found[1])) if found else None
            continue
        text = " ".join(el.get_text(" ", strip=True).split())
        tournament = (el.select_one(".match-tournament") or el).get_text(" ", strip=True)
        if current != day or not re.fullmatch(r"UPL|УПЛ", tournament.strip(), re.I):
            continue
        if team.casefold() in text.casefold():
            link = el.select_one("a[href*='/report/view/']")
            if link:
                href = str(link["href"])
                return "https://upl.ua" + href if href.startswith("/") else href
    return None


def parse_upl_goals(report_html: bytes | str, score: str) -> tuple[Goal, ...] | None:
    """
    Goals from a upl.ua referee report. Event classes (verified against final
    scores): type-score-1 goal, type-score-3 penalty, type-score-2 own goal
    (listed under the player's team, counts for the other one); others ignored.
    The home/away side is where the player's name stands relative to the minute.
    """
    soup = BeautifulSoup(report_html, "html.parser")
    goals = []
    for ev in soup.select(".events-container .event"):
        kind = {"type-score-1": "goal", "type-score-3": "penalty", "type-score-2": "own_goal"}.get(
            next((c for c in ev.get("class", []) if c.startswith("type-score")), ""))
        if not kind:
            continue
        players = ev.select(".player")
        listed_home = bool(players) and players[0].select_one(".players") is not None
        name_el = ev.select_one(".players")
        minute_el = ev.select_one(".point")
        if not name_el or not minute_el:
            continue
        for_home = not listed_home if kind == "own_goal" else listed_home
        goals.append(Goal(minute_el.get_text(strip=True), name_el.get_text(" ", strip=True)[:80], for_home, kind))
    home, away = (int(v) for v in score.split(":"))
    probe = Match("x", "h", "a", "", "finished", None, f"{home}:{away}", "", True)
    return _checked(goals, probe)


def _checked(goals: list[Goal], match: Match) -> tuple[Goal, ...] | None:
    """Scorers only if they add up to the final score - never a half-right list."""
    if not match.score:
        return None
    home, away = (int(v) for v in match.score.split(":"))
    if sum(g.for_home for g in goals) != home or sum(not g.for_home for g in goals) != away:
        return None
    return tuple(goals)


# --- presentation -----------------------------------------------------------------------------------

def mentions_score(text: str, score: str) -> bool:
    """Whether a post states the final score (either order, 2:1 / 2-1 / 2–1)."""
    home, away = score.split(":")
    return any(re.search(rf"(?<!\d){a}\s*[:\-–—]\s*{b}(?!\d)", text) for a, b in ((home, away), (away, home)))


def describe(match: Match, tz: ZoneInfo) -> str:
    """Plain facts for /<team> and for the AI (the AI may rephrase, never change them)."""
    title = {"finished": "sports_result", "live": "sports_live", "postponed": "sports_postponed"}.get(match.status, "sports_next")
    lines = [f"🏆 {match.league} — {t(title)}" if match.league else t(title),
             f"{match.home} {match.score or '—'} {match.away}"]
    if match.goals:
        for side, team in ((True, match.home), (False, match.away)):
            scorers = [f"{g.player} {g.minute}" + {"penalty": " (pen)", "own_goal": " (og)"}.get(g.kind, "")
                       for g in match.goals if g.for_home == side]
            if scorers:
                lines.append(f"⚽ {team}: " + ", ".join(scorers))
    if match.kickoff:
        lines.append(f"📅 {match.kickoff.astimezone(tz):%d.%m.%Y, %H:%M} ({tz.key})")
    if match.venue and match.status != "finished":
        lines.append("🏟 " + match.venue)
    return "\n".join(lines)


def facts(match: Match, team: Team, table: dict[str, Standing], tz: ZoneInfo, next_match: Match | None = None,
          now: datetime | None = None) -> dict:
    """Everything the AI may use - and nothing it has to guess (not even "today" or "tomorrow")."""
    today = (now or datetime.now(tz)).astimezone(tz).date()

    def days_until(m: Match) -> int | None:
        return (m.kickoff.astimezone(tz).date() - today).days if m.kickoff else None

    ours = match.home if match.team_is_home else match.away

    def local(name: str) -> str:
        # The UPL table has the names the family knows ("Кривбас", not "Kryvbas Kryvyi Rih").
        found = find_standing(table, name) if team.upl else None
        return (team.name if name == ours else found.name) if found else name

    def standing(name: str) -> dict | None:
        found = find_standing(table, name) or (find_standing(table, team.upl_name) if name == ours and team.upl_name else None)
        return {"position": found.position, "points": found.points, "played": found.played} if found else None

    data = {
        "our_team": team.name, "our_team_in_source": ours, "opponent": local(match.opponent),
        "home": local(match.home), "away": local(match.away), "we_play_at_home": match.team_is_home,
        "competition": match.league, "status": match.status,
        "kickoff_local": match.kickoff.astimezone(tz).strftime("%d.%m %H:%M") if match.kickoff else None,
        "days_until_kickoff": days_until(match),
        "venue": match.venue or None, "score": match.score, "result_for_our_team": match.outcome(),
        "goals": [{"minute": g.minute, "player": g.player, "for": "us" if g.for_home == match.team_is_home else "them",
                   "type": g.kind, "assist": g.assist or None} for g in match.goals] if match.goals else None,
        "table_position": {"us": standing(ours), "opponent": standing(match.opponent)} if table else None,
    }
    if next_match:
        data["next_match"] = {"opponent": local(next_match.opponent), "home": next_match.team_is_home,
                              "competition": next_match.league,
                              "date_local": next_match.kickoff.astimezone(tz).strftime("%d.%m %H:%M") if next_match.kickoff else None,
                              "days_until": days_until(next_match),
                              "opponent_table_position": (standing(next_match.opponent) or {}).get("position") if table else None}
    return data


# --- the service --------------------------------------------------------------------------------------

@dataclass
class TeamState:
    upcoming: Match | None = None
    last: Match | None = None
    table: dict[str, Standing] = field(default_factory=dict)
    fetched_at: float = -1e9
    fetched: datetime | None = None
    attempt: datetime | None = None
    error: str | None = None


class SportsService:
    def __init__(self, settings, client: httpx.AsyncClient, ai=None, teams: tuple[Team, ...] = ()):
        self.settings, self.client, self.ai, self.teams = settings, client, ai, teams
        self.state: dict[str, TeamState] = {team.key: TeamState() for team in teams}
        self.lock = asyncio.Lock()

    def team(self, command: str) -> Team | None:
        return next((team for team in self.teams if team.command == command), None)

    @property
    def last_error(self) -> str | None:
        return "; ".join(f"{k}: {s.error}" for k, s in self.state.items() if s.error) or None

    @property
    def last_success(self) -> str | None:
        stamps = [s.fetched for s in self.state.values() if s.fetched]
        return max(stamps).isoformat(timespec="seconds") if stamps else None

    def _wanted(self, team: Team, match: Match | None) -> Match | None:
        if match and team.competitions and not any(c.casefold() in match.league.casefold() for c in team.competitions):
            return None
        return match

    async def fetch(self, team: Team, *, force: bool = False) -> TeamState:
        state = self.state.setdefault(team.key, TeamState())
        async with self.lock:
            if not force and time.monotonic() - state.fetched_at < 300:
                return state
            try:
                if team.source == "espn":
                    upcoming, last, table = await self._espn(team)
                else:
                    upcoming, last, table = await self._tsdb(team)
            except ServiceError as error:
                state.error = str(error)
                raise
            state.upcoming, state.last = self._wanted(team, upcoming), self._wanted(team, last)
            state.table = table
            state.fetched_at, state.error = time.monotonic(), None
            state.fetched = datetime.now(self.settings.tz)
            return state

    async def _json(self, url: str):
        try:
            return json.loads(await fetch_bytes(self.client, url))
        except ValueError:
            raise ServiceError("sports_bad_response") from None

    async def _tsdb(self, team: Team):
        key = self.settings.sports_api_key
        nxt, prev = await asyncio.gather(fetch_bytes(self.client, TSDB.format(key=key, endpoint="eventsnext", team=team.team_id)),
                                         fetch_bytes(self.client, TSDB.format(key=key, endpoint="eventslast", team=team.team_id)))
        future = sorted((m for m in parse_tsdb(nxt, "events", team.team_id) if m.kickoff), key=lambda m: m.kickoff)
        past = sorted((m for m in parse_tsdb(prev, "results", team.team_id) if m.kickoff), key=lambda m: m.kickoff)
        upcoming, last = (future[0] if future else None), (past[-1] if past else None)
        table: dict[str, Standing] = {}
        if team.upl:
            table, last = await self._upl_enrich(team, last)
        return upcoming, last, table

    async def _upl_enrich(self, team: Team, last: Match | None):
        """Full UPL table + scorers of the last match. Optional: failures only drop the extras."""
        table: dict[str, Standing] = {}
        try:
            url = upl_standings_url(await fetch_bytes(self.client, UPL_HOME))
            if url:
                table = await asyncio.to_thread(parse_upl_standings, await fetch_bytes(self.client, url))
        except ServiceError as error:
            log.info("UPL table unavailable: %s", error)
        if last and last.status == "finished" and last.kickoff and team.upl_name:
            try:
                calendar = await fetch_bytes(self.client, UPL_CALENDAR)
                day = last.kickoff.astimezone(ZoneInfo("Europe/Kyiv")).date()
                link = upl_report_link(calendar, team.upl_name, day)
                if link:
                    goals = await asyncio.to_thread(parse_upl_goals, await fetch_bytes(self.client, link), last.score)
                    last = replace(last, goals=goals, url=link)
            except ServiceError as error:
                log.info("UPL report unavailable: %s", error)
        return table, last

    async def _espn(self, team: Team):
        base = ESPN_SITE.format(league=team.league)
        schedule, fixtures = await asyncio.gather(self._json(f"{base}/teams/{team.team_id}/schedule"),
                                                  self._json(f"{base}/teams/{team.team_id}/schedule?fixture=true"))
        matches = [m for m in (parse_espn_event(e, team.team_id) for e in (schedule.get("events") or []) +
                               (fixtures.get("events") or [])) if m and m.kickoff]
        now = datetime.now(UTC)
        future = sorted((m for m in matches if m.status in {"upcoming", "live"} and m.kickoff > now - timedelta(hours=3)),
                        key=lambda m: m.kickoff)
        past = sorted((m for m in matches if m.status == "finished"), key=lambda m: m.kickoff)
        upcoming, last = (future[0] if future else None), (past[-1] if past else None)
        if last:
            try:
                summary = await self._json(f"{base}/summary?event={last.id.removeprefix('espn:')}")
                last = replace(last, goals=parse_espn_goals(summary, last))
            except ServiceError:
                pass
        table: dict[str, Standing] = {}
        try:
            table = parse_espn_standings(await self._json(ESPN_STANDINGS.format(league=team.league)))
        except ServiceError:
            pass
        return upcoming, last, table

    async def summary(self, team: Team) -> str:
        try:
            state = await self.fetch(team)
        except ServiceError:
            state = self.state[team.key]
            if not (state.upcoming or state.last):
                raise  # nothing known yet - say that the source is down
            # otherwise: the last known data is better than an error
        parts = [describe(m, self.settings.tz) for m in (state.upcoming, state.last) if m]
        ours = next((m for m in (state.last, state.upcoming) if m), None)
        if ours and state.table:
            standing = find_standing(state.table, ours.home if ours.team_is_home else ours.away) or (
                find_standing(state.table, team.upl_name) if team.upl_name else None)
            if standing:
                parts.append(t("sports_table_line", position=standing.position, points=standing.points, played=standing.played))
        return "\n\n".join(parts) if parts else t("sports_nothing")

    async def _write(self, task: str, team: Team, match: Match, next_match: Match | None = None,
                     *, max_parts: int = 3) -> tuple[str, dict]:
        data = facts(match, team, self.state[team.key].table, self.settings.tz, next_match)
        if self.ai is None or not team.analysis:
            return "", data
        try:
            return await self.ai.post(task, data, max_parts=max_parts, maximum=500), data
        except ServiceError:
            return "", data

    async def preview_message(self, team: Team, match: Match) -> str:
        text, data = await self._write("match_preview", team, match, max_parts=2)
        if not text:
            return "\n\n".join((t("sports_today"), describe(match, self.settings.tz)))
        if match.kickoff:
            local = match.kickoff.astimezone(self.settings.tz)
            if not re.search(rf"(?<!\d)0?{local.hour}[:.]{local.minute:02d}(?!\d)", text):
                # The AI forgot the time - the one fact nobody can do without.
                text += f"\n⏰ {local:%H:%M} {data['home']} — {data['away']}"  # same message, no extra one
        return text

    async def result_message(self, team: Team, match: Match, next_match: Match | None) -> str:
        if match.status != "finished" or not match.score:
            raise ValueError("only confirmed final results may be announced")
        text, data = await self._write("match_result", team, match, next_match, max_parts=3)
        if not text:
            return describe(match, self.settings.tz)
        if not mentions_score(text, match.score):
            # The AI forgot the score in digits: put it at the very start, in the names the family knows,
            # instead of repeating the result at the end.
            text = f"{data['home']} {match.score} {data['away']}\n{text}"  # first line of the first message
        return text
