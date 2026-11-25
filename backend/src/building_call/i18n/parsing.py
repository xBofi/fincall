"""The deterministic paths of the graph, per language.

The graph only calls the model when a word list or a format can't decide (one LLM call per
turn at most; yes/no, a short address, "3º B", set phrases cost ~0 ms). Those word lists live
in the locale files; this module holds the rules that use them:

- `Parser` is the generic implementation, driven only by the lexicon. A new language gets it
  without code: compact apartments ("3A", "3 A"), "<door word> <letter>", spoken floors.
- `SpanishParser` keeps the Spanish rules tuned on real calls ("3º B", "tercero B", "puerta
  izquierda", "treinta y dos", the STT turning a short "sí" into "S.").
- `EnglishParser` is the generic one: the English rules from the US version are what the
  generic parser does.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from difflib import SequenceMatcher
from functools import cache
from typing import Any, Literal

from babel import Locale
from babel.lists import format_list

from building_call.i18n.models import Language
from building_call.i18n.text import basic_normalize, contains_phrase

#: "No," / "لا،" / "नहीं।": a pause right after the first word
_PAUSE_AFTER_FIRST_WORD = re.compile(r"^\W*\w+[\u0900-\u097F\u064B-\u065F]*\s*[,.;:!?،؛।]")
#: One letter of any script (normalized text is lowercase)
_LETTER = r"[^\W\d_]"
_SENTENCE_END = re.compile(r"[.!?؟।]\s+")


def _alternation(words: Iterable[str]) -> str:
    # Longest first, so "tercera" wins over "tercer"
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True) if w)


class Parser:
    """Generic, lexicon-driven rules. Subclasses only change what the data can't express."""

    #: "it's" -> "its" (English) instead of "it s"
    join_apostrophes = False

    def __init__(self, language: Language) -> None:
        self.language = language
        lx = language.lexicon
        n = self.normalize
        self.yes = frozenset(n(w) for w in lx.yes)
        self.no = frozenset(n(w) for w in lx.no)
        self.no_start = frozenset(n(w) for w in lx.no_start)
        self.short_yes = frozenset(n(w) for w in lx.short_yes)
        self.short_no = frozenset(n(w) for w in lx.short_no)
        self.not_heard = tuple(n(w) for w in lx.not_heard)
        self.hello = tuple(n(w) for w in lx.hello)
        self.filler = tuple(n(w) for w in lx.filler)
        self.greetings = tuple(n(w) for w in lx.greetings)
        self.verbal_tics = frozenset(w for p in lx.verbal_tics for w in n(p).split())
        self.person = tuple(n(w) for w in lx.person)
        self.farewell = tuple(n(w) for w in lx.farewell)
        self.contact = tuple(n(w) for w in lx.contact)
        self.meta_bot = tuple(n(w) for w in lx.meta_bot)
        self.address_filler = frozenset(n(w) for w in lx.address_filler)
        self.numbers = {n(k): v for k, v in lx.numbers.items()}
        self.number_joiners = frozenset(n(w) for w in lx.number_joiners)
        self.gas = tuple(n(w) for w in lx.gas)
        self.fire = tuple(n(w) for w in lx.fire)
        self.refusal_words = frozenset(n(w) for w in lx.refusal_words)
        self.refusal_followers = frozenset(n(w) for w in lx.refusal_followers)
        home = language.home
        self.floors = {n(k): v for k, v in home.floors.items()}
        self.doors = {n(k): v for k, v in home.doors.items()}
        stop = _alternation(n(w) for w in lx.address_stopwords)
        self._address_stop = re.compile(rf"(?<!\S)(?:{stop})(?!\S)") if stop else None
        self._entrance = _alternation(n(w) for w in home.entrance_markers)
        self._floor_markers = _alternation(n(w) for w in home.floor_markers)
        self._door_markers = _alternation(n(w) for w in home.door_markers)
        self._floor_words = _alternation(
            k for k in self.floors if k not in {n(w) for w in home.floor_words_not_in_text}
        )

    # ------------------------------------------------------------------ text

    def normalize(self, text: str) -> str:
        return basic_normalize(text, join_apostrophes=self.join_apostrophes)

    def numbers_to_digits(self, text: str) -> str:
        """'thirty two' -> '32', 'treinta y dos' -> '32', 'doce' -> '12'. The STT may spell
        numbers out. `text` is already normalized."""
        tokens = text.split()
        output: list[str] = []
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if t not in self.numbers:
                output.append(t)
                i += 1
                continue
            value = self.numbers[t]
            if 20 <= value < 100 and value % 10 == 0:
                j = i + 1
                joined = j < len(tokens) and tokens[j] in self.number_joiners
                if joined:
                    j += 1
                required = self.language.lexicon.number_joiner_required
                if (
                    (joined or not required)
                    and j < len(tokens)
                    and tokens[j] in self.numbers
                    and 0 < self.numbers[tokens[j]] < 10
                ):
                    value += self.numbers[tokens[j]]
                    i = j
            output.append(str(value))
            i += 1
        return " ".join(output)

    def question_start(self, text: str) -> int | None:
        """Where the first question sentence starts ("Hi ... recorded. Which building...?" ->
        the index of "Which"), or None if the text asks nothing. Spanish opens it with "¿"."""
        opening = text.find("¿")
        if opening >= 0:
            return opening
        marks = [i for i in (text.find("?"), text.find("؟")) if i >= 0]
        if not marks:
            return None
        start = 0
        for m in _SENTENCE_END.finditer(text, 0, min(marks)):
            start = m.end()
        return start

    def asks_question(self, text: str) -> bool:
        return "?" in text or "؟" in text

    def mid_sentence(self, text: str) -> str:
        """'The elevator is stuck' -> 'the elevator is stuck', to read it after 'right: '."""
        if not self.language.phrases.lowercase_summary:
            return text
        if text[1:2].isupper():
            return text  # acronyms ("TV")
        return text[:1].lower() + text[1:]

    # ------------------------------------------------------------------ addresses

    def normalize_address(self, text: str) -> str:
        text = self.numbers_to_digits(self.normalize(text))
        if self._address_stop is not None:
            text = self._address_stop.sub(" ", text)
        return re.sub(r"\s+", " ", text).strip()

    def address_similarity(self, utterance: str, address: str) -> float:
        a, b = self.normalize_address(utterance), self.normalize_address(address)
        if not a or not b:
            return 0.0
        tokens_b = set(b.split())
        contained = sum(1 for t in tokens_b if t in a.split())
        coverage = contained / len(tokens_b)
        return max(SequenceMatcher(None, a, b).ratio(), coverage)

    def only_address(self, utterance: str, address: str) -> bool:
        """The sentence is only the address (with filler words). "12 Main Street, the intercom
        is broken" is not: the rest is the problem and the model has to read it (2026-10-21)."""
        own = set(self.normalize_address(address).split())
        leftover = [
            t
            for t in self.normalize_address(utterance).split()
            if t not in own and t not in self.address_filler and t not in self.verbal_tics
        ]
        return not leftover

    # ------------------------------------------------------------------ yes / no

    def yes_no(self, text: str) -> bool | None:
        t = self.normalize(text)
        if t in self.short_yes:
            return True
        if t in self.short_no:
            return False
        words = t.split()
        word_set = set(words)
        # Multi-word phrases ("that's right", "eso es") are matched as substrings: the STT may
        # put noise in front ("Ese, eso es.")
        has_yes = bool(word_set & self.yes) or any(" " in s and s in t for s in self.yes)
        has_no = bool(word_set & self.no) or any(" " in s and s in t for s in self.no)
        if has_yes and not has_no:
            return True
        if has_no or (words and words[0] in self.no_start):
            return False
        return None

    def explicit_no(self, text: str) -> bool:
        """A "no" said in so many words ("no, it's 3A", "wrong"). A "no" read only from "isn't"
        is usually the problem itself ("the intercom isn't working").

        Where the problem itself often starts with the word for "no" ("No funciona el
        portero", "لا يعمل المصعد"), `no_start_needs_pause` asks for more than the first word:
        a pause after it ("No, es el 3º B"), nothing else, or one of `refusal_followers`."""
        words = self.normalize(text).split()
        if not words:
            return False
        if self.refusal_words & set(words):
            return True
        if words[0] not in self.no_start:
            return False
        if not self.language.lexicon.no_start_needs_pause or len(words) == 1:
            return True
        return bool(_PAUSE_AFTER_FIRST_WORD.match(text)) or words[1] in self.refusal_followers

    def moves_on_to_problem(self, problem: str | None, address: str | None, text: str) -> bool:
        """Answering a read-back with the problem ("the elevator is stuck") accepts it.

        The model often marks it as a "no" because it is not the yes/no that was asked; only an
        explicit refusal counts ("no, …", "wrong")."""
        if not problem or address:
            return False
        return not self.explicit_no(text)

    # ------------------------------------------------------------------ apartments

    def normalize_entrance(self, value: str | None) -> str | None:
        if value is None:
            return None
        v = self.numbers_to_digits(self.normalize(str(value)))
        if self._entrance:
            v = re.sub(rf"(?<!\S)(?:{self._entrance})(?!\S)", "", v).strip()
        # An entrance is a number (1-2 digits) or a single letter; "the", "el"... are noise
        if not v or not re.fullmatch(rf"\d{{1,2}}|{_LETTER}", v):
            return None
        return v

    def normalize_floor(self, value: str | None) -> str | None:
        if value is None:
            return None
        v = self.normalize(str(value))
        strip = _alternation(
            [*(self.normalize(w) for w in self.language.home.floor_markers),
             *(self.normalize(w) for w in self.language.home.floor_noise)]
        )  # fmt: skip
        if strip:
            v = re.sub(rf"(?<!\S)(?:{strip})(?!\S)", "", v).strip()
        v = self.floors.get(v, v)
        v = self.numbers_to_digits(v)
        m = re.match(r"(\d+)", v)
        return m.group(1) if m else (v or None)

    def normalize_door(self, value: str | None) -> str | None:
        if value is None:
            return None
        v = self.normalize(str(value))
        strip = _alternation(
            [*(self.normalize(w) for w in self.language.home.door_markers),
             *(self.normalize(w) for w in self.language.home.door_noise)]
        )  # fmt: skip
        if strip:
            v = re.sub(rf"(?<!\S)(?:{strip})(?!\S)", "", v).strip()
        v = self.doors.get(v, v)
        return v.upper() or None

    def extract_home(self, text: str) -> tuple[str | None, str | None, str | None]:
        """Deterministic fallback for the usual formats: 'apartment 3A', 'unit 3 B', 'third
        floor, B', 'entrance 2, 3A'."""
        t = self.numbers_to_digits(self.normalize(text))
        entrance = None
        remainder = t
        if self._entrance:
            m_entrance = re.search(rf"(?<!\S)(?:{self._entrance})\s+(\S+)", t)
            entrance = self.normalize_entrance(m_entrance.group(1)) if m_entrance else None
            remainder = re.sub(rf"(?<!\S)(?:{self._entrance})\s+\S+", " ", t)
        door_words = rf"(?:{self._door_markers})" if self._door_markers else None
        # "3a", "3 a": floor and apartment letter together
        prefix = rf"(?:(?<!\S){door_words}\s+)?" if door_words else ""
        m_compact = re.search(rf"{prefix}(?<!\S)(\d{{1,2}})\s?({_LETTER})(?!\S)", remainder)
        # "on 3 a leak..." is not apartment 3A: a spaced one-letter word only counts at the end
        spaced_word = (
            m_compact is not None
            and m_compact.group(2) in self.language.home.one_letter_words
            and remainder[m_compact.start(2) - 1] == " "
            and m_compact.end() < len(remainder)
        )
        if m_compact and not spaced_word:
            return (
                entrance,
                self.normalize_floor(m_compact.group(1)),
                self.normalize_door(m_compact.group(2)),
            )
        floor_alt = rf"\d{{1,2}}|{self._floor_words}" if self._floor_words else r"\d{1,2}"
        marker_after = rf"(?:\s+(?:{self._floor_markers}))?" if self._floor_markers else ""
        m_floor: Any = re.search(rf"(?<!\S)({floor_alt})(?!\S){marker_after}", remainder)
        if self._floor_markers:
            m_floor_word = re.search(rf"(?<!\S)(?:{self._floor_markers})\s+(\d{{1,2}})(?!\S)", remainder)
            if m_floor_word:
                m_floor = m_floor_word
        floor = self.normalize_floor(m_floor.group(1)) if m_floor else None
        door = None
        m_explicit = (
            re.search(rf"(?<!\S){door_words}\s+({_LETTER}|\d{{1,2}})(?!\S)", remainder)
            if door_words
            else None
        )
        if m_explicit and (m_floor is None or m_explicit.start(1) != m_floor.start(1)):
            door = self.normalize_door(m_explicit.group(1))
        elif m_explicit and m_explicit.group(1).isdigit():
            # "apartment 5", "फ्लैट 5", "شقة 5": the number after the apartment word is the
            # apartment, not the floor; the floor is asked for next
            floor, door = None, self.normalize_door(m_explicit.group(1))
        else:
            m_door = re.search(rf"(?<!\S)({_LETTER})(?!\S)\s*$", remainder.strip())
            if m_door and m_floor is not None and m_door.start() > m_floor.start():
                door = self.normalize_door(m_door.group(1))
        return entrance, floor, door

    def ordinal(self, floor: str) -> str:
        """'3' -> '3rd' / '3º' / '3r'. Anything that is not a number is left as it is."""
        if not floor.isdigit():
            return floor
        suffixes = self.language.home.ordinal_suffixes
        if not suffixes:
            return floor
        category = _locale(self.language.locale).ordinal_form(int(floor))
        return floor + suffixes.get(category, suffixes.get("other", ""))

    def home_label(self, entrance: str | None, floor: str, door: str) -> str:
        """'Apt 3A', 'portal 2 3º B': what the console shows and the call keeps."""
        home = self.language.home
        template = home.label_with_entrance if entrance else home.label
        return template.format(
            entrance=entrance or "", floor=floor, door=door, ordinal=self.ordinal(floor)
        )

    def spoken_home(self, entrance: str | None, floor: str, door: str) -> str:
        """How the apartment is read back: 'entrance 1, apartment 3A'."""
        home = self.language.home
        template = home.spoken_with_entrance if entrance else home.spoken
        return template.format(
            entrance=entrance or "", floor=floor, door=door, ordinal=self.ordinal(floor)
        )

    def stt_terms(
        self, addresses: list[str], entrances: list[str], floors: list[str], doors: list[str]
    ) -> list[str]:
        """Keyterms for the STT: without them AssemblyAI drops single letters ("puerta A" ->
        "Puerta.", "apartment 3A" -> "apartment 3"). At most 100 (the provider's limit)."""
        home = self.language.home
        terms = [*self.language.lexicon.stt_terms, *addresses]
        if home.stt_entrance:
            terms += [home.stt_entrance.format(entrance=e) for e in entrances]
        if home.stt_door:
            terms += [home.stt_door.format(door=d) for d in doors]
        if home.stt_home:
            for floor in sorted(floors, key=lambda x: int(x) if x.isdigit() else 99):
                name = home.floor_names.get(floor, floor)
                terms += [home.stt_home.format(floor=floor, floor_name=name, door=d) for d in doors]
        return list(dict.fromkeys(terms))[:100]

    def missing_home_part(self, floor: str | None, door: str | None) -> str:
        missing = self.language.home.missing
        return missing["door"] if floor else missing["floor"] if door else missing["both"]

    def given_home_parts(self, parts: Iterable[tuple[str, str | None]]) -> str:
        names = self.language.home.part_names
        return ", ".join(f"{names.get(k, k)} {v}" for k, v in parts if v)

    def describe_homes(self, entrances: list[str], floors: list[str], doors: list[str]) -> str:
        """'That building has floors 1 to 4 and apartments A or B. ' from the real apartments."""
        d = self.language.home.describe
        parts = []
        if entrances:
            parts.append(d["entrances"].format(list=self.join_and(entrances)))
        parts.append(
            d["floors"].format(first=floors[0], last=floors[-1])
            if len(floors) > 1
            else d["floor"].format(floor=floors[0])
        )
        parts.append(
            d["doors"].format(list=self.join_or(doors))
            if len(doors) > 1
            else d["door"].format(door=doors[0])
        )
        return d["sentence"].format(parts=", ".join(parts))

    # ------------------------------------------------------------------ equipment

    def equipment_in(self, text: str, equipment: list[tuple[str, str]]) -> str | None:
        """The one piece of the building's equipment the sentence names ("the elevator is
        stuck" -> the elevator's id), or None. Used when the model leaves `equipment` out.

        A description word (not the brand: brands are rarely said) or a synonym must appear,
        and not right after a place word ("a puddle in front of the elevator" is not about
        the elevator). Two candidates, or none: None, the model's silence stands."""
        lx = self.language.lexicon
        words = self.normalize(text).split()
        synonyms = {self.normalize(k): self.normalize(v) for k, v in lx.equipment_synonyms.items()}
        words = [synonyms.get(w, w) for w in words]
        places = [self.normalize(p).split() for p in lx.place_words]
        found: set[str] = set()
        for eq_id, description in equipment:
            generic = {self.normalize(g) for g in lx.equipment_generic_words}
            names = {
                w
                for w in self.normalize(description.split("(")[0]).split()
                if len(w) >= 4 and w not in generic
            }
            for i, w in enumerate(words):
                if w not in names:
                    continue
                before = words[max(0, i - 4) : i]
                if any(
                    before[j : j + len(p)] == p for p in places for j in range(len(before))
                ):
                    continue
                found.add(eq_id)
        return found.pop() if len(found) == 1 else None

    # ------------------------------------------------------------------ set phrases

    def only_set_phrase(self, text: str, phrases: tuple[str, ...]) -> bool:
        """The phrase appears whole and the rest are filler words. Replaces a substring search,
        which hijacked real issues ("tarda", "cuando", "repite") (2026-10-21)."""
        words = self.normalize(text).split()
        if not words or any(p.isdigit() for p in words):
            return False
        for phrase in phrases:
            f = phrase.split()
            for i in range(len(words) - len(f) + 1):
                if words[i : i + len(f)] == f:
                    remainder = words[:i] + words[i + len(f) :]
                    if all(p in self.verbal_tics for p in remainder):
                        return True
        return False

    def did_not_hear(self, text: str, delivery: dict[str, Any] | None) -> bool:
        """Asks to repeat, or says hello / asks if anyone is there after a message that was
        not played in full."""
        if self.only_set_phrase(text, self.not_heard):
            return True
        delivery = delivery or {}
        not_delivered = delivery.get("state") in ("interrupted", "not_played") or (
            delivery.get("ratio") is not None and delivery.get("ratio", 1.0) < 0.8
        )
        if not not_delivered or not self.hello:
            return False
        t = self.normalize(text)
        return t in self.hello or t.split(" ", 1)[0] == self.hello[0]

    def is_filler(self, text: str) -> bool:
        """'Oh, okay, hold on': the caller asks for time. Neither data nor a failure: we wait."""
        return self.only_set_phrase(text, self.filler)

    def is_greeting(self, text: str) -> bool:
        """Only a greeting/filler, no data: it must not count as a failed attempt."""
        return self.only_set_phrase(text, self.greetings)

    def _any(self, text: str, phrases: tuple[str, ...], substring: bool) -> bool:
        t = self.normalize(text)
        if substring:
            return any(p in t for p in phrases)
        return any(contains_phrase(t, p) for p in phrases)

    def asks_for_person(self, text: str) -> bool:
        return self._any(text, self.person, self.language.lexicon.person_farewell_substring)

    def asks_for_contact(self, text: str) -> bool:
        return self._any(text, self.contact, self.language.lexicon.person_farewell_substring)

    def says_goodbye(self, text: str) -> bool:
        return self._any(text, self.farewell, self.language.lexicon.person_farewell_substring)

    def is_real_correction(self, fresh: str, previous: str, utterance: str) -> bool:
        """A correction of the problem must add something new and be about the problem, not the
        bot. 'You'll say you've classified it as low urgency' is a comment about the assistant:
        the model returned it as `problem` and the graph took it as a correction (2026-08-28)."""
        e = self.normalize(utterance)
        if any(m in e for m in self.meta_bot):
            return False
        a, b = set(self.normalize(fresh).split()), set(self.normalize(previous).split())
        if not a:
            return False
        return len(a - b) / len(a) >= 0.4  # at least 40% new words

    def answer_question(self, text: str) -> str:
        t = self.normalize(text)
        for entry in self.language.faq:
            keys = [self.normalize(k) for k in entry.keys]
            if entry.stems:
                found = any(k in t for k in keys)
            else:
                found = any(contains_phrase(t, k) for k in keys)
            if found:
                from building_call.i18n import personas

                persona = personas.active(self.language.code)
                if persona and entry.id and entry.id in persona.faq:
                    return persona.faq[entry.id]
                return entry.answer
        return self.language.phrases.faq_default

    def safety_kind(self, text: str) -> str:
        """gas | fire | general: which safety advice an emergency gets. By keyword: it is a
        deterministic path (0 ms) and the safety text is never decided by the model."""
        t = self.normalize(text)
        if any(contains_phrase(t, w) for w in self.gas):
            return "gas"
        if any(contains_phrase(t, w) for w in self.fire):
            return "fire"
        return "general"

    # ------------------------------------------------------------------ wording

    def spoken_category(self, category: str) -> str:
        return self.language.categories.get(category, category.replace("_", " "))

    def detail_question(self, category: str | None) -> str:
        questions = self.language.detail_questions
        return questions.get(category or "", questions["_"])

    def _join(self, items: list[str], word: str, style: Literal["standard", "or"]) -> str:
        if not word:
            # CLDR list patterns: "A, B, or C" (en), "A، B أو C" (ar)...
            return format_list(items, style=style, locale=_babel_locale(self.language.locale))
        if len(items) <= 2:
            return f" {word} ".join(items)
        return ", ".join(items[:-1]) + f" {word} " + items[-1]

    def join_or(self, items: list[str]) -> str:
        """'A, B or C' in the language (no serial comma: it is read out, not printed)."""
        return self._join(items, self.language.formats.list_or, "or")

    def join_and(self, items: list[str]) -> str:
        return self._join(items, self.language.formats.list_and, "standard")


