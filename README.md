# family-pet-bot 🐾

[![CI](https://github.com/setsun-ai/family-pet-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/setsun-ai/family-pet-bot/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

🇷🇺 **[Русская версия →](README.ru.md)**

**Your family's pet, living in your family chat on Telegram or Discord.** An AI character you describe in a text file: your cat, dog, parrot or anyone else. It chats with the family in its own voice, like a person in a messenger: a few short messages, not a wall of text. It brings a kind news story, cheers for your teams, praises one of you every week and never forgets a birthday. English and Russian.

```
Anna:     Whiskers, did you eat my sandwich?
Whiskers: A sandwich? Never seen one. The fridge is my witness 😼

Whiskers: 3:1! We won
Whiskers: Miller in the 12th, Costa with a penalty, then Novak. Easy
Whiskers: Second place, two points behind the top. Harbour City away
          on Sunday, they're 7th - should be fine

Whiskers: Anna, I heard you won the chess tournament at school
Whiskers: Three games, three wins. I only knock the pieces over. Respect 😻
```

## Features

- 🎭 **Your own character.** The personality is a plain text file, with examples in English and Russian. Family names stay in a private `*.local.md` file that is never committed.
- 🔀 **Telegram or Discord.** The same pet in either app: pick one in the setup wizard.
- 💬 **Natural group chat.**
  - It answers replies, @mentions and its name, including grammatical forms: *Мурзика*, *Мурзику*.
  - It remembers the recent conversation per chat.
  - With nicknames (`/name Mom`) it knows who is who.
- 📰 **Daily good news** from RSS feeds you choose. The AI picks genuinely kind stories and retells them in the pet's voice. If nothing fits, nothing is posted; nothing is invented.
- ⚽ **Your teams, any sport.** Match-day previews with a mini analysis, and after the final whistle the score, scorers, table position and what to expect next. [TheSportsDB](https://www.thesportsdb.com) for any sport, ESPN for football leagues and the Champions League (free). The command names are yours: `/match`, `/arsenal`...
- 🏆 **Weekly praise and birthdays.** Once a week the pet praises one family member for a small, believable achievement, a different person every week. Birthdays are never forgotten.
- 📱 **Writes like a person.** A few short messages with a moment of "typing…" instead of one wall of text.
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

1. Create a bot with **@BotFather** in Telegram (or in the [Discord Developer Portal](docs/en/discord.md)) and get an AI key: [Claude](https://platform.claude.com/settings/keys) or [OpenAI](https://platform.openai.com/api-keys).
2. **Code → Download ZIP**, unpack it, and double-click **`run.bat`** on Windows or run `sh run.sh` on macOS/Linux. The setup wizard asks everything.
3. Claim the bot with the one-time code from the console, add it to your family group and send `/setup_chat` there (Discord: `/setup_channel`).
4. Tell it about your family: **[Your family's data](docs/en/your-data.md)**.

Step by step: **[Getting started](docs/en/getting-started.md)** · **[Discord](docs/en/discord.md)**.

## Documentation

| | |
|---|---|
| [Getting started](docs/en/getting-started.md) | Bot, AI key, costs, install, first start |
| [Your pet's character](docs/en/persona.md) | Writing the persona, names, emoji, nicknames |
| [Your family's data](docs/en/your-data.md) | Hobbies, birthdays, teams, nicknames: where each goes |
| [Discord](docs/en/discord.md) | The same pet in Discord: bot, invite, commands |
| [Running 24/7](docs/en/running.md) | Window, Windows autostart, Raspberry Pi, backups, moving |
| [Configuration](docs/en/configuration.md) | Every option and every command |
| [Troubleshooting & FAQ](docs/en/troubleshooting.md) | When it's silent, costs, privacy questions |
| [How it works](docs/en/how-it-works.md) | Architecture and design decisions, for developers |
| [Privacy](PRIVACY.md) · [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) | |

## Good to know

- **It costs a little.** AI APIs are pay-as-you-go. With the small default models a family chat usually costs cents a day. Set a spending limit at your provider.
- **Respect privacy.** The bot processes only messages addressed to it. Still, tell your family that a bot is in the chat and that addressed messages go to an AI provider. See [PRIVACY.md](PRIVACY.md).
- **Not affiliated** with Telegram, Discord, Anthropic, OpenAI, ESPN or TheSportsDB. MIT license, no warranty.

## License

[MIT](LICENSE)
