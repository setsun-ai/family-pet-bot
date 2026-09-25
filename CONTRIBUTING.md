# Contributing / Участие

🇬🇧 English below · 🇷🇺 [по-русски ниже](#по-русски)

Contributions are welcome, from a typo fix to a new feature.

```bash
git clone https://github.com/setsun-ai/family-pet-bot.git && cd family-pet-bot
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt       # Windows: .venv\Scripts\pip ...
.venv/bin/python -m pytest                           # must pass - no network, no accounts needed
.venv/bin/python -m ruff check .                     # must be clean
```

Start with [How it works](docs/en/how-it-works.md).

## Rules of thumb

- **Every user-facing text goes through `t("key")`**, and AI instructions through `prompt("key")`. Add the key to `petbot/i18n.py` in **both** languages with the same `{placeholders}`; a test checks this.
- **Nothing personal in the repository.** No family names in examples or tests; personas with real people go into `*.local.md`.
- **Money and messages are serious.**
  - Every AI call must go through `AIService.complete` (limits).
  - Every post must go through `DeliveryService.send` (no duplicates).
- **Tests don't touch the network.** Use `tests/support.py` (fake Telegram) and `httpx.MockTransport`.
- **Docs in both languages** (`docs/en`, `docs/ru`) when behaviour changes.

## Ideas

- 🌍 More languages: add `"uk"`, `"pl"`, `"de"`... to `i18n.py` plus an example persona.
- 🖼️ Photos: let the pet react to a picture (vision models).
- 🎂 Birthdays and reminders set by the owner.
- 📅 Several family groups per bot (the database has one family chat today).
- 🐳 A Dockerfile.

Found a bug? Open an issue with the output of `/status`, after removing names and IDs.

---

## По-русски

Любая помощь приветствуется, от опечатки до новой функции.

- Перед pull request должны проходить тесты (`python -m pytest`) и линтер (`python -m ruff check .`).
- Все тексты для людей идут через `t("ключ")`, инструкции ИИ — через `prompt("ключ")`, обязательно на **обоих** языках.
- Ничего личного в репозитории: персонажи с настоящими людьми хранятся в `*.local.md`.
- Каждый вызов ИИ должен идти через `AIService.complete` (лимиты), каждая публикация — через `DeliveryService.send` (без дублей).
- Документацию обновляйте на обоих языках.

**Идеи для начала:**
- новые языки;
- реакция на фото;
- дни рождения и напоминания;
- несколько групп;
- Dockerfile.
