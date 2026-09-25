"""Weekly praise, birthdays, chat-style bursts and /preview."""
import json
import random
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from petbot.common import ServiceError
from petbot.config import Settings
from petbot.delivery import typing_pause
from petbot.family import FamilyService, Member, load_family, next_in_rotation, praise_due, week_key
from petbot.handlers import handle
from petbot.persona import fix_mixed_script, split_messages, tidy_messages
from tests.support import FAMILY, OWNER, Harness

WARSAW = ZoneInfo("Europe/Warsaw")
SATURDAY_NOON = datetime(2026, 10, 3, 12, 30, tzinfo=WARSAW)


class TestRotation:
    def test_five_people_in_five_weeks(self):
        names, state, rng = ["Mom", "Dad", "Anna", "Nick", "Art"], {}, random.Random(1)
        picked = []
        for _ in range(5):
            name, state = next_in_rotation(state, names, rng)
            picked.append(name)
        assert sorted(picked) == sorted(names)
        for _ in range(30):  # a new round never starts with the person praised last
            last = picked[-1]
            name, state = next_in_rotation(state, names, rng)
            if state["index"] == 1:
                assert name != last
            picked.append(name)

    def test_family_changes_restart_the_round(self):
        name, state = next_in_rotation({}, ["A", "B"], random.Random(2))
        name2, state2 = next_in_rotation(state, ["A", "B", "C"], random.Random(2))
        assert set(state2["order"]) == {"A", "B", "C"} and state2["index"] == 1 and name2 != name

    def test_week_and_window(self):
        assert week_key(SATURDAY_NOON) == "2026-W40"
        assert praise_due(SATURDAY_NOON, 5, 12) and not praise_due(SATURDAY_NOON, 4, 12)
        assert not praise_due(SATURDAY_NOON.replace(hour=15), 5, 12)


class TestFamilyFile:
    def test_load(self, tmp_path):
        path = tmp_path / "family.local.json"
        path.write_text(json.dumps({"members": [{"name": "Anna", "about": "draws", "birthday": "02-29"},
                                                {"name": ""}, {"name": "Dad"}]}), encoding="utf-8")
        assert load_family(path) == (Member("Anna", "draws", "02-29"), Member("Dad"))
        assert load_family(None) == ()

    def test_bad_birthday(self, tmp_path):
        path = tmp_path / "f.json"
        path.write_text(json.dumps({"members": [{"name": "A", "birthday": "31-12"}]}), encoding="utf-8")
        with pytest.raises(ServiceError):
            load_family(path)

    def test_leap_day_birthday(self):
        service = FamilyService(Settings(), None, None, (Member("Leap", birthday="02-29"), Member("Other", birthday="03-01")))
        assert [m.name for m in service.birthdays(datetime(2027, 2, 28))] == ["Leap"]
        assert [m.name for m in service.birthdays(datetime(2028, 2, 29))] == ["Leap"]
        assert service.birthdays(datetime(2028, 2, 28)) == []


class TestChatStyle:
    def test_bursts(self):
        text = tidy_messages("Мур!\n\n  Я тут.  \n \nИ ещё\nстрока\n\nлишнее\n\nсовсем лишнее", "", max_parts=3)
        assert split_messages(text) == ["Мур!", "Я тут.", "И ещё строка\nлишнее\nсовсем лишнее"]
        assert split_messages("one") == ["one"]

    def test_burst_length_limit(self):
        parts = split_messages(tidy_messages("A" * 60 + "\n\n" + "B" * 60 + "\n\n" + "C" * 60, "", 130))
        assert parts[:2] == ["A" * 60, "B" * 60] and sum(map(len, parts)) <= 131

    def test_mixed_script_repair(self):
        assert fix_mixed_script("Полісся 1:0 Криვბас") == "Полісся 1:0 Кривбас"
        assert fix_mixed_script("გამარჯობა") == "გამარჯობა"

    def test_typing_pause(self):
        assert typing_pause("hi") < 1 and typing_pause("x" * 500) == 4


