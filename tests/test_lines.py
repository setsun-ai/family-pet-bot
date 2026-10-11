"""OWN_LINES=local: what the pet says unasked comes from ready lines, without the AI."""
import random
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from petbot import lines
from petbot.config import ConfigError, Settings
from petbot.core import preview_text
from petbot.handlers import handle
from petbot.i18n import set_language
from petbot.lines import Lines
from petbot.notice import Noticer
from petbot.sentiment import Reader
from petbot.spontaneous import Spontaneous
from tests.support import FAMILY, OWNER, TEAM, TOKEN, Harness, event
from tests.test_core import KEY, env

WARSAW = ZoneInfo("Europe/Warsaw")


def every_line():
    for table in (lines.SPONTANEOUS, lines.CHIME, lines.RESULT):
        for language in table.values():
            yield from (line for pool in language.values() for line in pool)
    for table in (lines.KICKOFF, lines.IGNORED):
        for pool in table.values():
            yield from pool


@pytest.mark.parametrize("line", list(every_line()))
def test_every_line_formats_and_is_short(line):
    text = line.format(team="Riverside", opponent="Hillside", place="away", score="2:1")
    assert text.strip() and len(text) <= 100 and "{" not in text


def test_both_languages_have_the_same_kinds():
    assert lines.SPONTANEOUS["ru"].keys() == lines.SPONTANEOUS["en"].keys()
    assert lines.CHIME["ru"].keys() == lines.CHIME["en"].keys()
    assert lines.RESULT["ru"].keys() == lines.RESULT["en"].keys() == {"win", "draw", "loss"}


def test_own_lines_setting(tmp_path):
    assert Settings.load(env(tmp_path, f"BOT_TOKEN={TOKEN}\nANTHROPIC_API_KEY={KEY}\n")).own_lines == "local"
    assert Settings.load(env(tmp_path, f"BOT_TOKEN={TOKEN}\nANTHROPIC_API_KEY={KEY}\nOWN_LINES=AI\n")).own_lines == "ai"
    with pytest.raises(ConfigError, match="OWN_LINES"):
        Settings.load(env(tmp_path, f"BOT_TOKEN={TOKEN}\nANTHROPIC_API_KEY={KEY}\nOWN_LINES=gpt\n"))


class Picking(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()

    async def asyncTearDown(self):
        await self.h.close()

    async def test_no_line_comes_back_too_soon_even_after_a_restart(self):
        pool = ["a", "b", "c", "d"]
        said = [await Lines(self.h.db, random.Random(n)).pick("test", pool) for n in range(12)]  # a new Lines each time
        for before, after in zip(said, said[1:], strict=False):
            self.assertNotEqual(before, after)
        self.assertEqual(set(said), set(pool))

    async def test_preview_does_not_use_up_a_line(self):
        self.h.app.mood = None
        text = await preview_text(self.h.app, "spontaneous")
        self.assertTrue(text)
        self.assertIsNone(await self.h.db.get("own_lines_recent"))
        self.h.ai.post.assert_not_called()

    async def test_russian_match_lines_keep_team_names_as_they_are(self):
        set_language("ru")
        try:
            line = await Lines().kickoff("Карпати", "Буря", at_home=False)
            result = await Lines().result("3:1", "win")
        finally:
            set_language("en")
        self.assertIn("Карпати", line)
        self.assertIn("Буря", line)
        self.assertIn("?", line)  # a question: the answers that follow count for 15 minutes
        self.assertIn("3:1", result)


class SpontaneousWithoutAI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(spontaneous_per_week=7, memory_enabled=True, timezone="Europe/Warsaw")
        self.h.scheduler.spontaneous = Spontaneous(self.h.settings, self.h.db, self.h.ai, self.h.delivery,
                                                   rng=random.Random(4))

    async def asyncTearDown(self):
        await self.h.close()

    async def test_a_ready_line_and_no_ai(self):
        await self.h.db.add_memory(FAMILY, "Anna: exam on Friday")  # only the AI could ask about it
        day = datetime(2026, 10, 7, 9, 0, tzinfo=WARSAW)
        now = day
        while now < day.replace(hour=22):
            self.h.scheduler.note_activity(FAMILY, now)
            await self.h.scheduler.spontaneous_tick(now)
            now += timedelta(minutes=10)
        [message] = self.h.session.sent
        pools = lines.SPONTANEOUS["en"]
        self.assertIn(message.text, [line for pool in pools.values() for line in pool])
        self.h.ai.post.assert_not_called()


class MatchesWithoutAI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from petbot.sports import parse_tsdb_event
        self.h = await Harness().open(sports_enabled=True, sports_style="casual")
        self.match = parse_tsdb_event(event(strTimestamp="2026-10-10T16:00:00"), "1001")

    async def asyncTearDown(self):
        await self.h.close()

    async def test_kickoff_result_and_sulking(self):
        sports = self.h.sports
        kickoff = await sports.kickoff_message(TEAM, self.match)
        self.assertIn("Riverside", kickoff)
        self.assertIn("?", kickoff)
        final = replace(self.match, status="finished", score="1:3", team_is_home=False)  # we won 3:1 away
        result = await sports.casual_result(TEAM, final)
        self.assertIn("3:1", result)
        self.assertIn(result, [line.format(score="3:1") for line in lines.RESULT["en"]["win"]])
        self.assertIn(await sports.ignored_result(TEAM, final), [line.format(score="3:1") for line in lines.IGNORED["en"]])
        self.h.ai.post.assert_not_called()


class ChimeWithoutAI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(chime_in_per_day=2)
        rng = random.Random(2)
        rng.random = lambda: 0.0
        self.h.app.noticer = Noticer(Reader(("Anna", "Mom")), None, delay=(0, 0), rng=rng)
        await self.h.db.allow_user(555, True)

    async def asyncTearDown(self):
        await self.h.close()

    async def test_a_laughing_conversation_gets_a_ready_line(self):
        for n, (uid, text) in enumerate([(OWNER, "hahaha"), (555, "lol"), (OWNER, "did you see that"),
                                         (555, "hahahaha"), (OWNER, "I can't 😂"), (555, "ok ok")]):
            await handle(self.h.message(text, uid=uid, chat=FAMILY, mid=300 + n), self.h.app)
        [message] = self.h.session.sent
        self.assertIn(message.text, lines.CHIME["en"]["laughing"])
        self.h.ai.post.assert_not_called()
