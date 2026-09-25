"""RSS news, matches (TheSportsDB, ESPN, upl.ua) and the scheduler - nothing invented, nothing posted twice or late."""
import json
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from petbot.ai import NewsDecision
from petbot.common import ServiceError
from petbot.config import Settings
from petbot.news import parse_feed
from petbot.persona import split_messages
from petbot.scheduler import morning_due, news_count, news_plan, quiet_time, result_due
from petbot.sports import (
    Goal,
    Match,
    _checked,
    describe,
    facts,
    load_teams,
    mentions_score,
    parse_espn_event,
    parse_espn_goals,
    parse_espn_standings,
    parse_tsdb,
    parse_tsdb_event,
    parse_upl_goals,
    team_key,
    upl_report_link,
)
from tests.support import FAMILY, TEAM, Harness, event, rss

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
WARSAW = ZoneInfo("Europe/Warsaw")


class TestFeeds:
    def test_fresh_dated_item_is_parsed(self):
        items = parse_feed(rss(), NOW)
        assert len(items) == 1 and items[0].title == "New wildlife sanctuary"

    def test_old_undated_and_ads_are_skipped(self):
        assert parse_feed(rss(date="Sat, 01 Aug 2026 09:00:00 GMT"), NOW) == []
        assert parse_feed(rss(date=""), NOW) == []
        assert parse_feed(rss(title="Your horoscope for today"), NOW) == []

    def test_tracking_parameters_do_not_create_duplicates(self):
        a = parse_feed(rss(link="https://example.org/a?utm_source=x"), NOW)[0]
        b = parse_feed(rss(link="https://example.org/a?fbclid=1"), NOW)[0]
        assert a.id == b.id

    def test_not_a_feed(self):
        with pytest.raises(ServiceError):
            parse_feed(b"<html>not rss</html>", NOW)


class TestTheSportsDB:
    def test_upcoming_match(self):
        m = parse_tsdb_event(event(), "1001")
        assert m.status == "upcoming" and m.team_is_home and m.score is None and m.id == "tsdb:2494052"
        assert m.kickoff == datetime(2026, 10, 10, 15, 0, tzinfo=UTC)  # TheSportsDB timestamps are UTC

    @pytest.mark.parametrize("status, expected", [("FT", "finished"), ("Match Finished", "finished"), ("AET", "finished"),
                                                  ("HT", "live"), ("2H", "live"), ("Postponed", "postponed")])
    def test_statuses(self, status, expected):
        assert parse_tsdb_event(event(strStatus=status, intHomeScore="1", intAwayScore="0"), "1001").status == expected

    def test_finished_without_score_is_never_final(self):
        assert parse_tsdb_event(event(strStatus="FT"), "1001").status != "finished"

    def test_outcome_from_our_side(self):
        home_win = parse_tsdb_event(event(strStatus="FT", intHomeScore="1", intAwayScore="0"), "1001")
        assert home_win.outcome() == "win" and home_win.opponent == "Hillside United"
        as_away = parse_tsdb_event(event(strStatus="FT", intHomeScore="1", intAwayScore="0"), "1002")
        assert as_away.outcome() == "loss" and as_away.opponent == "Riverside FC"

    def test_empty_and_bad_responses(self):
        assert parse_tsdb(json.dumps({"events": None}).encode(), "events", "1") == []
        with pytest.raises(ServiceError):
            parse_tsdb(b"<html>", "events", "1")

    def test_describe_uses_local_time_and_scorers(self):
        m = parse_tsdb_event(event(), "1001")
        text = describe(m, ZoneInfo("Europe/Kyiv"))
        assert "10.10.2026, 18:00 (Europe/Kyiv)" in text and "Riverside FC — Hillside United" in text
        done = replace(m, status="finished", score="2:1",
                       goals=(Goal("12'", "Ivanov", True), Goal("50'", "Petrov", True, "penalty"), Goal("88'", "Smith", False)))
        text = describe(done, WARSAW)
        assert "⚽ Riverside FC: Ivanov 12', Petrov 50' (pen)" in text and "⚽ Hillside United: Smith 88'" in text


