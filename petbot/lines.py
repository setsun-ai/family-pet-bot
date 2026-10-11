"""
The pet's own lines without the AI - OWN_LINES=local (the default).

Everything the pet says without being asked comes from here: speaking up on its own
(spontaneous.py), chiming into a lively conversation (notice.py) and the casual match
lines (sports.py). The AI only answers when someone talks to the pet (and writes
birthday wishes). OWN_LINES=ai brings the AI-written lines back.

A line is chosen at random, never one of the last few of its kind (kept in the
database, so a restart doesn't bring the same joke back). Moods (mood.py) have their
own lines, used half the time. The lines are a cat's; for another animal use
OWN_LINES=ai or edit the lists. Russian lines are written from a male cat's side,
like the cheers in phrases.py. Match lines never decline team names (Russian cases):
"{team} — {opponent}".
"""
from __future__ import annotations

import json
import random

from .i18n import language

SPONTANEOUS: dict[str, dict[str, list[str]]] = {
    "ru": {
        "any": [
            "Я тут лежу. Просто чтобы вы знали",
            "Мрр. Без повода",
            "Сижу на подоконнике, слежу за голубями. Всё под контролем",
            "Если что, я не спал. Я думал",
            "Шкаф опять подозрительно на меня смотрит",
            "Кто последний трогал мою коробку? Я всё равно узнаю",
            "Просто проверяю, все ли на месте",
            "Хвост на месте, усы на месте. Можно жить",
            "Сегодня я решил быть хорошим котом. Пока получается",
            "Нашёл солнечное пятно. Делиться не буду",
            "Я тут подумал… А, нет, забыл",
            "Что-то у вас тихо. Подозрительно",
            "Уронил ручку со стола. Не благодарите",
            "Мяу. Это всё, что я хотел сказать",
            "Кто-нибудь видел мою мышку? Игрушечную. Наверное",
            "Сидел в пакете. Рекомендую",
        ],
        "morning": [
            "Утро. Самое время кого-нибудь разбудить",
            "Кто уже встал? Мне нужен свидетель моего завтрака",
            "Солнце встало, я тоже. Почти",
        ],
        "evening": [
            "Вечер. Время занять самое удобное место. Твоё",
            "Скоро ночь, а значит — скоро тыгыдык",
            "Кто сегодня будет греть мне диван?",
        ],
        "sleepy": ["Зеваю так, что видно все зубы. Красиво", "Сплю. Это сообщение написано во сне",
                   "Ещё пять минуточек. Или пять часов"],
        "playful": ["Кто хочет поиграть? Я уже начал без вас", "Напал на собственный хвост. Ничья",
                    "Ваши шнурки выглядят опасно. Займусь ими"],
        "grumpy": ["Не трогайте меня. Но и далеко не уходите", "Всё не так. Особенно погода",
                   "Я сегодня не в духе. Это не обсуждается"],
        "cuddly": ["Кто-нибудь, погладьте кота. Срочно", "Скучаю по вашим коленкам",
                   "Мурчу в вашу сторону. Чувствуете?"],
        "hungry": ["Миска намекает, что пора. Я с ней согласен", "Чем это пахнет? Мне тоже можно?",
                   "Слышу, как шуршит пакет. Это мне?"],
        "philosophical": ["Если кот упал с дивана и никто не видел — он вообще падал?",
                          "Смотрю в окно и думаю о вечном. О сосиске",
                          "Жизнь — это путь от миски до подоконника. И обратно"],
        "zoomies": ["Пробежал по всей квартире три раза. Зачем — не знаю",
                    "Не обращайте внимания, у меня тыгыдык", "Я быстрый. Очень быстрый. Уже в другой комнате"],
        "royal": ["Можете подойти и погладить. Аудиенция — пять минут", "Сегодня принимаю только комплименты",
                  "Трон (диван) занят. Навсегда"],
        "offended": ["Я обиделся. На что — не скажу", "Я вам не пишу. Это случайно отправилось",
                     "Ушёл под кровать. Не ищите. Хотя ищите"],
    },
    "en": {
        "any": [
            "Just lying here. Thought you should know",
            "Mrrr. No reason",
            "On the windowsill, watching pigeons. Everything is under control",
            "For the record, I wasn't sleeping. I was thinking",
            "The wardrobe is looking at me suspiciously again",
            "Who touched my box last? I'll find out anyway",
            "Just checking that everyone is still here",
            "Tail: present. Whiskers: present. Life is good",
            "Today I decided to be a good cat. So far so good",
            "Found a patch of sunshine. Not sharing",
            "I was thinking… nope, forgot",
            "It's quiet in here. Suspiciously quiet",
            "Pushed a pen off the table. You're welcome",
            "Meow. That's all I wanted to say",
        ],
        "morning": ["Morning. The perfect time to wake somebody up", "Who's up? I need a witness for my breakfast"],
        "evening": ["Evening. Time to take the comfiest spot. Yours", "Night is coming, and so are the zoomies"],
        "sleepy": ["Yawning so wide you can see all my teeth. Beautiful", "Asleep. This message was typed in a dream"],
        "playful": ["Who wants to play? I've already started without you", "Attacked my own tail. It's a draw"],
        "grumpy": ["Don't touch me. But don't go far either", "Everything is wrong today. Especially the weather"],
        "cuddly": ["Somebody pet the cat. Urgently", "I miss your laps"],
        "hungry": ["The bowl is hinting it's time. I agree with the bowl", "What smells so good? Can I have some?"],
        "philosophical": ["If a cat falls off the sofa and nobody sees it, did it really fall?",
                          "Looking out of the window, thinking about eternal things. Sausages"],
        "zoomies": ["Ran around the whole flat three times. Don't ask why", "Ignore me, I have the zoomies"],
        "royal": ["You may approach and pet me. Audience: five minutes", "Today I accept compliments only"],
        "offended": ["I'm offended. Won't say why", "I'm not writing to you. This sent itself"],
    },
}

