"""SQLite (owner, spending limits, deliveries, migration), the AI client and log redaction."""
import json
import logging
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from petbot.ai import AIError, AIService
from petbot.app import RedactingFormatter, bot_commands
from petbot.backup import backup
from petbot.config import Settings
from petbot.db import Database
from petbot.delivery import DeliveryError
from tests.support import FAMILY, TOKEN, Harness


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "bot.sqlite3")
        await self.db.open()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    async def test_owner_can_be_claimed_only_once(self):
        self.assertTrue(await self.db.claim_owner(1))
        self.assertFalse(await self.db.claim_owner(2))
        self.assertEqual(await self.db.owner(), 1)

    async def test_daily_ai_limit_is_enforced(self):
        self.assertTrue(await self.db.consume_ai_call("2026-09-05", 2))
        self.assertTrue(await self.db.consume_ai_call("2026-09-05", 2))
        self.assertFalse(await self.db.consume_ai_call("2026-09-05", 2))
        self.assertTrue(await self.db.consume_ai_call("2026-09-06", 2))  # a new day

    async def test_news_has_its_own_sub_limit(self):
        self.assertTrue(await self.db.consume_ai_call("d", 100, category="news", category_limit=1))
        self.assertFalse(await self.db.consume_ai_call("d", 100, category="news", category_limit=1))
        self.assertTrue(await self.db.consume_ai_call("d", 100))  # conversation still works

    async def test_delivery_keys_prevent_duplicates(self):
        group = await self.db.reserve_delivery(FAMILY, [("news", "a")], "text")
        self.assertIsNotNone(group)
        self.assertIsNone(await self.db.reserve_delivery(FAMILY, [("news", "a")], "text"))

    async def test_interrupted_send_becomes_uncertain_after_restart(self):
        await self.db.reserve_delivery(FAMILY, [("news", "a")], "text")
        await self.db.close()
        await self.db.open()
        self.assertEqual(len(await self.db.uncertain_deliveries()), 1)

    async def test_forget_removes_memory(self):
        await self.db.save_exchange(FAMILY, "u", "a")
        await self.db.forget(FAMILY)
        self.assertEqual(await self.db.history(FAMILY), [])


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_from_the_first_version_is_reused(self):
        """A database from the first version keeps its owner, chat and notice journal."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.sqlite3"
            con = sqlite3.connect(path)
            con.executescript("""CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT INTO settings VALUES('owner_id','42'),('family_chat_id','-100');
                CREATE TABLE levan_cleanup_v1(bot_id INTEGER, chat_id INTEGER, message_id INTEGER, sent_at REAL,
                    due_at REAL, source TEXT, state TEXT, attempts INTEGER, error TEXT);""")
            con.close()
            db = Database(path)
            await db.open()
            self.assertEqual((await db.owner(), await db.family()), (42, -100))
            async with db.transaction() as c:
                async with c.execute("SELECT name FROM sqlite_master WHERE name='notice_cleanup'") as cur:
                    self.assertIsNotNone(await cur.fetchone())
            await db.close()


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = await Harness().open(persona_emoji="😼")

    async def asyncTearDown(self):
        await self.h.close()

    async def test_sent_text_is_plain_and_filtered(self):
        await self.h.delivery.send(FAMILY, "Hi 😼🔥 <b>x</b>", [("t", "1")])
        sent: SendMessage = self.h.session.sent[0]
        self.assertEqual(sent.text, "Hi 😼 <b>x</b>")
        self.assertIsNone(sent.parse_mode)

    async def test_network_error_is_not_retried_automatically(self):
        self.h.session.failure = TelegramNetworkError(method=SendMessage(chat_id=1, text="x"), message="boom")
        with self.assertRaises(DeliveryError):
            await self.h.delivery.send(FAMILY, "x", [("news", "1")])
        self.assertEqual(len(await self.h.db.uncertain_deliveries()), 1)
        self.assertFalse(await self.h.delivery.send(FAMILY, "x", [("news", "1")]))  # no duplicate


class AITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "bot.sqlite3")
        await self.db.open()
        self.requests = []
        self.settings = Settings(bot_token=TOKEN, api_key="sk-ant-secret-key-123456", persona_emoji="😼",
                                 database_path=Path(self.tmp.name) / "bot.sqlite3")

    async def asyncTearDown(self):
        await self.db.close()
        self.tmp.cleanup()

    def service(self, status=200, body=None, settings=None):
        def handler(request: httpx.Request):
            self.requests.append(request)
            return httpx.Response(status, json=body or {"content": [{"type": "text", "text": "Mrr 😼🔥 hi."}],
                                                         "stop_reason": "end_turn"})
        return AIService(settings or self.settings, self.db, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def test_chat_uses_persona_and_filters_emoji(self):
        answer = await self.service().chat([], "hello", "Anna")
        self.assertEqual(answer, "Mrr 😼 hi.")
        body = json.loads(self.requests[0].content)
        self.assertIn("Whiskers", body["system"])  # the persona file
        self.assertIn("Anna: hello", body["messages"][-1]["content"])
        self.assertNotIn("sk-ant-secret", body["system"])  # secrets never go into prompts

    async def test_http_errors_become_clear_messages(self):
        with self.assertRaises(AIError) as ctx:
            await self.service(status=401, body={"error": "x"}).chat([], "hi", "A")
        self.assertIn("rejected the API key", str(ctx.exception))

    async def test_daily_limit_stops_before_http(self):
        settings = replace(self.settings, max_ai_calls_per_day=1)
        ai = self.service(settings=settings)
        await ai.chat([], "one", "A")
        ai.cooldown_until = 0
        with self.assertRaises(AIError):
            await ai.chat([], "two", "A")
        self.assertEqual(len(self.requests), 1)

    async def test_news_decision_json(self):
        ai = self.service(body={"content": [{"type": "text", "text": json.dumps({"decision": "accept", "text": "Good."})}],
                                "stop_reason": "end_turn"})
        decision = await ai.news("Title", "Summary")
        self.assertTrue(decision.accepted)
        self.assertIn("output_config", json.loads(self.requests[0].content))  # structured output requested

    async def test_malformed_news_json_is_a_failure_not_a_rejection(self):
        ai = self.service(body={"content": [{"type": "text", "text": "not json"}], "stop_reason": "end_turn"})
        with self.assertRaises(AIError):
            await ai.news("Title", "Summary")


def test_logs_never_contain_secrets():
    formatter = RedactingFormatter((TOKEN, "sk-ant-secret-key-123456"))
    record = logging.LogRecord("x", logging.ERROR, "", 0, f"failed https://api.telegram.org/bot{TOKEN}/getMe "
                                                          "key sk-ant-secret-key-123456 other sk-live-abcdefghijklm", None, None)
    text = formatter.format(record)
    assert TOKEN not in text and "sk-ant-secret" not in text and "sk-live-abcdefghijklm" not in text


def test_command_menu_follows_features():
    names = [c for c, _ in bot_commands(Settings(sports_enabled=True, sports_command="football", news_enabled=False))]
    assert "football" in names and "news" not in names


def test_backup_is_consistent_and_rotated(tmp_path):
    db = tmp_path / "data" / "bot.sqlite3"
    db.parent.mkdir()
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    first = backup(db)
    assert sqlite3.connect(first).execute("SELECT x FROM t").fetchone() == (1,)
    for i in range(12):
        (first.parent / f"bot-2026010{i:02d}-000000.sqlite3").write_bytes(b"")
    backup(db)
    assert len(list(first.parent.glob("bot-*.sqlite3"))) == 10
