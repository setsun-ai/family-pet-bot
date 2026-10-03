"""
Telegram message handling: access control, commands and conversation.

Who may talk to the bot:
- the owner (claimed once with a one-time code from the console),
- people the owner allowed with /allow (private chat),
- members of the ONE family group chosen by the owner with /setup_chat.
Everyone else is ignored - they can't spend your AI budget.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, MessageReactionUpdated, ReactionTypeEmoji, User

from . import __version__, updater
from .ai import AIService
from .common import ChatLocks, RateLimiter, ServiceError, addressed, asks_for_news, limit_text, names_pattern
from .config import Settings
from .core import check_text, help_text, memory_text, mood_text, preview_text, stats_text, status_text  # noqa: F401
from .db import Database
from .delivery import DeliveryService
from .family import FamilyService
from .housekeeping import Housekeeping
from .i18n import t
from .menus import publish_menus
from .mood import MoodService
from .news import NewsService
from .notice import Noticer
from .persona import filter_emoji, split_messages, valid_nickname
from .scheduler import Scheduler
from .security import AccessControl
from .sports import SportsService

log = logging.getLogger(__name__)

OWNER_PRIVATE_COMMANDS = {"status", "check", "allow", "deny", "users", "deliveries", "retry_delivery", "preview",
                          "mood", "memory", "stats", "update", "rollback"}
STARTED = datetime.now(UTC).replace(microsecond=0)
NICKNAME_COMMANDS = {"who", "name", "names", "unname"}


@dataclass
class App:
    settings: Settings
    db: Database
    bot: Bot
    me: User
    ai: AIService
    news: NewsService
    sports: SportsService
    delivery: DeliveryService
    access: AccessControl
    scheduler: Scheduler
    limiter: RateLimiter
    locks: ChatLocks
    active_tasks: set[asyncio.Task] = field(default_factory=set)
    last_check_ok: bool = False
    housekeeping: Housekeeping | None = None
    family: FamilyService | None = None
    noticer: Noticer | None = None
    mood: MoodService | None = None
    dispatcher: object | None = None  # aiogram Dispatcher, so /update can stop polling and restart
    updating: bool = False
    stop_task: asyncio.Task | None = None

    def __post_init__(self):
        self.names = names_pattern(self.settings.bot_names)
        if self.housekeeping is None:
            self.housekeeping = Housekeeping(self)


async def reply(message: Message, text: str, app: App, *, transient: bool = True) -> None:
    text = limit_text(filter_emoji(text, app.settings.persona_emoji))
    await app.housekeeping.notice(message, text, transient=transient)


async def typing(message: Message) -> None:
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
    except TelegramAPIError:
        pass


def split_command(text: str, username: str) -> tuple[str, str]:
    """'/news@my_bot args' -> ('news', 'args'); commands for other bots -> ('__other_bot__', '')."""
    if not text.startswith("/"):
        return "", ""
    head, _, args = text.partition(" ")
    cmd, _, target = head[1:].partition("@")
    if target and target.casefold() != username.casefold():
        return "__other_bot__", ""
    return cmd.lower(), args.strip()


async def handle(message: Message, app: App) -> None:
    s = app.settings
    # Group -> supergroup upgrades change the chat id; follow it.
    if message.migrate_to_chat_id:
        if await app.db.family() == message.chat.id:
            await app.db.migrate_chat(message.chat.id, message.migrate_to_chat_id)
        return
    if message.migrate_from_chat_id:
        if await app.db.family() == message.migrate_from_chat_id:
            await app.db.migrate_chat(message.migrate_from_chat_id, message.chat.id)
        return
    user = message.from_user
    if not user or user.is_bot or message.sender_chat:
        return
    if message.chat.type not in {ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP}:
        return
    # Don't spend money on messages queued while the bot was offline.
    if (datetime.now(UTC) - message.date).total_seconds() > 300:
        return
    if not message.text:
        if message.photo or message.animation or message.video or message.sticker:
            await notice_media(message, app)
        return
    text = message.text.strip()
    command, args = split_command(text, app.me.username or "")
    if command == "__other_bot__":
        return
    is_private = message.chat.type == ChatType.PRIVATE
    owner = await app.access.is_owner(user.id)
    allowed = await app.access.allowed(user.id, message.chat.id, message.chat.type)
    if not app.limiter.allow(f"flood:{user.id}", 20):
        return
    if allowed and not is_private:
        app.scheduler.note_activity(message.chat.id, text=text)

    # --- available to anyone (needed before access is granted) ---
    if command == "start" and args.startswith("claim_"):
        command, args = "claim", args[len("claim_"):]
    if command == "claim":
        if not app.limiter.allow("claim:global", 20):
            return
        if await app.access.claim(user.id, args, message.chat.type):
            await reply(message, t("claim_ok"), app)
            await publish_menus(app.bot, app.db, s, app.sports.teams)  # the owner's menu appears right away
        elif is_private:
            await reply(message, t("claim_failed"), app)
        return
    if command == "id":
        if app.limiter.allow("public:global", 60):
            await reply(message, t("your_id", user=user.id, chat=message.chat.id), app)
        return
    if command == "setup_chat":
        if not owner:
            if allowed or is_private:
                await reply(message, t("setup_owner_only"), app)
            return
        if is_private:
            await reply(message, t("setup_in_group"), app)
            return
        # A successful send proves the bot can write here before we redirect posts.
        await reply(message, t("setup_probe"), app)
        await app.db.set("family_chat_id", str(message.chat.id))
        await reply(message, t("setup_done", chat=message.chat.id, tz=s.timezone), app)
        await publish_menus(app.bot, app.db, s, app.sports.teams)
        return
    if not allowed:
        if is_private and command in {"start", "help", "privacy"} and app.limiter.allow("public:global", 60):
            await reply(message, t("private_bot"), app)
        return

    # --- nicknames (owner, as a reply in the family group) ---
    if command in NICKNAME_COMMANDS:
        await nickname_command(message, app, command, args, owner, is_private)
        return
    if command == "del":
        await app.housekeeping.delete_reply(message)
        return
    if command in {"start", "help"}:
        await reply(message, help_text(app), app, transient=False)
        return
    if command == "privacy":
        await reply(message, t("privacy", days=s.history_days, platform="Telegram"), app, transient=False)
        return
    if command in OWNER_PRIVATE_COMMANDS:
        await owner_command(message, app, command, args, owner, is_private)
        return
    if command == "forget":
        if not is_private and not owner:
            await reply(message, t("forget_owner_only"), app)
            return
        async with app.locks.hold(message.chat.id):
            await app.db.forget(message.chat.id)
        await reply(message, t("forget_done", platform="Telegram"), app)
        return
    team = app.sports.team(command)
    if team is not None:
        if not app.limiter.allow(f"sports:{message.chat.id}", 4, 60):
            await reply(message, t("sports_rate_limited"), app)
            return
        await typing(message)
        await reply(message, await app.sports.summary(team), app, transient=False)
        return
    if command == "news" and s.news_enabled:
        if not app.limiter.allow(f"news:{message.chat.id}", 1, 60):
            await reply(message, t("news_rate_limited"), app)
            return
        await typing(message)
        if not await app.news.publish(message.chat.id):
            await reply(message, t("news_none"), app)
        return
    if command:
        await reply(message, t("unknown_command"), app)
        return

    # --- conversation ---
    reply_author = message.reply_to_message.from_user if message.reply_to_message else None
    if not is_private and not addressed(text, app.me.username or "", reply_author.id if reply_author else None,
                                        app.me.id, app.names):
        if app.noticer and not command:
            await app.noticer.notice(app, message.chat.id, message.message_id, user.id, text,
                                     lambda emoji: message.react([ReactionTypeEmoji(emoji=emoji)]))
        return
    if reply_author and reply_author.id == app.me.id:  # a reply to the pet's message counts for /stats
        await app.db.add_feedback(message.chat.id, message.reply_to_message.message_id, user.id, "↩")
    if len(text) > s.max_input_chars:
        await reply(message, t("too_long", n=s.max_input_chars), app)
        return
    # "Any news?" - a ready story from the stock: instant, no AI call.
    if s.news_enabled and asks_for_news(text) and app.limiter.allow(f"news:{message.chat.id}", 1, 60):
        if await app.news.from_stock(message.chat.id):
            return
    if not app.limiter.allow(f"ai:{user.id}", s.user_requests_per_minute):
        await reply(message, t("too_many"), app)
        return
    async with app.locks.hold(message.chat.id):
        # Re-check after waiting: access may have been revoked meanwhile.
        if not await app.access.allowed(user.id, message.chat.id, message.chat.type):
            return
        if await app.db.has_delivery(message.chat.id, "dialog", str(message.message_id)):
            return
        await typing(message)
        history = await app.db.history(message.chat.id, s.history_messages, s.history_days)
        nickname = await app.db.family_name(user.id)
        display_name = nickname or user.first_name or user.username or t("someone")
        # Quote only the bot's own message - never other family conversation.
        context = None
        if message.reply_to_message and reply_author and reply_author.id == app.me.id:
            context = message.reply_to_message.text or message.reply_to_message.caption
        answer = await app.ai.chat(history, text, display_name, reply_context=context,
                                   verified_name=nickname is not None, chat_id=message.chat.id)
        sent = await app.delivery.send(message.chat.id, answer, [("dialog", str(message.message_id))], message.message_id)
        if sent:
            await app.db.save_exchange(message.chat.id, f"{display_name[:100]}: {text}", answer, s.history_keep)


async def notice_media(message: Message, app: App) -> None:
    """A picture, GIF, video or sticker in the family group: maybe an emoji (REACTIONS_FILE rules with "media")."""
    if app.noticer is None or message.chat.type == ChatType.PRIVATE:
        return
    user = message.from_user
    if not await app.access.allowed(user.id, message.chat.id, message.chat.type) or \
            not app.limiter.allow(f"flood:{user.id}", 20):
        return
    caption = (message.caption or "").strip()
    app.scheduler.note_activity(message.chat.id, text=caption)
    await app.noticer.notice(app, message.chat.id, message.message_id, user.id, caption,
                             lambda emoji: message.react([ReactionTypeEmoji(emoji=emoji)]), media=True)


async def reaction_update(update: MessageReactionUpdated, app: App) -> None:
    """Someone reacted to a message: if it's the pet's, remember it for /stats (needs the bot to be a group admin)."""
    user = update.user
    if user is None or user.is_bot or update.chat.id != await app.db.family():
        return
    if await app.db.sent_kind(update.chat.id, update.message_id) is None:
        return
    emojis = [r.emoji for r in update.new_reaction if isinstance(r, ReactionTypeEmoji)]
    await app.db.set_feedback(update.chat.id, update.message_id, user.id, emojis)


async def nickname_command(message: Message, app: App, command: str, args: str, owner: bool, is_private: bool) -> None:
    if not owner:
        await reply(message, t("owner_only"), app)
        return
    if not app.limiter.allow(f"nicknames:{message.from_user.id}", 20):
        return
    if command == "names":
        rows = await app.db.family_names()
        body = "\n".join(f"{name} — {uid}" for uid, name in rows)
        await reply(message, t("names_list", names=body) if rows else t("names_empty"), app, transient=not rows)
        return
    if is_private:
        await reply(message, t("nickname_in_group", command=command), app)
        return
    target_message = message.reply_to_message
    target = None if target_message is None or target_message.sender_chat else target_message.from_user
    if target is None:
        await reply(message, t("nickname_reply_needed", command=command), app)
        return
    if target.is_bot:
        await reply(message, t("nickname_not_bots"), app)
        return
    if command == "who":
        nickname = await app.db.family_name(target.id)
        await reply(message, t("who", id=target.id, name=target.full_name,
                               username=f"@{target.username}" if target.username else "—",
                               nickname=nickname or "—"), app)
        return
    if command == "name":
        name = valid_nickname(args)
        if name is None:
            await reply(message, t("nickname_format"), app)
            return
        await app.db.set_family_name(target.id, name)
        await reply(message, t("nickname_saved", name=name), app)
        return
    old = await app.db.family_name(target.id)
    if await app.db.delete_family_name(target.id):
        await reply(message, t("nickname_removed", name=old), app)
    else:
        await reply(message, t("nickname_none"), app)


async def owner_command(message: Message, app: App, command: str, args: str, owner: bool, is_private: bool) -> None:
    if not owner or not is_private:
        await reply(message, t("owner_private_only"), app)
        return
    if not app.limiter.allow(f"admin:{message.from_user.id}", 10):
        return
    if command == "status":
        await reply(message, await status_text(app), app)
    elif command == "check":
        if app.limiter.allow("check:global", 1, 60):
            await typing(message)
            await reply(message, await check_text(app), app)
        else:
            await reply(message, t("check_rate_limited"), app)
    elif command in {"allow", "deny"}:
        try:
            uid = int(args)
            if not 0 < uid < 2**63:
                raise ValueError
        except ValueError:
            await reply(message, t("allow_format", command=command), app)
            return
        if uid == await app.db.owner() and command == "deny":
            await reply(message, t("deny_owner"), app)
            return
        await app.db.allow_user(uid, command == "allow")
        await reply(message, t("allowed" if command == "allow" else "denied", id=uid), app)
    elif command == "users":
        users = "\n".join(map(str, await app.db.allowed_list())) or t("status_none")
        await reply(message, t("users_list", users=users), app)
    elif command == "deliveries":
        rows = await app.db.uncertain_deliveries()
        body = "\n".join(f"ID {r['id']} | {r['kind']} | {r['chat_id']} | {r['created_at']}" for r in rows)
        await reply(message, t("deliveries_list", rows=body or t("status_none")), app)
    elif command == "preview":
        await preview_command(message, app, args)
    elif command == "mood":
        await reply(message, await mood_text(app, args), app)
    elif command == "memory":
        await reply(message, await memory_text(app, args), app, transient=False)
    elif command == "stats":
        await reply(message, await stats_text(app), app, transient=False)
    elif command in {"update", "rollback"}:
        # After a restart Telegram delivers the same /update again: commands older than this process are skipped.
        if message.date < STARTED:
            return
        await (update_command if command == "update" else rollback_command)(message, app)
    else:
        parts = args.split()
        if len(parts) != 2 or not parts[0].isdigit() or parts[1] != "confirm":
            await reply(message, t("retry_format"), app)
            return
        ok = await app.delivery.retry(int(parts[0]))
        await reply(message, t("retry_done") if ok else t("retry_missing"), app)


async def preview_command(message: Message, app: App, args: str) -> None:
    """/preview: see a post before the family does (details: core.preview_text)."""
    await typing(message)
    text = await preview_text(app, args)
    if text is None:
        await reply(message, t("preview_usage"), app)
        return
    if not text:
        await reply(message, t("preview_nothing"), app)
        return
    await reply(message, t("preview_header"), app)
    for part in split_messages(text):
        await reply(message, part, app, transient=False)


async def update_command(message: Message, app: App) -> None:
    """/update: install the newest GitHub release (details: updater.py), then restart."""
    if app.updating:
        await reply(message, t("update_busy"), app)
        return
    app.updating = True
    try:
        await reply(message, t("update_checking"), app)
        try:
            tag = await asyncio.to_thread(updater.latest_release)
            if not updater.newer(tag):
                await reply(message, t("update_latest", version=__version__), app, transient=False)
                return
            await reply(message, t("update_installing", tag=tag, version=__version__), app)
            notes = await asyncio.to_thread(updater.install, tag)
        except updater.UpdateError as error:
            await reply(message, t("update_failed", version=__version__, error=str(error)), app, transient=False)
            return
        except Exception as error:
            await reply(message, t("update_failed", version=__version__, error=type(error).__name__), app,
                        transient=False)
            return
        await reply(message, t("update_restart", tag=tag, notes=notes), app, transient=False)
        restart_bot(app)
    finally:
        app.updating = False


async def rollback_command(message: Message, app: App) -> None:
    """/rollback: back to the version that was active before the last /update (again = undo)."""
    if app.updating:
        await reply(message, t("update_busy"), app)
        return
    version = await asyncio.to_thread(updater.rollback)
    if version is None:
        await reply(message, t("rollback_none"), app, transient=False)
        return
    await reply(message, t("rollback_restart", version=version), app, transient=False)
    restart_bot(app)


def restart_bot(app: App) -> None:
    """Stop polling gracefully; __main__ then starts the new code (updater.restart)."""
    updater.request_restart()
    if app.dispatcher is not None:
        app.stop_task = asyncio.create_task(app.dispatcher.stop_polling())


def make_router(app: App) -> Router:
    router = Router(name="petbot")

    @router.message()
    async def dispatch(message: Message):
        task = asyncio.current_task()
        if task is not None:
            app.active_tasks.add(task)
        try:
            await handle(message, app)
        except ServiceError as error:
            try:
                await reply(message, "⚠️ " + str(error), app)
            except TelegramAPIError:
                log.warning("Could not deliver an error notice")
        except TelegramAPIError as error:
            log.warning("Telegram rejected a response (%s)", type(error).__name__)
        except Exception as error:
            log.error("Handler failed (%s); message content and secrets are not logged", type(error).__name__)
            try:
                await reply(message, t("internal_error"), app)
            except TelegramAPIError:
                pass
        finally:
            if task is not None:
                app.active_tasks.discard(task)

    @router.message_reaction()
    async def reactions(update: MessageReactionUpdated):
        try:
            await reaction_update(update, app)
        except Exception as error:
            log.warning("Reaction update failed (%s)", type(error).__name__)

    return router
