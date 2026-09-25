# Your family's data

🇷🇺 [Русская версия](../ru/your-data.md) · [← README](../../README.md)

The pet gets really good when it knows your family: who likes what, when birthdays are, which teams you follow. This page shows where each piece goes. It takes 15–30 minutes and is worth it.

**Everything personal stays on your computer.** Files ending in `.local.md` / `.local.json` are git-ignored: they are never committed and never published, even if you fork the project. Only the parts the pet needs at a given moment go to the AI provider.

| What | Where | Used for |
|---|---|---|
| The character, the family as the pet sees it | `personas/<name>.local.md` | Everything the pet says |
| Family members: hobbies, birthdays | `family.local.json` | Weekly praise, birthday greetings |
| Teams you follow | `teams.local.json` | Match previews and results |
| Who is who in the chat | `/name` in the chat | Addressing people by their family names |

## 1. The persona: the pet's character (10 min)

Copy an example and edit it in any text editor:

```bash
cp personas/cat.en.md personas/whiskers.local.md
```

In `.env`: `PERSONA_FILE=personas/whiskers.local.md`. How to write a good one: [Your pet's character](persona.md).

A section about the family makes chat much livelier. Write facts, not a list of jokes, and tell the pet how to use them:

```text
THE FAMILY
Anna is my favourite human: she plays the violin and beats everyone at chess. Mom teaches
maths and grows tomatoes, Dad fixes everything and cycles to work. Grandpa lives in the
countryside; I know him only from video calls. Mention these things only when they fit -
not in every message.
```

## 2. The family: weekly praise and birthdays (10 min)

Copy [`examples/family.example.json`](../../examples/family.example.json) to `family.local.json` in the bot's folder and write your family:

```json
{"members": [
  {"name": "Anna", "about": "14, plays the violin and chess, reads fantasy", "birthday": "03-14"},
  {"name": "Dad",  "about": "fixes everything, cycles to work, cheers for the local club", "birthday": "12-05"}
]}
```

- `name`: how the pet addresses the person. Use the same name as `/name` in the chat.
- `about`: hobbies, job, what they're busy with now. This is what the praise is built from: the more concrete, the more believable ("learned the whole piece by heart", "won the chess club tournament"). Avoid anything sensitive (health, money, relationships): the pet is told not to touch it, but it's better not to give it at all.
- `birthday`: **`MM-DD`** (month first!), e.g. 14 March = `"03-14"`. No year, so the pet never mentions an age. Leave `""` if unknown.

In `.env`:

```ini
FAMILY_FILE=family.local.json
PRAISE_ENABLED=true
PRAISE_WEEKDAY=5      # 0 = Monday ... 5 = Saturday
PRAISE_HOUR=12        # a 3-hour window from this hour
BIRTHDAY_HOUR=9
```

Once a week the pet praises **one** person: everyone gets a turn before anyone gets a second one, so 5 people means 5 different weeks. It presents the praise as its own gossip ("I heard on the phone..."), never as a fact, in 2–3 short messages.

## 3. Your teams (5 min)

One team with TheSportsDB is enough for most: the setup wizard finds it by name. For several teams, the Champions League, scorers and tables, copy [`examples/teams.example.json`](../../examples/teams.example.json) to `teams.local.json` and set `SPORTS_TEAMS_FILE=teams.local.json`. Every field is explained in [Configuration → Several teams](configuration.md#several-teams-other-sources-sports_teams_file).

Finding IDs:

- **ESPN** (football with scorers and tables): open the team on espn.com. The number in the address is the id: `espn.com/soccer/team/_/id/359/arsenal` → `359`. League codes: `eng.1`, `esp.1`, `ger.1`, `ita.1`, `fra.1`, `ukr.1`, `uefa.champions`, `uefa.europa`.
- **TheSportsDB** (any sport): search the team on thesportsdb.com. The number in the address is the id: `/team/134867-los-angeles-lakers` → `134867`.

## 4. Nicknames in the chat (2 min)

So the pet knows that the account *anna_2009* is Anna:

- **Telegram:** in the family group reply to Anna's message with `/name Anna`.
- **Discord:** `/name user:@anna nickname:Anna`.

`/names` lists everyone.

## 5. Check before the family sees it

In a private chat with the bot:

```text
/preview praise Anna
/preview birthday Mom
/preview arsenal
/preview news
```

The post is shown **only to you**. Nothing goes to the family, nothing is marked as sent. Don't like it? Edit the persona or `about` and try again: changes to the files apply after a restart.

`/status` shows the family, the birthdays and who gets the next praise.

## Backups and moving

Your data lives in `.env`, `personas/*.local.md`, `*.local.json` and `data/` (memory, nicknames, owner). When moving to another computer, copy these along with the bot folder; see [Running 24/7](running.md).
