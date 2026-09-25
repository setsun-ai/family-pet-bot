# family-pet-bot 🐾

[![CI](https://github.com/setsun-ai/family-pet-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/setsun-ai/family-pet-bot/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

🇷🇺 **[Русская версия →](README.ru.md)**

**Your family's pet, living in your Telegram group chat.** An AI character you describe in a text file: your cat, dog, parrot or anyone else. It chats with the family in its own voice, posts a kind news story every day and cheers for your team on match days. English and Russian.

```
Anna:     Whiskers, did you eat my sandwich?
Whiskers: A sandwich? Never seen one. The fridge is my witness 😼

Whiskers: A sea otter at an aquarium learned to use a basket to bring
          toys to her keepers. Honestly, I'd just knock the basket over
          Source (05.09.2026): https://...

Whiskers: ⚽ Match day today!
          🏆 Premier League — next match
          Arsenal — Chelsea
          📅 12.10.2026, 17:30 (Europe/London)
          Three points, then a nap. I believe in us
```

## Features

- 🎭 **Your own character.** The personality is a plain text file, with examples in English and Russian. Family names stay in a private `*.local.md` file that is never committed.
- 💬 **Natural group chat.**
  - It answers replies, @mentions and its name, including grammatical forms: *Мурзика*, *Мурзику*.
  - It remembers the recent conversation per chat.
  - With nicknames (`/name Mom`) it knows who is who.
- 📰 **Daily good news** from RSS feeds you choose. The AI picks genuinely kind stories and retells them in the pet's voice. If nothing fits, nothing is posted; nothing is invented.
- ⚽ **Your team, any sport.** Match-day announcements and final results from [TheSportsDB](https://www.thesportsdb.com) (free). The command name is yours: `/match`, `/arsenal`...
- 🤖 **Claude or OpenAI**, called directly with your own key.
- 🛡️ **Safe with your money and your chat:**
  - only you, people you allow and your one family group can reach the AI;
  - daily request limits survive restarts;
  - no duplicate posts, and no paid automatic retries of lost requests;
  - quiet hours;
  - secrets never appear in logs.
- 🖥️ **Runs anywhere:** a window on Windows/macOS/Linux, hidden autostart on Windows, or 24/7 on a Raspberry Pi with a systemd service and daily backups.
- 🌍 **English and Russian:** bot messages, menu, AI instructions and documentation.

## Quick start

1. Create a bot with **@BotFather** in Telegram and get an AI key: [Claude](https://platform.claude.com/settings/keys) or [OpenAI](https://platform.openai.com/api-keys).
2. **Code → Download ZIP**, unpack it, and double-click **`run.bat`** on Windows or run `sh run.sh` on macOS/Linux. The setup wizard asks everything.
3. Open the one-time owner link from the console, add the bot to your family group and send `/setup_chat` there.

Step by step: **[Getting started](docs/en/getting-started.md)**.

## Documentation

| | |
|---|---|
| [Getting started](docs/en/getting-started.md) | Bot, AI key, costs, install, first start |
| [Your pet's character](docs/en/persona.md) | Writing the persona, names, emoji, nicknames |
| [Running 24/7](docs/en/running.md) | Window, Windows autostart, Raspberry Pi, backups, moving |
| [Configuration](docs/en/configuration.md) | Every option and every command |
| [Troubleshooting & FAQ](docs/en/troubleshooting.md) | When it's silent, costs, privacy questions |
| [How it works](docs/en/how-it-works.md) | Architecture and design decisions, for developers |
| [Privacy](PRIVACY.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) | |

## Good to know

- **It costs a little.** AI APIs are pay-as-you-go. With the small default models a family chat usually costs cents a day. Set a spending limit at your provider.
- **Respect privacy.** The bot processes only messages addressed to it. Still, tell your family that a bot is in the chat and that addressed messages go to an AI provider. See [PRIVACY.md](PRIVACY.md).
- **Not affiliated** with Telegram, Anthropic, OpenAI or TheSportsDB. MIT license, no warranty.

## License

[MIT](LICENSE)
