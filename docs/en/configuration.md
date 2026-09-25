# Configuration reference

🇷🇺 [Русская версия](../ru/configuration.md) · [← README](../../README.md)

Everything lives in `.env` in the project folder: the wizard writes it, and `.env.example` is the annotated template. Changes apply after a **restart**. Invalid values stop the bot with a clear message; test them with `python -m petbot check-config`.

## Required

| Variable | Meaning |
|---|---|
| `PLATFORM` | `telegram` (default) or `discord`. The same pet in either app; see [Discord](discord.md). |
| `BOT_TOKEN` | Telegram bot token from @BotFather. |
| `DISCORD_TOKEN` | Discord bot token (Developer Portal → Bot), when `PLATFORM=discord`. |
| `AI_PROVIDER` | `claude` (default) or `openai`. |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | The key of the chosen provider (only that one). |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | Model name. Defaults: `claude-haiku-4-5-20251001`, `gpt-4o-mini`. |
| `POST_MODEL` | Optional stronger model only for automatic posts (match previews and results, praise, birthdays), e.g. `claude-sonnet-5`. They are a few a week, so it costs cents, and the writing is noticeably better than with a small model. Empty = the main model. |

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
| `NEWS_MODE` | `alternate` | `alternate`: 2 posts, 1, 2, 1... per day. `once`. `twice`. `every2days`: one post every other day. |
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

Scores, teams, times, scorers and table positions come **only from the sources**. The AI turns them into a few short chat messages (a match-day preview with a mini analysis; after the final whistle the score, scorers, table and what to expect next) and can't change them: if it forgets the score or the kick-off time, the app adds it. Scorers are named only if they add up to the final score.

### Several teams, other sources (`SPORTS_TEAMS_FILE`)

For more than one team, put them in a JSON file and set `SPORTS_TEAMS_FILE=teams.local.json` (then `SPORTS_TEAM_ID`/`SPORTS_COMMAND` are not used):

```json
[
  {"name": "Arsenal", "command": "arsenal", "source": "thesportsdb", "team_id": "133604", "hour": 10},
  {"name": "Arsenal in the CL", "command": "arsenal_cl", "source": "espn", "league": "uefa.champions",
   "team_id": "359", "hour": 12}
]
```

| Field | Meaning |
|---|---|
| `name`, `command` | How the family calls the team; the Telegram command (`a-z`, `0-9`, `_`). |
| `source` | `thesportsdb` (any sport, any league) or `espn` (football, with scorers and the table). |
| `team_id` | TheSportsDB id, or ESPN id (from the address of the team page on espn.com: `/team/_/id/359/`). |
| `league` | ESPN only: the league slug, e.g. `uefa.champions`, `eng.1`, `esp.1`, `ger.1`, `ukr.1`. |
| `competitions` | Optional list: only announce matches whose competition name contains one of these. |
| `hour` | The match-day preview window starts at this hour (default 9). |
| `results`, `analysis` | `false` turns off result posts / the AI text (then plain facts are posted). |
| `upl`, `upl_name` | Ukrainian Premier League: take the full table and the scorers from upl.ua (`upl_name` is the club's name there, e.g. `"Динамо"`). |

## The family: weekly praise and birthdays

| Variable | Default | Meaning |
|---|---|---|
| `FAMILY_FILE` | – | A JSON file with the family (below). Keep it private: `family.local.json` is git-ignored. |
| `PRAISE_ENABLED` | `false` | Once a week the pet praises ONE family member for a small, believable achievement. Everyone gets a turn before anyone is praised twice (5 people = 5 different weeks). |
| `PRAISE_WEEKDAY` / `PRAISE_HOUR` | `5` / `12` | Day (0 = Monday … 6 = Sunday) and the hour the 3-hour window opens. |
| `BIRTHDAY_HOUR` | `9` | Birthday greetings are posted from this hour. |

```json
{"members": [
  {"name": "Anna", "about": "16, loves drawing and her guitar", "birthday": "03-14"},
  {"name": "Dad", "about": "works in IT, makes pancakes on Sundays"}
]}
```

The "achievement" is invented, so the pet presents it as its own gossip or impression ("I heard it on the phone…"), never as a fact. Nothing serious or sensitive: no health, money, relationships, exam results. The more you write in `about`, the more believable it gets. Recent praise is remembered, so it doesn't repeat. `birthday` is `MM-DD` (29 February is celebrated on 28 February in other years); no year and no age.

## Limits and memory

| Variable | Default | Meaning |
|---|---|---|
| `MAX_AI_CALLS_PER_DAY` | `200` | HTTP attempts to the AI per day for the whole bot, counted in the database, so restarts don't reset it. **Not money**: set a spending limit at your provider too. |
| `USER_REQUESTS_PER_MINUTE` | `6` | Per person. |
| `AI_TIMEOUT_SECONDS` | `35` | A lost answer is **not** retried automatically, because it may already have been billed. |
| `HISTORY_DAYS` | `30` | Conversation memory: up to 50 lines per chat, none older than this. |
| `DATABASE_PATH` | `data/bot.sqlite3` | SQLite database. Readable only by you on Linux/macOS. |

## Owner commands (Telegram)

They all appear in the bot's "/" menu, but only for you: the admin commands in your private chat with the bot, the nickname commands in the family group. The family sees just the everyday commands.

| Command | Where | What |
|---|---|---|
| `/setup_chat` | in the group | Choose the family group (one per bot). |
| `/status`, `/check` | private | State (free) / connection test (one small AI request). |
| `/allow ID`, `/deny ID`, `/users` | private | Who may talk to the pet in private. |
| `/who`, `/name Name`, `/unname` | group, as a reply | A person's ID / nickname. `/names` lists all. |
| `/forget` | anywhere | Clear memory (in the group: owner only). |
| `/del` | as a reply to the bot | Delete that bot message (≤ 48 h). |
| `/deliveries`, `/retry_delivery ID confirm` | private | Posts whose delivery Telegram didn't confirm. They are never retried automatically, so no duplicates. |
| `/preview news`, `/preview praise [name]`, `/preview birthday [name]`, `/preview <team command>` | private | Generate a post and show it **only to you**: nothing goes to the group, nothing is marked as sent. For tuning the persona. One AI request. |
