"""What a language is, as configuration: one TOML file per language in `locales/`.

Everything the caller hears or that depends on the language lives in the file: providers and
voices, word lists for the deterministic paths, the templates' translations, admin-facing
phrases and formats. Code only holds the rules that no word list can express (see `parsing`).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    # A typo in a locale file must fail loudly at startup, not silently fall back
    model_config = ConfigDict(extra="forbid", frozen=True)


class SttSpec(_Strict):
    """Speech-to-text for this language. `provider` is the first choice; `fallback`, the next."""

    provider: Literal["assemblyai", "browser"] = "assemblyai"
    #: Code passed to the provider (AssemblyAI `language_codes`: ISO 639-1)
    language_code: str
    fallback: Literal["assemblyai", "browser", "none"] = "browser"


class TtsSpec(_Strict):
    """Text-to-speech for this language, per provider. Empty = the provider can't speak it."""

    #: ElevenLabs `language_code` (ISO 639-1). The v3/v4 models cover all six first languages
    elevenlabs_language: str = ""
    #: ElevenLabs voice ID for this language; empty = `BC_ELEVENLABS_VOICE` (or the persona's)
    elevenlabs_voice: str = ""
    #: Piper voice file stem (`<BC_PIPER_DIR>/<stem>.onnx`), local development only
    piper_voice: str = ""
    #: Cloudflare MeloTTS `lang` (en, es, fr, zh, ja, ko); empty = not supported
    melotts_lang: str = ""
    #: TwiML <Say> fallback (only for the "service unavailable" message)
    twilio_say_language: str = ""
    twilio_say_voice: str = ""
    #: BCP-47 tag for the browser's speechSynthesis / SpeechRecognition in the console
    browser_lang: str


class Formats(_Strict):
    #: ISO 4217 currency used when the assistant reads an amount
    currency: str = "EUR"
    #: Number to call in an emergency, read in the safety advice
    emergency_number: str = "112"
    #: Words to join lists ("A, B or C"); CLDR via Babel when empty
    list_or: str = ""
    list_and: str = ""


class FaqEntry(_Strict):
    #: Stable id so a persona can answer it its own way ("who" = "who are you?")
    id: str = ""
    keys: list[str]
    answer: str
    #: True = the keys are word stems ("grab" matches "grabando"); False = whole words
    stems: bool = False


class HomeWords(_Strict):
    """How apartments are said and read back."""

    entrance_markers: list[str] = Field(default_factory=list)  # "portal", "entrance"
    floor_markers: list[str] = Field(default_factory=list)  # "piso", "floor"
    door_markers: list[str] = Field(default_factory=list)  # "puerta", "apartment"
    #: Spoken floor words -> value: "tercero" -> "3", "ground" -> "0", "atico" -> "atico"
    floors: dict[str, str] = Field(default_factory=dict)
    #: Spoken door words -> code: "izquierda" -> "izq"
    doors: dict[str, str] = Field(default_factory=dict)
    #: Floor words that are not looked for inside a sentence ("principal", "basement")
    floor_words_not_in_text: list[str] = Field(default_factory=list)
    #: Words dropped around a floor or a door besides the markers ("the", "number")
    floor_noise: list[str] = Field(default_factory=list)
    door_noise: list[str] = Field(default_factory=list)
    #: One-letter words that are not an apartment letter mid-sentence ("on 3 a leak": "a")
    one_letter_words: list[str] = Field(default_factory=list)
    #: The label shown in the console and kept in the call: "{entrance}" may be empty
    label: str = "{floor}{door}"
    label_with_entrance: str = "{entrance}, {floor}{door}"
    #: How the label is read out loud
    spoken: str = "{floor}{door}"
    spoken_with_entrance: str = "{entrance}, {floor}{door}"
    #: What is still missing: keys door, floor, both
    missing: dict[str, str] = Field(default_factory=dict)
    #: Names of the parts already said: keys entrance, floor, door
    part_names: dict[str, str] = Field(default_factory=dict)
    #: "There are entrances 1 and 2, floors 1 to 4 and doors A or B": see `describe_homes`
    describe: dict[str, str] = Field(default_factory=dict)
    #: STT keyterms built from the real apartments ("portal 2", "tercero B", "apartment 3A");
    #: `{floor_name}` is `floor_names[floor]` (the spoken word) or the number
    stt_entrance: str = ""
    stt_door: str = ""
    stt_home: str = ""
    floor_names: dict[str, str] = Field(default_factory=dict)
    #: Ordinal suffix per CLDR plural category ("one" -> "st"); "º" for every one in Spanish
    ordinal_suffixes: dict[str, str] = Field(default_factory=dict)


