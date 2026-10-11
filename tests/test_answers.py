"""Local decisions without AI: answers to the pet without a reply, links, and when the pet keeps quiet."""
import asyncio
import random
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from aiogram.methods import SetMessageReaction

from petbot import linkpeek
from petbot.handlers import handle
from petbot.notice import Noticer
from petbot.reactions import parse_rules
from petbot.sentiment import Reader
from petbot.spontaneous import Spontaneous
from tests.support import FAMILY, Harness

ANNA, DAD = 200, 300
WARSAW = ZoneInfo("Europe/Warsaw")


class AnswersToThePet(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        self.h.app.noticer = Noticer(None, None, delay=(0, 0), rng=random.Random(1))

    async def asyncTearDown(self):
        await self.h.close()

    def reactions(self):
        return [(r.message_id, r.reaction[0].emoji) for r in self.h.session.requests if isinstance(r, SetMessageReaction)]

    async def say(self, text, uid, mid, **kwargs):
        await handle(self.h.message(text, uid=uid, chat=FAMILY, mid=mid, **kwargs), self.h.app)

    async def test_yes_to_watching_gets_an_emoji_and_counts_as_watching(self):
        await self.h.delivery.send(FAMILY, "Riverside are on right now, watching? 🐈", [("sports-kickoff", "k1")])
        await self.say("Канешн", ANNA, 50)
        [(mid, emoji)] = self.reactions()
        self.assertEqual(mid, 50)
        self.assertIn(emoji, ("👍", "❤"))
        self.assertIn(FAMILY, self.h.scheduler.match_talk)  # no sulking after the final whistle
        self.h.ai.chat.assert_not_called()

    async def test_grumbling_after_the_pet_teased_someone(self):
        await self.say("Whiskers, stop begging", DAD, 60)  # the pet answers Dad
        await self.say("Отстань", DAD, 61)
        await self.say("ну правда", DAD, 62)  # one emoji per pet message, not under everything
        self.assertEqual([mid for mid, _ in self.reactions()], [61])
        self.assertIn(self.reactions()[0][1], ("😇", "🙈"))
        self.assertEqual(self.h.ai.chat.call_count, 1)  # only the addressed message

    async def test_not_an_answer(self):
        await self.say("Whiskers, stop begging", DAD, 70)
        await self.say("кто купил хлеб", ANNA, 71)  # the pet was talking to Dad, not Anna
        await self.h.delivery.send(FAMILY, "Purr. Sunny day", [("spontaneous", "d1")])
        await self.say("ага", ANNA, 72, reply_to_message=self.h.message("x", uid=DAD, chat=FAMILY, mid=69))
        await self.say("ага", ANNA, 73)  # the second message still counts...
        await self.h.delivery.send(FAMILY, "Mrr", [("spontaneous", "d2")])
        await self.say("пока", ANNA, 74)
        await self.say("ок", ANNA, 75)
        await self.say("ок", DAD, 76)  # ...the third doesn't
        self.h.delivery.last_sent[FAMILY] = replace(self.h.delivery.last_sent[FAMILY], at=time.monotonic() - 600)
        await self.h.delivery.send(FAMILY, "Mrr?", [("spontaneous", "d3")])
        self.h.delivery.last_sent[FAMILY] = replace(self.h.delivery.last_sent[FAMILY], at=time.monotonic() - 1000)
        await self.say("ок", DAD, 77)  # too late, even after a question
        self.assertEqual([mid for mid, _ in self.reactions()], [73, 74])

    async def test_laughing_and_no(self):
        noticer = self.h.app.noticer
        self.assertIn(noticer._answer_emoji("ахахах", None), ("😁", "🤣"))
        self.assertIn(noticer._answer_emoji("нет, не буду", None), ("😢", "🤷"))
        self.assertIn(noticer._answer_emoji("Пошли гулять", None), ("❤", "🤗"))


# --- links ---------------------------------------------------------------------------------------

PAGE = b"""<html><head><title>Fallback</title>
<meta property="og:title" content="Cafe closes early &mdash; Example News">
<meta content="The cafe closes at noon because of the shelling" property="og:description">
</head><body>...</body></html>"""


async def public_resolver(host):
    return ["93.184.216.34"]


async def local_resolver(host):
    return ["192.168.1.1"]


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_first_link_and_summary():
    assert linkpeek.first_link("look: https://example.org/news/1. wow") == "https://example.org/news/1"
    assert linkpeek.first_link("no links here") is None
    assert linkpeek.summary(PAGE.decode()) == ("Cafe closes early — Example News. "
                                               "The cafe closes at noon because of the shelling")
    assert linkpeek.summary("<title> Just a title </title>") == "Just a title"


@pytest.mark.parametrize("url, resolver, ok", [
    ("https://example.org/a", public_resolver, True),
    ("http://example.org/a", public_resolver, False),  # https only
    ("https://example.org:8443/a", public_resolver, False),
    ("https://router.example/a", local_resolver, False),  # never the local network
    ("https://user@example.org/a", public_resolver, False),
])
def test_only_public_https(url, resolver, ok):
    assert asyncio.run(linkpeek.public(url, resolver)) is ok


def test_peek_reads_the_head_follows_safe_redirects_only():
    def handler(request):
        if request.url.path == "/short":
            return httpx.Response(301, headers={"location": "https://example.org/article"})
        if request.url.path == "/to-router":
            return httpx.Response(302, headers={"location": "https://192.168.1.1/admin"})
        if request.url.path == "/image":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")
        return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=PAGE)

    async def mixed(host):
        return ["192.168.1.1"] if host == "192.168.1.1" else ["93.184.216.34"]

    async def run():
        async with client(handler) as http:
            assert "shelling" in await linkpeek.peek("https://example.org/short", client=http, resolver=public_resolver)
            assert await linkpeek.peek("https://example.org/to-router", client=http, resolver=mixed) is None
            assert await linkpeek.peek("https://example.org/image", client=http, resolver=public_resolver) is None
            assert await linkpeek.peek("https://youtu.be/abc", client=http, resolver=public_resolver) is None

    asyncio.run(run())


