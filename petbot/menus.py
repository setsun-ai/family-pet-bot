"""
The "/" command menus in Telegram, one per audience:

- everyone: the everyday commands (help, news, teams, forget...);
- the owner's private chat with the bot: + /menu, the owner's commands as tiles (status, preview...);
- the owner in the family group: + the nickname and setup commands.

Family members never see admin commands they couldn't use anyway. Telegram
picks the most specific menu: owner-in-group > owner's private chat > default.
"""
from __future__ import annotations

import logging

from .i18n import t

log = logging.getLogger(__name__)

# The owner's tiles (/menu, private chat): group -> rows; a tile that is itself a group opens it. Two a row, so
# they fit a phone in both languages. Typed commands still work (/help lists them, incl. those with an argument).
OWNER_HUB = {"": [["status", "check"], ["mood", "memory"], ["stats", "users"], ["names", "deliveries"],
                  ["preview", "bot"]],
             "bot": [["update", "rollback"], ["help"]]}
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
    return [("menu", t("cmd_menu"))] + bot_commands(settings, teams)


def hub_view(group: str = "", teams=()):
    """(text, keyboard) of the owner's tiles; "preview" lists what can be previewed, sub-menus end with ↩️ Menu."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    def tile(label, data):
        return InlineKeyboardButton(text=label, callback_data=data[:64])

    if group == "preview":
        rows = [[tile(t("tile_news"), "hp:news"), tile(t("tile_praise"), "hp:praise")],
                [tile(t("tile_birthday"), "hp:birthday")]] + [[tile(f"⚽ {team.name}", f"hp:{team.command}")]
                                                              for team in teams]
    else:
        group = group if group in OWNER_HUB else ""
        rows = [[tile(t(f"tile_{name}"), f"hg:{name}" if name in OWNER_HUB or name == "preview" else f"h:{name}")
                 for name in row] for row in OWNER_HUB[group]]
    if group:
        rows.append([tile(t("tile_menu"), "h")])
    return t(f"hub_{group}_title" if group else "hub_title"), InlineKeyboardMarkup(inline_keyboard=rows)


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
