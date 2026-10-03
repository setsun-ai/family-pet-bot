"""The pet's inner life: mood, good/bad news without AI, memory, speaking up on its own, and feedback."""
import json
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from aiogram.methods import SendMessage, SetMessageReaction
from aiogram.types import Chat, MessageReactionUpdated, ReactionTypeEmoji, User

from petbot import sentiment
from petbot.ai import AIService
from petbot.config import Settings
from petbot.core import memory_text, stats_text, status_text
from petbot.db import Database
from petbot.handlers import handle, reaction_update
from petbot.i18n import MESSAGES, PROMPTS, set_language
from petbot.mood import MOODS, MoodService, next_mood
from petbot.notice import Noticer
from petbot.phrases import CHEERS, cheer
from petbot.reactions import TELEGRAM_REACTIONS, parse_rules
from petbot.sentiment import Reader
from petbot.spontaneous import Spontaneous
from tests.support import FAMILY, OWNER, TOKEN, Harness

WARSAW = ZoneInfo("Europe/Warsaw")
NAMES = ("Аня", "Мама", "Папа", "Миша", "Саша")


# --- mood --------------------------------------------------------------------------------------

class TestMoods:
    def test_every_mood_has_texts_and_valid_reactions(self):
        for name, mood in MOODS.items():
            assert all(lang in PROMPTS[f"mood_{name}"] for lang in ("en", "ru"))
            assert all(lang in MESSAGES[f"mood_name_{name}"] for lang in ("en", "ru"))
            assert set(mood.swap.values()) <= TELEGRAM_REACTIONS, name
            for language in ("ru", "en"):
                assert name in CHEERS[language], (language, name)

    def test_next_mood_changes_and_follows_the_clock(self):
        rng = random.Random(3)
        night = datetime(2026, 10, 3, 3, 0, tzinfo=WARSAW)
        picks = [next_mood(night, "playful", rng) for _ in range(400)]
        assert "playful" not in picks
        assert picks.count("sleepy") > picks.count("royal") * 2  # sleepy peaks at night
        assert len(set(picks)) >= 6  # but anything can happen


class MoodServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(timezone="Europe/Warsaw")
        self.mood = MoodService(self.h.settings, self.h.db, random.Random(1))

    async def asyncTearDown(self):
        await self.h.close()

    async def test_mood_lasts_then_changes_and_survives_a_restart(self):
        now = datetime(2026, 10, 3, 12, 0, tzinfo=WARSAW)
        first = await self.mood.current(now)
        assert await self.mood.current(now + timedelta(minutes=20)) == first
        restarted = MoodService(self.h.settings, self.h.db, random.Random(99))
        assert await restarted.current(now + timedelta(minutes=20)) == first  # saved in SQLite
        later = await self.mood.current(now + timedelta(hours=7))  # no mood lasts more than 6 hours
        assert later != first

    async def test_force_and_status(self):
        self.h.app.mood = self.mood
        await self.mood.force("grumpy")
        await handle(self.h.message("/status"), self.h.app)
        self.assertIn("grumpy", self.h.last_text())
        await handle(self.h.message("/mood nonsense"), self.h.app)
        self.assertIn("Unknown mood", self.h.last_text())
        await handle(self.h.message("/mood cuddly"), self.h.app)
        self.assertEqual((await self.mood.current()).name, "cuddly")

    async def test_grumpy_reactions_are_swapped(self):
        rules = parse_rules({"cooldown_minutes": 0, "rules": [{"any": ["soup"], "react": ["❤"]}]})
        grumpy = {rules.pick(1, "soup", None, MOODS["grumpy"]) for _ in range(50)}
        assert grumpy == {"🗿", None}  # swapped, and a grumpy cat sometimes doesn't bother at all
        assert {rules.pick(1, "soup", None, MOODS["cuddly"]) for _ in range(50)} == {"❤"}


# --- good and bad news without AI ------------------------------------------------------------------

