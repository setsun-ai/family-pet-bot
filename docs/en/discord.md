# Discord

🇷🇺 [Русская версия](../ru/discord.md) · [← README](../../README.md)

The same pet works in Discord: same character, conversation, good news, matches, weekly praise and birthdays. Only the chat app is different. Set `PLATFORM=discord` and follow these steps. It takes about **15 minutes**.

If you haven't yet, first read steps 2–3 of [Getting started](getting-started.md): the AI key, Python and downloading the bot are exactly the same.

## 1. Create the bot in the Developer Portal (5 min)

1. Open <https://discord.com/developers/applications> and log in. Click **New Application**, name it after your pet (e.g. *Whiskers*) and accept the terms.
2. **Bot** in the left menu:
   - **Reset Token** → copy the token. It looks like `MTEy...xyz.Ab12Cd.abc...`. Keep it secret: it controls the bot.
   - Under **Privileged Gateway Intents** turn on **MESSAGE CONTENT INTENT** and click **Save Changes**. Without it the pet can't read "Whiskers, hi" in the channel.
   - Optional: turn off **Public Bot**, so only you can add it to servers.
3. Optional: **General Information** → upload an avatar (a photo of your pet looks great).

## 2. A place for the family

The pet talks in **one family channel** and in direct messages. **Everyone who can see that channel can talk to it** and spend your AI budget. So:

- Best: a small **private server** just for the family (➕ in Discord → *Create My Own* → *For me and my friends*).
- Or a **private channel** on an existing server (channel settings → *Permissions* → *Private channel*, then add the family).

## 3. Configure (2 min)

Run the setup wizard (`run.bat` on Windows, `sh run.sh` on macOS/Linux), pick **2 — Discord** and paste the token. Or edit `.env` by hand:

```ini
PLATFORM=discord
DISCORD_TOKEN=MTEy...your token...
```

All other options (AI, persona, news, teams, family, schedule) are the same as for Telegram: see [Configuration](configuration.md).

## 4. First start: invite and claim (5 min)

1. Start the bot. The console shows two things:
   - **the invite link**, `https://discord.com/oauth2/authorize?client_id=...`: open it, choose your family server and click **Authorize**. The link already contains exactly the permissions the pet needs (View Channels, Send Messages, Read Message History);
   - **the one-time owner code**.
2. Send the bot a **direct message** from your own account: `/claim` and the code. You can also pick `/claim` from the menu. The code works once, for 20 minutes. If it expired, restart the bot to get a new one.
3. In the family channel run **`/setup_channel`**. The pet checks that it can write there and remembers the channel.
4. Test it: `/check` (AI and sources), then `Whiskers, hi` in the channel.

If the "/" menu doesn't show the commands yet, wait a minute and reopen Discord (Ctrl+R): Discord needs a moment to load new commands.

## How it works in Discord

| | |
|---|---|
| **Talking** | In the family channel the pet answers messages with its name (`BOT_NAMES`), @mentions and replies to its messages. In DMs it answers everything, but only to you and people you `/allow`. |
| **Commands** | Everything is in the "/" menu. Family members see only the everyday commands; admin commands are hidden from anyone who can't manage the server, and the bot checks that it's you anyway. |
| **Private answers** | Answers to admin commands (`/status`, `/preview`, `/names`...) are visible **only to you** ("Only you can see this"), even in the family channel. |
| **Notifications** | As in Telegram: a post is a few short messages, and only the first one makes a sound. Match results arrive silently. |
| **No pings** | The pet never pings anyone: no @everyone, no @here, no role mentions, whatever the AI writes. |
| **Nicknames** | `/name user:@Anna nickname:Anna`: pick the person from the list, no need to reply to their message. |
| **Deleting** | Right-click the pet's message → **Delete** (you need the *Manage Messages* permission, which the server owner has). |
| **IDs** | `/id` shows your Discord ID. For `/allow` just pick the person. |

## Commands

| Command | Who | What |
|---|---|---|
| `/help`, `/privacy`, `/id` | everyone | Help, what is stored and sent to the AI, your ID |
| `/news` | everyone | A good-news story now |
| `/<team>` (e.g. `/arsenal`) | everyone | Next match, last result, table |
| `/forget` | everyone in DMs; owner in the channel | Clear this chat's memory |
| `/claim code` | anyone, in DMs | Become the owner (first start) |
| `/setup_channel` | owner, in the channel | Make it the family channel |
| `/status`, `/check` | owner | State / connection test |
| `/preview what` | owner | See a post before the family: `news`, `praise Anna`, `birthday Mom`, or a team command |
| `/allow`, `/deny`, `/users` | owner | Who may write to the pet in DMs |
| `/name`, `/unname`, `/who`, `/names` | owner | Family nicknames |
| `/deliveries`, `/retry_delivery` | owner | Posts whose delivery Discord didn't confirm |

## Running 24/7

Exactly as for Telegram: see [Running 24/7](running.md). The same Raspberry Pi service works for Discord. Only `.env` differs.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Console: *Discord refused the connection: turn on MESSAGE CONTENT INTENT* | Developer Portal → your app → **Bot** → turn on **MESSAGE CONTENT INTENT** → Save. Restart. |
| Console: *Discord rejected DISCORD_TOKEN* | Developer Portal → **Bot** → **Reset Token**, paste the new one into `.env`. |
| `/setup_channel`: *I can't write in this channel* | Channel settings → Permissions → add the bot's role with View Channel, Send Messages, Read Message History. |
| The pet ignores its name in the channel | Did you run `/setup_channel` in *that* channel? Is the name in `BOT_NAMES`? Is MESSAGE CONTENT INTENT on? |
| Commands don't appear in "/" | Reopen Discord (Ctrl+R). Invite the bot with the link from the console: it includes the `applications.commands` scope. |
| Someone else uses the pet | Everyone who sees the family channel can. Make the channel private. |

One copy per token: don't run the same bot on two computers at once.
