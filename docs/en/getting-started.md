# Getting started

🇷🇺 [Русская версия](../ru/getting-started.md) · [← README](../../README.md)

About **15 minutes**. You need: a computer (Windows, macOS or Linux), a Telegram account and an AI API key.

## 1. Create the Telegram bot (2 min)

1. In Telegram open **@BotFather** (blue check mark) and send `/newbot`.
2. Pick a display name (e.g. *Whiskers*) and a username ending in `bot` (e.g. `whiskers_family_bot`).
3. BotFather sends a **token** like `123456789:AAH...`. Keep it secret: it controls the bot.

## 2. Get an AI key and set a spending limit (5 min)

You need **one** of these. The bot talks to the API directly; there is no middleman.

| Provider | Key | Default model |
|---|---|---|
| Claude (Anthropic) | <https://platform.claude.com/settings/keys> | `claude-haiku-4-5-20251001` |
| OpenAI | <https://platform.openai.com/api-keys> | `gpt-4o-mini` |

- The API is **pay-as-you-go** and separate from ChatGPT Plus or Claude.ai subscriptions. Add some credit (a few dollars go a long way with the small default models).
- **Set a monthly spending limit in the provider's console.** The bot has its own safety limit too (`MAX_AI_CALLS_PER_DAY=200` requests a day), but that counts requests, not money.

## 3. Install Python and download the bot (3 min)

- **Python 3.11–3.14**:
  - Windows: `winget install Python.Python.3.13`, or [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*);
  - macOS: `brew install python`;
  - Debian/Ubuntu/Raspberry Pi OS: `sudo apt install python3 python3-venv`.
- On this GitHub page click **Code → Download ZIP** and unpack it to a new folder, e.g. `C:\family-pet-bot` or `~/family-pet-bot`. Or use `git clone`.

## 4. First start and setup wizard

| System | How |
|---|---|
| Windows | double-click **`run.bat`** |
| macOS / Linux | `sh run.sh` in the project folder |

The first start creates a private Python environment (`.venv`) and installs the libraries, which takes a minute. Then the **setup wizard** asks for:
1. the language: English or Russian;
2. the bot token and the AI key, typed hidden;
3. the pet's names and its persona file (see [Your pet's character](persona.md));
4. your time zone;
5. whether to post daily good news;
6. whether to follow a sports team (type its name and the wizard finds it).

To change the answers later, run `run.bat --setup` or `sh run.sh --setup`. You can also edit `.env` by hand; `.env.example` explains every line.

## 5. Become the owner

After the wizard, the bot starts and prints a **one-time link** in the console:

```
=== CLAIM THE BOT (first start) ===
Open this link from YOUR Telegram account and press START:
https://t.me/whiskers_family_bot?start=claim_...
```

Open it **from your own account** and press **Start**. You are now the owner: the only person who can configure the bot. The link works once, for 20 minutes; restart the bot for a new one.

## 6. Add it to the family group

1. Add the bot to your family group, like adding a person.
2. Send **`/setup_chat@your_bot_username`** in the group, from your own account and not anonymously.
3. To make the pet react to its **name** (not only to replies and @mentions), turn off *Group Privacy* in @BotFather → `/mybots` → your bot → *Bot Settings* → *Group Privacy* → *Turn off*. Then **remove the bot from the group and add it again**; Telegram needs that for the change to apply.

## 7. Check everything

In a **private chat** with the bot, send `/check`. It tests the database, the AI (one small, possibly paid request), the RSS feeds and the sports API.

Now try it in the group: *"Whiskers, what are you doing?"*

## What family members can do

- In the family group, anyone can talk to the pet: reply to its message, @mention it or call it by name.
- For a **private** chat with the pet, a person sends `/id` to the bot and you allow them with `/allow <id>` (in your private chat with the bot).
- `/help` lists all commands.

## Next

- [Your pet's character](persona.md): make it *your* pet.
- [Running it 24/7](running.md): the bot only works while the program runs.
- [Configuration](configuration.md) · [Privacy](../../PRIVACY.md) · [Troubleshooting](troubleshooting.md)
