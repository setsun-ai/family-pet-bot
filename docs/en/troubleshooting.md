# Troubleshooting & FAQ

🇷🇺 [Русская версия](../ru/troubleshooting.md) · [← README](../../README.md)

First step for anything: `/status` in a private chat with the bot. Next: `/check`, or `python -m petbot check` when the bot is stopped.

## The pet doesn't answer

| Symptom | Cause / fix |
|---|---|
| Doesn't react to its name in the group | *Group Privacy* is on. Turn it off in @BotFather, then **remove and re-add** the bot to the group. Meanwhile, reply to its messages or @mention it. |
| Silent in the group at all | The group isn't the family chat. Send `/setup_chat@bot_username` there from your account (not anonymously). |
| "This is a private family bot" in private | That person isn't allowed. They send `/id`, and you `/allow ID`. |
| Silent after being offline | Messages older than 5 minutes are skipped on purpose, so they don't cost money after downtime. |
| "rejected the API key" | Wrong or revoked key: fix it in `.env` and restart. |
| "overloaded or the API balance/limit is used up" | Add credit or raise the limit in the provider's console. |
| "daily AI request limit is reached" | `MAX_AI_CALLS_PER_DAY` was reached. It resets at midnight in `TIMEZONE`. |
| "model was not found" | The model name in `.env` is wrong or not available on your account. |

## Posts

- **No news today.** Possible reasons:
  - the sources are down (see `/status`);
  - every candidate was rejected: correct behaviour, nothing is invented;
  - quiet hours;
  - a post was made less than 2 hours ago.
- **No match announcement.**
  - Check `SPORTS_TEAM_ID` with `/check` or `/match`.
  - Announcements go out only on match day, in the window from `SPORTS_HOUR`, and before kick-off.
- **"Uncertain sends" in `/status`.** Telegram didn't confirm a post (network cut). Check the chat, then `/deliveries` and, only if the message really isn't there, `/retry_delivery ID confirm`.

## Setup

- **The owner link expired or was lost.** Restart the bot for a new one. Or set `OWNER_ID=<your numeric id>` in `.env`; get the id with `/id`.
- **"The bot is already running with this database".** A second copy was started. Stop the old window or service.
- **`TelegramConflictError` / 409 in the log.** The same token runs somewhere else (another folder or machine). Stop that copy.
- **Windows: nothing happens after `autostart.bat`.** Open `logs\bot.log`. Start `run.bat` once first, so `.venv` exists.

## FAQ

**How much does it cost?** A few requests per conversation line, plus up to `MAX_NEWS_AI_CALLS_PER_DAY` for news. With the small default models an active family chat typically costs cents per day. It depends on your usage and the provider's prices, so check your console and set a spending limit.

**Can the pet search the internet?** No. In conversation it has no web access and is told not to invent current facts. News comes from RSS and matches from TheSportsDB, never from the model's imagination.

**Does it read all our messages?** No. In the group, only messages addressed to it (a reply, an @mention or its name) are processed, stored and sent to the AI. With Group Privacy off, Telegram delivers all group messages to the bot, and it ignores the rest without storing them. See [PRIVACY.md](../../PRIVACY.md).

**Several groups?** One family group per bot, by design. For another family, run another bot with its own token and folder.

**Voice messages, photos?** Not supported: text only.
