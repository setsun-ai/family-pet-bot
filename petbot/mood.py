"""
The pet's mood: a random walk of its own, not a reaction to the chat.

A real cat is sleepy, then suddenly mad, then cuddly, and nobody knows why.
Every mood lasts a random 1-6 hours; the next one is drawn with weights that
depend on the time of day (sleepy at night and after lunch, zoomies late in the
evening, hungry around feeding time). The current mood is saved in SQLite, so a
restart doesn't reset it.

The mood colours everything the pet does, a little:
- a line in the AI prompt (the tone of replies and its own posts),
- how often it reacts and with which emoji (a grumpy cat gives 🗿 instead of ❤),
- how chatty it is on its own, and how it cheers someone's achievement.
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from .i18n import prompt, t


@dataclass(frozen=True)
class Mood:
    name: str
    weight: float  # how common it is
    hours: tuple[float, float]  # how long it lasts
    peak: tuple[int, ...] = ()  # local hours when it's three times as likely
    reactions: float = 1.0  # multiplier for reaction chances
    chatty: float = 1.0  # multiplier for speaking up on its own
    swap: dict[str, str] = field(default_factory=dict)  # emoji it uses instead (positive reactions only)

    @property
    def line(self) -> str:
        return prompt(f"mood_{self.name}")

    @property
    def label(self) -> str:
        return t(f"mood_name_{self.name}")


MOODS = {m.name: m for m in (
    Mood("sleepy", 3, (1.5, 5), peak=(0, 1, 2, 3, 4, 5, 6, 7, 8, 14, 15), reactions=0.5, chatty=0.4,
         swap={"🔥": "🥱", "🎉": "😴", "🏆": "🥱", "😁": "🥱"}),
    Mood("playful", 3, (1, 3), peak=(17, 18, 19, 20), reactions=1.4, chatty=1.5, swap={"👍": "🤪", "👀": "🤪"}),
    Mood("grumpy", 2, (1, 4), reactions=0.8, chatty=0.7,
         swap={"❤": "🗿", "😍": "🤨", "🔥": "🗿", "🤗": "🗿", "🤩": "🤨", "🥰": "🗿"}),
    Mood("cuddly", 2.5, (1, 4), peak=(20, 21, 22), reactions=1.3, chatty=1.2,
         swap={"🗿": "❤", "🤨": "🥰", "👀": "😍", "👍": "❤"}),
    Mood("hungry", 2, (1, 2.5), peak=(7, 8, 18, 19), reactions=1.1, chatty=1.4, swap={"🔥": "🌭", "🏆": "🍓"}),
    Mood("philosophical", 1.5, (1.5, 4), peak=(22, 23), reactions=0.7, chatty=0.8, swap={"😁": "🤔", "👀": "🤔"}),
    Mood("zoomies", 1.5, (0.5, 1.5), peak=(21, 22, 23), reactions=1.6, chatty=1.3, swap={"👍": "⚡", "❤": "⚡"}),
    Mood("royal", 1.5, (2, 6), reactions=0.8, chatty=0.9, swap={"👍": "😎", "❤": "😎", "🔥": "🆒"}),
    Mood("offended", 0.7, (1, 3), reactions=0.4, chatty=0.3, swap={"❤": "😐", "🔥": "😐", "😍": "😐", "🤗": "😐"}),
)}


def next_mood(now: datetime, current: str | None, rng: random.Random) -> str:
    """Any mood but the current one, weighted by how common it is and the time of day."""
    names = [n for n in MOODS if n != current]
    weights = [MOODS[n].weight * (3 if now.hour in MOODS[n].peak else 1) for n in names]
    return rng.choices(names, weights)[0]


class MoodService:
    def __init__(self, settings, db, rng: random.Random | None = None):
        self.settings, self.db = settings, db
        self.rng = rng or random.Random()
        self.state: dict | None = None

    async def current(self, now: datetime | None = None) -> Mood:
        now = (now or datetime.now(self.settings.tz)).astimezone(self.settings.tz)
        if self.state is None:
            raw = await self.db.get("mood")
            self.state = json.loads(raw) if raw else {}
        name, until = self.state.get("name"), self.state.get("until")
        if name not in MOODS or not until or now >= datetime.fromisoformat(until):
            await self._switch(now, next_mood(now, name, self.rng))
        if self._birthday(now):
            return MOODS["royal"]  # its own birthday: royal all day, whatever the dice say
        return MOODS[self.state["name"]]

    def _birthday(self, now: datetime) -> bool:
        day = getattr(self.settings, "pet_birthday", "")[-5:]
        return bool(day) and now.strftime("%m-%d") == day

    async def _switch(self, now: datetime, name: str, hours: float | None = None) -> None:
        low, high = MOODS[name].hours
        until = now + timedelta(hours=hours if hours is not None else self.rng.uniform(low, high))
        self.state = {"name": name, "since": now.isoformat(timespec="minutes"), "until": until.isoformat(timespec="minutes")}
        await self.db.set("mood", json.dumps(self.state))

    async def force(self, name: str, now: datetime | None = None) -> Mood:
        """/mood <name> (owner): for testing, or because the cat really is offended today. Lasts 3 hours."""
        now = (now or datetime.now(self.settings.tz)).astimezone(self.settings.tz)
        await self._switch(now, name, 3)
        return MOODS[name]

    def until(self) -> datetime | None:
        return datetime.fromisoformat(self.state["until"]) if self.state and self.state.get("until") else None
