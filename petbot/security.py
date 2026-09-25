"""
Who may use the bot: the owner (claimed once with a one-time code), people
the owner allowed in private, and members of the ONE family chat/channel.
Works for Telegram and Discord alike (chat types are plain strings).
"""
from __future__ import annotations

import secrets
import time

from .db import Database

PRIVATE, GROUP = "private", "group"
GROUP_TYPES = {"group", "supergroup"}


def _kind(chat_type) -> str:
    """aiogram's ChatType enum or a plain string -> "private" / "group" / ..."""
    return str(getattr(chat_type, "value", chat_type))


class AccessControl:
    def __init__(self, db: Database):
        self.db = db
        self.claim_code = secrets.token_urlsafe(24)
        self.claim_created = time.monotonic()

    async def claim(self, user_id: int, code: str, chat_type) -> bool:
        if _kind(chat_type) != PRIVATE or not self.claim_code:
            return False
        if time.monotonic() - self.claim_created > 1200:
            return False
        if not secrets.compare_digest(code.encode("utf-8"), self.claim_code.encode("utf-8")):
            return False
        result = await self.db.claim_owner(user_id)
        if result:
            self.claim_code = ""
        return result

    async def is_owner(self, user_id: int) -> bool:
        return user_id == await self.db.owner()

    async def allowed(self, user_id: int, chat_id: int, chat_type) -> bool:
        kind = _kind(chat_type)
        if kind == PRIVATE:
            return await self.is_owner(user_id) or await self.db.is_allowed_user(user_id)
        if kind in GROUP_TYPES:
            return chat_id == await self.db.family()
        return False
