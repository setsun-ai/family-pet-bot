# Privacy / Приватность

🇬🇧 English below · 🇷🇺 [по-русски ниже](#по-русски)

**Short version:** family-pet-bot runs on **your** computer. The project has **no servers, no analytics and no telemetry**; its authors never receive any data. What leaves your machine goes only to the services you configure: Telegram, your AI provider, and optionally RSS feeds and TheSportsDB.

## What is processed

| Data | When | Stored locally? |
|---|---|---|
| Messages **addressed to the bot** (reply, @mention, its name, private chat) | always | yes: up to 50 lines per chat, deleted after `HISTORY_DAYS` (30) |
| Other group messages | never processed | **no**: dropped before touching the database |
| Telegram user IDs | for access control | yes: owner, allowed users, nicknames |
| Nicknames set by the owner (`/name`) | always | yes, until `/unname` |
| Sent posts (news, matches) | for de-duplication | IDs yes; the text of conversation answers is erased once sent |

## What goes where

| Destination | What exactly |
|---|---|
| **Telegram** | The bot's messages; Telegram also delivers the messages to the bot. |
| **Your AI provider** (Anthropic or OpenAI) | For an addressed message: the persona text, the author's name, the message, up to 8 recent lines of *that* chat, and the quoted bot message if it's a reply. For news: the article's title and summary. For match lines: team names and the score. OpenAI requests are sent with `store: false`. Both providers process data under their own terms. |
| **RSS feeds, TheSportsDB** | Ordinary public HTTPS requests. No personal data. |

Nothing else is sent anywhere: no crash reports, statistics or update checks.

## Things to know

- The local SQLite database is **not encrypted**. On Linux/macOS it's readable only by your user. Protect the machine.
- **Tell your family** there is a bot in the chat, and that messages addressed to it are processed by an AI provider. With *Group Privacy* off, Telegram delivers all group messages to the bot, which ignores those not addressed to it.
- Your **persona file** is sent with every AI request, so don't put secrets in it. Name it `*.local.md` so it can't be committed to git by accident.
- Children in the chat: if they talk to the bot, their messages go to the AI provider too. Check the provider's terms for minors.

## Your control

- `/forget` clears the chat's memory in the bot. Messages in Telegram and data at the provider are not affected.
- `/unname` removes a nickname; `/deny ID` removes private access.
- `/privacy` shows a short summary in the chat.
- **Delete everything:**
  1. stop the bot;
  2. delete the project folder (`data/`, `backups/`, `logs/`, `.env`);
  3. revoke the token in @BotFather (`/revoke`) and the AI key in the provider's console.

---

## По-русски

**Коротко:** family-pet-bot работает на **вашем** компьютере. У проекта **нет серверов, аналитики и телеметрии**; его авторы не получают никаких данных. С вашей машины данные уходят только в сервисы, которые вы настроили: Telegram, ваш ИИ-провайдер и, при желании, RSS-ленты и TheSportsDB.

### Что обрабатывается

| Данные | Когда | Хранится локально? |
|---|---|---|
| Сообщения, **адресованные боту** (Reply, @упоминание, его имя, личка) | всегда | да: до 50 реплик на чат, удаляются через `HISTORY_DAYS` (30) |
| Остальные сообщения группы | не обрабатываются | **нет**: отбрасываются до обращения к базе |
| Telegram ID пользователей | для контроля доступа | да: владелец, разрешённые, домашние имена |
| Домашние имена от владельца (`/name`) | всегда | да, до `/unname` |
| Отправленные публикации (новости, матчи) | против дублей | ID да; текст ответов в разговоре стирается после отправки |

### Что и куда уходит

| Куда | Что именно |
|---|---|
| **Telegram** | Сообщения бота; Telegram также доставляет сообщения боту. |
| **Ваш ИИ-провайдер** (Anthropic или OpenAI) | Для обращения: текст персонажа, имя автора, сообщение, до 8 последних реплик *этого* чата и цитируемое сообщение бота, если это Reply. Для новостей: заголовок и краткое содержание статьи. Для реплик о матчах: названия команд и счёт. Запросы к OpenAI отправляются с `store: false`. Оба провайдера обрабатывают данные по своим условиям. |
| **RSS-ленты, TheSportsDB** | Обычные публичные HTTPS-запросы. Без личных данных. |

Больше ничего никуда не отправляется: ни отчётов об ошибках, ни статистики, ни проверок обновлений.

### Что важно знать

- Локальная база SQLite **не зашифрована**. В Linux/macOS она доступна только вашему пользователю. Берегите компьютер.
- **Предупредите семью**, что в чате есть бот и что обращённые к нему сообщения обрабатывает ИИ-провайдер. При выключенном *Group Privacy* Telegram доставляет боту все сообщения группы, и бот игнорирует те, что не адресованы ему.
- **Файл персонажа** отправляется с каждым запросом к ИИ, поэтому не пишите в него секретов. Называйте его `*.local.md`, чтобы он случайно не попал в git.
- Дети в чате: если они обращаются к боту, их сообщения тоже уходят ИИ-провайдеру. Проверьте условия провайдера для несовершеннолетних.

### Ваш контроль

- `/forget` очищает память чата в боте. Сообщения в Telegram и данные у провайдера это не затрагивает.
- `/unname` удаляет домашнее имя; `/deny ID` отзывает личный доступ.
- `/privacy` показывает краткую сводку прямо в чате.
- **Удалить всё:**
  1. остановите бота;
  2. удалите папку проекта (`data/`, `backups/`, `logs/`, `.env`);
  3. отзовите токен в @BotFather (`/revoke`) и ключ ИИ в кабинете провайдера.