class Lexicon(_Strict):
    """Word lists of the deterministic paths. All of them are compared normalized."""

    yes: list[str]
    no: list[str]
    #: First words that make a short answer a "no" ("no, it's 3A")
    no_start: list[str] = Field(default_factory=list)
    #: What the STT makes of a bare yes / no ("S." / "N." in Spanish)
    short_yes: list[str] = Field(default_factory=list)
    short_no: list[str] = Field(default_factory=list)
    not_heard: list[str] = Field(default_factory=list)
    hello: list[str] = Field(default_factory=list)
    filler: list[str] = Field(default_factory=list)
    greetings: list[str] = Field(default_factory=list)
    verbal_tics: list[str] = Field(default_factory=list)
    person: list[str] = Field(default_factory=list)
    farewell: list[str] = Field(default_factory=list)
    #: Substring match, as in the original Spanish rules; False = whole words
    person_farewell_substring: bool = False
    meta_bot: list[str] = Field(default_factory=list)
    address_stopwords: list[str] = Field(default_factory=list)
    address_filler: list[str] = Field(default_factory=list)
    numbers: dict[str, int] = Field(default_factory=dict)
    #: "treinta y dos": the word between tens and units; empty = "thirty two"
    number_joiners: list[str] = Field(default_factory=list)
    number_joiner_required: bool = False
    gas: list[str] = Field(default_factory=list)
    fire: list[str] = Field(default_factory=list)
    #: Words that refuse wherever they are ("wrong", "incorrecto")
    refusal_words: list[str] = Field(default_factory=list)
    #: The problem often starts with the word for "no" ("No funciona…"): a first "no" only
    #: refuses with a pause after it, alone, or followed by one of `refusal_followers`
    no_start_needs_pause: bool = False
    refusal_followers: list[str] = Field(default_factory=list)
    #: Turn fillers for the voice agent: a turn made only of these waits for more ("Ah,
    #: vale, perdón."). Multi-word ones go together ("a ver": a lone "A." is a door letter)
    turn_fillers: list[str] = Field(default_factory=list)
    #: Other words for a piece of equipment: "buzzer" -> "intercom", "lift" -> "elevator"
    equipment_synonyms: dict[str, str] = Field(default_factory=dict)
    #: "In front of the elevator": a place next to the equipment, not the equipment
    place_words: list[str] = Field(default_factory=list)
    #: Description words too generic to name equipment ("door" in "garage door")
    equipment_generic_words: list[str] = Field(default_factory=list)
    #: Alone they count as a "yes", so they are not fillers
    loose_answers: list[str] = Field(default_factory=list)
    #: Words of the base STT vocabulary (keyterms)
    stt_terms: list[str] = Field(default_factory=list)
    #: Words that switch the call to this language when said alone ("English, please")
    names: list[str] = Field(default_factory=list)
    #: Short function words used to detect the language of a sentence written in Latin script
    detection: list[str] = Field(default_factory=list)


class Phrases(_Strict):
    """Small pieces of text the graph builds with, outside the editable templates."""

    default_question: str
    your_building: str
    detail_prefix: str
    #: Shown to the administrator, never said to the caller
    objection: str
    equipment: str
    maintainer: str
    contract: str
    warranty: str
    proposed: str
    emergency_note: str
    equipment_detail: str
    faq_default: str
    #: Bridge (phone) messages when a turn fails
    turn_failed: str
    technical_failure: str
    service_unavailable: str
    #: The read-back of the request starts lowercase after "Let me check: ..." (English) or not
    lowercase_summary: bool = True


class Language(_Strict):
    code: str
    #: BCP-47 locale (formats, Babel, Twilio, the browser): "en-US", "es-ES"
    locale: str
    #: English name, used inside the LLM prompts ("The caller speaks Catalan")
    name: str
    native_name: str
    rtl: bool = False
    #: Rules that a word list can't express: "spanish", "english" or "generic"
    parser: str = "generic"
    #: Prompt set for the language model: "es" (tuned in Spanish) or "en" (the rest)
    prompts: str = "en"
    #: Template translations: id -> text. Spanish is the source language (in code)
    templates: dict[str, str] = Field(default_factory=dict)
    stt: SttSpec
    tts: TtsSpec
    formats: Formats = Formats()
    lexicon: Lexicon
    home: HomeWords = HomeWords()
    phrases: Phrases
    faq: list[FaqEntry] = Field(default_factory=list)
    #: Category code -> how it is said ("plumbing" -> "fontanería")
    categories: dict[str, str] = Field(default_factory=dict)
    #: Category code -> detail question; "_" generic, "unclassifiable" when nothing is known
    detail_questions: dict[str, str] = Field(default_factory=dict)
    #: Work type -> why it reaches the administrator
    work_type_reasons: dict[str, str] = Field(default_factory=dict)