@pytest.mark.parametrize("text, author, kind, target", [
    ("Я сдала экзамен!", "Аня", "achievement", None),
    ("Ура, получилось!!! 🎉", "Миша", "achievement", None),
    ("Саша поймал щуку", "Папа", "achievement", "Саша"),
    ("Мама приготовила борщ", "Аня", "achievement", "Мама"),
    ("получила пятёрку", "Аня", "achievement", None),
    ("похудела на 2 кг!", "Аня", "achievement", None),
    ("Нет, я сдала!", "Аня", "achievement", None),
    ("Не сдала(", "Аня", "sad", None),
    ("Аня заболела", "Мама", "sad", "Аня"),
    ("сломался ноутбук", "Миша", "sad", None),
    ("I passed the exam!", "Anna", "achievement", None),
    ("didn't pass", "Anna", "sad", None),
    ("Завтра сдаю экзамен", "Аня", None, None),  # a plan
    ("хочу похудеть", "Аня", None, None),
    ("если поступлю, будет круто", "Аня", None, None),
    ("Сдала?", "Мама", None, None),  # a question
    ("не болею, всё ок", "Папа", None, None),
    ("болею за Полесье", "Папа", None, None),  # football, not illness
    ("ахахаха он упал с холодильника", "Аня", None, None),  # laughing: not bad news
    ("У нас пока летают только реактивные дроны. Сегодня 5 прилётов", "Мама", "worry", None),
    ("опять тревога", "Папа", "worry", None),
    ("отбой тревоги", "Папа", None, None),
    # the family's dark humour is not news
    ("Доехал и никого не задавил", "Саша", None, None),
    ("Шкаф вывозили, чуть не убились", "Мама", None, None),
    ("Соседей ограбил", "Саша", None, None),
    ("Тогда я брата чуть не угробил", "Саша", None, None),
    ("Тебя за такое посадят", "Мама", None, None),
    ("получила посылку", "Аня", None, None),
    ("всем привет", "Мама", None, None),
])
def test_reader(text, author, kind, target):
    feeling = Reader(NAMES).read(text, author)
    assert (feeling.kind, feeling.target) == (kind, target) if kind else feeling is None


def test_reader_without_pymorphy(monkeypatch):
    monkeypatch.setattr(sentiment, "_morph", lambda: None)
    sentiment._forms.cache_clear()
    try:
        assert Reader(NAMES).read("Сдала экзамен!", "Аня").kind == "achievement"
        assert Reader(NAMES).read("заболела", "Мама").kind == "sad"
    finally:
        sentiment._forms.cache_clear()


def test_cheer_lines():
    set_language("ru")
    try:
        rng = random.Random(1)
        for _ in range(50):
            line = cheer(None, about_someone_else=False, mood="grumpy", rng=rng)
            assert "{name}" not in line and line
        assert "Саша" in cheer("Саша", about_someone_else=True, mood=None, rng=rng)
        for table in CHEERS.values():
            assert all("{name}" in line for line in table["them"])  # praise of someone else always names them
    finally:
        set_language("en")


class NoticingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        self.h.app.noticer = Noticer(Reader(("Anna", "Mom")), None, delay=(0, 0), rng=random.Random(1))

    async def asyncTearDown(self):
        await self.h.close()

    def reactions(self):
        return [r.reaction[0].emoji for r in self.h.session.requests if isinstance(r, SetMessageReaction)]

    async def test_achievement_gets_a_cheer_as_a_reply_then_cools_down(self):
        await handle(self.h.message("I passed the exam!", chat=FAMILY, mid=20), self.h.app)
        [sent] = self.h.session.sent
        self.assertEqual(sent.reply_parameters.message_id if sent.reply_parameters else sent.reply_to_message_id, 20)
        self.h.ai.chat.assert_not_called()  # local phrases, no AI
        await handle(self.h.message("We won the match!", chat=FAMILY, mid=21), self.h.app)
        self.assertEqual(len(self.h.session.sent), 1)  # one cheer per person in 3 hours
        self.assertTrue(self.reactions())  # the second achievement gets a 🔥-like reaction instead

    async def test_bad_news_gets_a_sad_reaction_whatever_the_mood(self):
        self.h.app.mood = MoodService(self.h.settings, self.h.db)
        await self.h.app.mood.force("grumpy")
        await handle(self.h.message("Mom is sick", chat=FAMILY, mid=22), self.h.app)
        [emoji] = self.reactions()
        self.assertIn(emoji, {"😢", "💔", "🤗", "😭"})
        self.assertFalse(self.h.session.sent)

    async def test_air_raid_gets_support_and_blocks_chiming_in(self):
        self.h.app.noticer.reader = Reader(("Anna", "Mom"))
        await handle(self.h.message("air raid sirens again, drones everywhere", chat=FAMILY, mid=24), self.h.app)
        self.assertEqual(self.reactions(), ["😢"])  # a quiet tear: not sobbing, not praying hands
        self.assertFalse(self.h.session.sent)

    async def test_private_chats_and_addressed_messages_are_not_noticed(self):
        await handle(self.h.message("I passed the exam!", chat=OWNER, mid=23), self.h.app)
        self.h.ai.chat.assert_called_once()  # a normal conversation
        self.assertEqual(self.reactions(), [])


