"""
What the pet does with a family message nobody addressed to it - locally, without the AI.

1. Good or bad news (sentiment.py, SENTIMENT_ENABLED): an achievement gets a short
   cheer from phrases.py as a reply - once per person in 3 hours, at most one per 10
   minutes in the chat - or a 🔥 when the pet isn't in the mood to talk;
   bad news gets a sad reaction.
2. Otherwise the keyword rules of REACTIONS_FILE (reactions.py) may add an emoji.
3. Chiming in (CHIME_IN_PER_DAY): when the family is in a lively conversation (6+
   messages from 2+ people within 10 minutes) with a clear vibe - laughing,
   celebrating, sad, talking about food - the pet joins with one short line.
   The AI gets only that one word and the people's names, never the messages.

Nothing is stored, and the messages themselves are never sent to the AI. Telegram and Discord pass a
`react(emoji)` callback; the cheer goes through the normal delivery.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta

from .common import ServiceError
from .phrases import cheer
from .reactions import Reactions
from .sentiment import LAUGHING, Reader

log = logging.getLogger(__name__)

PROUD = ("🔥", "🏆", "👏", "🤩")
SAD = ("😢", "💔", "🤗", "😭")
WORRY = ("😢",)  # air raids: a quiet tear, not sobbing and not "praying hands"
FOOD = re.compile(r"\b(?:ужин|обед|завтрак|суп|борщ|котлет|пирог|пицц|шашлык|кебаб|бургер|торт|блин|пельмен|"
                  r"есть хочу|жрать|кушать|dinner|lunch|breakfast|soup|pizza|cake|burger)", re.I)
LIVELY = (6, 2, 600)  # messages, different people, seconds


class Noticer:
    def __init__(self, reader: Reader | None = None, reactions: Reactions | None = None, *,
                 cheer_minutes: float = 10, person_minutes: float = 180, sad_minutes: float = 5, delay: tuple[float, float] = (2.0, 10.0),
                 rng: random.Random | None = None, clock=time.monotonic):
        self.reader, self.reactions = reader, reactions
        self.cheer_cooldown, self.sad_cooldown = cheer_minutes * 60, sad_minutes * 60
        self.person_cooldown = person_minutes * 60  # everyone gets their own cheer, not "first come, first served"
        self.delay_range, self.rng, self.clock = delay, rng or random.Random(), clock
        self.last: dict[tuple[str, int], float] = {}
        self.recent: dict[int, deque] = {}  # chat -> (time, user, vibe) of the last unaddressed messages
        self.chimes: dict[int, list[float]] = {}  # chat -> when the pet chimed in

    def _fresh(self, what: str, chat_id: int, cooldown: float) -> bool:
        return self.clock() - self.last.get((what, chat_id), -1e12) >= cooldown

    def _allow(self, what: str, chat_id: int, cooldown: float) -> bool:
        now = self.clock()
        if now - self.last.get((what, chat_id), -1e12) < cooldown:
            return False
        self.last[(what, chat_id)] = now
        return True

    async def notice(self, app, chat_id: int, message_id: int, user_id: int, text: str, react,
                     media: bool = False) -> str | None:
        """
        Returns what it did ("cheer", "react", "chime" or None), for tests and logs.
        media=True: a picture, GIF, video or sticker; `text` is its caption (may be empty).
        """
        if chat_id != await app.db.family():
            return None
        author = await app.db.family_name(user_id)
        mood = await app.mood.current() if app.mood else None
        feeling = self.reader.read(text, author) if self.reader and text else None
        did = await self._act(app, chat_id, message_id, text, author, mood, feeling, react, media)
        vibe = self._vibe(text, feeling)
        self.recent.setdefault(chat_id, deque(maxlen=20)).append((self.clock(), user_id, vibe))
        if did != "cheer" and await self._chime_in(app, chat_id, mood):
            return "chime"
        return did

    @staticmethod
    def _vibe(text: str, feeling) -> str | None:
        if feeling:
            return {"achievement": "celebrating", "sad": "sad", "worry": "worry"}[feeling.kind]
        if LAUGHING.search(text):
            return "laughing"
        if FOOD.search(text):
            return "food"
        return None

    async def _chime_in(self, app, chat_id: int, mood) -> bool:
        s = app.settings
        if s.chime_in_per_day <= 0:
            return False
        now = self.clock()
        count, people, seconds = LIVELY
        window = [m for m in self.recent.get(chat_id, ()) if now - m[0] <= seconds]
        vibes = [m[2] for m in window if m[2]]
        if "worry" in vibes:
            return False  # air raids, alarms: the pet doesn't butt in with a quip
        if len(window) < count or len({m[1] for m in window}) < people or not vibes:
            return False
        vibe = max(set(vibes), key=vibes.count)
        if vibes.count(vibe) < 3:
            return False
        done = [t for t in self.chimes.get(chat_id, []) if now - t < 86400]
        if len(done) >= s.chime_in_per_day or (done and now - done[-1] < 3 * 3600):
            return False
        local = datetime.now(s.tz)
        last = await app.db.last_delivery_at(chat_id, ("dialog", "cheer", "chime", "spontaneous", "news"))
        if last and local - last < timedelta(minutes=30):
            return False  # it has just spoken
        if self.rng.random() >= min(1.0, 0.7 * (mood.chatty if mood else 1)):
            self.recent[chat_id].clear()  # not in the mood: wait for the next lively moment
            return False
        names = []
        for _, user, _ in window:
            name = await app.db.family_name(user)
            if name and name not in names:
                names.append(name)
        self.chimes[chat_id] = done + [now]
        self.recent[chat_id].clear()
        try:
            text = await app.ai.post("chime_in", {"vibe": vibe, "people": names or None}, max_parts=1, maximum=160)
            return await app.delivery.send(chat_id, text, [("chime", f"{local:%Y-%m-%d %H:%M}")])
        except ServiceError as error:
            log.info("Chime-in skipped (%s)", error)
            return False

    async def _act(self, app, chat_id: int, message_id: int, text: str, author, mood, feeling, react,
                   media: bool = False) -> str | None:
        if feeling and feeling.kind == "achievement":
            talk = min(1.0, 0.8 * (mood.chatty if mood else 1))
            hero = feeling.target or author or "?"
            if self.rng.random() < talk and self._fresh("cheer:" + hero, chat_id, self.person_cooldown) and                     self._allow("cheer", chat_id, self.cheer_cooldown):
                self.last[("cheer:" + hero, chat_id)] = self.clock()
                line = cheer(feeling.target or author, about_someone_else=feeling.target is not None,
                             mood=mood.name if mood else None, rng=self.rng)
                await asyncio.sleep(self.rng.uniform(*self.delay_range))
                try:
                    await app.delivery.send(chat_id, line, [("cheer", str(message_id))], reply_to=message_id)
                except ServiceError as error:  # nobody asked for it: never answer with an error notice
                    log.info("Cheer not sent (%s)", error)
                    return None
                return "cheer"
            emoji = self.rng.choice(PROUD)
            return await self._react(react, mood.swap.get(emoji, emoji) if mood else emoji)
        if feeling and feeling.kind == "worry":
            if self._allow("worry", chat_id, self.sad_cooldown):
                return await self._react(react, self.rng.choice(WORRY))  # never swapped by a mood
            return None
        if feeling and feeling.kind == "sad":
            if self._allow("sad", chat_id, self.sad_cooldown):
                return await self._react(react, self.rng.choice(SAD))  # sad news is never "swapped" by a mood
            return None
        if self.reactions:
            emoji = self.reactions.pick(chat_id, text, author, mood, media)
            if emoji:
                return await self._react(react, emoji)
        return None

    async def _react(self, react, emoji: str) -> str | None:
        await asyncio.sleep(self.rng.uniform(*self.delay_range))  # a cat needs a moment to notice
        try:
            await react(emoji)
        except Exception as error:  # e.g. the group allows only some reactions
            log.info("Reaction not set (%s)", type(error).__name__)
            return None
        return "react"
