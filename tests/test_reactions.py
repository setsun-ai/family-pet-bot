"""Emoji reactions by local rules: matching, cooldown, validation, and the Telegram handler."""
import json
import random
import unittest

import pytest
from aiogram.methods import SetMessageReaction

from petbot.common import ServiceError
from petbot.handlers import handle
from petbot.notice import Noticer
from petbot.reactions import load_reactions, parse_rules
from tests.support import FAMILY, OWNER, Harness

RULES = {"cooldown_minutes": 10, "rules": [
    {"author": ["Anna"], "any": [r"\bnot going\b", r"\bcan'?t\b"], "none": ["joke"], "react": ["😢"]},
    {"any": ["dinner", "soup"], "all": ["mom"], "react": ["👀"]},
    {"any": [r"\b(ha){2,}"], "react": ["😁", "🤣"], "chance": 0},
]}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def engine(data=RULES, platform="telegram"):
    reactions = parse_rules(data, platform)
    reactions.clock, reactions.rng = Clock(), random.Random(1)
    return reactions


class TestRules:
    def test_author_any_none(self):
        r = engine()
        assert r.pick(1, "I'm NOT going to the gym", "anna") == "😢"  # names and text are case-insensitive
        assert engine().pick(1, "I'm not going to the gym", "Dad") is None  # wrong author
        assert engine().pick(1, "not going, just a joke", "Anna") is None  # excluded by 'none'

    def test_all_patterns_must_match(self):
        assert engine().pick(1, "Mom made soup", None) == "👀"
        assert engine().pick(1, "Soup again", None) is None

    def test_first_matching_rule_decides_and_chance_zero_never_fires(self):
        assert engine().pick(1, "hahaha mom soup", None) == "👀"  # rule 2 is first
        assert engine().pick(1, "hahaha", None) is None  # chance 0

    def test_cooldown_per_chat(self):
        r = engine()
        assert r.pick(1, "mom, dinner!", None)
        assert r.pick(1, "mom, dinner!", None) is None  # the pet doesn't react to everything
        assert r.pick(2, "mom, dinner!", None)  # another chat has its own cooldown
        r.clock.now += 601
        assert r.pick(1, "mom, dinner!", None)

    def test_a_miss_does_not_start_the_cooldown(self):
        r = engine()
        assert r.pick(1, "nothing here", None) is None
        assert r.pick(1, "mom, dinner!", None)

    @pytest.mark.parametrize("data, message", [
        ({"rules": [{"any": ["x"], "react": ["🐈"]}]}, "Telegram can't react"),
        ({"rules": [{"any": ["(unclosed"], "react": ["👀"]}]}, "bad pattern"),
        ({"rules": [{"any": ["x"]}]}, "'react'"),
        ({"rules": [{"any": ["x"], "react": ["👀"], "chance": 2}]}, "'chance'"),
        ({"rules": "nope"}, "rules"),
    ])
    def test_invalid_rules_are_explained(self, data, message):
        with pytest.raises(ValueError, match=message):
            parse_rules(data)

    def test_variation_selector_and_discord(self):
        assert engine({"rules": [{"any": ["love"], "react": ["❤️"]}]}).pick(1, "love you", None) == "❤"
        assert engine({"rules": [{"any": ["cat"], "react": ["🐈"]}]}, "discord").pick(1, "cat", None) == "🐈"

    def test_file_errors_are_service_errors(self, tmp_path):
        assert load_reactions(None) is None
        bad = tmp_path / "r.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(ServiceError, match="reactions file"):
            load_reactions(bad)


class ReactionHandling(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()
        reactions = parse_rules({"cooldown_minutes": 0, "rules": [{"any": ["gym"], "react": ["😢"]}]})
        self.h.app.noticer = Noticer(None, reactions, delay=(0, 0))

    async def asyncTearDown(self):
        await self.h.close()

    def reactions(self):
        return [r for r in self.h.session.requests if isinstance(r, SetMessageReaction)]

    async def test_reacts_to_an_unaddressed_family_message_without_ai(self):
        await handle(self.h.message("skipping the gym today", chat=FAMILY, mid=7), self.h.app)
        [request] = self.reactions()
        self.assertEqual((request.chat_id, request.message_id, request.reaction[0].emoji), (FAMILY, 7, "😢"))
        self.h.ai.chat.assert_not_called()
        self.assertFalse(self.h.session.sent)
        self.assertEqual(await self.h.db.history(FAMILY), [])  # nothing stored

    async def test_no_reaction_when_addressed_private_or_unmatched(self):
        await handle(self.h.message("Whiskers, gym?", chat=FAMILY, mid=8), self.h.app)  # gets an answer instead
        await handle(self.h.message("gym", chat=OWNER, mid=9), self.h.app)  # private chat: a normal reply
        await handle(self.h.message("hello all", chat=FAMILY, mid=10), self.h.app)
        self.assertEqual(self.reactions(), [])

    async def test_other_groups_get_no_reactions(self):
        await self.h.db.allow_user(555, True)
        await handle(self.h.message("gym", uid=555, chat=-2000, mid=11), self.h.app)
        self.assertEqual(self.reactions(), [])

    async def test_reactions_off_by_default(self):
        self.h.app.noticer = None
        await handle(self.h.message("gym", chat=FAMILY, mid=12), self.h.app)
        self.assertEqual(self.reactions(), [])


def test_example_files_are_valid():
    from petbot.config import ROOT
    for path in sorted((ROOT / "examples").glob("reactions*.json")):
        assert load_reactions(path) is not None, path
        json.loads(path.read_text(encoding="utf-8"))


class MediaReactions(unittest.IsolatedAsyncioTestCase):
    """Pictures from one person: an emoji, without looking at the picture."""

    async def asyncSetUp(self):
        from aiogram.types import PhotoSize
        self.photo = [PhotoSize(file_id="f", file_unique_id="u", width=10, height=10)]
        self.h = await Harness().open()
        rules = parse_rules({"cooldown_minutes": 0, "rules": [
            {"author": ["Anna"], "media": True, "react": ["😁"]},
            {"any": ["lol"], "react": ["🤣"]}]})
        self.h.app.noticer = Noticer(None, rules, delay=(0, 0))
        await self.h.db.set_family_name(555, "Anna")
        await self.h.db.allow_user(555, True)

    async def asyncTearDown(self):
        await self.h.close()

    def reactions(self):
        return [r.reaction[0].emoji for r in self.h.session.requests if isinstance(r, SetMessageReaction)]

    async def test_annas_picture_gets_an_emoji(self):
        await handle(self.h.message(None, uid=555, chat=FAMILY, mid=30, photo=self.photo), self.h.app)
        self.assertEqual(self.reactions(), ["😁"])
        self.h.ai.chat.assert_not_called()

    async def test_media_rules_need_media_and_text_rules_see_captions(self):
        await handle(self.h.message("just text from Anna", uid=555, chat=FAMILY, mid=31), self.h.app)
        self.assertEqual(self.reactions(), [])  # "media" rules ignore plain text
        await handle(self.h.message(None, uid=OWNER, chat=FAMILY, mid=32, photo=self.photo, caption="lol"), self.h.app)
        self.assertEqual(self.reactions(), ["🤣"])  # someone else's picture: only the caption counts
        await handle(self.h.message(None, uid=OWNER, chat=FAMILY, mid=33, photo=self.photo), self.h.app)
        self.assertEqual(self.reactions(), ["🤣"])

    async def test_private_pictures_are_ignored(self):
        await handle(self.h.message(None, uid=OWNER, chat=OWNER, mid=34, photo=self.photo), self.h.app)
        self.assertEqual(self.h.session.requests, [])
