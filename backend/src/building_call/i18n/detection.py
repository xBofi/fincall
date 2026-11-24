"""Which language is the caller speaking? Deterministic, no model, ~0 ms.

Two signals, combined by `decide`:

1. What the STT reports. AssemblyAI streaming with `language_detection=true` adds
   `language_code` and `language_confidence` to each final turn.
2. The text itself (`detect`): the script first (Devanagari is Hindi; Arabic script is Arabic or
   Urdu, told apart by the letters only Urdu uses and by function words), then, for Latin
   script, how many of each language's short function words appear (`lexicon.detection`).

A detection only switches the call when it is confident enough (`BC_LANGUAGE_DETECTION_
THRESHOLD`) and only in the first turns of a call whose language was not chosen explicitly
(`CallState.language_locked`). Asking for a language by name ("English, please", "¿Habla
español?") switches at any time: see `requested_language`.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field

from building_call.i18n import registry
from building_call.i18n.text import basic_normalize

#: Letters Urdu writes and Arabic does not (Persian-derived and Urdu-specific forms)
_URDU_LETTERS = frozenset("ٹڈڑںےۓھیکگپچژ")
#: Arabic forms Urdu replaces with its own (kaf, yeh, teh marbuta, alef maksura)
_ARABIC_LETTERS = frozenset("كيةى")
_URDU_WORDS = frozenset("ہے ہیں میں کے کی کا اور نہیں ہاں یہ وہ کر رہا رہی ہوں آپ".split())
_ARABIC_WORDS = frozenset("في من على هذا هذه أن التي الذي لا نعم هل ما عن مع كان".split())


#: Share of the words that must be function words of the winner (Latin script)
_MIN_COVERAGE = 0.2


@dataclass(frozen=True)
class Detection:
    """`language` is None when nothing can be said (too short, numbers only, a tie)."""

    language: str | None
    confidence: float
    source: str  # script | words | stt | none
    scores: dict[str, float] = field(default_factory=dict)


NONE = Detection(None, 0.0, "none")


def _script_counts(text: str) -> dict[str, int]:
    counts = {"latin": 0, "arabic": 0, "devanagari": 0}
    for c in text:
        if not c.isalpha():
            continue
        name = unicodedata.name(c, "")
        if name.startswith("LATIN"):
            counts["latin"] += 1
        elif name.startswith("ARABIC"):
            counts["arabic"] += 1
        elif name.startswith("DEVANAGARI"):
            counts["devanagari"] += 1
    return counts


def _arabic_or_urdu(text: str, candidates: set[str]) -> Detection:
    words = set(basic_normalize(text).split())
    urdu = sum(1 for c in text if c in _URDU_LETTERS) + 2 * len(words & _URDU_WORDS)
    arabic = sum(1 for c in text if c in _ARABIC_LETTERS) + 2 * len(words & _ARABIC_WORDS)
    scores = {"ur": float(urdu), "ar": float(arabic)}
    options = {k: v for k, v in scores.items() if k in candidates}
    if not options:
        return NONE
    if len(options) == 1:
        (only,) = options
        return Detection(only, 0.9, "script", scores)
    total = urdu + arabic
    if total == 0:
        return Detection(None, 0.0, "script", scores)
    best = max(options, key=lambda k: options[k])
    return Detection(best, options[best] / total, "script", scores)


def _latin(text: str, candidates: set[str]) -> Detection:
    tokens = basic_normalize(text).split()
    words = [t for t in tokens if not t.isdigit()]
    if len(words) < 2:
        return NONE
    lowered = text.lower()
    vocabularies = {
        code: {basic_normalize(w) for w in registry.get(code).lexicon.detection}
        for code in candidates
    }
    vocabularies = {code: v for code, v in vocabularies.items() if v}
    scores: dict[str, float] = dict.fromkeys(vocabularies, 0.0)
    distinct: dict[str, set[str]] = {code: set() for code in vocabularies}
    for w in words:
        owners = [code for code, vocabulary in vocabularies.items() if w in vocabulary]
        for code in owners:
            # A word that several languages share ("el", "no") is weaker evidence
            scores[code] += 1 / len(owners)
            distinct[code].add(w)
    scores = {code: score / len(words) for code, score in scores.items()}
    # Spelling that only one of them uses
    if "es" in scores and any(c in lowered for c in "ñ¿¡"):
        scores["es"] += 0.3
    if "ca" in scores and ("l·l" in lowered or any(c in lowered for c in "çàèò")):
        scores["ca"] += 0.3
    if not scores:
        return NONE
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    # Too few function words of any candidate (it may be a language we don't detect here),
    # or one word said twice ("No, no, 5 Elm Avenue"): no evidence
    if top < _MIN_COVERAGE or len(distinct[best]) < 2:
        return Detection(None, 0.0, "words", scores)
    # Share of the evidence that points to the winner, damped for very short sentences
    confidence = (top - second) / top * min(1.0, len(words) / 4)
    return Detection(best, round(confidence, 3), "words", scores)


def detect(text: str, candidates: tuple[str, ...] | None = None) -> Detection:
    """The language of `text` among `candidates` (default: every registered language)."""
    pool = set(candidates or registry.supported())
    counts = _script_counts(text)
    letters = sum(counts.values())
    if letters == 0:
        return NONE
    dominant = max(counts, key=lambda k: counts[k])
    share = counts[dominant] / letters
    if dominant == "devanagari":
        return Detection("hi", share, "script") if "hi" in pool else NONE
    if dominant == "arabic":
        return _arabic_or_urdu(text, pool)
    latin_pool = {c for c in pool if c not in {"ar", "ur", "hi"}}
    return _latin(text, latin_pool)


def decide(
    text: str,
    stt_language: str | None = None,
    stt_confidence: float | None = None,
    candidates: tuple[str, ...] | None = None,
) -> Detection:
    """The STT's report when it is confident and supported, otherwise the text's evidence."""
    pool = candidates or registry.supported()
    code = registry.normalize_code(stt_language)
    if code and code in pool and stt_confidence is not None:
        text_guess = detect(text, pool)
        # Both agree: the stronger of the two; they disagree: trust the STT only if it is sure
        if text_guess.language in (None, code):
            return Detection(code, max(stt_confidence, text_guess.confidence), "stt")
        if stt_confidence >= 0.9:
            return Detection(code, stt_confidence, "stt")
        return text_guess
    return detect(text, pool)


def requested_language(
    text: str, current: str, candidates: tuple[str, ...] | None = None
) -> str | None:
    """'English, please' / '¿Puede hablar en español?' / 'عربي': the caller asks for another
    language by name. Only short sentences without numbers (an address can contain a name:
    'Calle Inglés 3')."""
    tokens = basic_normalize(text).split()
    if not tokens or len(tokens) > 7 or any(t.isdigit() for t in tokens):
        return None
    current_no = {basic_normalize(w) for w in registry.get(current).lexicon.no}
    if current_no & set(tokens):
        return None  # "No hablo inglés" asks for nothing
    joined = " " + " ".join(tokens) + " "
    for code in candidates or registry.supported():
        if code == current:
            continue
        for name in registry.get(code).lexicon.names:
            if f" {basic_normalize(name)} " in joined:
                return code
    return None
