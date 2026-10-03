# Changelog

## 1.2.0 (2026-10)

The pet talks less, notices more and has an inner life.

- **Mood** (`MOOD_ENABLED`, on by default): nine moods (sleepy, playful, grumpy, cuddly, hungry, philosophical, zoomies, royal, offended) that change at random every 1-6 hours, weighted by the time of day, independent of the chat. They colour the tone of replies and posts, the reactions (grumpy: 🗿 instead of ❤) and how chatty the pet is. `/mood` (owner) shows or sets it; `/status` shows it.
- **Good and bad news** (`SENTIMENT_ENABLED`): a local dictionary reader (pymorphy3) tells "сдала экзамен!" from "завтра сдаю", "не сдала" or "сдала?". Achievements get a short cheer from ready, mood-flavoured lines; bad news gets a sad reaction. No AI, nothing stored. New dependency: `pymorphy3`.
- **Memory** (`MEMORY_ENABLED`, `MEMORY_DAYS`): the chat answer can carry one fact to remember (structured output, no extra request); remembered facts return in later conversations. `/memory` lists and deletes them; `/forget` clears them.
- **Speaking up on its own** (`SPONTANEOUS_PER_WEEK`): on random days the pet starts a conversation, only while someone is writing, often asking about something it remembers. `/preview spontaneous` (owner) shows an example.
- **Chiming in** (`CHIME_IN_PER_DAY`): when the family is in a lively conversation with a clear vibe (laughing, celebrating, sad, food), the pet joins with one short line. The vibe is read locally; the AI gets only that word and the names, never the messages.
- **Matches without statistics** (`SPORTS_STYLE=casual`): one line when the match starts ("watching?") and one line with the score and an emotion afterwards; tables and scorers only on request. If nobody reacted to "watching?", the result becomes a sulky "Uh-huh." (or silence).
- **upl.ua schedule** for teams with `upl`: the official calendar (league and cup) instead of TheSportsDB's free key, which misses matches and once had a match a day early; TheSportsDB stays as a fallback. A score counts as final only 2 h 05 min after kick-off.
- Good/bad news reader: air raids, drones and shelling are "worry" - a quiet 😢, never a cheer or a quip, and they keep the pet from chiming in. Dark humour ("чуть не угробил") is not read as bad news.
- Reaction rules can react to pictures, GIFs, videos and stickers (`"media": true`), e.g. to one person's memes; the pet doesn't look at them, nothing is sent anywhere.
- Reaction rules can be `important` (not held back by the cooldown after a trivial reaction); cheers have a per-person cooldown, so one person's success doesn't use up another's.
- **Feedback** (`/stats`, owner): reactions and replies to the pet's messages, per kind of post, for the last 30 days. Telegram needs the bot to be a group admin to report reactions.

- **Shorter replies.** The pet writes like a comment under a video: no intros, no explaining the joke, no follow-up questions. Each reply gets a random size (a few words / one phrase / two short messages), so it doesn't answer everything with the same paragraph. `REPLY_MAX_CHARS` (300) caps an ordinary reply; "explain in detail" still gets a long one. Posts are shorter too: praise and birthdays 1-2 messages, match results 1-2, news up to ~380 characters.
- **Praise without a schedule.** `PRAISE_WEEKDAY=any` picks a random day every week and `PRAISE_UNTIL_HOUR` a random minute between `PRAISE_HOUR` and that hour. The moment is stable within a week (every tick and `/status` agree) and changes from week to week. Defaults are unchanged.
- **Reactions** (`REACTIONS_FILE`): an emoji under family messages the pet wasn't asked to answer, by your keyword rules (`any`/`all`/`none` patterns, `author`, `chance`) with a per-chat cooldown and a short "noticing" delay. Matched locally: no AI call, nothing stored. Telegram and Discord. Example: `examples/reactions.example.json`.
- **News on request** (`NEWS_MODE=on_request`): the pet stops posting news by itself. On `NEWS_REFRESH_DAYS` (Monday and Thursday) it reviews fresh stories in advance and keeps up to `NEWS_STOCK_SIZE` ready; `/news` or "any news?" / «есть новости?» gets one instantly, without downloading feeds or calling the AI while people wait. "I have news: …" is still a normal conversation.
- Thinking is turned off for the pet's one-liners on models that think by default (`claude-sonnet-5-5`: `between_tools`); measured ~10x fewer tokens and half the latency, same quality.
- **The pet's own birthday** (`PET_BIRTHDAY`): it celebrates itself and is royal all day.
- `/status` shows the date and time of the next praise and, with `on_request`, the news stock.

