"""
Discord front end (PLATFORM=discord): the same pet as on Telegram - AI,
character, good news, matches, praise, birthdays, the database - only the
chat app is different.

- Commands are slash commands (the "/" menu). Admin commands are hidden from
  members who can't manage the server, and the bot still checks that the
  caller is the owner. Answers to them are visible only to the caller.
- The pet talks in ONE family channel (/setup_channel) and in direct messages
  with the owner and people allowed with /allow. Everyone else is ignored.
- Mentions never ping anyone (@everyone included): the AI can't spam.
- Needs the privileged MESSAGE CONTENT intent (Developer Portal -> Bot) to
  read "Mittens, hi" in the channel.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import aiohttp
import discord
from discord import app_commands

from .ai import AIService
from .common import ChatLocks, RateLimiter, ServiceError, limit_text, names_pattern
from .config import Settings
from .core import check_text, help_text, preview_text, status_text
from .db import Database
from .delivery import RATE_LIMITED, REJECTED, UNCERTAIN, Delivery
from .family import FamilyService
from .i18n import t
from .news import NewsService
from .persona import filter_emoji, split_messages, valid_nickname
from .scheduler import Scheduler
from .security import GROUP, PRIVATE, AccessControl
from .sports import SportsService

log = logging.getLogger(__name__)
LIMIT = 1900  # Discord allows 2000 characters per message
NO_PINGS = discord.AllowedMentions.none()
# View Channel + Send Messages + Read Message History
INVITE_PERMISSIONS = 1024 | 2048 | 65536
BUILTIN = {"help", "news", "forget", "privacy", "id", "claim", "setup_channel", "status", "check", "preview",
           "users", "allow", "deny", "deliveries", "retry_delivery", "names", "name", "unname", "who"}


class DiscordDelivery(Delivery):
    max_units = LIMIT

    def __init__(self, client: discord.Client, db: Database, allowed_emoji: str = "", *, pauses: bool = True):
        super().__init__(db, allowed_emoji, pauses=pauses)
        self.client = client

    async def _channel(self, chat_id: int):
        return self.client.get_channel(chat_id) or await self.client.fetch_channel(chat_id)

    async def _post(self, chat_id: int, text: str, reply_to: int | None, silent: bool) -> int | None:
        channel = await self._channel(chat_id)
        reference = (discord.MessageReference(message_id=reply_to, channel_id=chat_id, fail_if_not_exists=False)
                     if reply_to else None)
        message = await channel.send(text, reference=reference, silent=silent, suppress_embeds=True,
                                     allowed_mentions=NO_PINGS)
        return message.id

    async def _typing_action(self, chat_id: int) -> None:
        try:
            await (await self._channel(chat_id)).typing()
        except (discord.DiscordException, aiohttp.ClientError, TimeoutError):
            pass

    def _classify(self, error: Exception) -> str | None:
        if isinstance(error, discord.RateLimited):
            return RATE_LIMITED
        if isinstance(error, discord.DiscordServerError):
            return UNCERTAIN  # 5xx: the message may have been created
        if isinstance(error, discord.HTTPException):
            return RATE_LIMITED if error.status == 429 else REJECTED
        if isinstance(error, (aiohttp.ClientError, TimeoutError, OSError)):
            return UNCERTAIN
        return None


@dataclass
class DiscordApp:
    settings: Settings
    db: Database
    client: discord.Client
    ai: AIService
    news: NewsService
    sports: SportsService
    delivery: DiscordDelivery
    access: AccessControl
    scheduler: Scheduler
    limiter: RateLimiter
    locks: ChatLocks
    family: FamilyService | None = None
    last_check_ok: bool = False
    active_tasks: set[asyncio.Task] = field(default_factory=set)

    def __post_init__(self):
        self.names = names_pattern(self.settings.bot_names)


# --- conversation --------------------------------------------------------------------------------

async def _replied_message(message: discord.Message) -> discord.Message | None:
    ref = message.reference
    if ref is None or ref.message_id is None:
        return None
    if isinstance(ref.resolved, discord.Message):
        return ref.resolved
    try:
        return await message.channel.fetch_message(ref.message_id)
    except (discord.DiscordException, aiohttp.ClientError, TimeoutError):
        return None


async def handle_message(app: DiscordApp, message: discord.Message) -> None:
    """A normal (non-slash) message: typed /claim in DMs, or conversation."""
    s = app.settings
    me = app.client.user
    if me is None or message.author.bot or message.webhook_id or not message.content:
        return
    if (datetime.now(UTC) - message.created_at).total_seconds() > 300:
        return
    user, chat_id = message.author, message.channel.id
    is_private = message.guild is None
    if not app.limiter.allow(f"flood:{user.id}", 20):
        return
    text = message.clean_content.strip()  # mentions become readable @names

    claim = re.fullmatch(r"/?claim\s+(\S+)", text, re.I)
    if claim:  # typed instead of picked from the menu
        if is_private:
            await claim_owner(app, user.id, claim[1], lambda answer: message.channel.send(answer, allowed_mentions=NO_PINGS))
        return
    if not await app.access.allowed(user.id, chat_id, PRIVATE if is_private else GROUP):
        return
    if text.startswith("/"):
        if is_private and app.limiter.allow(f"hint:{user.id}", 3, 600):
            await message.channel.send(t("discord_use_menu"))
        return

    replied = await _replied_message(message)
    to_me = replied is not None and replied.author.id == me.id
    if not is_private and not (to_me or me in message.mentions or (app.names and app.names.search(text))):
        return
    if len(text) > s.max_input_chars:
        await message.channel.send(t("too_long", n=s.max_input_chars))
        return
    if not app.limiter.allow(f"ai:{user.id}", s.user_requests_per_minute):
        await message.channel.send(t("too_many"))
        return
    async with app.locks.hold(chat_id):
        if not await app.access.allowed(user.id, chat_id, PRIVATE if is_private else GROUP):
            return
        if await app.db.has_delivery(chat_id, "dialog", str(message.id)):
            return
        history = await app.db.history(chat_id, s.history_messages, s.history_days)
        nickname = await app.db.family_name(user.id)
        display_name = nickname or user.display_name or t("someone")
        async with message.channel.typing():
            answer = await app.ai.chat(history, text, display_name, reply_context=replied.content if to_me else None,
                                       verified_name=nickname is not None)
        if await app.delivery.send(chat_id, answer, [("dialog", str(message.id))], message.id):
            await app.db.save_exchange(chat_id, f"{display_name[:100]}: {text}", answer, s.history_keep)


async def claim_owner(app: DiscordApp, user_id: int, code: str, answer) -> None:
    if not app.limiter.allow("claim:global", 20):
        return
    if await app.access.claim(user_id, code.strip(), PRIVATE):
        await answer(t("claim_ok_discord"))
    else:
        await answer(t("claim_failed"))


# --- slash commands -------------------------------------------------------------------------------
# Each command is a small function taking (app, interaction, ...) so tests can call it directly.

def _where(interaction: discord.Interaction) -> tuple[bool, int]:
    return interaction.guild_id is None, interaction.channel_id


async def respond(interaction: discord.Interaction, text: str, app: DiscordApp, *, public: bool = False) -> None:
    text = limit_text(filter_emoji(text, app.settings.persona_emoji), LIMIT)
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=not public, allowed_mentions=NO_PINGS, suppress_embeds=True)
    else:
        await interaction.response.send_message(text, ephemeral=not public, allowed_mentions=NO_PINGS,
                                                suppress_embeds=True)


async def _gate(app: DiscordApp, interaction: discord.Interaction, *, owner: bool = False) -> bool:
    """May this person use this command here? Answers the refusal itself."""
    is_private, chat_id = _where(interaction)
    if not app.limiter.allow(f"flood:{interaction.user.id}", 20):
        await respond(interaction, t("too_many"), app)
        return False
    if owner:
        if await app.access.is_owner(interaction.user.id):
            return True
        await respond(interaction, t("owner_only"), app)
        return False
    if await app.access.allowed(interaction.user.id, chat_id, PRIVATE if is_private else GROUP):
        return True
    await respond(interaction, t("private_bot_discord"), app)
    return False


async def cmd_claim(app, interaction, code: str) -> None:
    is_private, _ = _where(interaction)
    if not is_private:
        await respond(interaction, t("claim_in_dm"), app)
        return
    await claim_owner(app, interaction.user.id, code, lambda answer: respond(interaction, answer, app))


async def cmd_setup_channel(app, interaction) -> None:
    if not await _gate(app, interaction, owner=True):
        return
    is_private, chat_id = _where(interaction)
    if is_private:
        await respond(interaction, t("setup_in_channel"), app)
        return
    # Interaction answers work without channel permissions - posting must be proven separately.
    try:
        await interaction.channel.send(t("setup_probe"), allowed_mentions=NO_PINGS)
    except discord.Forbidden:
        await respond(interaction, t("setup_no_permission"), app)
        return
    await app.db.set("family_chat_id", str(chat_id))
    await respond(interaction, t("setup_done_discord", chat=chat_id, tz=app.settings.timezone), app)


async def cmd_help(app, interaction) -> None:
    if await _gate(app, interaction):
        await respond(interaction, help_text(app, "discord"), app)


async def cmd_privacy(app, interaction) -> None:
    if await _gate(app, interaction):
        await respond(interaction, t("privacy", days=app.settings.history_days, platform="Discord"), app)


async def cmd_id(app, interaction) -> None:
    if app.limiter.allow("public:global", 60):
        await respond(interaction, t("your_id_discord", user=interaction.user.id, chat=interaction.channel_id), app)


async def cmd_forget(app, interaction) -> None:
    if not await _gate(app, interaction):
        return
    is_private, chat_id = _where(interaction)
    if not is_private and not await app.access.is_owner(interaction.user.id):
        await respond(interaction, t("forget_owner_only"), app)
        return
    async with app.locks.hold(chat_id):
        await app.db.forget(chat_id)
    await respond(interaction, t("forget_done", platform="Discord"), app)


async def cmd_news(app, interaction) -> None:
    if not await _gate(app, interaction):
        return
    _, chat_id = _where(interaction)
    if not app.limiter.allow(f"news:{chat_id}", 1, 60):
        await respond(interaction, t("news_rate_limited"), app)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    posted = await app.news.publish(chat_id)  # the story goes to the chat like a normal post
    await respond(interaction, t("discord_done") if posted else t("news_none"), app)


async def cmd_team(app, interaction, team) -> None:
    if not await _gate(app, interaction):
        return
    _, chat_id = _where(interaction)
    if not app.limiter.allow(f"sports:{chat_id}", 4, 60):
        await respond(interaction, t("sports_rate_limited"), app)
        return
    await interaction.response.defer(thinking=True)
    await respond(interaction, await app.sports.summary(team), app, public=True)


async def cmd_status(app, interaction) -> None:
    if await _gate(app, interaction, owner=True):
        await respond(interaction, await status_text(app), app)


async def cmd_check(app, interaction) -> None:
    if not await _gate(app, interaction, owner=True):
        return
    if not app.limiter.allow("check:global", 1, 60):
        await respond(interaction, t("check_rate_limited"), app)
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    await respond(interaction, await check_text(app), app)


async def cmd_preview(app, interaction, what: str) -> None:
    if not await _gate(app, interaction, owner=True):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    text = await preview_text(app, what)
    if text is None:
        await respond(interaction, t("preview_usage"), app)
    elif not text:
        await respond(interaction, t("preview_nothing"), app)
    else:
        await respond(interaction, t("preview_header"), app)
        for part in split_messages(text):
            await respond(interaction, part, app)


async def cmd_access(app, interaction, user, allow: bool) -> None:
    if not await _gate(app, interaction, owner=True):
        return
    if not allow and user.id == await app.db.owner():
        await respond(interaction, t("deny_owner"), app)
        return
    await app.db.allow_user(user.id, allow)
    await respond(interaction, t("allowed" if allow else "denied", id=user.id), app)


async def cmd_users(app, interaction) -> None:
    if await _gate(app, interaction, owner=True):
        users = "\n".join(map(str, await app.db.allowed_list())) or t("status_none")
        await respond(interaction, t("users_list", users=users), app)


async def cmd_deliveries(app, interaction) -> None:
    if await _gate(app, interaction, owner=True):
        rows = await app.db.uncertain_deliveries()
        body = "\n".join(f"ID {r['id']} | {r['kind']} | {r['chat_id']} | {r['created_at']}" for r in rows)
        await respond(interaction, t("deliveries_list", rows=body or t("status_none")), app)


async def cmd_retry_delivery(app, interaction, delivery_id: int) -> None:
    if await _gate(app, interaction, owner=True):
        ok = await app.delivery.retry(delivery_id)
        await respond(interaction, t("retry_done") if ok else t("retry_missing"), app)


async def cmd_names(app, interaction) -> None:
    if await _gate(app, interaction, owner=True):
        rows = await app.db.family_names()
        body = "\n".join(f"{name} — {uid}" for uid, name in rows)
        await respond(interaction, t("names_list", names=body) if rows else t("names_empty_discord"), app)


async def cmd_name(app, interaction, user, nickname: str) -> None:
    if not await _gate(app, interaction, owner=True):
        return
    if user.bot:
        await respond(interaction, t("nickname_not_bots"), app)
        return
    name = valid_nickname(nickname)
    if name is None:
        await respond(interaction, t("nickname_format_discord"), app)
        return
    await app.db.set_family_name(user.id, name)
    await respond(interaction, t("nickname_saved", name=name), app)


async def cmd_unname(app, interaction, user) -> None:
    if not await _gate(app, interaction, owner=True):
        return
    old = await app.db.family_name(user.id)
    if await app.db.delete_family_name(user.id):
        await respond(interaction, t("nickname_removed", name=old), app)
    else:
        await respond(interaction, t("nickname_none"), app)


async def cmd_who(app, interaction, user) -> None:
    if await _gate(app, interaction, owner=True):
        nickname = await app.db.family_name(user.id)
        await respond(interaction, t("who_discord", id=user.id, name=user.display_name, nickname=nickname or "—"), app)


def build_tree(app: DiscordApp, client: discord.Client) -> app_commands.CommandTree:
    """The "/" menu. Admin commands are hidden from members without Manage Server."""
    tree = app_commands.CommandTree(client)
    admin = app_commands.default_permissions(manage_guild=True)

    def add(name: str, description: str, callback, *, owner: bool = False, **options):
        if options:
            callback = app_commands.describe(**{option: text[:100] for option, text in options.items()})(callback)
        command = app_commands.Command(name=name, description=description[:100], callback=callback)
        tree.add_command(admin(command) if owner else command)

    async def claim(interaction: discord.Interaction, code: str):
        await cmd_claim(app, interaction, code)

    async def setup_channel(interaction: discord.Interaction):
        await cmd_setup_channel(app, interaction)

    async def help_(interaction: discord.Interaction):
        await cmd_help(app, interaction)

    async def privacy(interaction: discord.Interaction):
        await cmd_privacy(app, interaction)

    async def id_(interaction: discord.Interaction):
        await cmd_id(app, interaction)

    async def forget(interaction: discord.Interaction):
        await cmd_forget(app, interaction)

    async def news(interaction: discord.Interaction):
        await cmd_news(app, interaction)

    async def status(interaction: discord.Interaction):
        await cmd_status(app, interaction)

    async def check(interaction: discord.Interaction):
        await cmd_check(app, interaction)

    async def preview(interaction: discord.Interaction, what: str):
        await cmd_preview(app, interaction, what)

    async def allow(interaction: discord.Interaction, user: discord.User):
        await cmd_access(app, interaction, user, True)

    async def deny(interaction: discord.Interaction, user: discord.User):
        await cmd_access(app, interaction, user, False)

    async def users(interaction: discord.Interaction):
        await cmd_users(app, interaction)

    async def deliveries(interaction: discord.Interaction):
        await cmd_deliveries(app, interaction)

    async def retry_delivery(interaction: discord.Interaction, delivery_id: int):
        await cmd_retry_delivery(app, interaction, delivery_id)

    async def names(interaction: discord.Interaction):
        await cmd_names(app, interaction)

    async def name(interaction: discord.Interaction, user: discord.User, nickname: str):
        await cmd_name(app, interaction, user, nickname)

    async def unname(interaction: discord.Interaction, user: discord.User):
        await cmd_unname(app, interaction, user)

    async def who(interaction: discord.Interaction, user: discord.User):
        await cmd_who(app, interaction, user)

    add("help", t("cmd_help"), help_)
    if app.settings.news_enabled:
        add("news", t("cmd_news"), news)
    for team in app.sports.teams:
        if team.command in BUILTIN:
            log.warning("Team command /%s clashes with a built-in command; skipped", team.command)
            continue

        tree.add_command(app_commands.Command(name=team.command, description=f"⚽ {team.name}"[:100],
                                              callback=_team_callback(app, team)))
    add("forget", t("cmd_forget"), forget)
    add("privacy", t("cmd_privacy"), privacy)
    add("id", t("dcmd_id"), id_)
    add("claim", t("cmd_claim"), claim, code=t("opt_code"))
    add("setup_channel", t("cmd_setup_channel"), setup_channel, owner=True)
    add("status", t("cmd_status"), status, owner=True)
    add("check", t("cmd_check"), check, owner=True)
    add("preview", t("cmd_preview"), preview, owner=True, what=t("opt_what"))
    add("allow", t("dcmd_allow"), allow, owner=True, user=t("opt_user"))
    add("deny", t("dcmd_deny"), deny, owner=True, user=t("opt_user"))
    add("users", t("cmd_users"), users, owner=True)
    add("deliveries", t("cmd_deliveries"), deliveries, owner=True)
    add("retry_delivery", t("dcmd_retry_delivery"), retry_delivery, owner=True, delivery_id=t("opt_id"))
    add("names", t("dcmd_names"), names, owner=True)
    add("name", t("dcmd_name"), name, owner=True, user=t("opt_user"), nickname=t("opt_nickname"))
    add("unname", t("dcmd_unname"), unname, owner=True, user=t("opt_user"))
    add("who", t("dcmd_who"), who, owner=True, user=t("opt_user"))
    return tree


def _team_callback(app: DiscordApp, team):
    """One command per team; the callback may only take the interaction (Discord would show extra args as options)."""
    async def callback(interaction: discord.Interaction):
        await cmd_team(app, interaction, team)
    return callback


# --- running ------------------------------------------------------------------------------------------

class PetClient(discord.Client):
    def __init__(self):
        discord.VoiceClient.warn_nacl = discord.VoiceClient.warn_dave = False  # the pet never uses voice
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents, allowed_mentions=NO_PINGS)
        self.app: DiscordApp | None = None
        self.tree: app_commands.CommandTree | None = None

    async def setup_hook(self) -> None:
        self.tree = build_tree(self.app, self)
        self.tree.on_error = self.on_command_error
        await self.tree.sync()
        url = discord.utils.oauth_url(self.application_id, permissions=discord.Permissions(INVITE_PERMISSIONS),
                                      scopes=("bot", "applications.commands"))
        print("\n" + t("console_discord_invite", url=url), flush=True)
        if not await self.app.db.owner():
            print(t("console_claim_discord", code=self.app.access.claim_code) + "\n", flush=True)
        self.app.scheduler.start()

    async def on_ready(self) -> None:
        logging.info(t("console_started_discord", name=str(self.user), tz=self.app.settings.timezone))

    async def on_message(self, message: discord.Message) -> None:
        task = asyncio.current_task()
        if task is not None:
            self.app.active_tasks.add(task)
        try:
            await handle_message(self.app, message)
        except ServiceError as error:
            try:
                await message.channel.send("⚠️ " + str(error), allowed_mentions=NO_PINGS)
            except discord.DiscordException:
                log.warning("Could not deliver an error notice")
        except discord.DiscordException as error:
            log.warning("Discord rejected a response (%s)", type(error).__name__)
        except Exception as error:
            log.error("Handler failed (%s); message content and secrets are not logged", type(error).__name__)
        finally:
            if task is not None:
                self.app.active_tasks.discard(task)

    async def on_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        original = getattr(error, "original", error)
        text = "⚠️ " + str(original) if isinstance(original, ServiceError) else t("internal_error")
        if not isinstance(original, ServiceError):
            log.error("Command failed (%s)", type(original).__name__)
        try:
            await respond(interaction, text, self.app)
        except discord.DiscordException:
            pass


async def run(settings: Settings, check: bool = False) -> int:
    import httpx

    from .family import load_family
    from .sports import teams_from_settings

    logging.getLogger("discord").setLevel(logging.WARNING)
    db = Database(settings.database_path)
    client = PetClient()
    scheduler = None
    try:
        await db.open()
        if settings.owner_id is not None:
            await db.set("owner_id", str(settings.owner_id))
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(settings.ai_timeout, connect=10),
            limits=httpx.Limits(max_connections=12, max_keepalive_connections=8),
            max_redirects=5,
        ) as http:
            ai = AIService(settings, db, http)
            delivery = DiscordDelivery(client, db, settings.persona_emoji)
            news = NewsService(settings, db, ai, http, delivery)
            sports = SportsService(settings, http, ai, teams_from_settings(settings))
            family = FamilyService(settings, db, ai, load_family(settings.family_file))
            access = AccessControl(db)
            scheduler = Scheduler(settings, db, news, sports, delivery, family)
            client.app = DiscordApp(settings, db, client, ai, news, sports, delivery, access, scheduler,
                                    RateLimiter(), ChatLocks(), family=family)
            if check:
                await client.login(settings.bot_token)  # validates the token; no gateway connection
                print(f"Discord: OK — {client.user}")
                print(await check_text(client.app))
                return 0 if client.app.last_check_ok else 1
            await client.start(settings.bot_token)
        return 0
    except discord.LoginFailure:
        logging.error(t("console_discord_login"))
        return 1
    except discord.PrivilegedIntentsRequired:
        logging.error(t("console_discord_intents"))
        return 1
    except Exception as error:
        logging.error(t("console_crash", kind=type(error).__name__))
        return 1
    finally:
        if scheduler:
            await scheduler.stop()
        if client.app and client.app.active_tasks:
            _, pending = await asyncio.wait(list(client.app.active_tasks), timeout=10)
            for task in pending:
                task.cancel()
        if not client.is_closed():
            await client.close()
        await db.close()
