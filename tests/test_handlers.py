"""Access control and conversation - who may spend the AI budget, and what gets stored."""
import asyncio
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from aiogram import Dispatcher
from aiogram.types import Chat, Message, Update

from petbot.ai import AIError
from petbot.handlers import handle, make_router, split_command
from petbot.i18n import set_language
from tests.support import FAMILY, ME, OWNER, Harness


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open()

    async def asyncTearDown(self):
        await self.h.close()

    # --- access ---
    async def test_outsider_cannot_spend_api(self):
        await handle(self.h.message(uid=200, chat=200), self.h.app)
        self.h.ai.chat.assert_not_awaited()
        self.assertFalse(self.h.session.sent)

    async def test_outsider_cannot_redirect_family_chat(self):
        await handle(self.h.message("/setup_chat", uid=200, chat=-999), self.h.app)
        self.assertEqual(await self.h.db.family(), FAMILY)

    async def test_setup_chat_in_private_is_rejected(self):
        await handle(self.h.message("/setup_chat"), self.h.app)
        self.assertEqual(await self.h.db.family(), FAMILY)
        self.assertIn("inside the family group", self.h.last_text())

    async def test_owner_can_bind_another_group(self):
        await handle(self.h.message("/setup_chat", chat=-999), self.h.app)
        self.assertEqual(await self.h.db.family(), -999)

    async def test_family_member_cannot_rebind(self):
        await handle(self.h.message("/setup_chat", uid=200, chat=FAMILY), self.h.app)
        self.assertEqual(await self.h.db.family(), FAMILY)
        self.assertIn("Only the owner", self.h.last_text())

    async def test_anonymous_group_admin_is_not_the_owner(self):
        msg = self.h.message("/setup_chat", chat=-999, sender_chat=Chat(id=-999, type="supergroup"))
        await handle(msg, self.h.app)
        self.assertEqual(await self.h.db.family(), FAMILY)

    async def test_commands_for_other_bots_are_ignored(self):
        await handle(self.h.message("/news@another_bot"), self.h.app)
        self.assertFalse(self.h.session.sent)
        self.h.ai.news.assert_not_awaited()

    async def test_owner_commands_only_in_private(self):
        await handle(self.h.message("/status", uid=200, chat=FAMILY), self.h.app)
        self.assertIn("only for the owner", self.h.last_text())
        self.assertNotIn(f"Owner: {OWNER}", self.h.last_text())

    async def test_allow_and_deny(self):
        await handle(self.h.message("/allow 200", mid=1), self.h.app)
        self.assertTrue(await self.h.db.is_allowed_user(200))
        await handle(self.h.message("/deny 200", mid=2), self.h.app)
        self.assertFalse(await self.h.db.is_allowed_user(200))

    async def test_owner_cannot_deny_self(self):
        await handle(self.h.message(f"/deny {OWNER}"), self.h.app)
        self.assertIn("can't remove", self.h.last_text())

    async def test_public_id_costs_nothing(self):
        await handle(self.h.message("/id", uid=200, chat=200), self.h.app)
        self.assertIn("200", self.h.last_text())
        self.h.ai.chat.assert_not_awaited()

    # --- group conversation ---
    async def test_unaddressed_family_message_is_not_stored(self):
        await handle(self.h.message("Who buys milk?", uid=200, chat=FAMILY), self.h.app)
        self.assertFalse(self.h.session.sent)
        self.assertEqual(await self.h.db.history(FAMILY), [])
        self.h.ai.chat.assert_not_awaited()

    async def test_name_in_family_group_is_answered(self):
        await handle(self.h.message("Whiskers, hi!", uid=200, chat=FAMILY), self.h.app)
        self.h.ai.chat.assert_awaited_once()
        self.assertEqual(len(await self.h.db.history(FAMILY)), 2)
        self.assertIsNone(self.h.session.sent[0].parse_mode)  # plain text, never HTML from the AI

    async def test_inflected_name_is_answered(self):
        await handle(self.h.message("where is Whiskers's bowl", uid=200, chat=FAMILY), self.h.app)
        self.h.ai.chat.assert_awaited_once()

    async def test_reply_to_bot_is_answered_without_name(self):
        parent = Message(message_id=50, date=datetime.now(UTC), chat=Chat(id=FAMILY, type="supergroup"),
                         from_user=ME, text="Hi")
        await handle(self.h.message("Yes!", uid=200, chat=FAMILY, reply_to_message=parent), self.h.app)
        self.h.ai.chat.assert_awaited_once()
        self.assertEqual(self.h.ai.chat.call_args.kwargs["reply_context"], "Hi")

    async def test_duplicate_update_is_not_charged_twice(self):
        msg = self.h.message("Hello")
        await handle(msg, self.h.app)
        await handle(msg, self.h.app)
        self.assertEqual(self.h.ai.chat.await_count, 1)
        self.assertEqual(len(self.h.session.sent), 1)

    async def test_concurrent_messages_see_sequential_history(self):
        snapshots = []

        async def respond(history, text, name, **kwargs):
            snapshots.append(list(history))
            await asyncio.sleep(0.01)
            return "Answer to " + text

        self.h.ai.chat.side_effect = respond
        await asyncio.gather(handle(self.h.message("First", mid=1), self.h.app),
                             handle(self.h.message("Second", mid=2), self.h.app))
        self.assertEqual([len(x) for x in snapshots], [0, 2])

    async def test_private_and_group_memory_are_isolated(self):
        await self.h.db.save_exchange(FAMILY, "GROUP_SECRET", "reply")
        await handle(self.h.message("Hello"), self.h.app)
        self.assertEqual(self.h.ai.chat.call_args.args[0], [])

    async def test_per_user_rate_limit(self):
        self.h.app.settings = replace(self.h.settings, user_requests_per_minute=1)
        await handle(self.h.message("First", mid=1), self.h.app)
        await handle(self.h.message("Second", mid=2), self.h.app)
        self.assertEqual(self.h.ai.chat.await_count, 1)

    async def test_long_input_is_rejected_before_the_api(self):
        await handle(self.h.message("x" * 4001), self.h.app)
        self.h.ai.chat.assert_not_awaited()
        self.assertIn("too long", self.h.last_text())

    async def test_stale_update_after_downtime_is_not_charged(self):
        msg = self.h.message().model_copy(update={"date": datetime.now(UTC) - timedelta(hours=1)}).as_(self.h.bot)
        await handle(msg, self.h.app)
        self.h.ai.chat.assert_not_awaited()

    async def test_nickname_is_used_as_the_author_name(self):
        await self.h.db.set_family_name(200, "Anna")
        await handle(self.h.message("Whiskers?", uid=200, chat=FAMILY), self.h.app)
        self.assertEqual(self.h.ai.chat.call_args.args[2], "Anna")
        self.assertTrue(self.h.ai.chat.call_args.kwargs["verified_name"])

    # --- memory and commands ---
    async def test_member_cannot_forget_group_memory(self):
        await self.h.db.save_exchange(FAMILY, "hello", "reply")
        await handle(self.h.message("/forget", uid=200, chat=FAMILY), self.h.app)
        self.assertEqual(len(await self.h.db.history(FAMILY)), 2)

    async def test_owner_forgets_group_memory(self):
        await self.h.db.save_exchange(FAMILY, "hello", "reply")
        await handle(self.h.message("/forget", chat=FAMILY), self.h.app)
        self.assertEqual(await self.h.db.history(FAMILY), [])

    async def test_group_migration_follows_the_chat(self):
        await handle(self.h.message("", chat=FAMILY, migrate_to_chat_id=-2000), self.h.app)
        self.assertEqual(await self.h.db.family(), -2000)

    async def test_status_does_not_call_the_ai(self):
        await handle(self.h.message("/status"), self.h.app)
        self.assertIn("not yet", self.h.last_text())
        self.h.ai.chat.assert_not_awaited()

    async def test_retry_requires_confirmation(self):
        await handle(self.h.message("/retry_delivery 12"), self.h.app)
        self.assertIn("confirm", self.h.last_text())

    async def test_help_lists_only_enabled_features(self):
        await handle(self.h.message("/help"), self.h.app)
        self.assertIn("/news", self.h.last_text())
        self.assertNotIn("/match", self.h.last_text())  # sports are off by default

    async def test_custom_sports_command(self):
        self.h.app.settings = replace(self.h.settings, sports_enabled=True, sports_team_id="1001", sports_command="football")
        self.h.sports.cached_at = float("inf")  # use the (empty) cache, no network
        await handle(self.h.message("/football"), self.h.app)
        self.assertIn("TheSportsDB", self.h.last_text())

    async def test_russian_interface(self):
        set_language("ru")
        await handle(self.h.message("x" * 4001), self.h.app)
        self.assertIn("слишком длинное", self.h.last_text())

    async def test_no_get_me_per_message(self):
        await handle(self.h.message(), self.h.app)
        self.assertFalse(any(type(x).__name__ == "GetMe" for x in self.h.session.requests))

    # --- through the real aiogram dispatcher ---
    async def test_real_dispatcher_with_fake_telegram(self):
        dp = Dispatcher()
        dp.include_router(make_router(self.h.app))
        await dp.feed_update(self.h.bot, Update(update_id=1, message=self.h.message()))
        self.h.ai.chat.assert_awaited_once()
        self.assertFalse(self.h.app.active_tasks)

    async def test_ai_failure_is_shown_and_not_saved(self):
        self.h.ai.chat.side_effect = AIError("ai_http_401")
        dp = Dispatcher()
        dp.include_router(make_router(self.h.app))
        await dp.feed_update(self.h.bot, Update(update_id=1, message=self.h.message()))
        self.assertIn("rejected the API key", self.h.last_text())
        self.assertEqual(await self.h.db.history(OWNER), [])


def test_split_command():
    assert split_command("/news@Whiskers_Bot now", "whiskers_bot") == ("news", "now")
    assert split_command("/news@other_bot", "whiskers_bot") == ("__other_bot__", "")
    assert split_command("hello", "whiskers_bot") == ("", "")