CHIME: dict[str, dict[str, list[str]]] = {
    "ru": {
        "laughing": ["Я ничего не понял, но смешно 😹", "Смеётесь без меня? Подвиньтесь", "Ахах. Я тоже так умею. Почти"],
        "celebrating": ["Ура! Я тоже рад, хоть и не знаю чему", "Праздник? Значит, будет вкусное",
                        "Мур! Поздравляю всех на всякий случай"],
        "sad": ["Я рядом. Могу просто посидеть", "Мурчу вам. Это лечит, проверено",
                "Можно меня погладить. Говорят, помогает"],
        "food": ["Кто сказал «еда»? Я здесь", "Мне тоже кусочек. Маленький. Ну, средний",
                 "Я просто посижу рядом. Совершенно случайно"],
    },
    "en": {
        "laughing": ["No idea what's going on, but it's funny 😹", "Laughing without me? Move over"],
        "celebrating": ["Yay! I'm happy too, not sure what for", "A celebration? So there will be treats"],
        "sad": ["I'm here. I can just sit with you", "Purring in your direction. It helps, I checked"],
        "food": ["Did somebody say food? I'm here", "A small piece for me too. Well, medium"],
    },
}

KICKOFF: dict[str, list[str]] = {
    "ru": [
        "⚽ {team} — {opponent}, начинается! Смотрим?",
        "{team} — {opponent}, {place}. Уже начали. Смотрим?",
        "Матч начался: {team} — {opponent}. Кто со мной у телевизора?",
        "Свисток! {team} — {opponent}. Я уже на диване, а вы?",
    ],
    "en": [
        "⚽ {team} vs {opponent} is starting! Watching?",
        "{team} vs {opponent}, {place}. Kick-off! Watching?",
        "Kick-off: {team} vs {opponent}. Who's joining me at the TV?",
    ],
}
PLACE = {"ru": ("дома", "в гостях"), "en": ("at home", "away")}

RESULT: dict[str, dict[str, list[str]]] = {
    "ru": {
        "win": ["{score}! Победа 😻", "{score}. Выиграли! Мурчу на весь дом", "Победа, {score}. Это надо отметить"],
        "draw": ["{score}. Ничья. Ну, хоть не проиграли", "Ничья, {score}. Сойдёт", "{score}. Ни туда ни сюда"],
        "loss": ["{score}. Проиграли. Пойду полежу", "{score}. Не наш день 😿", "Проиграли, {score}. Не хочу об этом"],
    },
    "en": {
        "win": ["{score}! We won 😻", "{score}. A win! Purring all over the house"],
        "draw": ["{score}. A draw. At least we didn't lose", "A draw, {score}. It'll do"],
        "loss": ["{score}. We lost. Going to lie down", "{score}. Not our day 😿"],
    },
}

IGNORED: dict[str, list[str]] = {
    "ru": ["Ага.", "Ну и ладно. {score}, если кому интересно", "{score}. Смотрел один, между прочим",
           "Хм. Сам посмотрел. {score}"],
    "en": ["Uh-huh.", "Fine. {score}, if anyone cares", "{score}. Watched it alone, by the way"],
}


class Lines:
    """Picks a ready line and remembers the last few of each kind (in the database when one is given)."""

    def __init__(self, db=None, rng: random.Random | None = None):
        self.db, self.rng = db, rng or random.Random()
        self.memory: dict[str, list[str]] = {}  # without a database: recent lines in memory only

    async def _recent(self) -> dict[str, list[str]]:
        if self.db is None:
            return self.memory
        try:
            return json.loads(await self.db.get("own_lines_recent") or "{}")
        except ValueError:
            return {}

    async def pick(self, key: str, pool: list[str], *, remember: bool = True, **values) -> str:
        recent = await self._recent()
        used = recent.get(key, [])
        line = self.rng.choice([line for line in pool if line not in used] or pool)
        if remember:
            recent[key] = (used + [line])[-max(1, len(pool) // 2):]  # never one of the last half of the list
            if self.db is not None:
                await self.db.set("own_lines_recent", json.dumps(recent, ensure_ascii=False))
        return line.format(**values)

    async def spontaneous(self, mood: str | None, hour: int, *, remember: bool = True) -> str:
        table = SPONTANEOUS.get(language(), SPONTANEOUS["en"])
        if mood in table and self.rng.random() < 0.5:
            return await self.pick("spontaneous:" + mood, table[mood], remember=remember)
        pool = table["any"] + (table["morning"] if hour < 12 else table["evening"] if hour >= 18 else [])
        return await self.pick("spontaneous", pool, remember=remember)

    async def chime(self, vibe: str) -> str:
        table = CHIME.get(language(), CHIME["en"])
        return await self.pick("chime:" + vibe, table.get(vibe) or table["laughing"])

    async def kickoff(self, team: str, opponent: str, at_home: bool) -> str:
        lang = language() if language() in KICKOFF else "en"
        place = PLACE[lang][0 if at_home else 1]
        return await self.pick("kickoff", KICKOFF[lang], team=team, opponent=opponent, place=place)

    async def result(self, score: str, outcome: str) -> str:
        table = RESULT.get(language(), RESULT["en"])
        return await self.pick("result:" + outcome, table[outcome], score=score)

    async def ignored(self, score: str) -> str:
        return await self.pick("ignored", IGNORED.get(language(), IGNORED["en"]), score=score)