class LinkReactions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        self.pages = {}

        async def peek(url):
            return self.pages.get(url)
        self.h.app.noticer = Noticer(Reader(), None, delay=(0, 0), rng=random.Random(1), peek=peek)

    async def asyncTearDown(self):
        await self.h.close()

    def reactions(self):
        return [(r.message_id, r.reaction[0].emoji) for r in self.h.session.requests if isinstance(r, SetMessageReaction)]

    async def test_bad_news_behind_a_link_gets_a_quiet_tear(self):
        self.pages["https://example.org/cafe"] = ("Кав'ярня сьогодні зачиняється раніше. Через обстріл "
                                                     "немає світла")
        self.pages["https://example.org/cats"] = "Кот выиграл конкурс красоты!"  # someone else's success: no cheer
        await handle(self.h.message("Кав'ярня зачиняється https://example.org/cafe", uid=ANNA, chat=FAMILY, mid=80),
                     self.h.app)
        await handle(self.h.message("https://example.org/cats", uid=ANNA, chat=FAMILY, mid=81), self.h.app)
        await handle(self.h.message("https://example.org/missing", uid=ANNA, chat=FAMILY, mid=82), self.h.app)
        self.assertEqual(self.reactions(), [(80, "😢")])
        self.assertFalse(self.h.session.sent)
        self.assertIn(FAMILY, self.h.scheduler.hushed)

    async def test_off(self):
        self.h.app.noticer.peek = None
        self.pages["https://example.org/cafe"] = "Ночной обстрел, все в укрытии"
        await handle(self.h.message("https://example.org/cafe", uid=ANNA, chat=FAMILY, mid=83), self.h.app)
        self.assertEqual(self.reactions(), [])


# --- the pet keeps quiet after news --------------------------------------------------------------