class SpanishParser(Parser):
    """The Spanish rules tuned on real calls (2026-08/10): "3º B", "tercero B", "puerta
    izquierda", the last letter or number of the sentence as the door."""

    def extract_home(self, text: str) -> tuple[str | None, str | None, str | None]:
        """Respaldo determinista para formatos habituales: 'portal 2', '3º B', 'tercero B'."""
        t = self.normalize(text)
        m_entrance = re.search(rf"\b(?:{self._entrance})\s+(\w+)", t)
        entrance = self.normalize_entrance(m_entrance.group(1)) if m_entrance else None
        remainder = re.sub(rf"\b(?:{self._entrance})\s+\w+", " ", t)
        m_floor = re.search(
            rf"\b(\d{{1,2}}|{self._floor_words})\b\s*(?:{self._floor_markers})?", remainder
        )
        floor = self.normalize_floor(m_floor.group(1)) if m_floor else None
        door = None
        sides = _alternation(self.doors)
        m_explicit = re.search(
            rf"\b(?:{self._door_markers})\s+([a-z]|\d{{1,2}}|{sides})\b", remainder
        )
        if m_explicit:
            door = self.normalize_door(m_explicit.group(1))
        else:
            m_door = re.search(rf"\b([a-z]|\d{{1,2}}|{sides})\b\s*$", remainder.strip())
            if m_door and (m_floor is None or m_door.start() > m_floor.start()):
                door = self.normalize_door(m_door.group(1))
        return entrance, floor, door

    def normalize_door(self, value: str | None) -> str | None:
        if value is None:
            return None
        v = self.normalize(str(value))
        for word in self.language.home.door_markers:
            v = v.replace(self.normalize(word), "")
        v = v.strip()
        v = self.doors.get(v, v)
        return v.upper() or None


class EnglishParser(Parser):
    join_apostrophes = True

    def mid_sentence(self, text: str) -> str:
        if text[:2] in ("I ", "I'"):
            return text  # "I can't…"
        return super().mid_sentence(text)


_PARSERS: dict[str, type[Parser]] = {
    "generic": Parser,
    "spanish": SpanishParser,
    "english": EnglishParser,
}


@cache
def _locale(tag: str) -> Locale:
    return Locale.parse(_babel_locale(tag))


def _babel_locale(tag: str) -> str:
    return tag.replace("-", "_")


@cache
def parser_for(code: str) -> Parser:
    from building_call.i18n.registry import get

    language = get(code)
    return _PARSERS[language.parser](language)