def espn_event(status="STATUS_SCHEDULED", completed=False, home_score=None, away_score=None, home_id="493"):
    side = lambda where, team_id, name, score: {"homeAway": where, "team": {"id": team_id, "displayName": name},  # noqa: E731
                                                "score": {"displayValue": score} if score is not None else None}
    return {"id": "740001", "date": "2026-10-14T19:00Z", "league": {"name": "UEFA Champions League"},
            "competitions": [{"competitors": [side("home", home_id, "Shakhtar Donetsk", home_score),
                                              side("away", "999", "AEK Athens", away_score)],
                              "status": {"type": {"name": status, "completed": completed}},
                              "venue": {"fullName": "Arena Lviv"}}]}


class TestESPN:
    def test_schedule_event(self):
        m = parse_espn_event(espn_event(), "493")
        assert m.status == "upcoming" and m.team_is_home and m.id == "espn:740001"
        assert m.league == "UEFA Champions League" and m.kickoff == datetime(2026, 10, 14, 19, 0, tzinfo=UTC)
        done = parse_espn_event(espn_event("STATUS_FULL_TIME", True, "1", "1"), "493")
        assert done.status == "finished" and done.score == "1:1" and done.outcome() == "draw"
        assert parse_espn_event({"id": "1"}, "493") is None

    def test_goals_must_add_up(self):
        m = replace(parse_espn_event(espn_event("STATUS_FULL_TIME", True, "1", "1"), "493"))
        play = lambda who, team, minute, kind="Goal": {  # noqa: E731
            "scoringPlay": True, "type": {"text": kind}, "clock": {"displayValue": minute},
            "team": {"displayName": team}, "participants": [{"athlete": {"displayName": who}}]}
        summary = {"keyEvents": [play("Mendoza", "Shakhtar Donetsk", "45'+1'"), play("Pineda", "AEK Athens", "70'"),
                                 {"scoringPlay": False, "type": {"text": "Yellow Card"}}]}
        goals = parse_espn_goals(summary, m)
        assert [(g.player, g.for_home) for g in goals] == [("Mendoza", True), ("Pineda", False)]
        summary["keyEvents"].pop(1)
        assert parse_espn_goals(summary, m) is None  # a half-right list is worse than none

    def test_own_goal_either_listing(self):
        m = parse_espn_event(espn_event("STATUS_FULL_TIME", True, "1", "0"), "493")
        og = {"scoringPlay": True, "type": {"text": "Own Goal"}, "clock": {"displayValue": "30'"},
              "team": {"displayName": "AEK Athens"}, "participants": [{"athlete": {"displayName": "Unlucky"}}]}
        goals = parse_espn_goals({"keyEvents": [og]}, m)
        assert goals and goals[0].kind == "own_goal" and goals[0].for_home

    def test_standings(self):
        data = {"children": [{"standings": {"entries": [
            {"team": {"displayName": "Shakhtar Donetsk"},
             "stats": [{"name": "rank", "value": 18}, {"name": "gamesPlayed", "value": 2}, {"name": "points", "value": 2}]},
            {"team": {"displayName": "Broken"}, "stats": []}]}}]}
        table = parse_espn_standings(data)
        assert list(table) == ["shakhtar donetsk"] and table["shakhtar donetsk"].position == 18


UPL_REPORT = """<div class="events-container">
  <div class="event type-score-1"><div class="player"><span class="players">Гусол</span></div><span class="point">12'</span><div class="player"></div></div>
  <div class="event type-card-1"><div class="player"><span class="players">Хтось</span></div><span class="point">30'</span></div>
  <div class="event type-score-3"><div class="player"></div><span class="point">55'</span><div class="player"><span class="players">Бабенко</span></div></div>
  <div class="event type-score-2"><div class="player"></div><span class="point">80'</span><div class="player"><span class="players">Невдаха</span></div></div>
</div>"""

