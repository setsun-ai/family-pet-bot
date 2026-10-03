"""
Reactions: the pet puts an emoji under a family message it wasn't asked to answer.

The rules live in a JSON file (REACTIONS_FILE) and are matched right here with
regular expressions: no AI call, nothing sent anywhere, nothing stored. A pet
that silently drops a 😢 under "I'm not going to the gym today" feels alive,
and it costs nothing.

    {"cooldown_minutes": 20,
     "rules": [
        {"author": ["Anna"], "any": ["\\\\bnot? going\\\\b", "\\\\bcan'?t\\\\b"], "react": ["😢", "💔"], "chance": 0.8},
        {"any": ["\\\\b(ha){2,}", "lol"], "react": ["😁", "🤣"], "chance": 0.3}
     ]}

- any:    at least one pattern must match (case-insensitive); omitted = no condition
- all:    every pattern must match
- none:   no pattern may match
- author: the author's family nickname (set by the owner with /name) is one of these
- react:  the emoji to choose from; Telegram accepts only its own reaction set
- chance: how often a matching rule fires, 0-1 (default 1)
- important: true = not held back by the cooldown of a trivial reaction just before
- media:  true = only pictures, GIFs, videos and stickers (a caption, if any, is the text); the pet
          doesn't see what's on them, it only knows that someone sent one

The FIRST rule whose conditions match decides (if its chance fails, the pet
just doesn't react). After a reaction the pet keeps quiet in that chat for
cooldown_minutes, so it never reacts to everything.
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .common import ServiceError

# The emoji Telegram accepts as a message reaction (Bot API ReactionTypeEmoji).
TELEGRAM_REACTIONS = frozenset(
    "👍 👎 ❤ 🔥 🥰 👏 😁 🤔 🤯 😱 🤬 😢 🎉 🤩 🤮 💩 🙏 👌 🕊 🤡 🥱 🥴 😍 🐳 ❤‍🔥 🌚 🌭 💯 🤣 ⚡ 🍌 🏆 💔 🤨 😐 🍓 🍾 "
    "💋 🖕 😈 😴 😭 🤓 👻 👨‍💻 👀 🎃 🙈 😇 😨 🤝 ✍ 🤗 🫡 🎅 🎄 ☃ 💅 🤪 🗿 🆒 💘 🙉 🦄 😘 💊 🙊 😎 👾 🤷‍♂ 🤷 🤷‍♀ 😡".split())


def normalize(emoji: str) -> str:
    """'❤️' and '❤' are the same reaction; Telegram wants it without the variation selector."""
    return str(emoji).replace("️", "").strip()


@dataclass(frozen=True)
class Rule:
    react: tuple[str, ...]
    any: tuple[re.Pattern, ...] = ()
    all: tuple[re.Pattern, ...] = ()
    none: tuple[re.Pattern, ...] = ()
    authors: frozenset[str] = frozenset()
    chance: float = 1.0
    important: bool = False
    media: bool = False

    def matches(self, text: str, author: str | None, media: bool = False) -> bool:
        if self.media and not media:
            return False
        if self.authors and (author or "").casefold() not in self.authors:
            return False
        if self.any and not any(p.search(text) for p in self.any):
            return False
        if not all(p.search(text) for p in self.all):
            return False
        return not any(p.search(text) for p in self.none)


class Reactions:
    def __init__(self, rules: tuple[Rule, ...], cooldown_minutes: float = 20, *,
                 delay: tuple[float, float] = (2.0, 12.0), rng: random.Random | None = None, clock=time.monotonic):
        self.rules, self.cooldown = rules, cooldown_minutes * 60
        self.delay_range, self.rng, self.clock = delay, rng or random.Random(), clock
        self.last: dict[int, float] = {}

    def pick(self, chat_id: int, text: str, author: str | None = None, mood=None, media: bool = False) -> str | None:
        """
        The emoji to put under this message, or None. Picking one starts the chat's cooldown.
        A mood (mood.py) makes the pet more or less reactive and swaps some emoji (grumpy: ❤ -> 🗿).
        """
        now = self.clock()
        cooling = chat_id in self.last and now - self.last[chat_id] < self.cooldown
        for rule in self.rules:
            if cooling and not rule.important:
                continue
            if rule.matches(text, author, media):
                if self.rng.random() >= min(1.0, rule.chance * (mood.reactions if mood else 1)):
                    return None
                self.last[chat_id] = now
                emoji = self.rng.choice(rule.react)
                return mood.swap.get(emoji, emoji) if mood else emoji
        return None

    def delay(self) -> float:
        """A cat needs a moment to notice: an instant reaction looks like a bot."""
        return self.rng.uniform(*self.delay_range)


def _patterns(item: dict, key: str) -> tuple[re.Pattern, ...]:
    raw = item.get(key, [])
    raw = [raw] if isinstance(raw, str) else raw
    if not isinstance(raw, list) or not all(isinstance(p, str) and p for p in raw):
        raise ValueError(f"'{key}' must be a list of patterns")
    try:
        return tuple(re.compile(p, re.IGNORECASE) for p in raw)
    except re.error as error:
        raise ValueError(f"bad pattern in '{key}': {error}") from None


def parse_rules(data, platform: str = "telegram") -> Reactions:
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError('expected {"rules": [...]}')
    rules = []
    for number, item in enumerate(data["rules"], 1):
        try:
            if not isinstance(item, dict):
                raise ValueError("a rule must be an object")
            react = item.get("react")
            react = [react] if isinstance(react, str) else react
            if not react or not isinstance(react, list):
                raise ValueError("'react' needs at least one emoji")
            react = tuple(normalize(e) for e in react)
            if platform == "telegram" and (bad := [e for e in react if e not in TELEGRAM_REACTIONS]):
                raise ValueError(f"Telegram can't react with {' '.join(bad)}")
            chance = float(item.get("chance", 1))
            if not 0 <= chance <= 1:
                raise ValueError("'chance' must be from 0 to 1")
            authors = item.get("author", [])
            authors = [authors] if isinstance(authors, str) else authors
            if not isinstance(authors, list):
                raise ValueError("'author' must be a list of names")
            rules.append(Rule(react, _patterns(item, "any"), _patterns(item, "all"), _patterns(item, "none"),
                              frozenset(str(a).casefold() for a in authors), chance, bool(item.get("important", False)),
                              bool(item.get("media", False))))
        except (ValueError, TypeError) as error:
            raise ValueError(f"rule {number}: {error}") from None
    cooldown = data.get("cooldown_minutes", 20)
    if not isinstance(cooldown, int | float) or not 0 <= cooldown <= 1440:
        raise ValueError("'cooldown_minutes' must be from 0 to 1440")
    return Reactions(tuple(rules), cooldown)


def load_reactions(path: Path | None, platform: str = "telegram") -> Reactions | None:
    if path is None:
        return None
    try:
        return parse_rules(json.loads(path.read_text(encoding="utf-8-sig")), platform)
    except OSError as error:
        raise ServiceError("reactions_file", error=type(error).__name__) from None
    except ValueError as error:  # also json.JSONDecodeError
        raise ServiceError("reactions_file", error=str(error)[:200]) from None
