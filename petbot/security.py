from __future__ import annotations

import secrets
import time

from aiogram.enums import ChatType

from .db import Database


class AccessControl:
    def __init__(self, db: Database):
        self.db = db
        self.claim_code = secrets.token_urlsafe(24)
        self.claim_created = time.monotonic()

    async def claim(self, user_id: int, code: str, chat_type: str) -> bool:
        if chat_type != ChatType.PRIVATE or not self.claim_code:
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

    async def allowed(self, user_id: int, chat_id: int, chat_type: str) -> bool:
        if chat_type == ChatType.PRIVATE:
            return await self.is_owner(user_id) or await self.db.is_allowed_user(user_id)
        if chat_type in {ChatType.GROUP, ChatType.SUPERGROUP}:
            return chat_id == await self.db.family()
        return False
