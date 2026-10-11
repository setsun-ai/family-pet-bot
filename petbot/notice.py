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
4. With CHAT_CONTEXT (context.py) the pet instead GLANCES at the chat: when a few
   new messages have piled up and the conversation pauses for a moment, one AI call
   sees the recent messages and decides where an emoji fits and whether the pet has
   something to say (at most CHIME_IN_PER_DAY lines a day; no quips about air raids).

5. Answers to the pet without a reply or its name ("Канешн" after "смотрим?", "Отстань"
   after it teased someone): one of the first two messages right after the pet's own message
   (5 minutes, 15 after a question) gets one emoji picked here - 👍 for "yes", 😇 for
   grumbling, 😁 for laughter, ❤ otherwise. No AI.

A link with nothing in its text (LINK_PREVIEW, linkpeek.py): the pet reads the page's title and
description with the same dictionary - only for bad news and alarms, never a cheer.
Bad news, a link or a forwarded post also keeps the pet from starting a conversation of its own
for a while (spontaneous.py).

Nothing is stored. Without CHAT_CONTEXT the messages themselves are never sent to the AI. Telegram and
Discord pass a `react(emoji)` callback; the cheer goes through the normal delivery.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from dataclasses import replace
from datetime import datetime, timedelta

from .common import ServiceError
from .linkpeek import first_link
from .phrases import cheer
from .reactions import Reactions
from .scheduler import quiet_time
from .sentiment import LAUGHING, Feeling, Reader

log = logging.getLogger(__name__)

PROUD = ("🔥", "🏆", "👏", "🤩")
SAD = ("😢", "💔", "🤗", "😭")
WORRY = ("😢",)  # air raids: a quiet tear, not sobbing and not "praying hands"
FOOD = re.compile(r"\b(?:ужин|обед|завтрак|суп|борщ|котлет|пирог|пицц|шашлык|кебаб|бургер|торт|блин|пельмен|"
                  r"есть хочу|жрать|кушать|dinner|lunch|breakfast|soup|pizza|cake|burger)", re.I)
LIVELY = (6, 2, 600)  # messages, different people, seconds
GLANCE_NEW = 3  # new messages since the last glance before the pet looks again
GLANCE_BURST = 10  # ...or this many: look right away, without waiting for a pause
GLANCE_GAP = 15 * 60  # seconds between glances
GLANCE_PER_DAY = 20  # AI calls a day for glances
CHIME_GAP = 3600  # seconds between lines the pet says on its own after a glance
ANSWER_WINDOW = 5 * 60  # seconds after the pet's message when the next words are probably meant for it
QUESTION_WINDOW = 15 * 60  # ...after a question ("смотрим?")
ANSWER_MESSAGES = 2  # only the first messages after the pet's one
PEEKS_PER_HOUR = 30  # pages opened for LINK_PREVIEW
ANNOYED = re.compile(r"(?<!\w)(?:не\s+беси\w*|бесишь|бесит|отстань|отвали|отвянь|хватит|замолчи|заткнись|надоел\w*|"
                     r"достал\w*|кыш|брысь|відчепись|stop it|shut up|go away)(?!\w)", re.I)
DECLINE = re.compile(r"(?<!\w)(?:нет|неа|не\s+(?:буду|смогу|могу|хочу|успею)|ні|no|nope)(?!\w)", re.I)
AGREE = re.compile(r"(?<!\w)(?:да+|ага+|угу+|конечно|канеш\w*|естествен+о|есес\w*|ессес\w*|го+|давай\w*|смотр\w*|"
                   r"гляну|буду|ок|окей|ok|okay|yes|yep|sure|звісно|авжеж|дивимос\w*|дивлюс\w*)(?!\w)|"
                   r"(?:^|\s)\+(?:\s|$)|👍", re.I)


