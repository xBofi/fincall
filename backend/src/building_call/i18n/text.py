"""Script-aware text normalization shared by every language.

The deterministic paths of the graph (yes/no, set phrases, addresses, apartments) compare what
the caller said against word lists. Both sides go through `basic_normalize`, so the comparison
ignores case, punctuation, Latin accents and Arabic diacritics, and reads any script's digits
as ASCII digits ("٣" and "३" are "3").

Until the registry existed this was Latin-only (`[^a-z0-9]` was dropped): an Arabic, Hindi or
Urdu sentence normalized to an empty string, so every deterministic path silently failed.
"""

from __future__ import annotations

import re
import unicodedata

#: Optional Arabic-script marks: harakat, superscript alef, Quranic marks. Written Arabic and
#: Urdu usually omit them, and an STT may or may not produce them
_ARABIC_MARKS = frozenset(
    [*range(0x0610, 0x061B), *range(0x064B, 0x0660), 0x0670, *range(0x06D6, 0x06EE)]
)
#: Letter variants that STT engines and keyboards mix freely
_FOLD = str.maketrans(
    {
        "ى": "ي",  # alef maksura -> yeh
        "ة": "ه",  # teh marbuta -> heh
        "ـ": None,  # tatweel (elongation)
        "‌": " ",  # zero-width non-joiner
        "‍": None,  # zero-width joiner
    }
)
_ORDINAL_MARKS = re.compile(r"[ºª°]")
_SPACES = re.compile(r"\s+")


def _is_latin_accent(c: str) -> bool:
    return 0x0300 <= ord(c) <= 0x036F


_APOSTROPHES = re.compile(r"['’]")


def basic_normalize(text: str, *, join_apostrophes: bool = False) -> str:
    """Lowercase, no punctuation, no Latin accents or Arabic diacritics, ASCII digits.

    Words are kept in their own script: Devanagari vowel signs are part of the word and stay.
    `join_apostrophes`: "it's" -> "its" (English) instead of "it s" (Catalan "l'ascensor" wants
    the space).
    """
    # º/ª would decompose into "o"/"a" under NFKD ("3º" -> "3o"): they go first
    text = _ORDINAL_MARKS.sub(" ", text.lower()).translate(_FOLD)
    if join_apostrophes:
        text = _APOSTROPHES.sub("", text)
    output: list[str] = []
    for c in unicodedata.normalize("NFKD", text):
        if _is_latin_accent(c) or ord(c) in _ARABIC_MARKS:
            continue
        digit = unicodedata.decimal(c, None)
        if digit is not None:
            output.append(str(digit))
            continue
        category = unicodedata.category(c)
        if category[0] in "LM" or category == "Nd":
            output.append(c)
        else:
            output.append(" ")
    return _SPACES.sub(" ", "".join(output)).strip()


def word_pattern(phrase: str) -> re.Pattern[str]:
    """Whole-word match of an already normalized phrase, in any script.

    `\\b` is not enough: in Python it does not treat Devanagari vowel signs as word characters,
    so `\\bकर\\b` would match inside a longer word. Spaces are the real separators after
    `basic_normalize`."""
    return re.compile(rf"(?:^|\s){re.escape(phrase)}(?=\s|$)")


def contains_phrase(normalized: str, phrase: str) -> bool:
    return bool(word_pattern(phrase).search(normalized))
