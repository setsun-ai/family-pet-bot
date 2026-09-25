"""
Async calls to the official Anthropic / OpenAI REST APIs.

Secrets never become part of a prompt or a URL. Every HTTP attempt is counted
against the daily limit in SQLite (MAX_AI_CALLS_PER_DAY) before it is made.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from .common import ServiceError, limit_text
from .config import Settings
from .db import Database
from .i18n import prompt
from .persona import load_persona, tidy, wants_detail

log = logging.getLogger(__name__)

NEWS_SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string", "enum": ["accept", "reject"]}, "text": {"type": "string"}},
    "required": ["decision", "text"],
    "additionalProperties": False,
}


class AIError(ServiceError):
    pass


@dataclass(frozen=True)
class NewsDecision:
    accepted: bool
    text: str = ""


class AIService:
    def __init__(self, settings: Settings, db: Database, client: httpx.AsyncClient):
        self.settings, self.db, self.client = settings, db, client
        self.persona = load_persona(settings.persona_file)
        self.semaphore = asyncio.Semaphore(2)
        self.last_error: str | None = None
        self.last_success: str | None = None
        self.cooldown_until = 0.0

    def _emoji_rule(self) -> str:
        allowed = self.settings.persona_emoji
        return prompt("emoji_rule", emoji=" ".join(allowed)) if allowed else ""

    async def complete(self, system: str, messages: list[dict], *, json_output: bool = False, max_tokens: int = 700,
                       purpose: str = "chat", allow_partial: bool = False) -> str:
        s = self.settings
        async with self.semaphore:
            if time.monotonic() < self.cooldown_until:
                raise AIError(self.last_error or "ai_unavailable")
            if not s.api_key:
                raise AIError("ai_no_key")
            for attempt in range(2):
                day = datetime.now(s.tz).date().isoformat()
                category = {"category": "news", "category_limit": s.max_news_ai_calls_per_day} if purpose == "news" else {}
                if not await self.db.consume_ai_call(day, s.max_ai_calls_per_day, **category):
                    if purpose == "news" and await self.db.category_usage(day, "news") >= s.max_news_ai_calls_per_day:
                        raise AIError("ai_news_limit")
                    raise AIError("ai_daily_limit")
                if s.ai_provider == "claude":
                    url = "https://api.anthropic.com/v1/messages"
                    headers = {"x-api-key": s.api_key, "anthropic-version": "2023-06-01"}
                    body = {"model": s.model, "max_tokens": max_tokens, "system": system, "messages": messages}
                    if json_output:
                        # Structured outputs: valid JSON by construction, not by asking nicely.
                        body["output_config"] = {"format": {"type": "json_schema", "schema": NEWS_SCHEMA}}
                else:
                    url = "https://api.openai.com/v1/chat/completions"
                    headers = {"Authorization": "Bearer " + s.api_key}
                    body = {"model": s.model, "max_completion_tokens": max_tokens,
                            "messages": [{"role": "system", "content": system}, *messages], "store": False}
                    if json_output:
                        body["response_format"] = {"type": "json_schema", "json_schema": {
                            "name": "news_decision", "strict": True, "schema": NEWS_SCHEMA}}
                try:
                    async with asyncio.timeout(s.ai_timeout + 5):
                        response = await self.client.post(url, headers=headers, json=body,
                                                          timeout=s.ai_timeout, follow_redirects=False)
                except (httpx.HTTPError, TimeoutError):
                    # A lost response may still have been billed: never repeat it automatically.
                    self._failed("ai_no_response", 30)
                    raise AIError("ai_no_response") from None
                status = response.status_code
                if status == 429 or status >= 500:
                    if attempt == 0:
                        try:
                            delay = min(5.0, max(1.0, float(response.headers.get("retry-after", "1"))))
                        except ValueError:
                            delay = 1.0
                        await asyncio.sleep(delay)
                        continue
                    self._failed("ai_overloaded", 60)
                    raise AIError("ai_overloaded")
                if status != 200:
                    key = {400: "ai_http_400", 401: "ai_http_401", 403: "ai_http_403", 404: "ai_http_404"}.get(status)
                    self._failed(key or "ai_http_other", 60, status=status)
                    raise AIError(key or "ai_http_other", status=status)
                try:
                    data = response.json()
                    if s.ai_provider == "claude":
                        if data.get("stop_reason") == "refusal" or (data.get("stop_reason") == "max_tokens" and not allow_partial):
                            raise ValueError("truncated")
                        content = "\n".join(x["text"] for x in data["content"] if x.get("type") == "text")
                        truncated = data.get("stop_reason") == "max_tokens"
                    else:
                        choice = data["choices"][0]
                        if (choice.get("finish_reason") == "content_filter" or choice["message"].get("refusal")
                                or (choice.get("finish_reason") == "length" and not allow_partial)):
                            raise ValueError("incomplete")
                        content = choice["message"]["content"]
                        truncated = choice.get("finish_reason") == "length"
                    if not isinstance(content, str) or not content.strip():
                        raise ValueError("empty")
                except (ValueError, TypeError, KeyError, IndexError):
                    self._failed("ai_empty", 5)
                    raise AIError("ai_empty") from None
                self.last_error = None
                self.last_success = datetime.now(s.tz).isoformat(timespec="seconds")
                if truncated:
                    # Keep complete sentences instead of a broken last word; no paid retry.
                    ends = list(re.finditer(r"[.!?…](?=\s|$)", content))
                    content = content[: ends[-1].end()] if ends else content.rsplit(" ", 1)[0] + "…"
                return content.strip()
        raise AIError("ai_unavailable")

    def _failed(self, key: str, cooldown: int, **params) -> None:
        self.last_error = str(AIError(key, **params))
        self.cooldown_until = time.monotonic() + cooldown
        log.warning("%s", self.last_error)  # only our own sanitized text

    async def chat(self, history: list[dict], text: str, name: str, *,
                   reply_context: str | None = None, verified_name: bool = False) -> str:
        messages: list[dict] = []
        for row in history[-self.settings.history_messages:]:
            role = row.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = str(row.get("content", ""))[:4500]
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] += "\n" + content
            else:
                messages.append({"role": role, "content": content})
        while messages and messages[0]["role"] != "user":
            messages.pop(0)
        current = f"{name[:100]}: {text[:self.settings.max_input_chars]}"
        if messages and messages[-1]["role"] == "user":
            messages[-1]["content"] += "\n" + current
        else:
            messages.append({"role": "user", "content": current})
        details = wants_detail(text)
        identity = json.dumps({"author_name": name[:100], "nickname_set_by_owner": verified_name}, ensure_ascii=False)
        system = "\n\n".join(part for part in (self.persona, self._emoji_rule(), prompt("chat_rules"),
                                                prompt("author_info") + " " + identity) if part)
        if reply_context:
            system += "\n" + prompt("reply_context") + " " + json.dumps(reply_context[:1800], ensure_ascii=False)
        content = await self.complete(system, messages, max_tokens=1200 if details else 450, allow_partial=True)
        return limit_text(tidy(content, self.settings.persona_emoji, 2300 if details else 650, compact=not details))

    async def news(self, title: str, summary: str) -> NewsDecision:
        system = "\n\n".join(part for part in (self.persona, self._emoji_rule(), prompt("news_task")) if part)
        article = json.dumps({"title": title[:300], "summary": summary[:1800]}, ensure_ascii=False)
        raw = await self.complete(system, [{"role": "user", "content": article}], json_output=True,
                                  max_tokens=700, purpose="news")
        # A malformed answer is a failure, never a permanent editorial rejection.
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"decision", "text"}:
                raise ValueError
            decision, text = str(value["decision"]).lower(), value["text"]
            if decision not in {"accept", "reject"} or not isinstance(text, str):
                raise ValueError
            if decision == "accept":
                text = tidy(text, self.settings.persona_emoji, 550)
                if not text:
                    raise ValueError
                return NewsDecision(True, text)
            return NewsDecision(False, "")
        except (ValueError, TypeError, KeyError):
            raise AIError("ai_bad_json") from None

    async def sports_comment(self, facts: dict) -> str:
        """One short in-character line about a match. Facts come from the API, never from the model."""
        system = "\n\n".join(part for part in (self.persona, self._emoji_rule(), prompt("sports_task")) if part)
        raw = await self.complete(system, [{"role": "user", "content": json.dumps(facts, ensure_ascii=False)}],
                                  max_tokens=200, allow_partial=True)
        return tidy(raw, self.settings.persona_emoji, 220)

    async def check(self) -> str:
        return await self.complete("Reply with exactly: OK", [{"role": "user", "content": "Connection check."}],
                                   max_tokens=64)