# --- memory ---------------------------------------------------------------------------------------

class MemoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "bot.sqlite3")
        await self.db.open()
        self.requests = []
        self.answer = {"reply": "Мрр, удачи", "remember": "Anna: chemistry exam on Friday 10.10"}
        self.settings = Settings(bot_token=TOKEN, api_key="sk-ant-secret-key-123456", memory_enabled=True,
                                 database_path=Path(self.tmp.name) / "bot.sqlite3")

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    def service(self):
        def handler(request):
            self.requests.append(json.loads(request.content))
            text = self.answer if isinstance(self.answer, str) else json.dumps(self.answer, ensure_ascii=False)
            return httpx.Response(200, json={"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"})
        return AIService(self.settings, self.db, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def test_a_fact_is_kept_from_the_same_answer_and_used_later(self):
        ai = self.service()
        self.assertEqual(await ai.chat([], "exam on Friday!", "Anna", chat_id=FAMILY), "Мрр, удачи")
        self.assertEqual(len(self.requests), 1)  # no extra AI call for memory
        self.assertIn("json_schema", json.dumps(self.requests[0]["output_config"]))
        [fact] = await self.db.memories(FAMILY)
        self.assertIn("chemistry exam", fact["fact"])
        self.answer = {"reply": "Ага", "remember": ""}
        await ai.chat([], "hi", "Anna", chat_id=FAMILY)
        self.assertIn("chemistry exam", self.requests[1]["system"])
        self.assertEqual(len(await self.db.memories(FAMILY)), 1)  # nothing new to remember
        self.assertEqual(await self.db.memories(-5), [])  # another chat knows nothing

    async def test_plain_text_answer_still_works(self):
        self.answer = "Просто мрр"
        self.assertEqual(await self.service().chat([], "hi", "Anna", chat_id=FAMILY), "Просто мрр")

    async def test_memory_off_means_plain_chat(self):
        self.settings = replace(self.settings, memory_enabled=False)
        self.answer = "Мрр"
        await self.service().chat([], "hi", "Anna", chat_id=FAMILY)
        self.assertNotIn("output_config", self.requests[0])


class MemoryCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(memory_enabled=True)

    async def asyncTearDown(self):
        await self.h.close()

    async def test_list_delete_and_forget(self):
        await self.h.db.add_memory(FAMILY, "Anna: exam on Friday")
        await self.h.db.add_memory(FAMILY, "Dad: bought a bike")
        text = await memory_text(self.h.app, "")
        self.assertIn("exam on Friday", text)
        first = (await self.h.db.memories(FAMILY))[0]["id"]
        await handle(self.h.message(f"/memory del {first}"), self.h.app)
        self.assertEqual([m["fact"] for m in await self.h.db.memories(FAMILY)], ["Dad: bought a bike"])
        await handle(self.h.message("/forget", chat=FAMILY), self.h.app)
        self.assertEqual(await self.h.db.memories(FAMILY), [])


# --- speaking up on its own -------------------------------------------------------------------------

class SpontaneousTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(spontaneous_per_week=7, memory_enabled=True, timezone="Europe/Warsaw")
        self.h.ai.post.return_value = "Ну что, как экзамен?"
        self.h.scheduler.spontaneous = Spontaneous(self.h.settings, self.h.db, self.h.ai, self.h.delivery,
                                                   rng=random.Random(4))
        self.day = datetime(2026, 10, 7, 0, 0, tzinfo=WARSAW)

    async def asyncTearDown(self):
        await self.h.close()

    async def run_day(self, active: bool):
        now = self.day.replace(hour=9)
        while now < self.day.replace(hour=22):
            if active:
                self.h.scheduler.note_activity(FAMILY, now)
            await self.h.scheduler.spontaneous_tick(now)
            now += timedelta(minutes=10)

    async def test_only_when_someone_is_around_and_once_a_day(self):
        await self.run_day(active=False)
        self.assertFalse(self.h.session.sent)  # nobody in the chat: the pet doesn't talk to an empty room
        await self.run_day(active=True)
        self.assertEqual([m.text for m in self.h.session.sent], ["Ну что, как экзамен?"])
        self.assertEqual(self.h.ai.post.call_args.args[0], "spontaneous")

    async def test_asks_about_something_it_remembers(self):
        await self.h.db.add_memory(FAMILY, "Anna: exam on Friday")
        self.h.scheduler.spontaneous.rng = random.Random(0)
        for _ in range(5):  # memory is used most of the time
            await self.h.db.set("spontaneous_plan", "")
            await self.h.db.connection.execute("DELETE FROM deliveries")
            await self.run_day(active=True)
        facts = [call.args[1]["memory"] for call in self.h.ai.post.call_args_list]
        self.assertTrue(any(f and "exam on Friday" in f for f in facts))

    async def test_never_with_zero_per_week(self):
        self.h.scheduler.spontaneous.settings = replace(self.h.settings, spontaneous_per_week=0)
        await self.run_day(active=True)
        self.assertFalse(self.h.session.sent)


# --- feedback --------------------------------------------------------------------------------------

class FeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()

    async def asyncTearDown(self):
        await self.h.close()

    def reacted(self, message_id, emojis, uid=OWNER):
        return MessageReactionUpdated(chat=Chat(id=FAMILY, type="supergroup"), message_id=message_id,
                                      user=User(id=uid, is_bot=False, first_name="A"), date=datetime.now(UTC),
                                      old_reaction=[], new_reaction=[ReactionTypeEmoji(emoji=e) for e in emojis])

    async def test_reactions_and_replies_to_the_pets_posts_are_counted(self):
        await self.h.delivery.send(FAMILY, "Story one\n\nand a remark", [("news", "a1")])
        await self.h.delivery.send(FAMILY, "Go team!", [("sports-result", "m1")])
        news_id = self.h.session.sent[0]
        first = 1001  # RecordingSession numbers messages from 1001
        await reaction_update(self.reacted(first, ["😁"]), self.h.app)
        await reaction_update(self.reacted(first, ["😁", "❤"], uid=555), self.h.app)
        await reaction_update(self.reacted(first, ["❤"], uid=555), self.h.app)  # changed their mind
        await reaction_update(self.reacted(4242, ["😁"]), self.h.app)  # not the pet's message: ignored
        reply = self.h.message("Whiskers, lol", chat=FAMILY, mid=60,
                               reply_to_message=self.h.message("x", uid=self.h.app.me.id, chat=FAMILY, mid=first))
        await handle(reply, self.h.app)
        text = await stats_text(self.h.app)
        self.assertIn("news: 2 msg, reactions 2, replies 1", text)
        self.assertIn("match results: 1 msg, reactions 0", text)
        self.assertIn("😁×1", text)
        self.assertIsInstance(news_id, SendMessage)

    async def test_status_still_works_without_mood(self):
        self.h.app.mood = None
        self.assertIn("Whiskers", await status_text(self.h.app))


# --- matches without statistics ------------------------------------------------------------------

class CasualMatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from petbot.sports import parse_tsdb_event
        from tests.support import TEAM, event
        self.h = await Harness().open(sports_enabled=True, sports_style="casual", timezone="Europe/Warsaw")
        self.state = self.h.sports.state[TEAM.key]
        self.state.upcoming = parse_tsdb_event(event(strTimestamp="2026-10-10T16:00:00"), "1001")  # 18:00 in Warsaw
        self.state.fetched = datetime(2026, 10, 10, 17, 40, tzinfo=WARSAW)

    async def asyncTearDown(self):
        await self.h.close()

    async def kickoff(self):
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 9, 30, tzinfo=WARSAW))
        self.assertFalse(self.h.session.sent)  # no morning preview with tables
        self.state.fetched = datetime(2026, 10, 10, 17, 50, tzinfo=WARSAW)
        self.h.ai.post.return_value = "Полісся вже грає, дивимось?"
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 17, 55, tzinfo=WARSAW))
        await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 18, 5, tzinfo=WARSAW))
        self.assertEqual([m.text for m in self.h.session.sent], ["Полісся вже грає, дивимось?"])
        task, data = self.h.ai.post.call_args.args
        self.assertEqual(task, "match_kickoff")
        self.assertNotIn("table_position", data)

    async def final_whistle(self, text="Мы их сделали!"):
        if self.state.upcoming:
            self.state.last, self.state.upcoming = replace(self.state.upcoming, status="finished", score="1:3",
                                                           team_is_home=False), None
        self.state.fetched = datetime(2026, 10, 10, 20, 5, tzinfo=WARSAW)
        self.h.ai.post.return_value = text
        for minute in range(10, 20, 3):  # several ticks: the decision is made once
            await self.h.scheduler.sports_tick(datetime(2026, 10, 10, 20, minute, tzinfo=WARSAW))

    async def test_someone_answered_so_a_one_line_result(self):
        await self.kickoff()
        kickoff_id = 1001
        answer = self.h.message("Whiskers, смотрю!", chat=FAMILY, mid=70,
                                reply_to_message=self.h.message("x", uid=self.h.app.me.id, chat=FAMILY, mid=kickoff_id))
        await handle(answer, self.h.app)
        await self.final_whistle()
        task, data = self.h.ai.post.call_args.args
        self.assertEqual((task, data["score_ours_first"], data["result_for_our_team"]), ("match_result_casual", "3:1", "win"))
        self.assertEqual(self.h.session.sent[-1].text, "3:1. Мы их сделали!")  # the score is never missing
        self.assertNotIn("goals", data)

    async def test_talk_about_the_match_counts_as_an_answer(self):
        await self.kickoff()
        await handle(self.h.message("ого какой гол", chat=FAMILY, mid=71), self.h.app)
        await self.final_whistle()
        self.assertEqual(self.h.ai.post.call_args.args[0], "match_result_casual")

    async def test_nobody_answered_so_it_sulks(self):
        await self.kickoff()
        await handle(self.h.message("кто купил хлеб", chat=FAMILY, mid=72), self.h.app)  # unrelated chatter
        self.h.scheduler.rng = random.Random(1)
        self.h.scheduler.rng.random = lambda: 0.1  # the 70 % branch: an ironic line
        await self.final_whistle("Ага. 3:1, если кому интересно")
        self.assertEqual(self.h.ai.post.call_args.args[0], "match_result_ignored")
        self.assertEqual([m.text for m in self.h.session.sent][-1], "Ага. 3:1, если кому интересно")
        self.assertEqual(len(self.h.session.sent), 2)

    async def test_nobody_answered_so_it_may_stay_silent(self):
        await self.kickoff()
        self.h.scheduler.rng.random = lambda: 0.9  # the 30 % branch: silence, decided once
        await self.final_whistle()
        self.assertEqual(len(self.h.session.sent), 1)
        self.h.scheduler.rng.random = lambda: 0.1
        await self.final_whistle()  # later ticks don't re-roll the dice
        self.assertEqual(len(self.h.session.sent), 1)


