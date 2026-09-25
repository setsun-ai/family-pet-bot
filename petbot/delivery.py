"""At-most-once automatic delivery: ambiguous sends require explicit owner review."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import LinkPreviewOptions, ReplyParameters

from .common import ServiceError, limit_text
from .db import Database
from .persona import filter_emoji

log = logging.getLogger(__name__)


class DeliveryError(ServiceError):
    pass


class DeliveryService:
    def __init__(self, bot: Bot, db: Database, allowed_emoji: str = ""):
        self.bot, self.db, self.allowed_emoji = bot, db, allowed_emoji
        self.last_error: str | None = None

    async def send(self, chat_id: int, text: str, keys: list[tuple[str, str]], reply_to: int | None = None) -> bool:
        text = limit_text(filter_emoji(text, self.allowed_emoji))
        group = await self.db.reserve_delivery(chat_id, keys, text)
        if group is None:
            return False
        await self._send(group, chat_id, text, reply_to)
        return True

    async def _send(self, group: str, chat_id: int, text: str, reply_to: int | None = None) -> None:
        try:
            message = await self.bot.send_message(
                chat_id=chat_id, text=text, parse_mode=None,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None,
            )
        except (TelegramNetworkError, TelegramServerError, TimeoutError):
            await self.db.finish_delivery(group, "uncertain", "network_ambiguous")
            self.last_error = str(DeliveryError("delivery_uncertain"))
            log.warning("Delivery outcome uncertain; inspect /deliveries")
            raise DeliveryError("delivery_uncertain") from None
        except asyncio.CancelledError:
            await self.db.finish_delivery(group, "uncertain", "cancelled")
            raise
        except TelegramRetryAfter:
            await self.db.release_delivery(group)
            self.last_error = str(DeliveryError("delivery_rate_limited"))
            raise DeliveryError("delivery_rate_limited") from None
        except TelegramAPIError:
            await self.db.release_delivery(group)
            self.last_error = str(DeliveryError("delivery_rejected"))
            log.warning("Telegram rejected delivery; check chat permissions")
            raise DeliveryError("delivery_rejected") from None
        except Exception:
            await self.db.finish_delivery(group, "uncertain", "unexpected")
            raise
        # Never retry if the success acknowledgement cannot be saved locally.
        await self.db.finish_delivery(group, "sent", message_id=message.message_id)
        self.last_error = None

    async def retry(self, delivery_id: int) -> bool:
        row = await self.db.take_retry(delivery_id)
        if not row:
            return False
        await self._send(row["group_id"], row["chat_id"], row["text"])
        return True
