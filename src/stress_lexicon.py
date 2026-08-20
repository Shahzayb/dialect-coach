"""Lexical stress from the CMU Pronouncing Dictionary, aligned onto Azure's phonemes.

**Azure returns syllable spans and accuracy scores but no stress marks.** There is no field
that says which syllable of "computer" is stressed — verified against every committed fixture,
where `unpredictable` comes back as `ʌn/pɹə/dɪk/tə/bəl` with four scores and nothing else. So
reduction and stress placement are not merely hard to measure without a dictionary; they are
unmeasurable. That makes this a dependency, not a detail.

## Why CMUdict and not a grapheme-to-phoneme model

`cmudict` is pure ARPABET data with 0/1/2 stress digits, no nltk, no model download and no
compiler. `g2p-en` drags four packages and a model. The third option — hand-annotating stress
for the fixed calibration passage and drill inventory only — was free and permanent but would
have made every reduction measure **scripted-only**, returning nothing for Mode C's unscripted
speech. It is not needed, because the dictionary covers what this project reads:

    unique words of the benchmark passage found        128 / 128
    words alignable across the committed Azure fixtures 100 / 101

Measured, not assumed. The one failure is honest and is refused rather than guessed: *our*
comes back from Azure as a single r-coloured `ɑɹ` and from CMUdict as `AW1 ER0`, two vowels
against one, so that word contributes no stress information.

## The alignment, and why counting works

CMUdict marks stress on vowels and only on vowels, so the stress digit **is** the vowel test.
`R` is a consonant in ARPABET and carries no digit. That is what makes the counts line up
across two notations that segment r-colouring completely differently: Azure writes START as
one phoneme `ɑɹ`, CMUdict writes it `AA1 R` — one vowel each side. Both count one.

So: take the word's vowel phones in order, take Azure's vocalic phonemes in order, and if the
counts agree, pair them by index. If they disagree, refuse. A word that cannot be aligned is
reported as unaligned and its tokens carry no stress, rather than being paired off by
approximate position and quietly mislabelling a stressed vowel as reduced.

**This is a citation-form dictionary, not a transcription of what was said.** It says what the
stress pattern of the word is, which is exactly the question — "your second syllable should be
the strong one" — and says nothing about what the speaker actually did, which is what the
acoustics measure. Keeping those two apart is the whole design.
"""

from __future__ import annotations

import functools
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import phoneme_reference

logger = logging.getLogger(__name__)

# CMUdict's primary/secondary/reduced digits.
PRIMARY, SECONDARY, REDUCED = 1, 2, 0

# What counts as vocalic on Azure's side. The same predicate `rhythm.py` uses, and for the
# same reason: `phoneme_reference` already classifies every symbol Azure emits, and restating
# an inventory here would give it two places to drift from.
VOCALIC_KINDS = frozenset({"vowel", "diphthong", "r-coloured"})

_WORD = re.compile(r"[a-z']+")


@dataclass(frozen=True)
class Syllable:
    """One vowel of a word as the dictionary has it: what it is, and how strong it is.

    `stressed` folds primary and secondary together. English reduction is a two-way contrast
    for this purpose — a secondary-stressed vowel keeps its full quality, and it is the
    reduced ones that collapse toward schwa. The raw digit is kept so a later chunk can
    separate them without a re-measurement.
    """

    vowel: str  # the IPA Azure would use for the dictionary's phone
    stress: int  # 0 reduced, 1 primary, 2 secondary

    @property
    def stressed(self) -> bool:
        return self.stress != REDUCED

    @property
    def primary(self) -> bool:
        return self.stress == PRIMARY


@dataclass(frozen=True)
class Alignment:
    """The result of pairing one word's dictionary vowels against Azure's.

    `syllables` is empty when alignment failed, and `reason` says why. Both are reported: a
    word absent from the dictionary and a word whose vowel counts disagree are different
    findings, and the second is the interesting one.
    """

    word: str
    syllables: tuple[Syllable, ...]
    reason: str = ""

    @property
    def aligned(self) -> bool:
        return bool(self.syllables)


@functools.lru_cache(maxsize=1)
def _dictionary() -> Mapping[str, list[list[str]]]:
    """CMUdict, loaded once. ~126,000 entries, each with one or more pronunciations."""
    import cmudict

    return cmudict.dict()


def normalise_word(word: str | None) -> str:
    """The dictionary's key for a word: lowercase, letters and apostrophes only.

    Deliberately not `utils.normalise_words`, which strips apostrophes — CMUdict keys
    contractions with them (`don't`, `it's`), and stripping would turn a hit into a miss.
    """
    if not word:
        return ""
    match = _WORD.search(word.lower())
    return match.group(0) if match else ""


def variants(word: str | None) -> list[list[str]]:
    """Every pronunciation the dictionary lists for a word, as ARPABET phone lists."""
    key = normalise_word(word)
    return list(_dictionary().get(key, [])) if key else []


def _syllables(phones: Sequence[str]) -> tuple[Syllable, ...]:
    """One pronunciation's vowels, in order, as Azure-flavoured IPA plus stress."""
    found: list[Syllable] = []
    for phone in phones:
        stress = phoneme_reference.arpabet_stress(phone)
        if stress is None:
            continue
        found.append(Syllable(vowel=phoneme_reference.from_arpabet(phone), stress=stress))
    return tuple(found)


def azure_vowels(word: Mapping[str, object]) -> list[str]:
    """The vocalic phonemes of one normalised Azure word, in order, as IPA symbols."""
    symbols: list[str] = []
    for phoneme in word.get("phonemes") or []:  # type: ignore[union-attr]
        symbol = phoneme.get("phoneme") if isinstance(phoneme, dict) else None
        entry = phoneme_reference.lookup(symbol)
        if entry is not None and entry.kind in VOCALIC_KINDS:
            symbols.append(phoneme_reference.normalise(symbol))
    return symbols


def align(word_text: str | None, spoken_vowels: Sequence[str]) -> Alignment:
    """Pair a word's dictionary stress pattern against the vowels Azure actually reported.

    When the dictionary lists several pronunciations, the one chosen is the variant with the
    right number of vowels that **agrees with Azure's symbols most often**. That matters more
    than it sounds: *the* is `DH AH0` or `DH IY0`, *a* is `AH0` or `EY1`, and taking the first
    listed variant would label a full `/eɪ/` as reduced roughly whenever the speaker was
    emphasising. Agreement is only a tie-break — the stress digits come from the dictionary
    either way, never from the acoustics, or the measurement would be circular.
    """
    word = normalise_word(word_text)
    if not word:
        return Alignment(word="", syllables=(), reason="no word to look up")

    listed = variants(word)
    if not listed:
        return Alignment(word=word, syllables=(), reason="not in CMUdict")

    wanted = len(spoken_vowels)
    candidates = [_syllables(phones) for phones in listed]
    matching = [entry for entry in candidates if len(entry) == wanted]
    if not matching:
        counts = sorted({len(entry) for entry in candidates})
        return Alignment(
            word=word,
            syllables=(),
            reason=(
                f"Azure reported {wanted} vowel(s), CMUdict has "
                f"{' or '.join(str(count) for count in counts)}"
            ),
        )

    def agreement(entry: tuple[Syllable, ...]) -> int:
        return sum(
            1 for syllable, spoken in zip(entry, spoken_vowels) if syllable.vowel == spoken
        )

    return Alignment(word=word, syllables=max(matching, key=agreement))


def align_word(word: Mapping[str, object]) -> Alignment:
    """`align`, reading both sides out of one normalised Azure word."""
    return align(str(word.get("word") or ""), azure_vowels(word))