# --- chiming into a lively conversation -------------------------------------------------------------

class ChimeInTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(chime_in_per_day=2)
        rng = random.Random(2)
        rng.random = lambda: 0.0  # always in the mood, so the test is about the rules, not luck
        self.h.app.noticer = Noticer(Reader(("Anna", "Mom")), None, delay=(0, 0), rng=rng)
        self.h.ai.post.return_value = "Мяу, я тоже хочу смеяться"
        await self.h.db.set_family_name(OWNER, "Anna")
        await self.h.db.set_family_name(555, "Mom")
        await self.h.db.allow_user(555, True)

    async def asyncTearDown(self):
        await self.h.close()

    async def chat(self, lines):
        for n, (uid, text) in enumerate(lines):
            await handle(self.h.message(text, uid=uid, chat=FAMILY, mid=100 + n + len(self.h.session.requests)), self.h.app)

    async def test_joins_a_laughing_conversation_with_only_the_vibe(self):
        await self.chat([(OWNER, "hahaha"), (555, "lol"), (OWNER, "did you see that"), (555, "hahahaha"),
                         (OWNER, "I can't 😂"), (555, "ok ok")])
        task, data = self.h.ai.post.call_args.args
        self.assertEqual((task, data["vibe"], sorted(data["people"])), ("chime_in", "laughing", ["Anna", "Mom"]))
        self.assertNotIn("did you see", json.dumps(data))  # the messages never reach the AI
        self.assertEqual([m.text for m in self.h.session.sent], ["Мяу, я тоже хочу смеяться"])
        await self.chat([(OWNER, "hahaha"), (555, "lol"), (OWNER, "haha"), (555, "hahaha"), (OWNER, "😂"), (555, "lol")])
        self.assertEqual(len(self.h.session.sent), 1)  # at most once in 3 hours

    async def test_quiet_or_one_person_or_no_vibe_means_no_chime(self):
        await self.chat([(OWNER, "hahaha")] * 7)  # one person laughing alone
        self.h.app.noticer.recent.clear()
        await self.chat([(OWNER, "ok"), (555, "ok"), (OWNER, "ok"), (555, "ok"), (OWNER, "ok"), (555, "ok")])
        self.h.ai.post.assert_not_called()

    async def test_off_by_default(self):
        self.h.app.settings = replace(self.h.settings, chime_in_per_day=0)
        await self.chat([(OWNER, "hahaha"), (555, "lol"), (OWNER, "haha"), (555, "hahaha"), (OWNER, "😂"), (555, "lol")])
        self.h.ai.post.assert_not_called()


