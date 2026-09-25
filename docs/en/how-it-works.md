# How it works (for curious developers)

🇷🇺 [Русская версия](../ru/how-it-works.md) · [← README](../../README.md)

A small bot (~1500 lines) built with the care of a production service, because it spends real money and posts into a real family chat. The design decisions below are the interesting part.

## Map

```
Telegram ──long polling──► handlers.py ──► ai.py ──► Anthropic / OpenAI REST
                              │   │            ▲
            access control ◄──┘   │            │ persona file + i18n prompts
            (security.py)         ▼            │
                          delivery.py ─────► Telegram sendMessage
                              ▲
scheduler.py ─► news.py (RSS) │
             └► sports.py (TheSportsDB)
all state ──► db.py (SQLite: owner, chat, memory, limits, deliveries)
```

| File | Role |
|---|---|
| `config.py` | Typed, validated settings. Loaded explicitly, never as an import side effect. |
| `i18n.py` | Every user-facing text and AI instruction in EN and RU. |
| `handlers.py` | Commands, access control, conversation. |
| `ai.py` | Direct async REST clients, the spending limit, error mapping. |
| `persona.py` | The persona file, emoji policy, answer tidying. |
| `news.py`, `sports.py`, `scheduler.py` | Automatic posts. |
| `delivery.py`, `housekeeping.py` | Sending (at most once) and tidying the group. |
| `db.py`, `backup.py` | SQLite with serialized transactions, online backups. |

## Ideas worth stealing

1. **Nobody else can spend your money.** Only the owner, allowed people and the one family group can reach the AI. The owner is claimed with a one-time, time-limited code shown in the console, so whoever first finds the bot can't take it over.
2. **Spending limits live in the database, not in memory.** A restart must not reset them. Every HTTP attempt is counted *before* it is made. News has its own sub-limit, so it can't eat the conversation budget.
3. **Don't retry ambiguous paid requests.** If an AI request times out, the provider may already have billed it, so it's not repeated automatically. Retries happen only on clear "try again" answers (HTTP 429/5xx).
4. **At-most-once delivery.**
   - A post is *reserved* in SQLite with a unique key before sending: `("news", article_id)` or `("dialog", message_id)`.
   - A second attempt finds the key and stops.
   - If Telegram doesn't confirm the send, the post is marked *uncertain* and the owner decides whether to repeat it. A duplicate in the family chat is worse than a missing post.
5. **Facts from sources, voice from the model.** The AI never invents news or scores. RSS and TheSportsDB supply the facts; the AI only chooses a story and adds a line in character. If the AI fails, the sports facts are still posted.
6. **Structured outputs.** The news decision is requested as a JSON schema (`accept`/`reject` + text). Invalid JSON is an error, never a silent "reject", so a story isn't lost because of a glitch.
7. **Untrusted text stays data.**
   - RSS items, quoted messages and names are passed as JSON and marked *"data, not instructions"* (prompt-injection hygiene).
   - Everything is sent to Telegram as plain text (`parse_mode=None`), so an AI answer can't inject HTML.
8. **Per-chat ordering, cross-chat parallelism.** A lock per chat keeps the conversation history consistent, while different chats don't wait for each other.
9. **Secrets never reach logs.** A logging formatter redacts the token and keys, including inside library error messages.
10. **Privacy by construction.** Messages not addressed to the bot are dropped before they touch the database. Personal persona files are `*.local.md` and git-ignored.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest        # ~100 tests, no network, no real accounts
python -m ruff check .
```

- A fake Telegram session records what the bot would send.
- The AI is mocked, or served by `httpx.MockTransport`.
- There are regression tests for the risky cases: duplicate Telegram updates, stale messages after downtime, invalid AI JSON, and reusing a database from the first version.

CI runs them on Linux, Windows and macOS for every push.