UPL_CALENDAR = """<div class="tournaments-games">
  <div class="tour-date">Субота, 04.10.2026</div>
  <div class="tour-match"><span class="match-tournament">УПЛ</span> Полісся — Динамо <a href="/ua/report/view/111">звіт</a></div>
  <div class="tour-match"><span class="match-tournament">U19</span> Полісся U19 — Динамо U19 <a href="/ua/report/view/112">звіт</a></div>
  <div class="tour-date">Неділя, 05.10.2026</div>
  <div class="tour-match"><span class="match-tournament">УПЛ</span> Шахтар — Зоря <a href="/ua/report/view/113">звіт</a></div>
</div>"""


class TestUPL:
    def test_goal_codes(self):
        goals = parse_upl_goals(UPL_REPORT, "2:1")  # own goal of an away player counts for home
        assert [(g.player, g.for_home, g.kind) for g in goals] == [
            ("Гусол", True, "goal"), ("Бабенко", False, "penalty"), ("Невдаха", True, "own_goal")]
        assert parse_upl_goals(UPL_REPORT, "3:0") is None

    def test_report_link_of_the_main_league_only(self):
        assert upl_report_link(UPL_CALENDAR, "Полісся", date(2026, 10, 4)) == "https://upl.ua/ua/report/view/111"
        assert upl_report_link(UPL_CALENDAR, "Полісся", date(2026, 10, 5)) is None

    def test_team_key(self):
        assert team_key("ФК «Полісся» (Житомир)") == "полісся" == team_key("Полісся")


class TestFacts:
    def test_checked(self):
        m = Match("x", "A", "B", "", "finished", None, "1:0", "", True)
        assert _checked([Goal("1'", "a", True)], m) and _checked([Goal("1'", "a", False)], m) is None

    def test_mentions_score(self):
        assert mentions_score("Выиграли 2:1!", "2:1") and mentions_score("ну 1–2, бывает", "2:1")
        assert not mentions_score("было 12:1", "2:1") and not mentions_score("ничего", "2:1")

    def test_facts_are_from_our_side(self):
        from petbot.sports import Standing
        m = replace(parse_tsdb_event(event(strStatus="FT", intHomeScore="0", intAwayScore="2"), "1002"),
                    goals=(Goal("5'", "X", False), Goal("9'", "Y", False)))
        table = {"hillside united": Standing("Hillside United", 1, 7, 18), "riverside fc": Standing("Riverside FC", 9, 7, 8)}
        nxt = parse_tsdb_event(event(idEvent="2", strHomeTeam="Lakeside", idHomeTeam="7", strAwayTeam="Hillside United",
                                     idAwayTeam="1002"), "1002")
        data = facts(m, TEAM, table, WARSAW, nxt, now=datetime(2026, 10, 9, 23, 0, tzinfo=WARSAW))
        assert data["result_for_our_team"] == "win" and data["table_position"]["us"]["position"] == 1
        assert data["days_until_kickoff"] == 1 and data["next_match"]["days_until"] == 1  # "tomorrow", computed, not guessed
        assert {g["for"] for g in data["goals"]} == {"us"} and data["next_match"]["opponent"] == "Lakeside"

    def test_upl_names_the_family_knows(self):
        from petbot.sports import Standing, Team
        team = Team("polissya", "Полісся", "polissya", "thesportsdb", "140180", upl=True, upl_name="Полісся")
        m = replace(parse_tsdb_event(event(strHomeTeam="Polissya Zhytomyr", strAwayTeam="Kryvbas Kryvyi Rih",
                                           idHomeTeam="140180"), "140180"))
        table = {"полісся": Standing("Полісся", 1, 7, 18), "кривбас": Standing("Кривбас", 14, 6, 5)}
        data = facts(m, team, table, WARSAW)
        assert (data["home"], data["opponent"]) == ("Полісся", "Кривбас")
        assert data["table_position"] == {"us": {"position": 1, "points": 18, "played": 7},
                                          "opponent": {"position": 14, "points": 5, "played": 6}}

    def test_teams_file(self, tmp_path):
        path = tmp_path / "teams.json"
        path.write_text(json.dumps([{"name": "Shakhtar", "command": "shakhtar", "source": "espn", "league": "uefa.champions",
                                     "team_id": "493"}]), encoding="utf-8")
        (team,) = load_teams(path, None)
        assert team.source == "espn" and team.command == "shakhtar"
        path.write_text(json.dumps([{"name": "X", "command": "Bad Command!", "team_id": "1"}]), encoding="utf-8")
        with pytest.raises(ServiceError):
            load_teams(path, None)