class FamilyPosting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(praise_enabled=True, praise_weekday=5, praise_hour=12, timezone="Europe/Warsaw")

    async def asyncTearDown(self):
        await self.h.close()

    def texts(self):
        return [m.text for m in self.h.session.sent]

    async def test_weekly_praise_once_and_everybody_in_turn(self):
        self.h.ai.post.return_value = "Слышал, кто-то испёк пирог!\n\nМолодец"
        praised = []
        for week in range(3):
            now = SATURDAY_NOON + timedelta(weeks=week)
            await self.h.scheduler.family_tick(now)
            await self.h.scheduler.family_tick(now + timedelta(minutes=40))  # once a week
            task, data = self.h.ai.post.call_args.args
            self.assertEqual(task, "praise")
            praised.append(data["person"])
        self.assertEqual(sorted(praised), ["Anna", "Dad", "Mom"])
        self.assertEqual(len(self.h.session.sent), 6)  # 3 weeks x 2 short messages
        self.assertEqual(self.texts()[:2], ["Слышал, кто-то испёк пирог!", "Молодец"])
        self.assertIn("Слышал", json.dumps(await self.h.family._history(praised[0]), ensure_ascii=False))

    async def test_failed_praise_does_not_skip_the_person(self):
        self.h.session.failure = TelegramNetworkError(method=SendMessage(chat_id=FAMILY, text="x"), message="down")
        with self.assertRaises(ServiceError):  # the scheduler loop logs it and shows it in /status
            await self.h.scheduler.family_tick(SATURDAY_NOON)
        person = self.h.ai.post.call_args.args[1]["person"]
        self.assertEqual((await self.h.family.choose())[0].name, person)  # still their turn

    async def test_praise_off_and_wrong_day(self):
        await self.h.scheduler.family_tick(SATURDAY_NOON - timedelta(days=1))
        self.h.scheduler.settings = self.h.family.settings = self.h.settings.__class__(
            **{**self.h.settings.__dict__, "praise_enabled": False})
        await self.h.scheduler.family_tick(SATURDAY_NOON)
        self.assertFalse(self.h.session.sent)

    async def test_birthday(self):
        self.h.ai.post.return_value = "Анна, с днём рождения!\n\nМурр"
        await self.h.scheduler.family_tick(datetime(2027, 3, 14, 8, 30, tzinfo=WARSAW))  # before BIRTHDAY_HOUR
        self.assertFalse(self.h.session.sent)
        await self.h.scheduler.family_tick(datetime(2027, 3, 14, 10, 0, tzinfo=WARSAW))
        await self.h.scheduler.family_tick(datetime(2027, 3, 14, 11, 0, tzinfo=WARSAW))
        self.assertEqual(self.texts(), ["Анна, с днём рождения!", "Мурр"])
        self.assertEqual(self.h.ai.post.call_args.args[0], "birthday")

    async def test_preview_goes_only_to_the_owner(self):
        await handle(self.h.message("/preview praise Anna"), self.h.app)
        self.assertEqual(self.h.ai.post.call_args.args[1]["person"], "Anna")
        self.assertTrue(all(int(m.chat_id) == OWNER for m in self.h.session.sent))
        self.assertEqual(self.texts()[-2:], ["Go team!", "Meow"])
        self.assertIsNone(await self.h.db.get("praise_rotation"))  # a preview changes nothing
        await handle(self.h.message("/preview", uid=200, chat=FAMILY), self.h.app)
        self.assertEqual(self.h.ai.post.await_count, 1)
        await handle(self.h.message("/preview nonsense"), self.h.app)
        self.assertIn("/preview", self.h.last_text())

    async def test_burst_is_one_delivery(self):
        await self.h.delivery.send(FAMILY, "one\n\ntwo\n\nthree", [("praise", "w1")], reply_to=5)
        self.assertEqual(self.texts(), ["one", "two", "three"])
        replies = [m.reply_parameters for m in self.h.session.sent]
        self.assertIsNotNone(replies[0])
        self.assertEqual(replies[1:], [None, None])  # only the first message replies
        self.assertEqual([m.disable_notification for m in self.h.session.sent], [False, True, True])  # one sound
        self.assertFalse(await self.h.delivery.send(FAMILY, "again", [("praise", "w1")]))


class MoreCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(praise_enabled=True, sports_enabled=True, timezone="Europe/Warsaw")

    async def asyncTearDown(self):
        await self.h.close()

    async def test_preview_news_and_team(self):
        from datetime import UTC

        from petbot.news import parse_feed
        from tests.support import rss
        fresh = datetime.now(UTC) - timedelta(hours=1)
        self.h.news.cached = parse_feed(rss(date=f"{fresh:%a, %d %b %Y %H:%M:%S} GMT"), datetime.now(UTC))
        self.h.news.cached_at = float("inf")
        await handle(self.h.message("/preview news"), self.h.app)
        self.assertIn("Source", self.h.last_text())
        with self.assertRaises(ServiceError):  # the source is down in tests; the router replies "⚠️ ..."
            await handle(self.h.message("/preview riverside"), self.h.app)

    async def test_status_shows_teams_family_and_the_next_praise(self):
        await handle(self.h.message("/status"), self.h.app)
        first = self.h.last_text()
        self.assertIn("Riverside", first)
        await handle(self.h.message("/status"), self.h.app)
        self.assertEqual(first, self.h.last_text())  # the announced next person doesn't change between calls
        member, _ = await self.h.family.choose()
        self.assertIn(member.name, first)
        self.h.ai.post.return_value = "Молодец"
        await self.h.scheduler.family_tick(SATURDAY_NOON)  # Saturday picks the announced person
        self.assertEqual(self.h.ai.post.call_args.args[1]["person"], member.name)

    async def test_chat_reply_is_a_burst(self):
        self.h.ai.chat.return_value = "Аня!\n\nТы где была так долго"
        await handle(self.h.message("Whiskers, I'm home"), self.h.app)
        self.assertEqual([m.text for m in self.h.session.sent][-2:], ["Аня!", "Ты где была так долго"])