def test_important_rules_skip_the_cooldown():
    rules = parse_rules({"cooldown_minutes": 60, "rules": [
        {"author": ["Anna"], "any": ["not going"], "react": ["😢"], "important": True},
        {"any": ["morning"], "react": ["🥱"]}]})
    assert rules.pick(1, "good morning", None) == "🥱"
    assert rules.pick(1, "good morning", None) is None  # a trivial one waits
    assert rules.pick(1, "not going to the gym", "Anna") == "😢"  # Anna's sad news doesn't


class CheerPerPersonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        rng = random.Random(1)
        rng.random = lambda: 0.0
        self.clock = [0.0]
        self.h.app.noticer = Noticer(Reader(("Anna", "Dad")), None, delay=(0, 0), rng=rng, clock=lambda: self.clock[0])
        await self.h.db.set_family_name(OWNER, "Dad")
        await self.h.db.set_family_name(555, "Anna")
        await self.h.db.allow_user(555, True)

    async def asyncTearDown(self):
        await self.h.close()

    async def test_everyone_gets_their_own_cheer(self):
        await handle(self.h.message("I ran 12 km and we won!", uid=OWNER, chat=FAMILY, mid=1), self.h.app)
        self.clock[0] += 15 * 60
        await handle(self.h.message("I passed the exam!", uid=555, chat=FAMILY, mid=2), self.h.app)
        self.assertEqual(len(self.h.session.sent), 2)  # Dad's run didn't use up Anna's cheer
        self.clock[0] += 15 * 60
        await handle(self.h.message("I passed another exam!", uid=555, chat=FAMILY, mid=3), self.h.app)
        self.assertEqual(len(self.h.session.sent), 2)  # but Anna twice within 3 hours is too much


class PetBirthdayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(pet_birthday="2018-11-19", timezone="Europe/Warsaw")
        self.h.ai.post.return_value = "Сегодня мой день. Где паштет?"

    async def asyncTearDown(self):
        await self.h.close()

    async def test_it_celebrates_itself_once_and_knows_its_age(self):
        day = datetime(2026, 11, 19, 8, 30, tzinfo=WARSAW)
        await self.h.scheduler.family_tick(day)  # before BIRTHDAY_HOUR
        self.assertFalse(self.h.session.sent)
        await self.h.scheduler.family_tick(day.replace(hour=10))
        await self.h.scheduler.family_tick(day.replace(hour=12))
        self.assertEqual([m.text for m in self.h.session.sent], ["Сегодня мой день. Где паштет?"])
        task, facts = self.h.ai.post.call_args.args
        self.assertEqual((task, facts["age"]), ("pet_birthday", 8))
        await self.h.scheduler.family_tick(day.replace(day=20, hour=10))
        self.assertEqual(len(self.h.session.sent), 1)

    async def test_royal_all_day(self):
        mood = MoodService(self.h.settings, self.h.db, random.Random(1))
        self.assertEqual((await mood.current(datetime(2026, 11, 19, 15, 0, tzinfo=WARSAW))).name, "royal")
        self.assertIsNotNone(await mood.current(datetime(2026, 11, 20, 15, 0, tzinfo=WARSAW)))


def test_pet_birthday_setting(tmp_path):
    from petbot.config import ConfigError
    from tests.test_core import KEY, env
    base = f"BOT_TOKEN={TOKEN}\nANTHROPIC_API_KEY={KEY}\n"
    assert Settings.load(env(tmp_path, base + "PET_BIRTHDAY=2018-11-19\n")).pet_birthday == "2018-11-19"
    assert Settings.load(env(tmp_path, base + "PET_BIRTHDAY=11-19\n")).pet_birthday == "11-19"
    with pytest.raises(ConfigError, match="PET_BIRTHDAY"):
        Settings.load(env(tmp_path, base + "PET_BIRTHDAY=19.11\n"))

