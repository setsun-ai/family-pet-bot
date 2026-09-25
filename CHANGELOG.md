# Changelog

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
