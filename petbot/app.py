"""Starting the bot: logging with secret redaction, the Telegram polling loop (Discord: discord_bot.py), shutdown."""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from pathlib import Path

from .config import Settings
from .i18n import t
from .menus import bot_commands, publish_menus  # noqa: F401  (bot_commands: public API)


class RedactingFormatter(logging.Formatter):
    """Logs never contain the bot token or API keys - even inside library error messages."""

    def __init__(self, secrets: tuple[str, ...]):
        super().__init__("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        self.secrets = tuple(value for value in secrets if value)

    def format(self, record: logging.LogRecord) -> str:
        result = super().format(record)
        for value in self.secrets:
            result = result.replace(value, "[REDACTED]")
        result = re.sub(r"\b\d{5,16}:[A-Za-z0-9_-]{20,}", "[BOT_TOKEN]", result)
        result = re.sub(r"\b[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}", "[BOT_TOKEN]", result)  # Discord
        return re.sub(r"\bsk-[A-Za-z0-9_-]{10,}", "[API_KEY]", result)


def setup_logging(settings: Settings, log_file: str | None = None) -> None:
    formatter = RedactingFormatter((settings.bot_token, settings.api_key, settings.sports_api_key
                                    if settings.sports_api_key != "3" else ""))
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # pythonw.exe (hidden autostart on Windows) has no console
        handlers.append(logging.StreamHandler())
    if log_file:
        from logging.handlers import RotatingFileHandler

        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers or [logging.NullHandler()], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


async def run(settings: Settings, check: bool = False) -> int:
    if settings.platform == "discord":
        from .discord_bot import run as run_discord
        return await run_discord(settings, check)
    # The Telegram/HTTP stack is imported only when the bot really runs.
    import httpx
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.exceptions import TelegramAPIError

    from .ai import AIService
    from .common import ChatLocks, RateLimiter
    from .db import Database
    from .delivery import DeliveryService
    from .family import FamilyService, load_family
    from .handlers import App, check_text, make_router
    from .news import NewsService
    from .scheduler import Scheduler
    from .security import AccessControl
    from .sports import SportsService, teams_from_settings

    db = Database(settings.database_path)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=None))
    scheduler = app = None
    try:
        await db.open()
        if settings.owner_id is not None:
            await db.set("owner_id", str(settings.owner_id))
        me = await bot.me()  # cached identity - not requested for every message
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_timeout, connect=10),
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=8),
            max_redirects=5,
        ) as client:
            ai = AIService(settings, db, client)
            delivery = DeliveryService(bot, db, settings.persona_emoji)
            news = NewsService(settings, db, ai, client, delivery)
            sports = SportsService(settings, client, ai, teams_from_settings(settings))
            family = FamilyService(settings, db, ai, load_family(settings.family_file))
            access = AccessControl(db)
            scheduler = Scheduler(settings, db, news, sports, delivery, family)
            app = App(settings, db, bot, me, ai, news, sports, delivery, access, scheduler, RateLimiter(), ChatLocks(),
                      family=family)
            if check:
                print("Telegram: OK — @" + (me.username or str(me.id)))
                print(await check_text(app))
                return 0 if app.last_check_ok else 1
            if not await db.owner():
                print("\n" + t("console_claim", username=me.username, code=access.claim_code) + "\n", flush=True)
            await app.housekeeping.start()
            dispatcher = Dispatcher()
            dispatcher.include_router(make_router(app))
            await bot.delete_webhook(drop_pending_updates=False)
            await publish_menus(bot, db, settings, sports.teams)
            scheduler.start()
            logging.info(t("console_started", username=me.username, tz=settings.timezone))
            try:
                await dispatcher.start_polling(bot, allowed_updates=["message"], tasks_concurrency_limit=32,
                                               close_bot_session=False)
            finally:
                await scheduler.stop()
                if app.active_tasks:
                    _, pending = await asyncio.wait(list(app.active_tasks), timeout=10)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
        return 0
    except TelegramAPIError as error:
        logging.error(t("console_telegram_error", kind=type(error).__name__))
        return 1
    except Exception as error:
        logging.error(t("console_crash", kind=type(error).__name__))
        return 1
    finally:
        if scheduler:
            await scheduler.stop()
        if app is not None:
            await app.housekeeping.stop()
        await bot.session.close()
        await db.close()
