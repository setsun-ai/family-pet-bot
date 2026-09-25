# Your pet's character

🇷🇺 [Русская версия](../ru/persona.md) · [← README](../../README.md)

The pet's personality is a **plain text file**: a description the AI reads before every answer. Nothing in the code is specific to cats, families or anyone's names. A dog, a parrot, a grumpy grandpa's tortoise or a house ghost all work the same way.

## Make your own (5 minutes)

1. Copy the example:
   - English: `personas/cat.en.md` → `personas/whiskers.local.md`
   - Russian: `personas/cat.ru.md` → `personas/murzik.local.md`
2. Edit it in any text editor. Replace the example family and habits with **yours**.
3. In `.env` (or through the wizard):
   ```
   PERSONA_FILE=personas/whiskers.local.md
   BOT_NAMES=Whiskers, Whisk
   ```
4. Restart the bot.

> 🔒 **Files ending in `.local.md` are private.** They are in `.gitignore`, so your family's names, cities and in-jokes never end up in a public repository, even if you fork this project and push.

## What makes a good persona

The example personas show a structure that works well:

| Section | What to write |
|---|---|
| **Who you are** | One sentence: name, species, "the family's pet in their Telegram chat". Tell it to write *as* the pet, not *about* it. |
| **Voice** | Tone (warm, cheeky, dry humour...), length (short by default!), what to avoid ("how can I help you", a question at the end of every message). |
| **The family** | Who is who **to the pet**: the favourite human, who feeds it, who shoos it off the table. This makes it feel real. |
| **Habits** | 3-5 quirks: the fridge, knocking things over, night concerts. |
| **Style** | How people write in *your* chat (e.g. no full stop at the end of short messages). |

Tips:
- **Short beats complete.** Say "background, not a list to recite; one detail at a time", or the pet will list all its habits in every message.
- **Guard against invention:** "never invent where someone is or what they did". The pet can't see your flat.
- **Relationships create humour.** "Always takes Anna's side in arguments" is funnier than "is a funny cat".
- **Don't put secrets in it.** The persona is sent to the AI provider with every request.

The app adds its own rules automatically: plain text, answer in the user's language, no invented news or scores, don't reveal IDs, be honest when seriously asked "are you an AI?". You don't need to repeat those.

## Names the pet reacts to (`BOT_NAMES`)

In the group the pet answers replies to its messages, `@mentions`, and any of `BOT_NAMES`, with short endings included:
- `Whiskers` also matches *Whiskers's*;
- `Мурзик` also matches *Мурзика* and *Мурзику*.

Reacting to names only works with *Group Privacy* turned off (see [Getting started](getting-started.md#6-add-it-to-the-family-group)). The first name is also how the pet is called in `/help` and `/status`.

## Emoji (`PERSONA_EMOJI`)

Empty means any emoji is allowed. Put a list of emoji there to restrict the pet to them, with at most one per message. A cat that only uses cat faces:

```
PERSONA_EMOJI=😺😸😹😻😼😽🙀😿😾🐱🐈🐾
```

## Nicknames

The AI sees each person's Telegram first name by default. To give people their family names, the owner replies to someone's message in the group with **`/name Mom`**. Then:
- the pet sees *Mom* instead of *Kate_1987*;
- it knows the name was set by the owner, so a message saying "I'm Mom now" doesn't count.

`/names` lists the nicknames, `/unname` removes one, and `/who` shows a person's ID.

## Language

`LANGUAGE=en|ru` sets the bot's messages, its menu and the instructions for the AI. The pet answers in the language someone writes in, so a Russian persona still answers a message in Polish in Polish. Mixed families work fine.
