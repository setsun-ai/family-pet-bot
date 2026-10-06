"""
The pet speaks up on its own - rarely, and only when someone is around.

SPONTANEOUS_PER_WEEK = how many days a week, on average, the pet starts a
conversation itself. Each morning it's decided at random whether today is such a
day and from what time. The message is sent only while the family chat is alive
(someone wrote in the last 15 minutes): talking into an empty room is what a
newsletter does, not a cat. The mood decides too: an offended or sleepy cat
often postpones it.

What it says: if it remembers something recent (MEMORY_ENABLED), it may ask
about it ("so how was the exam?"); otherwise something in its current mood.
One AI call, one short message.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from .i18n import weekday_name

ACTIVE = timedelta(minutes=15)


class Spontaneous:
    def __init__(self, settings, db, ai, delivery, mood=None, rng: random.Random | None = None):
        self.settings, self.db, self.ai, self.delivery, self.mood = settings, db, ai, delivery, mood
        self.rng = rng or random.Random()
        self.context = None  # ChatContext (CHAT_CONTEXT): what the family is writing right now

    def _window(self) -> tuple[int, int]:
        start, end = self.settings.quiet_end_hour + 1, self.settings.quiet_start_hour - 1
        return (start, end) if start < end else (10, 21)

    async def _plan(self, local: datetime) -> dict:
        raw = await self.db.get("spontaneous_plan")
        plan = json.loads(raw) if raw else {}
        day = local.date().isoformat()
        if plan.get("day") != day:
            start, end = self._window()
            minute = self.rng.randrange(start * 60, end * 60)
            at = local.replace(hour=minute // 60, minute=minute % 60, second=0, microsecond=0)
            plan = {"day": day, "chatty": self.rng.random() < self.settings.spontaneous_per_week / 7,
                    "at": at.isoformat(), "done": False}
            await self.db.set("spontaneous_plan", json.dumps(plan))
        return plan

    async def tick(self, chat: int, now: datetime, last_activity: datetime | None) -> bool:
        """True if the pet said something."""
        local = now.astimezone(self.settings.tz)
        plan = await self._plan(local)
        if not plan["chatty"] or plan["done"] or local < datetime.fromisoformat(plan["at"]):
            return False
        if last_activity is None or now - last_activity > ACTIVE:
            return False  # wait until someone is around
        mood = await self.mood.current(now) if self.mood else None
        if mood and self.rng.random() > min(1.0, mood.chatty):
            # not in the mood: maybe later today
            plan["at"] = (local + timedelta(minutes=self.rng.randint(60, 150))).isoformat()
            await self.db.set("spontaneous_plan", json.dumps(plan))
            return False
        plan["done"] = True
        await self.db.set("spontaneous_plan", json.dumps(plan))  # never twice a day, even if the AI fails
        facts = {"today": f"{weekday_name(local.weekday())} {local:%d.%m %H:%M}", "memory": None}
        if self.settings.memory_enabled:
            recent = await self.db.memories(chat, 14)
            if recent and self.rng.random() < 0.7:
                memory = self.rng.choice(recent)
                facts["memory"] = f"{datetime.fromisoformat(memory['created_at']).astimezone(self.settings.tz):%d.%m}: " \
                                  f"{memory['fact']}"
        if self.context:
            facts["chat"] = self.context.render(self.context.recent(chat, ACTIVE.total_seconds())) or None
        text = await self.ai.post("spontaneous", facts, max_parts=1, maximum=200)
        return await self.delivery.send(chat, text, [("spontaneous", plan["day"])])
