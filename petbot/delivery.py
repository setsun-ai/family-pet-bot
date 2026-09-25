"""
At-most-once delivery: ambiguous sends require explicit owner review.

A post may be a short burst of chat messages (the AI separates them with an
empty line). They are sent one by one with a "typing…" indicator and a pause
that grows with the length - like a person typing, not a robot pasting a wall
of text. Only the first message of a burst makes a sound. The whole burst is
reserved under one delivery key, so it is never sent twice.

`Delivery` holds this logic for every platform; `DeliveryService` (Telegram)
and `DiscordDelivery` (petbot/discord_bot.py) only know how to post one
message and how to tell a rejected send from one with an unknown outcome.
"""
from __future__ import annotations

import asyncio
import logging

from .common import ServiceError, limit_text
from .db import Database
from .persona import MESSAGE_BREAK, filter_emoji, split_messages

log = logging.getLogger(__name__)
UNCERTAIN, RATE_LIMITED, REJECTED = "uncertain", "rate_limited", "rejected"


class DeliveryError(ServiceError):
    pass


def typing_pause(text: str) -> float:
    """Seconds of "typing…" before a message: ~20 characters a second, 0.8-4 s."""
    return min(4.0, 0.8 + len(text) / 20)


class Delivery:
    """Platform-neutral burst delivery. Subclasses implement _post, _typing_action and _classify."""
    max_units = 3800

    def __init__(self, db: Database, allowed_emoji: str = "", *, pauses: bool = True):
        self.db, self.allowed_emoji, self.pauses = db, allowed_emoji, pauses
        self.last_error: str | None = None

    # --- platform hooks ---
    async def _post(self, chat_id: int, text: str, reply_to: int | None, silent: bool) -> int | None:
        """Send one message; return its id."""
        raise NotImplementedError

    async def _typing_action(self, chat_id: int) -> None:
        """Show "typing…" (best effort, must not raise)."""

    def _classify(self, error: Exception) -> str | None:
        """UNCERTAIN (may have arrived), RATE_LIMITED / REJECTED (surely not sent), None = unexpected."""
        return UNCERTAIN if isinstance(error, TimeoutError) else None

    # --- shared logic ---
    async def send(self, chat_id: int, text: str, keys: list[tuple[str, str]], reply_to: int | None = None,
                   *, sound: str = "first") -> bool:
        """sound: "first" - only the first message of a burst notifies (default), "none" - all silent."""
        parts = [limit_text(filter_emoji(part, self.allowed_emoji), self.max_units) for part in split_messages(text)]
        group = await self.db.reserve_delivery(chat_id, keys, MESSAGE_BREAK.join(parts))
        if group is None:
            return False
        await self._send(group, chat_id, parts, reply_to, sound=sound)
        return True

    async def _typing(self, chat_id: int, text: str) -> None:
        if not self.pauses:
            return
        await self._typing_action(chat_id)
        await asyncio.sleep(typing_pause(text))

    async def _send(self, group: str, chat_id: int, parts: list[str], reply_to: int | None = None,
                    *, sound: str = "first") -> None:
        sent_any, last_id = False, None
        for index, part in enumerate(parts):
            try:
                if index:
                    await self._typing(chat_id, part)
                last_id = await self._post(chat_id, part, reply_to if index == 0 else None,
                                           silent=sound == "none" or index > 0)
                sent_any = True
            except asyncio.CancelledError:
                await self.db.finish_delivery(group, "uncertain", "cancelled")
                raise
            except Exception as error:
                kind = self._classify(error)
                if kind == UNCERTAIN:
                    await self.db.finish_delivery(group, "uncertain", "network_ambiguous")
                    self.last_error = str(DeliveryError("delivery_uncertain"))
                    log.warning("Delivery outcome uncertain; inspect /deliveries")
                    raise DeliveryError("delivery_uncertain") from None
                if kind in (RATE_LIMITED, REJECTED):
                    if sent_any:  # part of the burst is already in the chat: let the owner decide
                        await self.db.finish_delivery(group, "uncertain", "partial")
                        self.last_error = str(DeliveryError("delivery_uncertain"))
                        raise DeliveryError("delivery_uncertain") from None
                    await self.db.release_delivery(group)
                    key = "delivery_rate_limited" if kind == RATE_LIMITED else "delivery_rejected"
                    self.last_error = str(DeliveryError(key))
                    log.warning("The platform rejected a delivery (%s)", type(error).__name__)
                    raise DeliveryError(key) from None
                await self.db.finish_delivery(group, "uncertain", "unexpected")
                raise
        # Never retry if the success acknowledgement cannot be saved locally.
        await self.db.finish_delivery(group, "sent", message_id=last_id)
        self.last_error = None

    async def retry(self, delivery_id: int) -> bool:
        row = await self.db.take_retry(delivery_id)
        if not row:
            return False
        await self._send(row["group_id"], row["chat_id"], split_messages(row["text"]))
        return True


class DeliveryService(Delivery):
    """Telegram."""

    def __init__(self, bot, db: Database, allowed_emoji: str = "", *, pauses: bool = True):
        super().__init__(db, allowed_emoji, pauses=pauses)
        self.bot = bot

    async def _post(self, chat_id: int, text: str, reply_to: int | None, silent: bool) -> int | None:
        from aiogram.types import LinkPreviewOptions, ReplyParameters

        message = await self.bot.send_message(
            chat_id=chat_id, text=text, parse_mode=None,
            link_preview_options=LinkPreviewOptions(is_disabled=True), disable_notification=silent,
            reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None,
        )
        return message.message_id

    async def _typing_action(self, chat_id: int) -> None:
        from aiogram.exceptions import TelegramAPIError

        try:
            await self.bot.send_chat_action(chat_id, "typing")
        except TelegramAPIError:
            pass

    def _classify(self, error: Exception) -> str | None:
        from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError

        if isinstance(error, (TelegramNetworkError, TelegramServerError, TimeoutError)):
            return UNCERTAIN
        if isinstance(error, TelegramRetryAfter):
            return RATE_LIMITED
        if isinstance(error, TelegramAPIError):
            return REJECTED
        return None
