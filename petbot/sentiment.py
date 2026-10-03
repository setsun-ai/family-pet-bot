"""
Good news or bad news? A small dictionary-based reader for family messages.

No AI and nothing stored: words are reduced to their dictionary form with
pymorphy3 (Russian morphology, offline) and compared with short word lists.
Grammar matters more than the words themselves:

    "Сдала экзамен!"      past tense of an achievement verb -> achievement
    "Завтра сдаю"          a plan, not an achievement     -> nothing
    "Не сдала"             negated achievement            -> sad
    "Сдала?"               a question                     -> nothing
    "Аня заболела"         past tense of a misfortune     -> sad
    "Не болею"             negated misfortune             -> nothing
    "Сегодня 5 прилётов"   air raids, alarms, shelling    -> worry (never a joke)

English gets a lighter, pattern-based version. If pymorphy3 isn't installed,
Russian falls back to word stems (less precise, still useful).
The result also names who it's about: a family name in the message, or the author.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger(__name__)

ACHIEVE_VERBS = frozenset("""
сдать пересдать защитить защититься выиграть победить получиться справиться закончить окончить доделать дописать
научиться поступить устроиться поймать пробежать проплыть проехать похудеть сбросить нарисовать дорисовать
приготовить испечь починить выучить освоить отремонтировать накопить заработать выступить
забить построить сшить связать посадить вырастить добиться успеть дочитать
""".split())
# Count only together with an achievement word: "получила пятёрку", but not "получила посылку".
WEAK_VERBS = frozenset("получить сделать заслужить взять".split())
ACHIEVE_WORDS = frozenset("""
ура наконец пятёрка пятерка отлично победа повышение рекорд медаль оффер грамота диплом зачёт зачет кубок
""".split())
FAIL_VERBS = frozenset("""
провалить завалить проиграть потерять сломать сломаться заболеть простудиться уволить отменить опоздать разбить
упасть умереть погибнуть расстаться отчислить порвать пропасть украсть обжечься порезаться подвернуть
""".split())
SAD_WORDS = frozenset("""
грустно грустный печально печаль тоска тоскливо обидно жаль плохо ужасно кошмар беда увы болеть болезнь
температура устать усталость одиноко больница
""".split())
# War: air-raid alarms, drones, shelling. "Worry" wins over everything else and never gets a joke.
WORRY_WORDS = frozenset("""
прилет прилететь дрон шахед обстрел обстрелять тревога сирена ракета ракетный взрыв взрываться бомбить бомбежка
укрытие бомбоубежище подвал атака атаковать
""".split())
RELIEF_WORDS = frozenset("отбой".split())  # "отбой тревоги" is the end of the alarm, not a new one
NEGATIONS = frozenset({"не", "ни"})  # "нет, я сдала!" is not a negated achievement
HAPPY_EMOJI = set("🎉🥳🏆💪🔥🤩🙌✨")
SAD_EMOJI = set("😢😭💔😞😔😿🤒🤕")
# Laughing about it ("ахаха он упал с холодильника") means it isn't bad news.
LAUGHING = re.compile(r"(?:ах|ха|хе|ha|he){2,}|😂|🤣|😹|\b(?:лол|lol|lmao|ржу|ору)\b", re.I)
# Something the person merely wants or plans: "хочу сдать", "надеюсь выиграть", "если поступлю".
PLAN_WORDS = frozenset("хочу хотела хотел надеюсь надо нужно если бы попробую планирую собираюсь завтра скоро".split())

EN_ACHIEVE = re.compile(r"\b(?:i|we|she|he|they)\s+(?:finally\s+)?(?:passed|won|finished|nailed|got the job|got promoted|"
                        r"did it|made it|caught|ran|graduated|defended)\b|\bwe won\b|\bpromotion\b|\bnailed it\b", re.I)
EN_SAD = re.compile(r"\b(?:sick|ill|fever|failed|lost|broke|broken|sad|died|passed away|fired|cancelled|exhausted|"
                    r"heartbroken)\b", re.I)
EN_NEGATED = re.compile(r"\b(?:didn'?t|did not|couldn'?t|could not|not)\s+(?:pass|win|finish|make it|get)\b", re.I)


@dataclass(frozen=True)
class Feeling:
    kind: str  # "achievement" | "sad" | "worry"
    target: str | None  # the family name it's about, or None (= the author)
    strength: int  # 1 = a hint, 3+ = obvious ("ура, сдала!!! 🎉")


@lru_cache(maxsize=1)
def _morph():
    try:
        import pymorphy3
    except ImportError:
        log.warning("pymorphy3 is not installed: good/bad news detection uses word stems")
        return None
    return pymorphy3.MorphAnalyzer()


@lru_cache(maxsize=20000)
def _forms(word: str) -> tuple[tuple[str, str | None, str | None], ...]:
    """(lemma, part of speech, tense) for every reading of the word."""
    morph = _morph()
    if morph is None:
        return ((word, None, None),)
    return tuple((p.normal_form, p.tag.POS, p.tag.tense) for p in morph.parse(word)[:4])


def _stem_hit(word: str, lexicon: frozenset[str]) -> bool:
    """Fallback without pymorphy3: the word starts with the lexicon word minus its ending."""
    return any(len(lemma) > 4 and word.startswith(lemma[:-2]) for lemma in lexicon)


class Reader:
    def __init__(self, names: tuple[str, ...] = ()):
        self.names = {name.casefold().replace("ё", "е"): name for name in names}

    def _target(self, words: list[str], author: str | None) -> str | None:
        for word in words:
            for lemma, _, _ in _forms(word):
                name = self.names.get(lemma.replace("ё", "е")) or self.names.get(word)
                if name and (author is None or name.casefold() != author.casefold()):
                    return name
        return None

    def read(self, text: str, author: str | None = None) -> Feeling | None:
        if "?" in text:
            return None  # "сдала?" is a question, not news
        words = re.findall(r"[\w'’-]+", text.casefold().replace("ё", "е"))
        if not words:
            return None
        happy = sum(ch in HAPPY_EMOJI for ch in text) + (1 if "!" in text else 0)
        sad_marks = sum(ch in SAD_EMOJI for ch in text)
        good = bad = 0
        lemmas_all = {lemma.replace("ё", "е") for word in words for lemma, _, _ in _forms(word)} | set(words)
        if lemmas_all & RELIEF_WORDS:
            return None
        worry = sum(1 for word in words if {lemma.replace("ё", "е") for lemma, _, _ in _forms(word)} & WORRY_WORDS
                    or (_morph() is None and _stem_hit(word, WORRY_WORDS)))
        if worry or re.search(r"\b(?:air[- ]raid|drones?|shelling|missiles?|sirens?)\b", text, re.I):
            return Feeling("worry", self._target(words, author), max(worry, 1))
        if re.search(r"[а-я]", " ".join(words)):
            plan = any(w in PLAN_WORDS for w in words)
            for i, word in enumerate(words):
                negated = any(w in NEGATIONS for w in words[max(0, i - 2):i])
                forms = _forms(word)
                lemmas = {lemma.replace("ё", "е") for lemma, _, _ in forms}
                # Done, not planned: a past-tense verb or a short participle ("сдано"); without pymorphy3 we can't tell.
                past = _morph() is None or any(pos == "PRTS" or (pos == "VERB" and tense == "past")
                                               for lemma, pos, tense in forms
                                               if lemma.replace("ё", "е") in ACHIEVE_VERBS | FAIL_VERBS | WEAK_VERBS)
                if "болеть" in lemmas and words[i + 1:i + 2] == ["за"]:
                    continue  # "болею за Карпати" is football, not illness
                if lemmas & ACHIEVE_VERBS or (_morph() is None and _stem_hit(word, ACHIEVE_VERBS)):
                    if not past or plan:
                        continue
                    if negated:
                        bad += 2
                    else:
                        good += 2
                elif lemmas & WEAK_VERBS:
                    if past and not plan:
                        bad, good = (bad + 1, good) if negated else (bad, good + 1)
                elif lemmas & FAIL_VERBS or (_morph() is None and _stem_hit(word, FAIL_VERBS)):
                    if past and not negated and not plan:
                        bad += 2
                elif lemmas & SAD_WORDS:
                    if not negated:
                        bad += 1
                elif lemmas & ACHIEVE_WORDS and not negated:
                    good += 1
        else:
            if EN_NEGATED.search(text):
                bad += 2
            elif EN_ACHIEVE.search(text):
                good += 2
            if EN_SAD.search(text):
                bad += 1
        if good >= 2 and good > bad:
            return Feeling("achievement", self._target(words, author), good + happy)
        if bad >= 1 and bad >= good and not LAUGHING.search(text):
            return Feeling("sad", self._target(words, author), bad + sad_marks)
        return None
