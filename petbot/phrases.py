"""
Ready lines for cheering an achievement noticed in the chat (sentiment.py), without the AI.

"you" lines answer the author ("Сдала экзамен!"), "them" lines praise someone the
message is about ("Саша поймал щуку"). {name} is filled in when it's known; lines
with {name} are skipped otherwise. A mood (mood.py) has its own lines, used half the time.
Russian lines avoid gendered forms ("молодец", not "умница/умничка"), since the pet
doesn't know who is who.
"""
from __future__ import annotations

import random

from .i18n import language

CHEERS: dict[str, dict[str, list[str]]] = {
    "ru": {
        "you": [
            "{name}, вот это да 😻", "Горжусь. Официально, с холодильника", "Молодец! Я бы так не смог, у меня лапки",
            "Вот это уровень 🐾", "{name}, уважаю. Это тянет на влажный корм", "Красота. Отмечаем? Мне паштет",
            "Мрр, вот это новость 😸", "Так и знал, что получится", "Записал в летопись холодильника",
            "{name}, лучший человек в этом доме. После меня, конечно", "Вот это я понимаю 😼",
        ],
        "them": [
            "{name} — молодец, кот одобряет 😼", "Вот это да. {name} — гордость семьи",
            "{name} — моё уважение 🐾", "{name}, кошачье уважение передано", "Ну всё, {name} теперь легенда",
        ],
        "grumpy": ["Ладно. Признаю. Неплохо", "Хм. Ну допустим, молодец. Не зазнавайся 😾",
                   "Неплохо. Корма от этого больше не стало, но неплохо"],
        "sleepy": ["Мрр… молодец… разбудите, когда будем праздновать", "Сквозь сон: горжусь", "Зевнул от восторга"],
        "cuddly": ["{name}, иди сюда, обниму лапами 😻", "Вот за это и люблю", "Мурчу от гордости"],
        "hungry": ["Это надо отметить. Вкусным. Мне", "Поздравляю! Где мой праздничный паштет?"],
        "royal": ["Королевское одобрение получено 😼", "Я, кот этого дома, одобряю"],
        "philosophical": ["Так и проходит жизнь: от миски к победам. Горжусь",
                          "Каждое достижение — маленький шаг к холодильнику. Молодец"],
        "zoomies": ["ДА!!! Бегу по коридору в твою честь", "Тыгыдык в твою честь 🐾"],
        "playful": ["Ура! Это надо обнюхать 😸", "Вот это прыжок! Засчитано"],
        "offended": ["Я вообще-то обижен. Но… молодец", "Не разговариваю. Но горжусь 😾"],
    },
    "en": {
        "you": [
            "{name}, wow 😻", "Proud of you. Officially, from the top of the fridge", "Well done! I couldn't, I have paws",
            "That's a level-up 🐾", "{name}, respect. That deserves wet food", "Beautiful. Shall we celebrate? Pâté for me",
            "Purr, now that's news 😸", "Knew you'd make it",
        ],
        "them": ["{name} did great, the cat approves 😼", "Wow. {name} is the pride of this family", "Respect to {name} 🐾"],
        "grumpy": ["Fine. I admit it. Not bad", "Hm. Well done, I suppose. Don't get cocky 😾"],
        "sleepy": ["Purr… well done… wake me for the party", "Proud of you, in my sleep"],
        "cuddly": ["{name}, come here, paw hug 😻", "This is why I love you"],
        "hungry": ["This calls for a celebration. With food. For me", "Congrats! Where's my party pâté?"],
        "royal": ["Royal approval granted 😼", "I, the cat of this house, approve"],
        "philosophical": ["Life goes on: from the bowl to victories. Proud of you"],
        "zoomies": ["YES!!! Running laps in your honour", "Zoomies in your honour 🐾"],
        "playful": ["Yay! I need to sniff this 😸", "What a jump! Counted"],
        "offended": ["I'm offended, actually. But… well done", "Not talking to you. But proud 😾"],
    },
}


def cheer(name: str | None, *, about_someone_else: bool, mood: str | None, rng: random.Random) -> str:
    table = CHEERS.get(language(), CHEERS["en"])
    pool = table["them" if about_someone_else else "you"]
    if mood in table and not about_someone_else and rng.random() < 0.5:
        pool = table[mood]
    lines = [line for line in pool if name or "{name}" not in line] or table["you"][1:2]
    return rng.choice(lines).format(name=name or "")
