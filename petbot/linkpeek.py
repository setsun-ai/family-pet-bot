"""
What a shared link is about - LINK_PREVIEW (on by default; used only with SENTIMENT_ENABLED).

Telegram never tells a bot what is in a link preview. "A café closes early today"
looks harmless; "because of the shelling" is only on the page. So the pet opens the page itself, takes only its title and short description
(the og:title / og:description a preview shows) and reads them with the local
dictionary (sentiment.py). No AI, nothing stored, nothing from the chat is sent: the
site sees an ordinary visit from your computer's address.

Safety: https only, public addresses only (never your router or anything else on the
local network), at most 3 redirects, 512 KB and 8 seconds. Video, music and social
sites are skipped: their titles are songs and memes, not news.
"""
from __future__ import annotations

import asyncio
import html
import ipaddress
import logging
import re
import socket
from urllib.parse import urljoin, urlsplit

import httpx

from .common import USER_AGENT

log = logging.getLogger(__name__)

LINK = re.compile(r"https?://[^\s<>\"'«»]+", re.I)
SKIPPED_HOSTS = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "spotify.com", "music.apple.com",
                 "soundcloud.com", "twitch.tv", "t.me", "telegram.me", "discord.com", "discord.gg")
MAX_BYTES = 512_000
REDIRECTS = 3
TIMEOUT = 8
META = re.compile(r"<meta\b[^>]*>", re.I)
ATTRIBUTE = re.compile(r"([\w:-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')")
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
WANTED = ("og:title", "og:description", "twitter:title", "twitter:description", "description")


def first_link(text: str) -> str | None:
    match = LINK.search(text or "")
    return match.group(0).rstrip(".,!?;:)]}…") if match else None


def skipped(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in SKIPPED_HOSTS)


async def resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def public(url: str, resolver=resolve) -> bool:
    """https, the default port, and every address of the host is on the public internet."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.port not in (None, 443) or parts.username:
        return False
    try:
        addresses = await resolver(parts.hostname)
    except (OSError, UnicodeError):
        return False
    try:
        return bool(addresses) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addresses)
    except ValueError:
        return False


def summary(page: str) -> str:
    """Title and description of an HTML page head, or an empty string."""
    found: dict[str, str] = {}
    for tag in META.findall(page):
        attributes = {name.lower(): first or second for name, first, second in ATTRIBUTE.findall(tag)}
        key = (attributes.get("property") or attributes.get("name") or "").lower()
        if key in WANTED and attributes.get("content") and key not in found:
            found[key] = attributes["content"]
    title = found.get("og:title") or found.get("twitter:title")
    if not title and (match := TITLE.search(page)):
        title = match.group(1)
    description = found.get("og:description") or found.get("twitter:description") or found.get("description")
    parts = [" ".join(html.unescape(part).split()) for part in (title, description) if part]
    return ". ".join(part for part in parts if part)[:1000]


async def peek(url: str, *, client: httpx.AsyncClient | None = None, resolver=resolve) -> str | None:
    """The title and description of a public page, or None (not allowed, not HTML, unreachable)."""
    if skipped(urlsplit(url).hostname or ""):
        return None
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT,
                                                                   "Accept-Language": "ru,uk;q=0.9,en;q=0.5"})
    try:
        async with asyncio.timeout(TIMEOUT):
            for _ in range(REDIRECTS + 1):
                if not await public(url, resolver) or skipped(urlsplit(url).hostname or ""):
                    return None
                async with client.stream("GET", url, follow_redirects=False) as response:
                    if response.is_redirect and "location" in response.headers:
                        url = urljoin(url, response.headers["location"])
                        continue
                    if response.status_code != 200 or "html" not in response.headers.get("content-type", ""):
                        return None
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) >= MAX_BYTES or b"</head>" in raw[-len(chunk) - 7:].lower():
                            break
                    page = bytes(raw).decode(response.charset_encoding or "utf-8", errors="replace")
                    return summary(page) or None
            return None
    except (httpx.HTTPError, TimeoutError, LookupError):
        return None
    finally:
        if own:
            await client.aclose()
