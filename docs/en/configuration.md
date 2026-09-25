# Configuration reference

🇷🇺 [Русская версия](../ru/configuration.md) · [← README](../../README.md)

Everything lives in `.env` in the project folder: the wizard writes it, and `.env.example` is the annotated template. Changes apply after a **restart**. Invalid values stop the bot with a clear message; test them with `python -m petbot check-config`.

## Required

| Variable | Meaning |
|---|---|
| `BOT_TOKEN` | Telegram bot token from @BotFather. |
| `AI_PROVIDER` | `claude` (default) or `openai`. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | The key of the chosen provider (only that one). |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | Model name. Defaults: `claude-haiku-4-5-20251001`, `gpt-4o-mini`. |

## The pet

| Variable | Default | Meaning |
|---|---|---|
| `LANGUAGE` | `en` | `en` / `ru`: messages, menu, AI instructions. |
| `BOT_NAMES` | – | Comma-separated names it reacts to in the group; short endings match too. The first is its display name. |
| `PERSONA_FILE` | `personas/cat.<LANGUAGE>.md` | The character file. Use `*.local.md` for your own (private). See [persona](persona.md). |
| `PERSONA_EMOJI` | – | Allowed emoji (max 1 per message). Empty = any. |
| `OWNER_ID` | – | Your numeric Telegram ID, if you prefer it to the one-time claim link. |

## Schedule

| Variable | Default | Meaning |
|---|---|---|
| `TIMEZONE` | `UTC` | IANA zone for all schedules (`Europe/Warsaw`, `Europe/Kyiv`, `America/New_York`...). Independent of the computer's clock settings. |
| `QUIET_START_HOUR` / `QUIET_END_HOUR` | `23` / `8` | No automatic posts in between. Conversation still works. |
| `NOTICE_SECONDS` | `8` | Service notices in the group ("saved", "owner only") disappear after N seconds. `0` = keep them. |

## Daily good news

| Variable | Default | Meaning |
|---|---|---|
| `NEWS_ENABLED` | `true` | Post good news in the family chat. |
| `NEWS_MODE` | `alternate` | `alternate`: 2 posts, 1, 2, 1... per day. `once`. `twice`. |
| `NEWS_HOUR` / `NEWS_MINUTE` | `11:00` | First slot (+ up to `NEWS_JITTER_MINUTES`=20 of stable per-chat randomness). |
| `NEWS_EVENING_HOUR` / `NEWS_EVENING_MINUTE` | `18:30` | Second slot (≥ 3 h after the first). |
| `NEWS_WINDOW_MINUTES` | `120` | How long a slot may retry if sources are down. |
| `NEWS_FEEDS` | 3 English good-news feeds | Comma-separated **https** RSS/Atom feeds. Any language: the AI retells in `LANGUAGE`. |
| `NEWS_MAX_AGE_DAYS` | `7` | Older items are ignored. |
| `MAX_NEWS_AI_CALLS_PER_DAY` | `6` | AI requests for choosing news, part of the total limit, so news can't starve the conversation. |

The AI rejects war, politics, disasters, ads, fundraising and horoscopes. When nothing fits, **nothing is posted**; no story is ever invented. Each article is reviewed once and never posted twice. `/news` gets one on demand.

## Your team

| Variable | Default | Meaning |
|---|---|---|
| `SPORTS_ENABLED` | `false` | Match-day announcements and results. |
| `SPORTS_TEAM_ID` | – | Numeric [TheSportsDB](https://www.thesportsdb.com) team id. The wizard finds it by name; or search the site, the id is in the address (`/team/133604-arsenal`). Any sport. |
| `SPORTS_COMMAND` | `match` | The Telegram command, e.g. `arsenal` gives `/arsenal`. |
| `SPORTS_HOUR` | `9` | Announcements are posted in a 3-hour window from this hour on match day, before kick-off. |
| `SPORTS_RESULTS` | `true` | Post the final result (only confirmed ones, max 18 h after kick-off). |
| `SPORTS_CHECK_MINUTES` / `SPORTS_IDLE_HOURS` | `15` / `6` | API polling: on match days / otherwise. |
| `SPORTS_API_KEY` | `3` | TheSportsDB's free public key. A Patreon key works too. |

Scores, teams and times come **only from the API**. The pet adds one line of commentary and can't change them.

## Limits and memory

| Variable | Default | Meaning |
|---|---|---|
| `MAX_AI_CALLS_PER_DAY` | `200` | HTTP attempts to the AI per day for the whole bot, counted in the database, so restarts don't reset it. **Not money**: set a spending limit at your provider too. |
| `USER_REQUESTS_PER_MINUTE` | `6` | Per person. |
| `AI_TIMEOUT_SECONDS` | `35` | A lost answer is **not** retried automatically, because it may already have been billed. |
| `HISTORY_DAYS` | `30` | Conversation memory: up to 50 lines per chat, none older than this. |
| `DATABASE_PATH` | `data/bot.sqlite3` | SQLite database. Readable only by you on Linux/macOS. |

## Owner commands (Telegram)

| Command | Where | What |
|---|---|---|
| `/setup_chat` | in the group | Choose the family group (one per bot). |
| `/status`, `/check` | private | State (free) / connection test (one small AI request). |
| `/allow ID`, `/deny ID`, `/users` | private | Who may talk to the pet in private. |
| `/who`, `/name Name`, `/unname` | group, as a reply | A person's ID / nickname. `/names` lists all. |
| `/forget` | anywhere | Clear memory (in the group: owner only). |
| `/del` | as a reply to the bot | Delete that bot message (≤ 48 h). |
| `/deliveries`, `/retry_delivery ID confirm` | private | Posts whose delivery Telegram didn't confirm. They are never retried automatically, so no duplicates. |
