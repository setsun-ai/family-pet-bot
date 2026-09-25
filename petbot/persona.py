"""
The pet's character: loaded from a plain-text persona file (PERSONA_FILE).

The persona is just a system prompt - edit it like a letter describing your
pet and your family. Example personas live in personas/. Your own, private
one should be named *.local.md: those files are git-ignored, so family names
and details never end up in a public repository by accident.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Unicode ranges of pictographic emoji and their modifiers.
_EMOJI_RANGES = ((0x1F000, 0x1FAFF), (0x2600, 0x27BF), (0x2300, 0x23FF), (0x2B00, 0x2BFF), (0xE0000, 0xE007F))
_EMOJI_EXTRA = {0x200D, 0x20E3, 0xFE0E, 0xFE0F, 0x3030, 0x303D, 0x3297, 0x3299, 0x2139, 0x2122}


def load_persona(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").strip()


def _is_emoji(ch: str) -> bool:
    n = ord(ch)
    return any(low <= n <= high for low, high in _EMOJI_RANGES) or n in _EMOJI_EXTRA


def filter_emoji(text: str, allowed: str, *, maximum: int | None = None) -> str:
    """
    Keep only the persona's emoji (PERSONA_EMOJI) - e.g. a cat uses only cat
    faces - at most `maximum` of them. Empty `allowed` = no filtering at all.
    Digits, letters and URLs are never touched.
    """
    if not allowed:
        return str(text)
    out: list[str] = []
    count = 0
    for ch in str(text):
        if ch in allowed:
            if maximum is None or count < maximum:
                out.append(ch)
                count += 1
        elif not _is_emoji(ch):
            out.append(ch)
    return "".join(out)


# Small models sometimes slip Georgian look-alike letters into Cyrillic words ("Крივბас").
GEORGIAN_TO_CYRILLIC = str.maketrans(dict(zip(
    "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ",
    "абгдевзтиклмнопжрстуфкгкшчцдццхжх", strict=True)))


def fix_mixed_script(text: str) -> str:
    """Repair words that mix Cyrillic with Georgian letters; genuine Georgian text is left alone."""
    def fix(word: re.Match) -> str:
        w = word[0]
        return w.translate(GEORGIAN_TO_CYRILLIC) if re.search(r"[а-яёіїєґ]", w, re.I) else w
    return re.sub(r"\w*[\u10a0-\u10ff]\w*", fix, text)


def tidy(text: str, allowed_emoji: str, maximum: int = 650, *, compact: bool = True) -> str:
    """Clean an AI answer: persona emoji only (max 1), no control chars, cut at a sentence end."""
    text = fix_mixed_script(filter_emoji(text, allowed_emoji, maximum=1))
    text = "".join(c for c in text if c in "\n\t" or not unicodedata.category(c).startswith("C"))
    text = re.sub(r"[ \t]+", " ", text).strip()
    if compact:
        text = " ".join(text.split())
    if len(text) <= maximum:
        return text
    part = text[:maximum]
    ends = list(re.finditer(r'[.!?…](?:[»"”])?(?=\s|$)', part))
    if ends:
        return part[: ends[-1].end()].strip()
    cut = part.rsplit(" ", 1)[0].rstrip(",;:—- ")
    return (cut or part)[: maximum - 1] + "…"


MESSAGE_BREAK = "\n\n"


def tidy_messages(text: str, allowed_emoji: str, maximum: int = 650, *, max_parts: int = 4) -> str:
    """
    Like tidy(), but keeps the split into separate chat messages: the AI writes
    them separated by an empty line, the result is joined with MESSAGE_BREAK
    and later sent as separate Telegram messages. Extra parts become new lines of
    the last one; the length limit applies to the whole burst.
    """
    parts = [p for p in (tidy(part, allowed_emoji, maximum) for part in re.split(r"\n\s*\n", str(text))) if p]
    if len(parts) > max_parts:
        parts = parts[: max_parts - 1] + ["\n".join(parts[max_parts - 1:])]  # extra messages become lines
    result, used = [], 0
    for part in parts:
        if used + len(part) > maximum and result:
            break
        result.append(part if used + len(part) <= maximum else tidy(part, allowed_emoji, maximum - used))
        used += len(part)
    return MESSAGE_BREAK.join(result)


def split_messages(text: str) -> list[str]:
    return [part.strip() for part in str(text).split(MESSAGE_BREAK) if part.strip()] or [str(text).strip()]


def wants_detail(text: str) -> bool:
    """The user explicitly asked for a long answer (EN / RU)."""
    return bool(re.search(r"\b(?:подробн\w*|пошаг\w*|детальн\w*|развернут\w*|развёрнут\w*|"
                          r"explain\w*|detail\w*|step[- ]by[- ]step|in depth)\b", text, re.I))


def valid_nickname(raw: str) -> str | None:
    """A short label, not an instruction or an invisible/bidi control sequence."""
    value = " ".join(raw.split())
    if not 1 <= len(value) <= 40:
        return None
    if not all(unicodedata.category(c)[0] in {"L", "M", "N"} or c in " -'’." for c in value):
        return None
    return value
