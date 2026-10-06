"""
What the family has just been writing - CHAT_CONTEXT, off by default.

Without it the pet is deaf to the chat: it answers only the one message
addressed to it and never knows what everybody is talking about. With
CHAT_CONTEXT=N the last N messages of the family group (at most 3 hours old)
are kept IN MEMORY ONLY - never written to disk, gone after a restart or
/forget - and shown to the AI when the pet answers, glances at the conversation
(notice.py) or speaks up on its own. That means family messages are sent to the
AI provider: turn it on only if the family is fine with that.

Each line carries a short number (#1, #2, ...), so the AI can point at a message
to react to or reply to.
"""
from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

MAX_AGE = 3 * 3600
TEXT_LIMIT = 300


@dataclass
class Line:
    number: int
    message_id: int
    author: str
    text: str
    at: float
    pet: bool = False
    user_id: int | None = None
    react: Callable | None = field(default=None, repr=False)  # put an emoji under this message
    addressed: bool = False  # someone spoke to the pet: it answers that directly


class ChatContext:
    def __init__(self, size: int, pet_name: str, *, max_age: float = MAX_AGE, clock=time.monotonic):
        self.size, self.pet_name, self.max_age, self.clock = size, pet_name, max_age, clock
        self.chats: dict[int, deque[Line]] = {}
        self.counter = 0

    def __bool__(self) -> bool:
        return self.size > 0

    def add(self, chat_id: int, message_id: int | None, author: str, text: str, *, pet: bool = False,
            user_id: int | None = None, react: Callable | None = None, addressed: bool = False,
            reply_to: int | None = None) -> Line | None:
        text = " ".join(str(text).split())
        if not self.size or not text:
            return None
        if len(text) > TEXT_LIMIT:
            text = text[:TEXT_LIMIT - 1] + "…"
        if reply_to is not None and (target := self.find(chat_id, reply_to)):
            text = f"(↩ #{target.number}) {text}"
        self.counter += 1
        line = Line(self.counter, message_id or 0, author[:60] or "?", text, self.clock(), pet, user_id, react,
                    addressed)
        self.chats.setdefault(chat_id, deque(maxlen=self.size)).append(line)
        return line

    def find(self, chat_id: int, message_id: int) -> Line | None:
        return next((line for line in self.chats.get(chat_id, ()) if line.message_id == message_id), None)

    def recent(self, chat_id: int, seconds: float | None = None) -> list[Line]:
        now = self.clock()
        age = self.max_age if seconds is None else min(seconds, self.max_age)
        return [line for line in self.chats.get(chat_id, ()) if now - line.at <= age]

    def render(self, lines: list[Line], *, skip: int | None = None, new_after: int | None = None) -> str:
        """
        '#12 Аня: текст' per line; the pet's own lines are marked, `skip` = a message id to leave out,
        `new_after` = the last line the pet has already seen: a "--- NEW ---" mark goes after it.
        """
        rows = []
        now = self.clock()
        for line in lines:
            if new_after is not None and line.number > new_after:
                if rows:
                    rows.append("--- NEW ---")
                new_after = None
            if skip is not None and line.message_id == skip and not line.pet:
                continue
            minutes = int((now - line.at) // 60)
            ago = f" [{minutes} min ago]" if minutes >= 5 else ""
            who = f"{line.author} (you)" if line.pet else line.author
            rows.append(f"#{line.number}{ago} {who}: {line.text}")
        return "\n".join(rows)

    def forget(self, chat_id: int) -> None:
        self.chats.pop(chat_id, None)