## 1.1.1 (2026-09)

Maintenance release: no changes in behaviour.

- CI tests every supported Python (3.11-3.14) on Linux, and 3.11 and 3.14 on Windows and macOS.
- Direct dependencies live in `requirements.in` (read by `pyproject.toml`); `requirements.txt`, the pinned set the launchers install, is generated from it with pip-compile, so Dependabot updates it as one consistent set. `pip install -e .` adds a `family-pet-bot` command.
- Dependabot opens weekly update PRs for Python packages and GitHub Actions; workflows use `actions/checkout@v7` and `actions/setup-python@v7`.
- Updated dependencies (all tests pass): `filelock` 4.0, `aiofiles` 25.1, `pydantic` 2.13.5 with `pydantic-core` 2.46.5 and 12 other minor/patch updates.

## 1.1.0 (2026-09)

The pet now writes like a family member in a messenger, not like a news robot - and it lives in Discord too.

- **Discord** (`PLATFORM=discord`): the same pet, character, news, matches, praise and birthdays. Slash commands with admin commands hidden from the family, answers to admin commands visible only to the owner, no pings ever, silent follow-up messages. Guide: docs/en/discord.md. The platform-independent parts (delivery, help/status/check/preview texts, access control) are shared, so both apps behave the same.
- **Your family's data** guide (docs/en/your-data.md) and example files in `examples/`.

- **Chat style:** replies are usually one short message; posts come as 2-4 short messages with a short "typing…" pause between them (one delivery, never duplicated). The pet no longer retells news or advertises its commands in conversation. It knows the local day and time.
- **Matches:** a match-day preview with a mini analysis (table positions of both teams) and, after the final whistle, the score, scorers with minutes, the table position and what to expect from the next match. Scorers only when they add up to the final score; the app adds the score or kick-off time if the AI forgets them.
- **Several teams** via `SPORTS_TEAMS_FILE`; new source **ESPN** (football leagues, Champions League: scorers and tables); optional upl.ua enrichment for the Ukrainian Premier League (full table and scorers). Team names are matched across alphabets.
- **Weekly praise** (`PRAISE_*`, `FAMILY_FILE`): one family member a week, everyone in turn, a small believable achievement told as the pet's gossip; recent praise is not repeated.
- **Birthdays** (`BIRTHDAY_HOUR`), 29 February handled.
- `NEWS_MODE=every2days`; news is 1-2 short messages.
- `/preview` (owner, private): see any automatic post before the family does.
- Automatic posts keep a distance from each other (no two posts back to back).
- Releases: pushing a version tag runs the tests and publishes a GitHub release with notes from this file.
- Command menus per audience: everyone sees the everyday commands; the owner additionally sees the admin commands in the private chat and the nickname commands in the family group.
- Quieter bursts: only the first message of a burst notifies; match results arrive silently. Match previews are 1-2 messages, results 2-3.
- Optional `POST_MODEL`: a stronger model only for the few automatic posts.

## 1.0.0 (2026-09)

First public release. A universal rewrite of a private family "cat" bot.

- **Any pet, any family:** the character is a persona text file (examples in EN/RU). Wake words (`BOT_NAMES`, with grammatical endings) and the emoji policy are configurable. Private personas are `*.local.md` and git-ignored.
- **English and Russian:** messages, menu, AI instructions, docs.
- **Sports for any team and any sport** via TheSportsDB (free) instead of a scraper for one league. Configurable command name. The AI adds one in-character line; the facts come only from the API.
- **Configurable RSS feeds** for the daily good news.
- Setup wizard with team search; `check`, `check-config` and `backup` commands; hidden Windows autostart; systemd service + daily backups for Raspberry Pi and servers.
- Kept from the original: owner claim with a one-time code, access control, spending limits in SQLite, at-most-once delivery with owner review, per-chat ordering, secret redaction.
- The database is compatible with the original version (owner, chat, memory and nicknames are kept).
- ~100 tests, CI on Linux, Windows and macOS.
