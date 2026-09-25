"""Small shared helpers: safe text, rate limits, per-chat locks, HTTP fetching."""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import PROJECT_URL, __version__
from .i18n import t


class ServiceError(Exception):
    """
    A safe, human-readable error: an i18n message key (+ parameters), shown to
    the owner in the configured language. Never contains credentials or HTTP bodies.
    """

    def __init__(self, key: str, **params):
        super().__init__(key)
        self.key, self.params = key, params

    def __str__(self) -> str:
        return t(self.key, **self.params)


def clean_text(text: str) -> str:
    return "".join(c for c in str(text) if c in "\n\t" or (ord(c) >= 32 and not 0xD800 <= ord(c) <= 0xDFFF))


def limit_text(text: str, units: int = 3800) -> str:
    """Bound Telegram text by UTF-16 units without splitting surrogate pairs."""
    text = clean_text(text).strip()
    raw = text.encode("utf-16-le")
    if len(raw) // 2 <= units:
        return text
    return raw[: (units - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def safe_url(raw: str) -> str | None:
    try:
        p = urlsplit(raw.strip())
        if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
            return None
        if any(ord(c) <= 32 for c in raw) or len(raw) > 1500:
            return None
        return urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
    except ValueError:
        return None


class RateLimiter:
    """Bounded, process-local sliding windows; spending limits live in SQLite."""

    def __init__(self, max_keys: int = 5000):
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()
        self.max_keys = max_keys

    def allow(self, key: str, limit: int, seconds: float = 60, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        bucket = self.entries.setdefault(key, deque())
        while bucket and bucket[0] <= now - seconds:
            bucket.popleft()
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_keys:
            self.entries.popitem(last=False)
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


class ChatLocks:
    """Serialize one chat, but don't keep idle chat ids forever."""

    def __init__(self):
        self.entries: dict[int, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, chat_id: int):
        lock, users = self.entries.get(chat_id, (asyncio.Lock(), 0))
        self.entries[chat_id] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            _, count = self.entries[chat_id]
            if count == 1:
                del self.entries[chat_id]
            else:
                self.entries[chat_id] = (lock, count - 1)


def names_pattern(names: tuple[str, ...]) -> re.Pattern | None:
    """
    Wake words for group chats. Each name also matches short inflected forms
    ("Whiskers" -> "Whiskers's"; "Мурзик" -> "Мурзика", "Мурзику"), because Slavic
    languages decline names.
    """
    if not names:
        return None

    def form(name: str) -> str:
        # Names ending in a vowel change it when declined (Мурка -> Мурке, Мурку):
        # match the stem + a 1-2 letter ending. Short names keep their vowel,
        # otherwise "Leo" would match "Lemon".
        if len(name) >= 4 and name[-1].lower() in "аяоеиыaeiouy":
            return re.escape(name[:-1]) + r"\w{1,2}"
        return re.escape(name) + r"\w{0,2}"

    alternatives = "|".join(form(name) for name in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alternatives + r")(?!\w)", re.IGNORECASE)


def addressed(text: str, bot_username: str, reply_author_id: int | None, bot_id: int,
              names: re.Pattern | None) -> bool:
    """Is a group message meant for the bot? A reply to it, an @mention, or one of its names."""
    if reply_author_id == bot_id:
        return True
    if bot_username and re.search(r"@" + re.escape(bot_username) + r"\b", text, re.I):
        return True
    return bool(names and names.search(text))


USER_AGENT = f"family-pet-bot/{__version__} (+{PROJECT_URL})"


async def fetch_bytes(client: httpx.AsyncClient, url: str, max_bytes: int = 2_000_000) -> bytes:
    """GET a public source (RSS, sports API). API keys of AI providers are never sent here."""
    for attempt in range(2):
        try:
            async with asyncio.timeout(25):
                async with client.stream("GET", url, timeout=15, follow_redirects=True,
                                         headers={"User-Agent": USER_AGENT}) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt == 0:
                            await asyncio.sleep(1)
                            continue
                    response.raise_for_status()
                    if response.url.scheme != "https":
                        raise ServiceError("source_insecure_redirect")
                    result = bytearray()
                    async for chunk in response.aiter_bytes():
                        result.extend(chunk)
                        if len(result) > max_bytes:
                            raise ServiceError("source_too_large")
                    return bytes(result)
        except (httpx.HTTPError, TimeoutError):
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            raise ServiceError("source_unavailable") from None
    raise ServiceError("source_unavailable")
