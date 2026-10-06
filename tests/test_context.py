"""CHAT_CONTEXT: the pet sees what the family has just been writing (in memory only) and glances at the chat."""
from __future__ import annotations

import asyncio
import json
import random
import tempfile
import unittest
from pathlib import Path

import httpx
from aiogram.methods import SetMessageReaction

from petbot.ai import AIService, Glance
from petbot.config import Settings
from petbot.context import ChatContext
from petbot.db import Database
from petbot.handlers import handle
from petbot.notice import Noticer
from petbot.sentiment import Reader
from tests.support import FAMILY, OWNER, TOKEN, Harness

MOM = 555


class ContextTests(unittest.IsolatedAsyncioTestCase):
    async def open(self, **overrides):
        self.h = await Harness().open(quiet_start_hour=0, quiet_end_hour=0, **overrides)
        rng = random.Random(1)
        rng.random = lambda: 0.0  # always in the mood: the tests are about the rules, not luck
        self.noticer = Noticer(Reader(("Anna", "Mom")), None, delay=(0, 0), rng=rng, glance_pause=0)
        self.h.app.noticer = self.noticer
        await self.h.db.set_family_name(OWNER, "Anna")
        await self.h.db.set_family_name(MOM, "Mom")
        await self.h.db.allow_user(MOM, True)
        self.mid = 100

    async def asyncTearDown(self):
        await self.h.close()

    async def say(self, uid, text, **kwargs):
        self.mid += 1
        await handle(self.h.message(text, uid=uid, chat=FAMILY, mid=self.mid, **kwargs), self.h.app)
        await self.settle()

    async def settle(self):
        for _ in range(3):
            await asyncio.sleep(0)
            await asyncio.gather(*self.noticer.glance_tasks.values())

    async def test_off_by_default_the_ai_sees_no_family_messages(self):
        await self.open()
        await self.say(MOM, "Pushkin is gone")
        await self.say(OWNER, "Whiskers, give me your phone")
        self.assertIsNone(self.h.ai.chat.call_args.kwargs["chat_log"])
        self.h.ai.glance.assert_not_called()

    async def test_the_answer_knows_what_the_conversation_is_about(self):
        await self.open(chat_context=20)
        self.h.ai.glance.return_value = Glance()
        await self.say(MOM, "Where is Pushkin?")
        await self.say(OWNER, "He was demolished in 2022")
        await self.say(OWNER, "Whiskers, give me your phone")
        log = self.h.ai.chat.call_args.kwargs["chat_log"]
        self.assertIn("Mom: Where is Pushkin?", log)
        self.assertIn("Anna: He was demolished in 2022", log)
        self.assertNotIn("give me your phone", log)  # the current message is passed separately
        await self.say(MOM, "and?")
        self.assertIn("(you)", self.h.app.context.render(self.h.app.context.recent(FAMILY)))  # its own answer too

    async def test_glance_reacts_and_says_one_line_in_a_reply(self):
        await self.open(chat_context=20, chime_in_per_day=2)
        self.h.ai.glance.side_effect = lambda log, allow_say: Glance(((2, "😁"),), "Мяу", 2)
        await self.say(MOM, "Get up, no sleeping")
        await self.say(OWNER, "I'm up already")
        await self.say(MOM, "Off to class!")
        log = self.h.ai.glance.call_args.args[0]
        self.assertIn("#2 Anna: I'm up already", log)
        self.assertTrue(self.h.ai.glance.call_args.kwargs["allow_say"])
        [reaction] = [r for r in self.h.session.requests if isinstance(r, SetMessageReaction)]
        self.assertEqual((reaction.message_id, reaction.reaction[0].emoji), (102, "😁"))
        self.assertEqual(self.h.last_text(), "Мяу")
        self.assertEqual(self.h.session.sent[-1].reply_parameters.message_id, 102)
        await self.say(MOM, "a")
        await self.say(OWNER, "b")
        await self.say(MOM, "c")
        self.assertEqual(self.h.ai.glance.call_count, 1)  # not again within 15 minutes

    async def test_no_lines_over_the_daily_limit_or_about_air_raids(self):
        await self.open(chat_context=20, chime_in_per_day=0)
        self.h.ai.glance.return_value = Glance()
        for text in ("one", "two", "three"):
            await self.say(MOM, text)
        self.assertFalse(self.h.ai.glance.call_args.kwargs["allow_say"])
        self.noticer.glances.clear()
        self.h.app.settings = self.h.settings.__class__(**{**self.h.settings.__dict__, "chime_in_per_day": 3})
        for text in ("тревога", "все в укрытие", "сирена"):
            await self.say(MOM, text)
        self.assertEqual(self.h.ai.glance.call_count, 2)
        self.assertFalse(self.h.ai.glance.call_args.kwargs["allow_say"])

    async def test_forwarded_post_and_forget(self):
        await self.open(chat_context=20)
        from datetime import UTC, datetime

        from aiogram.types import Chat, MessageOriginChannel
        origin = MessageOriginChannel(date=datetime.now(UTC), chat=Chat(id=-77, type="channel", title="Daily News"),
                                      message_id=5)
        self.h.ai.glance.return_value = Glance()
        await self.say(MOM, "McDonald's removed 11 burgers", forward_origin=origin)
        line = self.h.app.context.recent(FAMILY)[0]
        self.assertEqual(line.text, "[forwarded from Daily News] McDonald's removed 11 burgers")
        await handle(self.h.message("/forget", uid=OWNER, chat=FAMILY, mid=999), self.h.app)
        self.assertEqual(self.h.app.context.recent(FAMILY), [])