class TestSchedule:
    s = Settings(timezone="Europe/Warsaw", sports_hour=9)

    def test_quiet_hours_across_midnight(self):
        assert quiet_time(datetime(2026, 9, 5, 23, 30, tzinfo=WARSAW), self.s)
        assert quiet_time(datetime(2026, 9, 5, 7, 59, tzinfo=WARSAW), self.s)
        assert not quiet_time(datetime(2026, 9, 5, 12, 0, tzinfo=WARSAW), self.s)

    def test_news_rhythm_and_jitter(self):
        days = [news_count(datetime(2026, 9, d).date(), self.s) for d in range(1, 5)]
        assert sorted(set(days)) == [1, 2] and days[0] != days[1]
        slot = news_plan(NOW, self.s, FAMILY)[0]
        assert 0 <= (slot.start - slot.start.replace(hour=11, minute=0)).total_seconds() <= 20 * 60
        assert news_plan(NOW, self.s, FAMILY) == news_plan(NOW, self.s, FAMILY)  # stable per chat

    def test_news_every_other_day(self):
        s = replace(self.s, news_mode="every2days")
        days = [news_count(date(2026, 9, 1) + timedelta(days=d), s) for d in range(10)]
        assert days in ([1, 0] * 5, [0, 1] * 5)

    def test_morning_announcement_window(self):
        match = parse_tsdb_event(event(strTimestamp="2026-10-10T16:00:00"), "1001")  # 18:00 Warsaw
        at = lambda h: datetime(2026, 10, 10, h, 30, tzinfo=WARSAW)  # noqa: E731
        assert not morning_due(match, at(8), self.s)
        assert morning_due(match, at(9), self.s) and morning_due(match, at(11), self.s)
        assert not morning_due(match, at(12), self.s)
        assert not morning_due(match, at(9) - timedelta(days=1), self.s)
        assert morning_due(match, at(14), self.s, hour=14)  # per-team hour

    def test_result_only_while_fresh(self):
        done = parse_tsdb_event(event(strStatus="FT", intHomeScore="2", intAwayScore="2"), "1001")
        assert result_due(done, done.kickoff + timedelta(hours=2))
        assert not result_due(done, done.kickoff + timedelta(days=2))  # never posted late as "new"