class Noticer:
    def __init__(self, reader: Reader | None = None, reactions: Reactions | None = None, *,
                 cheer_minutes: float = 10, person_minutes: float = 180, sad_minutes: float = 5, delay: tuple[float, float] = (2.0, 10.0),
                 rng: random.Random | None = None, clock=time.monotonic, glance_pause: float = 75, peek=None):
        self.reader, self.reactions = reader, reactions
        self.peek = peek  # LINK_PREVIEW: async url -> the page's title and description, or None
        self.peeks: list[float] = []
        self.after_pet: dict[int, tuple[int, int, bool]] = {}  # chat -> (pet message, messages since, answered)
        self.cheer_cooldown, self.sad_cooldown = cheer_minutes * 60, sad_minutes * 60
        self.person_cooldown = person_minutes * 60  # everyone gets their own cheer, not "first come, first served"
        self.delay_range, self.rng, self.clock = delay, rng or random.Random(), clock
        self.last: dict[tuple[str, int], float] = {}
        self.recent: dict[int, deque] = {}  # chat -> (time, user, vibe) of the last unaddressed messages
        self.chimes: dict[int, list[float]] = {}  # chat -> when the pet chimed in
        self.glance_pause = glance_pause  # seconds of silence in the chat before the pet glances
        self.glance_due: dict[int, float] = {}
        self.glance_tasks: dict[int, asyncio.Task] = {}
        self.glances: dict[int, list[float]] = {}
        self.seen: dict[int, int] = {}  # chat -> the last context line number the pet has glanced at

    def _fresh(self, what: str, chat_id: int, cooldown: float) -> bool:
        return self.clock() - self.last.get((what, chat_id), -1e12) >= cooldown

    def _allow(self, what: str, chat_id: int, cooldown: float) -> bool:
        now = self.clock()
        if now - self.last.get((what, chat_id), -1e12) < cooldown:
            return False
        self.last[(what, chat_id)] = now
        return True

    async def notice(self, app, chat_id: int, message_id: int, user_id: int, text: str, react,
                     media: bool = False, replying_to_someone: bool = False) -> str | None:
        """
        Returns what it did ("cheer", "react", "chime" or None), for tests and logs.
        media=True: a picture, GIF, video or sticker; `text` is its caption (may be empty).
        replying_to_someone=True: a reply to another family member, so not an answer to the pet.
        """
        if chat_id != await app.db.family():
            return None
        answer = self._answer_to_pet(app, chat_id, user_id, replying_to_someone)
        author = await app.db.family_name(user_id)
        mood = await app.mood.current() if app.mood else None
        feeling = self.reader.read(text, author) if self.reader and text else None
        if feeling is None and self.reader and self.peek and (link := first_link(text)):
            feeling = await self._read_link(link)
        if feeling and feeling.kind in ("sad", "worry") and getattr(app, "scheduler", None):
            app.scheduler.hush(chat_id)
        did = await self._act(app, chat_id, message_id, text, author, mood, feeling, react, media)
        if answer:
            await self._answered(app, chat_id, user_id, answer)
            if did is None:
                did = await self._react(react, self._answer_emoji(text, mood), "answer to the pet")
        vibe = self._vibe(text, feeling)
        self.recent.setdefault(chat_id, deque(maxlen=20)).append((self.clock(), user_id, vibe))
        context = getattr(app, "context", None)
        if context:
            if did in ("react", "cheer") and (line := context.find(chat_id, message_id)):
                line.react = None  # one emoji per message is enough
            self._poke(app, chat_id)
            return did
        if did != "cheer" and await self._chime_in(app, chat_id, mood):
            return "chime"
        return did

    # --- answers to the pet without a reply or its name ---

    def _answer_to_pet(self, app, chat_id: int, user_id: int, replying_to_someone: bool):
        """The pet's message this one probably answers (delivery.PetMessage), or None. Once per pet message."""
        last = getattr(app.delivery, "last_sent", {}).get(chat_id)
        if last is None:
            return None
        pet_id, count, answered = self.after_pet.get(chat_id, (None, 0, False))
        if pet_id != last.message_id:
            count, answered = 0, False
        count += 1
        window = QUESTION_WINDOW if last.question else ANSWER_WINDOW
        if answered or replying_to_someone or count > ANSWER_MESSAGES or time.monotonic() - last.at > window or \
                last.to_user not in (None, user_id):  # the pet was talking to someone else
            self.after_pet[chat_id] = (last.message_id, count, answered)
            return None
        self.after_pet[chat_id] = (last.message_id, count, True)
        return last

    async def _answered(self, app, chat_id: int, user_id: int, answer) -> None:
        if answer.kind == "sports-kickoff" and getattr(app, "scheduler", None):
            app.scheduler.note_match_talk(chat_id)  # "Канешн" means "watching", whatever the words
        try:  # counts for /stats like a reply
            await app.db.add_feedback(chat_id, answer.message_id, user_id, "↩")
        except Exception as error:
            log.warning("Could not record an answer to the pet (%s)", type(error).__name__)

    def _answer_emoji(self, text: str, mood) -> str:
        if ANNOYED.search(text):
            choices = ("😇", "🙈")  # an innocent cat
        elif LAUGHING.search(text):
            choices = ("😁", "🤣")
        elif DECLINE.search(text):
            choices = ("😢", "🤷")
        elif AGREE.search(text):
            choices = ("👍", "❤")
        else:
            choices = ("❤", "🤗")
        emoji = self.rng.choice(choices)
        return mood.swap.get(emoji, emoji) if mood else emoji

    # --- links (LINK_PREVIEW) ---

    async def _read_link(self, link: str) -> Feeling | None:
        """Bad news or an alarm behind a link; good news there is someone else's, not a family success."""
        now = self.clock()
        self.peeks = [t for t in self.peeks if now - t < 3600]
        if len(self.peeks) >= PEEKS_PER_HOUR:
            return None
        self.peeks.append(now)
        try:
            page = await self.peek(link)
        except Exception as error:
            log.info("Link preview failed (%s)", type(error).__name__)
            return None
        feeling = self.reader.read(page.replace("?", ".")) if page else None  # headlines ask rhetorical questions
        if feeling and (feeling.kind == "worry" or (feeling.kind == "sad" and feeling.strength >= 2)):
            log.info("Link preview: %s", feeling.kind)
            return replace(feeling, target=None)
        log.info("Link preview: %s", "nothing to react to" if page else "unavailable")
        return None

    # --- glancing at the chat (CHAT_CONTEXT) ---

    def _poke(self, app, chat_id: int) -> None:
        """A new message: glance after a short pause in the conversation (or right away in a burst)."""
        self.glance_due[chat_id] = self.clock() + self.glance_pause
        task = self.glance_tasks.get(chat_id)
        if task is None or task.done():
            self.glance_tasks[chat_id] = asyncio.create_task(self._glance_when_quiet(app, chat_id))

    def _unseen(self, app, chat_id: int) -> list:
        seen = self.seen.get(chat_id, 0)
        return [line for line in app.context.recent(chat_id) if line.number > seen and not line.pet]

    async def _glance_when_quiet(self, app, chat_id: int) -> None:
        try:
            while (left := self.glance_due.get(chat_id, 0) - self.clock()) > 0 and \
                    len(self._unseen(app, chat_id)) < GLANCE_BURST:
                await asyncio.sleep(min(left, 5))
            await self.glance(app, chat_id)
        except ServiceError as error:
            log.info("Glance skipped (%s)", error)
        except Exception as error:
            log.warning("Glance failed (%s)", type(error).__name__)

    async def glance(self, app, chat_id: int) -> str | None:
        """Look at the new messages: maybe an emoji or two, maybe one line. Returns what it did, for tests."""
        s, now = app.settings, self.clock()
        new = self._unseen(app, chat_id)
        if len(new) < GLANCE_NEW or chat_id != await app.db.family():
            return None
        local = datetime.now(s.tz)
        if quiet_time(local, s):
            return None
        done = [t for t in self.glances.get(chat_id, []) if now - t < 86400]
        if len(done) >= GLANCE_PER_DAY or (done and now - done[-1] < GLANCE_GAP):
            return None
        lines = app.context.recent(chat_id)
        before = self.seen.get(chat_id, 0)
        self.seen[chat_id] = lines[-1].number
        self.glances[chat_id] = done + [now]
        mood = await app.mood.current() if app.mood else None
        allow_say = await self._may_say(app, chat_id, new, lines, mood)
        result = await app.ai.glance(app.context.render(lines, new_after=before), allow_say=allow_say)
        did = None
        by_number = {line.number: line for line in new}
        for number, emoji in result.reactions:
            line = by_number.get(number)
            if line is None or line.react is None or line.addressed:
                continue
            react, line.react = line.react, None
            did = await self._react(react, emoji, "glance") or did
        if allow_say and result.say:
            target = by_number.get(result.reply_to)
            if await app.delivery.send(chat_id, result.say, [("chime", f"{local:%Y-%m-%d %H:%M}")],
                                       target.message_id if target else None):
                self.chimes[chat_id] = [t for t in self.chimes.get(chat_id, []) if now - t < 86400] + [now]
                did = "chime"
        return did

    async def _may_say(self, app, chat_id: int, new: list, lines: list, mood) -> bool:
        """Lines are rarer than emoji: a daily limit, not right after the pet spoke, never about air raids."""
        s, now = app.settings, self.clock()
        chimes = [t for t in self.chimes.get(chat_id, []) if now - t < 86400]
        if len(chimes) >= s.chime_in_per_day or (chimes and now - chimes[-1] < CHIME_GAP):
            return False
        if any(line.pet and now - line.at < 600 for line in lines) or any(line.addressed for line in new):
            return False  # it has just spoken, or it's answering someone right now
        if self.reader and any((f := self.reader.read(line.text)) and f.kind == "worry" for line in new):
            return False
        return self.rng.random() < min(1.0, 0.8 * (mood.chatty if mood else 1))

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
                log.info("Cheer sent")
                return "cheer"
            emoji = self.rng.choice(PROUD)
            return await self._react(react, mood.swap.get(emoji, emoji) if mood else emoji, "achievement")
        if feeling and feeling.kind == "worry":
            if self._allow("worry", chat_id, self.sad_cooldown):
                return await self._react(react, self.rng.choice(WORRY), "alarm")  # never swapped by a mood
            return None
        if feeling and feeling.kind == "sad":
            if self._allow("sad", chat_id, self.sad_cooldown):
                return await self._react(react, self.rng.choice(SAD), "bad news")  # never "swapped" by a mood
            return None
        if self.reactions:
            emoji = self.reactions.pick(chat_id, text, author, mood, media)
            if emoji:
                return await self._react(react, emoji, "rule")
        return None

    async def _react(self, react, emoji: str, why: str = "") -> str | None:
        await asyncio.sleep(self.rng.uniform(*self.delay_range))  # a cat needs a moment to notice
        try:
            await react(emoji)
        except Exception as error:  # e.g. the group allows only some reactions
            log.info("Reaction not set (%s)", type(error).__name__)
            return None
        log.info("Reaction %s (%s)", emoji, why)  # the emoji and the reason, never the message
        return "react"
