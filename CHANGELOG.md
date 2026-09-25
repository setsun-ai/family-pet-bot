# Changelog

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
