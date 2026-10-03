"""
Single async SQLite connection with serialized, rollback-safe transactions.

The schema is compatible with databases created by the first version of this
bot, so an existing installation keeps its owner, chat and memory.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite


def utcstamp() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.connection: aiosqlite.Connection | None = None
        self.lock = asyncio.Lock()

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA busy_timeout=5000")
        await self.connection.execute("PRAGMA secure_delete=ON")
        await self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS allowed_users(user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS family_names(
            user_id INTEGER PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dialogs(
            id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, role TEXT NOT NULL,
            content TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS dialogs_chat ON dialogs(chat_id, id);
        CREATE TABLE IF NOT EXISTS news_reviews(
            article_id TEXT PRIMARY KEY, decision TEXT NOT NULL, post TEXT NOT NULL,
            reviewed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deliveries(
            id INTEGER PRIMARY KEY, group_id TEXT NOT NULL, chat_id INTEGER NOT NULL,
            kind TEXT NOT NULL, item_id TEXT NOT NULL, state TEXT NOT NULL,
            text TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '', message_id INTEGER,
            UNIQUE(chat_id, kind, item_id));
        CREATE INDEX IF NOT EXISTS deliveries_group ON deliveries(group_id);
        CREATE TABLE IF NOT EXISTS ai_usage(day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS ai_category_usage(
            day TEXT NOT NULL, category TEXT NOT NULL, calls INTEGER NOT NULL,
            PRIMARY KEY(day, category));
        CREATE TABLE IF NOT EXISTS scheduled_attempts(
            chat_id INTEGER NOT NULL, job_key TEXT NOT NULL, attempts INTEGER NOT NULL,
            last_attempt TEXT NOT NULL, PRIMARY KEY(chat_id, job_key));
        CREATE TABLE IF NOT EXISTS news_stock(
            article_id TEXT PRIMARY KEY, post TEXT NOT NULL, link TEXT NOT NULL,
            published TEXT NOT NULL, added_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(
            id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, fact TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS memories_chat ON memories(chat_id, id);
        CREATE TABLE IF NOT EXISTS sent_messages(
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, message_id));
        CREATE TABLE IF NOT EXISTS feedback(
            chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, user_id INTEGER NOT NULL, emoji TEXT NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(chat_id, message_id, user_id, emoji));
        PRAGMA user_version=6;
        """)
        # A database from the first version named the notice table after its cat character.
        async with self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('levan_cleanup_v1','notice_cleanup')") as cur:
            tables = {row[0] for row in await cur.fetchall()}
        if tables == {"levan_cleanup_v1"}:
            await self.connection.execute("ALTER TABLE levan_cleanup_v1 RENAME TO notice_cleanup")
        await self.connection.execute("UPDATE deliveries SET state='uncertain', error='process_interrupted' WHERE state='sending'")
        await self.connection.commit()
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    @asynccontextmanager
    async def transaction(self):
        if self.connection is None:
            raise RuntimeError("Database is not open")
        async with self.lock:
            try:
                yield self.connection
                await self.connection.commit()
            except BaseException:
                await self.connection.rollback()
                raise

    async def get(self, key: str) -> str | None:
        async with self.transaction() as db:
            async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def set(self, key: str, value: str) -> None:
        async with self.transaction() as db:
            await db.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    async def claim_owner(self, user_id: int) -> bool:
        async with self.transaction() as db:
            cur = await db.execute("INSERT OR IGNORE INTO settings VALUES('owner_id',?)", (str(user_id),))
            return cur.rowcount == 1

    async def owner(self) -> int | None:
        value = await self.get("owner_id")
        return int(value) if value else None

    async def family(self) -> int | None:
        value = await self.get("family_chat_id")
        return int(value) if value else None

    async def allow_user(self, user_id: int, allowed: bool) -> None:
        async with self.transaction() as db:
            if allowed:
                await db.execute("INSERT OR IGNORE INTO allowed_users VALUES(?)", (user_id,))
            else:
                await db.execute("DELETE FROM allowed_users WHERE user_id=?", (user_id,))

    async def is_allowed_user(self, user_id: int) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT 1 FROM allowed_users WHERE user_id=?", (user_id,)) as cur:
                return await cur.fetchone() is not None

    async def allowed_list(self) -> list[int]:
        async with self.transaction() as db:
            async with db.execute("SELECT user_id FROM allowed_users ORDER BY user_id") as cur:
                return [row[0] for row in await cur.fetchall()]

    async def set_family_name(self, user_id: int, name: str) -> None:
        async with self.transaction() as db:
            await db.execute(
                "INSERT INTO family_names(user_id,name,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name,updated_at=excluded.updated_at",
                (user_id, name, utcstamp()),
            )

    async def family_name(self, user_id: int) -> str | None:
        async with self.transaction() as db:
            async with db.execute("SELECT name FROM family_names WHERE user_id=?", (user_id,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def family_names(self) -> list[tuple[int, str]]:
        async with self.transaction() as db:
            async with db.execute("SELECT user_id,name FROM family_names ORDER BY name COLLATE NOCASE, user_id") as cur:
                return [(row[0], row[1]) for row in await cur.fetchall()]

    async def delete_family_name(self, user_id: int) -> bool:
        async with self.transaction() as db:
            cur = await db.execute("DELETE FROM family_names WHERE user_id=?", (user_id,))
            return cur.rowcount > 0

    async def history(self, chat_id: int, limit: int = 8, days: int = 30) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        async with self.transaction() as db:
            async with db.execute("SELECT role,content FROM dialogs WHERE chat_id=? AND created_at>=? ORDER BY id DESC LIMIT ?", (chat_id, cutoff, limit)) as cur:
                return [dict(row) for row in reversed(await cur.fetchall())]

    async def save_exchange(self, chat_id: int, user_text: str, reply: str, keep: int = 50) -> None:
        async with self.transaction() as db:
            await db.executemany("INSERT INTO dialogs(chat_id,role,content,created_at) VALUES(?,?,?,?)", [
                (chat_id, "user", user_text, utcstamp()), (chat_id, "assistant", reply, utcstamp())])
            await db.execute("DELETE FROM dialogs WHERE chat_id=? AND id NOT IN (SELECT id FROM dialogs WHERE chat_id=? ORDER BY id DESC LIMIT ?)", (chat_id, chat_id, keep))

    # --- long-term memory: short facts the pet chose to remember from conversations addressed to it ---
    async def add_memory(self, chat_id: int, fact: str, keep: int = 40) -> None:
        async with self.transaction() as db:
            await db.execute("INSERT INTO memories(chat_id,fact,created_at) VALUES(?,?,?)", (chat_id, fact, utcstamp()))
            await db.execute("DELETE FROM memories WHERE chat_id=? AND id NOT IN "
                             "(SELECT id FROM memories WHERE chat_id=? ORDER BY id DESC LIMIT ?)", (chat_id, chat_id, keep))

    async def memories(self, chat_id: int, days: int = 60) -> list[dict]:
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        async with self.transaction() as db:
            async with db.execute("SELECT id,fact,created_at FROM memories WHERE chat_id=? AND created_at>=? ORDER BY id",
                                  (chat_id, cutoff)) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def delete_memory(self, chat_id: int, memory_id: int) -> bool:
        async with self.transaction() as db:
            cur = await db.execute("DELETE FROM memories WHERE chat_id=? AND id=?", (chat_id, memory_id))
            return cur.rowcount > 0

    # --- feedback: how the family reacts to what the pet posts ---
    async def remember_sent(self, chat_id: int, message_ids: list[int], kind: str) -> None:
        async with self.transaction() as db:
            await db.executemany("INSERT OR IGNORE INTO sent_messages VALUES(?,?,?,?)",
                                 [(chat_id, mid, kind, utcstamp()) for mid in message_ids])

    async def sent_kind(self, chat_id: int, message_id: int) -> str | None:
        async with self.transaction() as db:
            async with db.execute("SELECT kind FROM sent_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)) as cur:
                row = await cur.fetchone()
                return row[0] if row else None

    async def set_feedback(self, chat_id: int, message_id: int, user_id: int, emojis: list[str]) -> None:
        """Telegram reports a person's whole reaction set on a message; replace it (replies, "↩", are kept)."""
        async with self.transaction() as db:
            await db.execute("DELETE FROM feedback WHERE chat_id=? AND message_id=? AND user_id=? AND emoji!='↩'",
                             (chat_id, message_id, user_id))
            await db.executemany("INSERT OR IGNORE INTO feedback VALUES(?,?,?,?,?)",
                                 [(chat_id, message_id, user_id, e, utcstamp()) for e in emojis])

    async def add_feedback(self, chat_id: int, message_id: int, user_id: int, emoji: str, added: bool = True) -> None:
        async with self.transaction() as db:
            if added:
                await db.execute("INSERT OR IGNORE INTO feedback VALUES(?,?,?,?,?)",
                                 (chat_id, message_id, user_id, emoji, utcstamp()))
            else:
                await db.execute("DELETE FROM feedback WHERE chat_id=? AND message_id=? AND user_id=? AND emoji=?",
                                 (chat_id, message_id, user_id, emoji))

    async def replies_since(self, chat_id: int, kind: str, since: datetime) -> int:
        """Replies ("↩") to the pet's messages of this kind sent after `since`."""
        async with self.transaction() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM feedback f JOIN sent_messages s ON f.chat_id=s.chat_id AND f.message_id=s.message_id "
                "WHERE s.chat_id=? AND s.kind=? AND s.created_at>=? AND f.emoji='↩'",
                (chat_id, kind, since.astimezone(UTC).isoformat())) as cur:
                return (await cur.fetchone())[0]

    async def delivery_time(self, chat_id: int, kind: str, item_id: str) -> datetime | None:
        async with self.transaction() as db:
            async with db.execute("SELECT created_at FROM deliveries WHERE chat_id=? AND kind=? AND item_id=?",
                                  (chat_id, kind, item_id)) as cur:
                row = await cur.fetchone()
                return datetime.fromisoformat(row[0]) if row else None

    async def feedback_stats(self, chat_id: int, days: int = 30) -> list[dict]:
        """Per kind of post: messages sent, reactions, replies and the most common emoji."""
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        async with self.transaction() as db:
            async with db.execute(
                "SELECT s.kind, COUNT(DISTINCT s.message_id) AS sent, "
                "SUM(CASE WHEN f.emoji IS NOT NULL AND f.emoji!='↩' THEN 1 ELSE 0 END) AS reactions, "
                "SUM(CASE WHEN f.emoji='↩' THEN 1 ELSE 0 END) AS replies "
                "FROM sent_messages s LEFT JOIN feedback f ON f.chat_id=s.chat_id AND f.message_id=s.message_id "
                "WHERE s.chat_id=? AND s.created_at>=? GROUP BY s.kind ORDER BY sent DESC", (chat_id, cutoff)) as cur:
                rows = [dict(row) for row in await cur.fetchall()]
            for row in rows:
                async with db.execute(
                    "SELECT f.emoji, COUNT(*) FROM feedback f JOIN sent_messages s ON f.chat_id=s.chat_id "
                    "AND f.message_id=s.message_id WHERE s.chat_id=? AND s.kind=? AND s.created_at>=? AND f.emoji!='↩' "
                    "GROUP BY f.emoji ORDER BY COUNT(*) DESC LIMIT 3", (chat_id, row["kind"], cutoff)) as cur:
                    row["top"] = [(e, n) for e, n in await cur.fetchall()]
            return rows

    async def forget(self, chat_id: int) -> None:
        async with self.transaction() as db:
            await db.execute("DELETE FROM dialogs WHERE chat_id=?", (chat_id,))
            await db.execute("DELETE FROM memories WHERE chat_id=?", (chat_id,))
            await db.execute("UPDATE deliveries SET text='' WHERE chat_id=? AND kind='dialog'", (chat_id,))

    async def consume_ai_call(self, day: str, limit: int, *, category: str | None = None,
                              category_limit: int | None = None) -> bool:
        async with self.transaction() as db:
            await db.execute("INSERT OR IGNORE INTO ai_usage VALUES(?,0)", (day,))
            if category is not None and category_limit is not None:
                await db.execute("INSERT OR IGNORE INTO ai_category_usage VALUES(?,?,0)", (day, category))
                async with db.execute("SELECT calls FROM ai_category_usage WHERE day=? AND category=?", (day, category)) as cur:
                    if (await cur.fetchone())[0] >= category_limit:
                        return False
            cur = await db.execute("UPDATE ai_usage SET calls=calls+1 WHERE day=? AND calls<?", (day, limit))
            if cur.rowcount != 1:
                return False
            if category is not None and category_limit is not None:
                await db.execute("UPDATE ai_category_usage SET calls=calls+1 WHERE day=? AND category=?", (day, category))
            return True

    async def category_usage(self, day: str, category: str) -> int:
        async with self.transaction() as db:
            async with db.execute("SELECT calls FROM ai_category_usage WHERE day=? AND category=?", (day, category)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def claim_scheduled_attempt(self, chat_id: int, key: str, now: datetime,
                                      limit: int = 2, cooldown_minutes: int = 20) -> bool:
        """Bound retries per slot across process restarts; not an editorial rejection."""
        async with self.transaction() as db:
            async with db.execute("SELECT attempts,last_attempt FROM scheduled_attempts WHERE chat_id=? AND job_key=?", (chat_id, key)) as cur:
                row = await cur.fetchone()
            if row:
                if row[0] >= limit or now - datetime.fromisoformat(row[1]) < timedelta(minutes=cooldown_minutes):
                    return False
            await db.execute(
                "INSERT INTO scheduled_attempts VALUES(?,?,1,?) ON CONFLICT(chat_id,job_key) "
                "DO UPDATE SET attempts=attempts+1,last_attempt=excluded.last_attempt",
                (chat_id, key, now.astimezone(UTC).isoformat()))
            return True

    async def last_delivery_at(self, chat_id: int, kinds: tuple[str, ...]) -> datetime | None:
        if not kinds:
            return None
        marks = ",".join("?" for _ in kinds)
        async with self.transaction() as db:
            async with db.execute(
                f"SELECT MAX(created_at) FROM deliveries WHERE chat_id=? AND kind IN ({marks})",
                (chat_id, *kinds),
            ) as cur:
                row = await cur.fetchone()
                return datetime.fromisoformat(row[0]) if row and row[0] else None

    async def usage(self, day: str) -> int:
        async with self.transaction() as db:
            async with db.execute("SELECT calls FROM ai_usage WHERE day=?", (day,)) as cur:
                row = await cur.fetchone()
                return row[0] if row else 0

    async def review(self, article_id: str) -> dict | None:
        async with self.transaction() as db:
            async with db.execute("SELECT * FROM news_reviews WHERE article_id=?", (article_id,)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def save_review(self, article_id: str, decision: str, post: str) -> None:
        async with self.transaction() as db:
            await db.execute("INSERT OR REPLACE INTO news_reviews VALUES(?,?,?,?)", (article_id, decision, post, utcstamp()))

    async def add_to_stock(self, article_id: str, post: str, link: str, published: datetime) -> None:
        async with self.transaction() as db:
            await db.execute("INSERT OR IGNORE INTO news_stock VALUES(?,?,?,?,?)",
                             (article_id, post, link, published.astimezone(UTC).isoformat(), utcstamp()))

    async def stock(self, chat_id: int) -> list[dict]:
        """Ready stories this chat hasn't seen yet, newest first."""
        async with self.transaction() as db:
            async with db.execute(
                "SELECT * FROM news_stock s WHERE NOT EXISTS (SELECT 1 FROM deliveries d WHERE d.chat_id=? "
                "AND d.kind='news' AND d.item_id=s.article_id) ORDER BY published DESC", (chat_id,)) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def in_stock(self, article_id: str) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT 1 FROM news_stock WHERE article_id=?", (article_id,)) as cur:
                return await cur.fetchone() is not None

    async def has_delivery(self, chat_id: int, kind: str, item_id: str) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT 1 FROM deliveries WHERE chat_id=? AND kind=? AND item_id=?", (chat_id, kind, item_id)) as cur:
                return await cur.fetchone() is not None

    async def reserve_delivery(self, chat_id: int, keys: list[tuple[str, str]], text: str) -> str | None:
        group = uuid.uuid4().hex
        async with self.transaction() as db:
            for kind, item in keys:
                async with db.execute("SELECT 1 FROM deliveries WHERE chat_id=? AND kind=? AND item_id=?", (chat_id, kind, item)) as cur:
                    if await cur.fetchone():
                        return None
            await db.executemany("INSERT INTO deliveries(group_id,chat_id,kind,item_id,state,text,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", [
                (group, chat_id, kind, item, "sending", text, utcstamp(), utcstamp()) for kind, item in keys])
        return group

    async def finish_delivery(self, group: str, state: str, error: str = "", message_id: int | None = None) -> None:
        async with self.transaction() as db:
            await db.execute("UPDATE deliveries SET state=?,error=?,message_id=?,updated_at=? WHERE group_id=?", (state, error, message_id, utcstamp(), group))
            if state == "sent":
                await db.execute("UPDATE deliveries SET text='' WHERE group_id=? AND kind='dialog'", (group,))

    async def release_delivery(self, group: str) -> None:
        async with self.transaction() as db:
            await db.execute("DELETE FROM deliveries WHERE group_id=? AND state='sending'", (group,))

    async def uncertain_deliveries(self) -> list[dict]:
        async with self.transaction() as db:
            async with db.execute("SELECT * FROM deliveries WHERE state='uncertain' AND id IN (SELECT MIN(id) FROM deliveries GROUP BY group_id) ORDER BY id DESC LIMIT 10") as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def take_retry(self, delivery_id: int) -> dict | None:
        async with self.transaction() as db:
            async with db.execute("SELECT * FROM deliveries WHERE id=? AND state='uncertain' AND text!=''", (delivery_id,)) as cur:
                row = await cur.fetchone()
            if not row:
                return None
            await db.execute("UPDATE deliveries SET state='sending',error='',updated_at=? WHERE group_id=?", (utcstamp(), row['group_id']))
            return dict(row)

    async def migrate_chat(self, old: int, new: int) -> None:
        async with self.transaction() as db:
            await db.execute("UPDATE settings SET value=? WHERE key='family_chat_id' AND value=?", (str(new), str(old)))
            await db.execute("UPDATE dialogs SET chat_id=? WHERE chat_id=?", (new, old))
            for table in ("memories", "sent_messages", "feedback"):
                await db.execute(f"UPDATE OR IGNORE {table} SET chat_id=? WHERE chat_id=?", (new, old))
            await db.execute("UPDATE OR IGNORE deliveries SET chat_id=? WHERE chat_id=?", (new, old))

    async def cleanup(self, history_days: int, memory_days: int = 60) -> None:
        now = datetime.now(UTC)
        async with self.transaction() as db:
            await db.execute("DELETE FROM memories WHERE created_at<?", ((now-timedelta(days=memory_days)).isoformat(),))
            await db.execute("DELETE FROM sent_messages WHERE created_at<?", ((now-timedelta(days=90)).isoformat(),))
            await db.execute("DELETE FROM feedback WHERE created_at<?", ((now-timedelta(days=90)).isoformat(),))
            await db.execute("DELETE FROM dialogs WHERE created_at<?", ((now-timedelta(days=history_days)).isoformat(),))
            await db.execute("UPDATE deliveries SET text='' WHERE kind='dialog' AND created_at<?", ((now-timedelta(days=history_days)).isoformat(),))
            await db.execute("DELETE FROM news_reviews WHERE reviewed_at<?", ((now-timedelta(days=45)).isoformat(),))
            await db.execute("DELETE FROM news_stock WHERE published<?", ((now-timedelta(days=21)).isoformat(),))
            await db.execute("DELETE FROM deliveries WHERE state='sent' AND updated_at<?", ((now-timedelta(days=90)).isoformat(),))
            await db.execute("DELETE FROM scheduled_attempts WHERE last_attempt<?", ((now-timedelta(days=14)).isoformat(),))
            await db.execute("DELETE FROM ai_category_usage WHERE day<?", ((now-timedelta(days=90)).date().isoformat(),))
            await db.execute("DELETE FROM ai_usage WHERE day<?", ((now-timedelta(days=90)).date().isoformat(),))

    async def ping(self) -> bool:
        async with self.transaction() as db:
            async with db.execute("SELECT 1") as cur:
                return (await cur.fetchone())[0] == 1
