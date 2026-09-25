"""Discord front end with a fake Discord: nothing here talks to discord.com."""
import itertools
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

import discord

from petbot.common import ChatLocks, RateLimiter
from petbot.delivery import DeliveryError
from petbot.discord_bot import (
    DiscordApp,
    DiscordDelivery,
    PetClient,
    build_tree,
    cmd_name,
    cmd_preview,
    cmd_setup_channel,
    cmd_status,
    cmd_team,
    handle_message,
)
from tests.support import FAMILY, OWNER, TEAM, Harness

ids = itertools.count(5000)
BOT = SimpleNamespace(id=999, bot=True, display_name="Whiskers")


class FakeTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __await__(self):
        yield from ()


class FakeChannel:
    def __init__(self, channel_id: int):
        self.id, self.sent, self.failure, self.messages = channel_id, [], None, {}

    async def send(self, text, **kwargs):
        if self.failure:
            raise self.failure
        message = SimpleNamespace(id=next(ids), content=text, author=BOT, kwargs=kwargs)
        self.sent.append(message)
        self.messages[message.id] = message
        return message

    def typing(self):
        return FakeTyping()

    async def fetch_message(self, message_id):
        return self.messages[message_id]


class FakeClient:
    def __init__(self):
        self.user = BOT
        self.channels = {}

    def channel(self, channel_id):
        return self.channels.setdefault(channel_id, FakeChannel(channel_id))

    def get_channel(self, channel_id):
        return self.channel(channel_id)

    async def fetch_channel(self, channel_id):
        return self.channel(channel_id)


class FakeResponse:
    def __init__(self, log):
        self.log, self.done = log, False

    def is_done(self):
        return self.done

    async def send_message(self, text, **kwargs):
        self.done = True
        self.log.append((text, kwargs))

    async def defer(self, **kwargs):
        self.done = True


class FakeInteraction:
    def __init__(self, user_id, channel, guild=True):
        self.user = SimpleNamespace(id=user_id, bot=False, display_name=f"User{user_id}")
        self.channel, self.channel_id = channel, channel.id
        self.guild_id = 1 if guild else None
        self.answers = []
        self.response = FakeResponse(self.answers)
        self.followup = SimpleNamespace(send=self._followup)

    async def _followup(self, text, **kwargs):
        self.answers.append((text, kwargs))

    def texts(self):
        return [text for text, _ in self.answers]


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


class DiscordTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(sports_enabled=True)
        self.client = FakeClient()
        self.family_channel = self.client.channel(FAMILY)
        self.delivery = DiscordDelivery(self.client, self.h.db, pauses=False)
        self.app = DiscordApp(self.h.settings, self.h.db, self.client, self.h.ai, self.h.news, self.h.sports,
                              self.delivery, self.h.access, self.h.scheduler, RateLimiter(), ChatLocks(),
                              family=self.h.family)

    async def asyncTearDown(self):
        await self.h.close()

    def message(self, text, uid=OWNER, channel=None, guild=True, mentions=(), reference=None):
        channel = channel or self.family_channel
        return SimpleNamespace(
            id=next(ids), content=text, clean_content=text, created_at=datetime.now(UTC), webhook_id=None,
            author=SimpleNamespace(id=uid, bot=False, display_name=f"User{uid}"), channel=channel,
            guild=object() if guild else None, mentions=list(mentions), reference=reference)

    # --- delivery ---
    async def test_burst_one_sound_and_no_pings(self):
        await self.delivery.send(FAMILY, "one\n\ntwo", [("praise", "w1")], reply_to=42)
        first, second = self.family_channel.sent
        self.assertEqual((first.content, second.content), ("one", "two"))
        self.assertEqual((first.kwargs["silent"], second.kwargs["silent"]), (False, True))
        self.assertIsNotNone(first.kwargs["reference"])
        self.assertIsNone(second.kwargs["reference"])
        self.assertEqual(first.kwargs["allowed_mentions"].to_dict(), {"parse": []})  # nobody is pinged
        await self.delivery.send(FAMILY, "result", [("sports-result", "m1")], sound="none")
        self.assertTrue(self.family_channel.sent[-1].kwargs["silent"])

    async def test_rejected_send_can_be_retried_but_5xx_is_uncertain(self):
        self.family_channel.failure = http_error(discord.Forbidden, 403)
        with self.assertRaises(DeliveryError):
            await self.delivery.send(FAMILY, "hi", [("praise", "w2")])
        self.family_channel.failure = None
        self.assertTrue(await self.delivery.send(FAMILY, "hi", [("praise", "w2")]))  # released, not lost
        self.family_channel.failure = http_error(discord.DiscordServerError, 500)
        with self.assertRaises(DeliveryError):
            await self.delivery.send(FAMILY, "hi", [("praise", "w3")])
        self.assertEqual(len(await self.h.db.uncertain_deliveries()), 1)  # the owner decides, no duplicate

    # --- conversation ---
    async def test_family_channel_answers_its_name(self):
        await handle_message(self.app, self.message("Whiskers, hi", uid=200))
        self.h.ai.chat.assert_awaited_once()
        self.assertEqual(self.family_channel.sent[0].content, "Mrrr, family! <3 & *cat*")
        self.assertIsNotNone(self.family_channel.sent[0].kwargs["reference"])  # a reply to the message

    async def test_mention_and_reply_count_too(self):
        await handle_message(self.app, self.message("@Whiskers hi", uid=200, mentions=[BOT]))
        answer = self.family_channel.sent[-1]
        reply = SimpleNamespace(message_id=answer.id, resolved=None)
        await handle_message(self.app, self.message("and you?", uid=200, reference=reply))
        self.assertEqual(self.h.ai.chat.await_count, 2)
        self.assertEqual(self.h.ai.chat.call_args.kwargs["reply_context"], answer.content)

    async def test_ignores_what_is_not_for_it(self):
        await handle_message(self.app, self.message("just family talk", uid=200))
        await handle_message(self.app, self.message("Whiskers, hi", uid=200, channel=self.client.channel(-7)))
        await handle_message(self.app, self.message("Whiskers, hi", uid=300, channel=self.client.channel(300), guild=False))
        self.h.ai.chat.assert_not_awaited()

    async def test_owner_dm_and_typed_claim(self):
        dm = self.client.channel(OWNER)
        await handle_message(self.app, self.message("hello", channel=dm, guild=False))
        self.h.ai.chat.assert_awaited_once()
        async with self.h.db.transaction() as db:
            await db.execute("DELETE FROM settings WHERE key='owner_id'")
        stranger = self.client.channel(555)
        await handle_message(self.app, self.message("/claim wrong", uid=555, channel=stranger, guild=False))
        self.assertIsNone(await self.h.db.owner())
        await handle_message(self.app, self.message(f"/claim {self.h.access.claim_code}", uid=555, channel=stranger,
                                                    guild=False))
        self.assertEqual(await self.h.db.owner(), 555)
        self.assertIn("owner", stranger.sent[-1].content)

    # --- slash commands ---
    async def test_setup_channel_owner_only_and_probed(self):
        new = self.client.channel(-3000)
        stranger = FakeInteraction(200, new)
        await cmd_setup_channel(self.app, stranger)
        self.assertEqual(await self.h.db.family(), FAMILY)
        owner = FakeInteraction(OWNER, new)
        await cmd_setup_channel(self.app, owner)
        self.assertEqual(await self.h.db.family(), -3000)
        self.assertTrue(new.sent)  # the probe proved the bot can post there
        self.assertTrue(owner.answers[-1][1]["ephemeral"])

    async def test_setup_channel_without_permission(self):
        new = self.client.channel(-4000)
        new.failure = http_error(discord.Forbidden, 403)
        interaction = FakeInteraction(OWNER, new)
        await cmd_setup_channel(self.app, interaction)
        self.assertEqual(await self.h.db.family(), FAMILY)
        self.assertIn("Send Messages", interaction.texts()[-1])

    async def test_owner_commands_are_private_and_checked(self):
        interaction = FakeInteraction(200, self.family_channel)
        await cmd_status(self.app, interaction)
        self.assertIn("owner", interaction.texts()[0])
        interaction = FakeInteraction(OWNER, self.family_channel)
        await cmd_status(self.app, interaction)
        self.assertIn("Riverside", interaction.texts()[0])
        self.assertTrue(interaction.answers[0][1]["ephemeral"])
        interaction = FakeInteraction(OWNER, self.family_channel)
        await cmd_preview(self.app, interaction, "praise Anna")
        self.assertEqual(interaction.texts()[1:], ["Go team!", "Meow"])
        self.assertFalse(self.family_channel.sent)  # a preview never reaches the family

    async def test_nickname_and_team(self):
        interaction = FakeInteraction(OWNER, self.family_channel)
        await cmd_name(self.app, interaction, SimpleNamespace(id=200, bot=False, display_name="A"), "Mom")
        self.assertEqual(await self.h.db.family_name(200), "Mom")
        self.h.sports.state[TEAM.key].fetched_at = float("inf")  # use the (empty) cache, no network
        interaction = FakeInteraction(200, self.family_channel)
        await cmd_team(self.app, interaction, TEAM)
        self.assertFalse(interaction.answers[-1][1]["ephemeral"])  # match info is for everyone

    async def test_command_tree(self):
        tree = build_tree(self.app, PetClient())
        commands = {c.name: c for c in tree.get_commands()}
        self.assertIn("riverside", commands)
        self.assertIsNone(commands["help"].default_permissions)
        self.assertTrue(commands["status"].default_permissions.manage_guild)  # hidden from the family
        for command in commands.values():
            command.to_dict(tree)  # valid for Discord
