"""
The "/" command menus in Telegram, one per audience:

- everyone: the everyday commands (help, news, teams, forget...);
- the owner's private chat with the bot: + all owner commands (status, preview...);
- the owner in the family group: + the nickname and setup commands.

Family members never see admin commands they couldn't use anyway. Telegram
picks the most specific menu: owner-in-group > owner's private chat > default.
"""
from __future__ import annotations

import logging

from .i18n import t

log = logging.getLogger(__name__)

OWNER_PRIVATE = ("status", "check", "preview", "names", "users", "allow", "deny", "deliveries", "retry_delivery")
OWNER_GROUP = ("who", "name", "unname", "names", "setup_chat")


def bot_commands(settings, teams=()) -> list[tuple[str, str]]:
    """The menu everyone sees (only what is enabled)."""
    commands = [("help", t("cmd_help"))]
    if settings.news_enabled:
        commands.append(("news", t("cmd_news")))
    for team in teams:
        commands.append((team.command, f"⚽ {team.name}"[:256]))
    commands += [("forget", t("cmd_forget")), ("privacy", t("cmd_privacy")), ("id", t("cmd_id")),
                 ("del", t("cmd_del"))]
    return commands


def owner_private_commands(settings, teams=()) -> list[tuple[str, str]]:
    return bot_commands(settings, teams) + [(c, t("cmd_" + c)) for c in OWNER_PRIVATE]


def owner_group_commands(settings, teams=()) -> list[tuple[str, str]]:
    return bot_commands(settings, teams) + [(c, t("cmd_" + c)) for c in OWNER_GROUP]


async def publish_menus(bot, db, settings, teams=()) -> None:
    """Set all menus. Never fatal: a missing menu must not stop the bot."""
    from aiogram.exceptions import TelegramAPIError
    from aiogram.types import (
        BotCommand,
        BotCommandScopeAllChatAdministrators,
        BotCommandScopeAllGroupChats,
        BotCommandScopeAllPrivateChats,
        BotCommandScopeChat,
        BotCommandScopeChatMember,
    )

    def menu(items):
        return [BotCommand(command=c, description=d) for c, d in items]

    try:
        # Broader scopes left by an older bot on this token would hide the default menu.
        for scope in (BotCommandScopeAllPrivateChats(), BotCommandScopeAllGroupChats(),
                      BotCommandScopeAllChatAdministrators()):
            await bot.delete_my_commands(scope=scope)
        await bot.set_my_commands(menu(bot_commands(settings, teams)))
        owner, family = await db.owner(), await db.family()
        if owner:
            await bot.set_my_commands(menu(owner_private_commands(settings, teams)),
                                      scope=BotCommandScopeChat(chat_id=owner))
        if owner and family:
            await bot.set_my_commands(menu(owner_group_commands(settings, teams)),
                                      scope=BotCommandScopeChatMember(chat_id=family, user_id=owner))
    except TelegramAPIError as error:
        log.warning("Could not set the command menus (%s)", type(error).__name__)
