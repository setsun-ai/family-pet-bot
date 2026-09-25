"""
The family: weekly praise and birthdays.

family.json (FAMILY_FILE) lists the people the pet knows:

    {"members": [
        {"name": "Anna", "about": "the favourite human, 16, loves drawing", "birthday": "03-14"},
        {"name": "Dad", "about": "works in IT, cooks on Sundays"}
    ]}

Weekly praise: once a week (PRAISE_WEEKDAY, from PRAISE_HOUR) the pet picks
ONE member and praises a small, believable, invented achievement - in a few
short chat messages, as family gossip ("heard it on the phone..."), never
anything serious. Everyone gets a turn before anyone is praised again, and
recent "achievements" are remembered so they don't repeat.

Birthdays: on the day (from BIRTHDAY_HOUR) a warm congratulation.
Keep this file private: name it family.local.json (git-ignored).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from .common import ServiceError
from .i18n import weekday_name


@dataclass(frozen=True)
class Member:
    name: str
    about: str = ""
    birthday: str = ""  # "MM-DD"


def load_family(path: Path | None) -> tuple[Member, ...]:
    if path is None:
        return ()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ServiceError("family_file", error=type(error).__name__) from None
    members = []
    for item in (data or {}).get("members", []) if isinstance(data, dict) else []:
        name = str(item.get("name", "")).strip()
        birthday = str(item.get("birthday", "")).strip()
        if not name:
            continue
        if birthday:
            try:
                datetime.strptime("2000-" + birthday, "%Y-%m-%d")
            except ValueError:
                raise ServiceError("family_file", error=f"birthday of {name}: use MM-DD") from None
        members.append(Member(name[:60], str(item.get("about", ""))[:500], birthday))
    return tuple(members)


def week_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def praise_due(now: datetime, weekday: int, hour: int) -> bool:
    return now.weekday() == weekday and hour <= now.hour < hour + 3


def next_in_rotation(state: dict, names: list[str], rng: random.Random | None = None) -> tuple[str, dict]:
    """
    Round robin over a shuffled order: everyone once before anyone twice.
    A new round never starts with the person praised last.
    """
    rng = rng or random.Random()
    order = [n for n in state.get("order", []) if n in names]
    index = state.get("index", 0)
    if set(order) != set(names) or index >= len(order):
        last = order[index - 1] if order and 0 < index <= len(order) else state.get("last")
        order = names[:]
        rng.shuffle(order)
        if len(order) > 1 and order[0] == last:
            order.append(order.pop(0))
        index = 0
    name = order[index]
    return name, {"order": order, "index": index + 1, "last": name}


class FamilyService:
    def __init__(self, settings, db, ai, members: tuple[Member, ...]):
        self.settings, self.db, self.ai, self.members = settings, db, ai, members

    def member(self, name: str) -> Member | None:
        return next((m for m in self.members if m.name.casefold() == name.casefold()), None)

    async def _history(self, name: str) -> list[str]:
        raw = await self.db.get("praise_history:" + name)
        return json.loads(raw) if raw else []

    async def praise_text(self, member: Member, now: datetime) -> str:
        facts = {"person": member.name, "about_them": member.about or None,
                 "today": f"{weekday_name(now.weekday())} {now:%d.%m}",
                 "do_not_repeat": await self._history(member.name)}
        return await self.ai.post("praise", facts, max_parts=3, maximum=450)

    async def remember_praise(self, member: Member, text: str) -> None:
        history = (await self._history(member.name) + [text[:300]])[-6:]
        await self.db.set("praise_history:" + member.name, json.dumps(history, ensure_ascii=False))

    async def choose(self) -> tuple[Member, dict]:
        """Who is next. A freshly shuffled round is saved right away (without advancing), so /status,
        /preview and Saturday all agree on the same person."""
        raw = await self.db.get("praise_rotation")
        old = json.loads(raw) if raw else {}
        name, state = next_in_rotation(old, [m.name for m in self.members])
        if state["order"] != old.get("order"):
            pending = {"order": state["order"], "index": state["index"] - 1, "last": old.get("last")}
            await self.db.set("praise_rotation", json.dumps(pending, ensure_ascii=False))
        return self.member(name), state

    async def birthday_text(self, member: Member, now: datetime) -> str:
        facts = {"person": member.name, "about_them": member.about or None, "today": now.strftime("%d.%m")}
        return await self.ai.post("birthday", facts, max_parts=3, maximum=450)

    def birthdays(self, now: datetime) -> list[Member]:
        today = now.strftime("%m-%d")
        # 29 February birthdays are celebrated on 28 February in other years.
        if today == "02-28" and (now + timedelta(days=1)).month == 3:
            return [m for m in self.members if m.birthday in {"02-28", "02-29"}]
        return [m for m in self.members if m.birthday == today]