class QuietAfterNews(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(spontaneous_per_week=7, timezone="Europe/Warsaw", own_lines="ai")
        self.h.ai.post.return_value = "Мрр, какой сонный день"
        self.h.scheduler.spontaneous = Spontaneous(self.h.settings, self.h.db, self.h.ai, self.h.delivery,
                                                   rng=random.Random(4))
        self.day = datetime(2026, 10, 7, 0, 0, tzinfo=WARSAW)

    async def asyncTearDown(self):
        await self.h.close()

    async def run_day(self, **activity):
        now = self.day.replace(hour=9)
        while now < self.day.replace(hour=22):
            self.h.scheduler.note_activity(FAMILY, now, **activity)
            await self.h.scheduler.spontaneous_tick(now)
            now += timedelta(minutes=10)

    async def test_no_small_talk_under_links_forwards_or_bad_news(self):
        await self.run_day(text="https://example.org/news")
        await self.run_day(forwarded=True)
        self.assertFalse(self.h.session.sent)

    async def test_it_waits_and_then_speaks(self):
        self.h.scheduler.hush(FAMILY, self.day.replace(hour=9))  # bad news in the morning, then ordinary chat
        await self.run_day(text="ну что, обедаем?")
        [message] = self.h.session.sent
        self.assertEqual(message.text, "Мрр, какой сонный день")


# --- edited messages -----------------------------------------------------------------------------

class EditedMessages(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        rules = parse_rules({"cooldown_minutes": 0, "rules": [{"any": ["хлеб"], "react": ["👀"]}]})
        self.h.app.noticer = Noticer(None, rules, delay=(0, 0), rng=random.Random(1))

    async def asyncTearDown(self):
        await self.h.close()

    def reactions(self):
        return [r for r in self.h.session.requests if isinstance(r, SetMessageReaction)]

    def edit(self, text, mid, minutes_ago=0):
        return self.h.message(text, uid=ANNA, chat=FAMILY, mid=mid, edit_date=int(time.time()) - minutes_ago * 60)

    async def test_adding_the_name_in_an_edit_gets_one_answer(self):
        await handle(self.h.message("кто купил хлеб", uid=ANNA, chat=FAMILY, mid=90), self.h.app)
        self.assertEqual(len(self.reactions()), 1)
        await handle(self.edit("Whiskers, кто купил хлеб", 90), self.h.app, edited=True)
        await handle(self.edit("Whiskers, кто купил хлеб?", 90), self.h.app, edited=True)  # edited once more
        self.assertEqual(self.h.ai.chat.call_count, 1)
        self.assertEqual(self.h.session.sent[0].reply_parameters.message_id, 90)
        self.assertEqual(len(self.reactions()), 1)  # no second emoji for the edit

    async def test_edits_that_change_nothing_for_the_pet(self):
        await handle(self.edit("кто купил хлеб!!", 91), self.h.app, edited=True)  # still not addressed
        await handle(self.edit("Whiskers, привет", 92, minutes_ago=10), self.h.app, edited=True)  # too late
        await handle(self.edit("/news", 93), self.h.app, edited=True)  # commands are not re-run
        self.h.ai.chat.assert_not_called()
        self.assertEqual((self.reactions(), self.h.session.sent), ([], []))


# --- how welcome the pet's own messages are ------------------------------------------------------

class Welcome(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(spontaneous_per_week=7)
        self.pet = Spontaneous(self.h.settings, self.h.db, self.h.ai, self.h.delivery, rng=random.Random(1))

    async def asyncTearDown(self):
        await self.h.close()

    async def posts(self, answered):
        ids = list(range(500, 500 + len(answered)))
        await self.h.db.remember_sent(FAMILY, ids, "spontaneous")
        for mid, yes in zip(ids, answered, strict=True):
            if yes:
                await self.h.db.add_feedback(FAMILY, mid, ANNA, "😁")

    async def test_too_early_to_tell(self):
        await self.posts([False, False])
        self.assertEqual(await self.pet.welcome(FAMILY), 1.0)

    async def test_nobody_answers_so_it_speaks_up_less(self):
        await self.posts([False, False, False])
        self.assertAlmostEqual(await self.pet.welcome(FAMILY), 0.4)
        self.pet.rng.random = lambda: 0.5  # would talk today with the full chance
        plan = await self.pet._plan(datetime(2026, 10, 7, 9, 0, tzinfo=WARSAW), FAMILY)
        self.assertFalse(plan["chatty"])

    async def test_answers_bring_it_back(self):
        await self.posts([False, True, False, True])
        self.assertEqual(await self.pet.welcome(FAMILY), 1.0)

    async def test_somewhere_in_between(self):
        await self.posts([True, False, False, False])
        self.assertAlmostEqual(await self.pet.welcome(FAMILY), 0.7)
