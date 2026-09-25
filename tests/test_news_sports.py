"""RSS news, TheSportsDB matches and the scheduler - nothing invented, nothing posted twice or late."""
import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from petbot.ai import NewsDecision
from petbot.common import ServiceError
from petbot.config import Settings
from petbot.news import parse_feed
from petbot.scheduler import morning_due, news_count, news_plan, quiet_time, result_due
from petbot.sports import describe, parse_event, parse_response
from tests.support import FAMILY, Harness, event, rss

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


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


class TestSportsParsing:
    def test_upcoming_match(self):
        m = parse_event(event(), "1001")
        assert m.status == "upcoming" and m.team_is_home and m.score is None
        assert m.kickoff == datetime(2026, 10, 10, 15, 0, tzinfo=UTC)  # TheSportsDB timestamps are UTC

    @pytest.mark.parametrize("status, expected", [("FT", "finished"), ("Match Finished", "finished"), ("AET", "finished"),
                                                  ("HT", "live"), ("2H", "live"), ("Postponed", "postponed")])
    def test_statuses(self, status, expected):
        assert parse_event(event(strStatus=status, intHomeScore="1", intAwayScore="0"), "1001").status == expected

    def test_finished_without_score_is_never_final(self):
        assert parse_event(event(strStatus="FT"), "1001").status != "finished"

    def test_outcome_from_our_side(self):
        home_win = parse_event(event(strStatus="FT", intHomeScore="1", intAwayScore="0"), "1001")
        assert home_win.outcome() == "win"
        as_away = parse_event(event(strStatus="FT", intHomeScore="1", intAwayScore="0"), "1002")
        assert as_away.outcome() == "loss"

    def test_empty_and_bad_responses(self):
        assert parse_response(json.dumps({"events": None}).encode(), "events", "1") == []
        with pytest.raises(ServiceError):
            parse_response(b"<html>", "events", "1")

    def test_describe_uses_local_time(self):
        text = describe(parse_event(event(), "1001"), ZoneInfo("Europe/Kyiv"))
        assert "10.10.2026, 18:00 (Europe/Kyiv)" in text and "Riverside FC — Hillside United" in text


class TestSchedule:
    s = Settings(timezone="Europe/Warsaw", sports_hour=9)

    def test_quiet_hours_across_midnight(self):
        tz = ZoneInfo("Europe/Warsaw")
        assert quiet_time(datetime(2026, 9, 5, 23, 30, tzinfo=tz), self.s)
        assert quiet_time(datetime(2026, 9, 5, 7, 59, tzinfo=tz), self.s)
        assert not quiet_time(datetime(2026, 9, 5, 12, 0, tzinfo=tz), self.s)

    def test_news_rhythm_and_jitter(self):
        days = [news_count(datetime(2026, 9, d).date(), self.s) for d in range(1, 5)]
        assert sorted(set(days)) == [1, 2] and days[0] != days[1]
        slot = news_plan(NOW, self.s, FAMILY)[0]
        assert 0 <= (slot.start - slot.start.replace(hour=11, minute=0)).total_seconds() <= 20 * 60
        assert news_plan(NOW, self.s, FAMILY) == news_plan(NOW, self.s, FAMILY)  # stable per chat

    def test_morning_announcement_window(self):
        match = parse_event(event(strTimestamp="2026-10-10T16:00:00"), "1001")  # 18:00 Warsaw
        at = lambda h: datetime(2026, 10, 10, h, 30, tzinfo=ZoneInfo("Europe/Warsaw"))  # noqa: E731
        assert not morning_due(match, at(8), self.s)
        assert morning_due(match, at(9), self.s) and morning_due(match, at(11), self.s)
        assert not morning_due(match, at(12), self.s)
        assert not morning_due(match, at(9) - timedelta(days=1), self.s)

    def test_result_only_while_fresh(self):
        done = parse_event(event(strStatus="FT", intHomeScore="2", intAwayScore="2"), "1001")
        assert result_due(done, done.kickoff + timedelta(hours=2))
        assert not result_due(done, done.kickoff + timedelta(days=2))  # never posted late as "new"


class NewsAndSportsPosting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(sports_enabled=True, sports_team_id="1001", timezone="Europe/Warsaw")

    async def asyncTearDown(self):
        await self.h.close()

    def feed(self, *items):
        self.h.news.cached = [a for raw in items for a in parse_feed(raw, NOW)]
        self.h.news.cached_at = float("inf")

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

    async def test_match_day_announcement_and_result(self):
        upcoming = parse_event(event(strTimestamp="2026-10-10T16:00:00"), "1001")
        self.h.sports.cached, self.h.sports.cached_at = (upcoming, None), float("inf")
        self.h.scheduler._sports_fetched = datetime(2026, 10, 10, 9, 5, tzinfo=ZoneInfo("Europe/Warsaw"))
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 9, 10, tzinfo=ZoneInfo("Europe/Warsaw")))
        self.assertIn("Match day today", self.h.last_text())
        self.assertIn("Go team!", self.h.last_text())
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 9, 20, tzinfo=ZoneInfo("Europe/Warsaw")))
        self.assertEqual(len(self.h.session.sent), 1)  # announced once

        final = replace(upcoming, status="finished", score="3:1")
        self.h.sports.cached = (None, final)
        self.h.scheduler._sports_fetched = datetime(2026, 10, 10, 20, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 20, 1, tzinfo=ZoneInfo("Europe/Warsaw")))
        self.assertIn("3:1", self.h.last_text())
        self.assertEqual(self.h.ai.sports_comment.call_args.args[0]["result_for_our_team"], "win")

    async def test_ai_failure_does_not_block_the_facts(self):
        self.h.ai.sports_comment.side_effect = ServiceError("ai_daily_limit")
        upcoming = parse_event(event(), "1001")
        text = await self.h.sports.morning_message(upcoming)
        self.assertIn("Riverside FC", text)
