"""
Tidy group chats: short-lived service notices and owner-only /del.

- Service replies in the family group (e.g. "chat saved", "owner only") are
  deleted after NOTICE_SECONDS (0 = keep them). Conversation answers and
  posts are never auto-deleted.
- The owner can delete one of the bot's own messages by replying /del to it
  (Telegram allows bots to delete their messages for 48 hours).

Deletions are journaled in SQLite, so they survive restarts and respect
Telegram rate limits. The bot never enumerates chat history or touches
other people's messages.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from .i18n import t

if TYPE_CHECKING:
    from aiogram.types import Message

    from .handlers import App

log = logging.getLogger(__name__)
MAX_AGE = 48 * 3600
TABLE = "notice_cleanup"


class Housekeeping:
    def __init__(self, app: App):
        self.app = app
        self._init_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._ready = False
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._pause_until = 0.0
        self._last_prune = 0.0

    async def ensure(self) -> None:
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            async with self.app.db.transaction() as db:
                await db.execute(f"""CREATE TABLE IF NOT EXISTS {TABLE}(
                    bot_id INTEGER NOT NULL, chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL, sent_at REAL NOT NULL,
                    due_at REAL NOT NULL, source TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(bot_id,chat_id,message_id))""")
                await db.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_due ON {TABLE}(bot_id,state,due_at)")
            self._ready = True

    async def start(self) -> None:
        await self.ensure()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker(), name="notice-cleanup")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def register(self, chat_id: int, message_id: int, sent_at: float, due_at: float, source: str) -> None:
        if message_id <= 0 or source not in {"system", "manual"}:
            raise ValueError("invalid deletion record")
        await self.ensure()
        async with self.app.db.transaction() as db:
            await db.execute(f"""INSERT INTO {TABLE}
                (bot_id,chat_id,message_id,sent_at,due_at,source) VALUES(?,?,?,?,?,?)
                ON CONFLICT(bot_id,chat_id,message_id) DO UPDATE SET
                due_at=MIN(due_at,excluded.due_at), state='pending', error=''
                """, (self.app.me.id, chat_id, message_id, sent_at, due_at, source))
        self._wake.set()

    async def notice(self, message: Message, text: str, *, transient: bool = True) -> None:
        """Reply with a service notice; in groups it disappears after NOTICE_SECONDS."""
        seconds = self.app.settings.notice_seconds
        expires = transient and seconds > 0 and message.chat.type in {"group", "supergroup"}
        if expires:
            await self.ensure()  # the journal must be usable before we send
        sent = await message.answer(text, parse_mode=None)
        if expires and sent.chat.id == message.chat.id and sent.message_id > 0:
            await self.register(sent.chat.id, sent.message_id, sent.date.timestamp(), time.time() + seconds, "system")

    async def _set(self, chat_id: int, mid: int, state: str, error: str = "", retry_at: float | None = None) -> None:
        async with self.app.db.transaction() as db:
            await db.execute(f"""UPDATE {TABLE} SET state=?,error=?,attempts=attempts+1,
                due_at=COALESCE(?,due_at) WHERE bot_id=? AND chat_id=? AND message_id=?""",
                (state, error, retry_at, self.app.me.id, chat_id, mid))

    async def attempt(self, row: dict) -> str:
        """Delete one journaled own message; serialized between the worker and /del."""
        async with self._operation_lock:
            if row["bot_id"] != self.app.me.id:
                return "refused"
            chat, mid = row["chat_id"], row["message_id"]
            now = time.time()
            if not 0 <= now - row["sent_at"] < MAX_AGE:
                await self._set(chat, mid, "expired", "age")
                return "expired"
            if now < self._pause_until:
                await self._set(chat, mid, "pending", "rate_limit", self._pause_until)
                return "pending"
            try:
                if await self.app.bot.delete_message(chat_id=chat, message_id=mid, request_timeout=15) is not True:
                    await self._set(chat, mid, "failed", "not_confirmed")
                    return "failed"
            except TelegramRetryAfter as error:
                self._pause_until = now + max(1, error.retry_after) + 1
                await self._set(chat, mid, "pending", "rate_limit", self._pause_until)
                return "pending"
            except (TelegramNetworkError, TelegramServerError, TimeoutError):
                attempts = row.get("attempts", 0)
                if attempts >= 7:
                    await self._set(chat, mid, "failed", "network")
                    return "failed"
                await self._set(chat, mid, "pending", "network", now + min(300, 10 * 2**attempts))
                return "pending"
            except TelegramBadRequest as error:
                # Inspected, never logged: error texts may contain URLs.
                if "message to delete not found" in str(error).lower():
                    await self._set(chat, mid, "deleted", "already_absent")
                    return "absent"
                await self._set(chat, mid, "failed", "telegram_rejected")
                return "failed"
            except TelegramAPIError:
                await self._set(chat, mid, "failed", "telegram_rejected")
                return "failed"
            await self._set(chat, mid, "deleted")
            return "deleted"

    async def sweep(self) -> int:
        await self.ensure()
        async with self.app.db.transaction() as db:
            async with db.execute(f"SELECT * FROM {TABLE} WHERE bot_id=? AND state='pending' AND due_at<=? "
                                  "ORDER BY due_at LIMIT 20", (self.app.me.id, time.time())) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
        for row in rows:
            await self.attempt(row)
            await asyncio.sleep(0.10)
        now = time.time()
        if now - self._last_prune > 3600:
            async with self.app.db.transaction() as db:
                await db.execute(f"DELETE FROM {TABLE} WHERE state!='pending' AND sent_at<?", (now - 7 * 86400,))
            self._last_prune = now
        return len(rows)

    async def _worker(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("Notice cleanup failed (%s); will retry", type(error).__name__)
                await asyncio.sleep(10)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def delete_reply(self, message: Message) -> None:
        """/del as a reply to one of the bot's messages (owner only)."""
        user = message.from_user
        if not user or user.is_bot or message.sender_chat:
            return
        if not await self.app.access.is_owner(user.id):
            await self.notice(message, t("del_owner_only"))
            return
        if not self.app.limiter.allow(f"manual_delete:{user.id}", 10, 60):
            await self.notice(message, t("del_rate_limited"))
            return
        target = message.reply_to_message
        if target is None:
            await self.notice(message, t("del_how"))
            return
        if (target.chat.id != message.chat.id or target.sender_chat or not target.from_user
                or target.from_user.id != self.app.me.id or target.message_id <= 0):
            await self.notice(message, t("del_not_mine"))
            return
        if not 0 <= time.time() - target.date.timestamp() < MAX_AGE:
            await self.notice(message, t("del_too_old"))
            return
        await self.register(message.chat.id, target.message_id, target.date.timestamp(), time.time(), "manual")
        async with self.app.db.transaction() as db:
            async with db.execute(f"SELECT * FROM {TABLE} WHERE bot_id=? AND chat_id=? AND message_id=?",
                                  (self.app.me.id, message.chat.id, target.message_id)) as cur:
                row = dict(await cur.fetchone())
        result = await self.attempt(row)
        await self.notice(message, t("del_" + result) if result in {"deleted", "absent", "expired", "pending", "failed"}
                          else t("del_failed"))