class NewsAndSportsPosting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(sports_enabled=True, timezone="Europe/Warsaw")

    async def asyncTearDown(self):
        await self.h.close()

    def feed(self, *items):
        self.h.news.cached = [a for raw in items for a in parse_feed(raw, NOW)]
        self.h.news.cached_at = float("inf")

    def texts(self):
        return [m.text for m in self.h.session.sent]

    def sounds(self):
        return [not m.disable_notification for m in self.h.session.sent]

    async def test_news_is_posted_once_with_source(self):
        self.feed(rss())
        self.assertTrue(await self.h.news.publish(FAMILY, now=NOW))
        self.assertIn("Source (05.09.2026): https://example.org/news", self.h.last_text())
        self.assertFalse(await self.h.news.publish(FAMILY, now=NOW))  # the same story never twice
        self.assertEqual(self.h.ai.news.await_count, 1)  # and never reviewed twice

    async def test_rejected_news_is_not_invented(self):
        self.h.ai.news.return_value = NewsDecision(False)
        self.feed(rss())
        self.assertFalse(await self.h.news.publish(FAMILY, now=NOW))
        self.assertFalse(self.h.session.sent)

    async def test_match_day_preview_and_result(self):
        state = self.h.sports.state[TEAM.key]
        state.upcoming = parse_tsdb_event(event(strTimestamp="2026-10-10T16:00:00"), "1001")
        state.fetched = datetime(2026, 10, 10, 9, 5, tzinfo=WARSAW)
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 9, 10, tzinfo=WARSAW))
        # A chat burst: the AI's two short messages + the kick-off time it forgot.
        # The AI's two short messages; the kick-off time it forgot goes into the last one, not a new message.
        self.assertEqual(self.texts(), ["Go team!", "Meow\n⏰ 18:00 Riverside FC — Hillside United"])
        self.assertEqual(self.sounds(), [True, False])  # only the first message of the preview makes a sound
        self.assertEqual(self.h.ai.post.call_args.args[0], "match_preview")
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 9, 20, tzinfo=WARSAW))
        self.assertEqual(len(self.h.session.sent), 2)  # announced once

        state.last, state.upcoming = replace(state.upcoming, status="finished", score="3:1"), None
        state.fetched = datetime(2026, 10, 10, 20, 0, tzinfo=WARSAW)
        self.h.ai.post.return_value = "3:1, мурр!\n\nИванов молодец"
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 20, 1, tzinfo=WARSAW))
        self.assertEqual(self.texts()[2:], ["3:1, мурр!", "Иванов молодец"])  # score present - nothing appended
        self.assertEqual(self.sounds()[2:], [False, False])  # the result arrives quietly
        task, data = self.h.ai.post.call_args.args
        self.assertEqual((task, data["result_for_our_team"], data["score"]), ("match_result", "win", "3:1"))

    async def test_result_without_score_gets_the_facts(self):
        final = replace(parse_tsdb_event(event(), "1001"), status="finished", score="0:0")
        text = await self.h.sports.result_message(TEAM, final, None)
        self.assertEqual(split_messages(text), ["Riverside FC 0:0 Hillside United\nGo team!", "Meow"])  # score first
        with self.assertRaises(ValueError):
            await self.h.sports.result_message(TEAM, replace(final, status="live"), None)

    async def test_source_outage_shows_the_last_known_data(self):
        self.h.sports.state[TEAM.key].upcoming = parse_tsdb_event(event(), "1001")
        self.assertIn("Riverside FC", await self.h.sports.summary(TEAM))  # the mock source answers 500
        self.h.sports.state[TEAM.key].upcoming = None
        with self.assertRaises(ServiceError):
            await self.h.sports.summary(TEAM)

    async def test_ai_failure_does_not_block_the_facts(self):
        self.h.ai.post.side_effect = ServiceError("ai_daily_limit")
        text = await self.h.sports.preview_message(TEAM, parse_tsdb_event(event(), "1001"))
        self.assertIn("Riverside FC", text)

    async def test_posts_keep_their_distance(self):
        self.h.scheduler.settings = replace(self.h.settings, quiet_start_hour=0, quiet_end_hour=0)
        fresh = datetime.now(UTC) - timedelta(hours=1)
        self.h.news.cached = parse_feed(rss(date=f"{fresh:%a, %d %b %Y %H:%M:%S} GMT"), datetime.now(UTC))
        self.h.news.cached_at = float("inf")
        self.assertTrue(await self.h.news.publish(FAMILY, now=datetime.now(UTC)))
        now = datetime.now(WARSAW)
        self.assertTrue(await self.h.scheduler._recent_post(FAMILY, now, 10))
        state = self.h.sports.state[TEAM.key]
        state.upcoming = replace(parse_tsdb_event(event(), "1001"), kickoff=now + timedelta(minutes=1))
        state.fetched = now
        await self.h.scheduler._team_tick(replace(TEAM, hour=now.hour), FAMILY, now)
        self.assertEqual(len(self.h.session.sent), 2)  # right after the news - the preview waits