def test_context_is_bounded_and_ages_out():
    clock = [0.0]
    context = ChatContext(3, "Whiskers", clock=lambda: clock[0])
    for n in range(5):
        context.add(1, n, "Anna", f"m{n}")
    assert [line.text for line in context.recent(1)] == ["m2", "m3", "m4"]
    context.add(1, 9, "Mom", "reply", reply_to=4)
    assert context.recent(1)[-1].text.startswith("(↩ #5)")
    clock[0] = 4 * 3600
    assert context.recent(1) == []
    assert context.render(context.recent(1), new_after=3) == ""  # all aged out
    clock[0] = 0
    context.add(1, 10, "Anna", "new one")
    assert context.render(context.recent(1), new_after=6).splitlines()[-2:] == ["--- NEW ---", "#7 Anna: new one"]
    assert not ChatContext(0, "Whiskers")  # CHAT_CONTEXT=0: off
    assert ChatContext(0, "x").add(1, 1, "a", "b") is None


class GlanceAITests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_answer_is_parsed_and_filtered(self):
        tmp = tempfile.TemporaryDirectory()
        db = Database(Path(tmp.name) / "bot.sqlite3")
        await db.open()
        sent = []

        def handler(request):
            sent.append(json.loads(request.content))
            answer = {"react": [{"n": 3, "emoji": "😁"}, {"n": 4, "emoji": "❤️"}, {"n": 5, "emoji": "🔥"}],
                      "say": "Мяу\n\nещё мяу", "reply_to": 3}
            return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(answer)}],
                                             "stop_reason": "end_turn"})
        try:
            settings = Settings(bot_token=TOKEN, api_key="sk-ant-secret-key-123456",
                                database_path=Path(tmp.name) / "bot.sqlite3")
            ai = AIService(settings, db, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
            result = await ai.glance("#3 Anna: haha", allow_say=True)
            self.assertEqual(result.reactions, ((3, "😁"), (4, "❤")))  # at most two, normalized
            self.assertEqual((result.say, result.reply_to), ("Мяу", 3))
            self.assertIn("json_schema", json.dumps(sent[0]["output_config"]))
            silent = await ai.glance("#3 Anna: haha", allow_say=False)
            self.assertEqual(silent.say, "")
            self.assertIn("empty string", sent[1]["system"])
        finally:
            await db.close()
            tmp.cleanup()
