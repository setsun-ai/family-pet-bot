# Security / Безопасность

🇬🇧 English below · 🇷🇺 [по-русски ниже](#по-русски)

## Your secrets

| Secret | Grants | Lives in |
|---|---|---|
| `BOT_TOKEN` | full control of the bot | `.env` |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | **spending your money** | `.env` |
| `data/bot.sqlite3`, `backups/` | conversation memory, IDs, nicknames | local files |
| `personas/*.local.md` | your family details | local file |

All of them are in `.gitignore`. Never commit, paste or screenshot them.

What the code does for you:
- Only the owner (claimed with a one-time, 20-minute code), allowed users and the single family group can make the bot call the AI. Everyone else is ignored.
- Spending limits are stored in the database and survive restarts. Lost paid requests are never retried automatically.
- Logs redact the bot token and API keys, including inside library errors.
- Replies are sent as plain text (`parse_mode=None`), so AI output can't inject Telegram HTML.
- Untrusted text (RSS, quotes, names) is passed to the AI as data marked "not instructions".
- `.env` and the database are created readable only by you (Linux/macOS).

**If a secret leaked:**
- Telegram: @BotFather → `/revoke`, then put the new token into `.env`.
- AI key: delete it in the provider's console and create a new one.
- Restart the bot.

## Reporting a vulnerability

Please use **Security → Report a vulnerability** on GitHub (a private advisory), not a public issue.

---

## По-русски

**Секреты:**
- `BOT_TOKEN` даёт полный контроль над ботом.
- API-ключ ИИ позволяет **тратить ваши деньги**.
- База и резервные копии содержат память разговоров и ID.
- `personas/*.local.md` содержит сведения о вашей семье.

Всё это есть в `.gitignore`. Никогда не публикуйте эти данные и не присылайте их скриншоты.

**Если секрет утёк:**
- Telegram: @BotFather → `/revoke`, затем новый токен в `.env`.
- Ключ ИИ: удалите его в кабинете провайдера и создайте новый.
- Перезапустите бота.

**Уязвимости** сообщайте приватно: GitHub → *Security → Report a vulnerability*, а не в публичном issue.
