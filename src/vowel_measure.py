"""The accent measurement: where vowels sit, how they move, how long they last, how far they
reduce — and what that is against a reference.

Azure's diagnosis is **categorical**: this phoneme is /θ/ or it is /t/, and here is a score out
of a hundred. Accent is **continuous**. A vowel scoring 78 while drifting toward the target and
one scoring 78 while drifting away are the same number to Azure and opposite findings to a
learner. This module measures the gradient part.

Pure: it reads the normalised word shape `speech_analyzer.normalise` produces plus the audio
those words were measured from, and returns numbers. No Streamlit, no network, no SDK — the
same boundary `rhythm.py` and `progress_view.py` sit on. The signal processing is
`acoustics.py`'s; the English is here.

## This has to run inside the assessment request

Not because the audio disappears — since v0.10.0 recordings are kept — but because the audio is
already in memory at that point and a second pass over stored files would re-do work for
nothing. The persistence exists so a **re-derivation** never needs a re-recording: normalisation
schemes and reference tables will change, and when they do this module is re-run over stored
audio rather than the user being asked to read the passage again.

**Every attempt recorded before v0.10.0 is permanently unmeasurable.** Their audio was deleted
on the way out, by the design that then applied.

## Four instruments, not one

Formant position is the one everybody builds and it is a quarter of an accent. All four fall
out of the same slice-and-measure loop, which is why they are all here and none is deferred:

- **Position** — where the vowel sits, F1/F2 in Lobanov space.
- **Trajectory** — the 20%→80% movement. Whether a diphthong is a diphthong.
- **Rhoticity** — F3, specifically F3−F2. The loudest, cleanest, most correctable marker
  available for a General American target, and it costs one extra column.
- **Duration and reduction** — length, the tense/lax and pre-fortis ratios, and how far the
  unstressed vowels collapse toward the speaker's own schwa.

Stress placement is the composite of the last two plus F0 and intensity, and its four
components are scored and reported **separately**. "Your stress is off" is the vague advice
this project exists to delete; "the second syllable is 40 ms longer and 3 dB louder than the
first, and General American puts it the other way round" is an instruction.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import acoustics
import phoneme_reference
import speech_analyzer
import stress_lexicon
import vowel_reference
from acoustics import FormantPoint, Segment

logger = logging.getLogger(__name__)

# What counts as a vowel. Same predicate as `rhythm.VOCALIC_KINDS` and `stress_lexicon`, from
# the same table, so the three can never disagree about what a vowel is.
VOCALIC_KINDS = stress_lexicon.VOCALIC_KINDS

# --- Where the pipeline refuses ------------------------------------------------------------
# Refusing is the point. A formant estimate from a 20 ms token, or from a token where the
# speaker said a different vowel, is not a weak measurement — it is a confident measurement of
# something else, and it poisons the category mean it lands in.

# Below this a vowel holds too few pitch periods for a stable formant estimate. Note Azure's
# 10 ms timing grid: a 90 ms vowel already carries about ±11% quantisation error on its
# duration, so this floor is coarse by necessity rather than by choice.
MIN_VOWEL_MS = 45.0

# How much of a token's middle the pitch tracker must call voiced. Formant analysis of an
# unvoiced span measures a whisper, or the frication of the consonant next door.
MIN_VOICED_FRACTION = 0.6

# Below this many tokens a vowel category has no usable mean, and below this many categories
# there is no usable speaker centroid — Lobanov is a statement about a whole inventory.
MIN_TOKENS_PER_CATEGORY = 3
MIN_CATEGORIES = 8

# Formant estimation degrades badly with room reverb and a poor microphone. Gated on Azure's
# own `snr_db_min` — the WORST utterance, never the average, because one utterance recorded
# into a fan ruins a reading and an average hides exactly that.
SNR_UNRELIABLE_DB = 15.0
SNR_MARGINAL_DB = 20.0

REJECT_NO_TIMING = "no timing (word was never spoken)"
REJECT_SHORT = f"shorter than {MIN_VOWEL_MS:.0f} ms"
REJECT_UNVOICED = "no reliable F0 through the middle"
REJECT_WRONG_VOWEL = "a different vowel was produced"
REJECT_OUT_OF_RANGE = "segment falls outside the audio"
REJECT_NO_FORMANTS = "no usable formant estimate"

_TICKS_PER_SECOND = speech_analyzer.TICKS_PER_SECOND


@dataclass(frozen=True)
class Token:
    """One vowel, measured. Stored raw — normalisation happens downstream and changes.

    `f3`, `rms_dbfs` and `stress` are carried even where nothing reads them yet. A column
    costs nothing; a re-recording is impossible.
    """

    vowel: str  # Azure IPA
    word: str
    word_index: int
    start_s: float
    end_s: float
    duration_ms: float
    at20: FormantPoint
    at50: FormantPoint
    at80: FormantPoint
    rms_dbfs: float | None
    f0_hz: float | None
    stress: int | None  # from CMUdict; None when the word could not be aligned
    azure_score: float | None
    coda_voiceless: bool | None  # None when the vowel is not followed by a consonant
    accepted: bool
    rejected_reason: str = ""

    @property
    def f2_travel(self) -> float | None:
        """Signed F2 movement from 20% to 80%. A monophthong sits near zero."""
        if self.at20.f2 is None or self.at80.f2 is None:
            return None
        return self.at80.f2 - self.at20.f2

    @property
    def f3_minus_f2(self) -> float | None:
        return self.at50.f3_minus_f2

    @property
    def stressed(self) -> bool | None:
        return None if self.stress is None else self.stress != stress_lexicon.REDUCED


@dataclass(frozen=True)
class Measurement:
    """One recording's tokens, plus everything needed to judge whether to trust them."""

    tokens: tuple[Token, ...]
    ceiling_hz: float
    snr_db_min: float | None
    style: str
    ceiling_choice: acoustics.CeilingChoice | None = None
    alignment_db: float | None = None

    @property
    def accepted(self) -> tuple[Token, ...]:
        return tuple(token for token in self.tokens if token.accepted)

    @property
    def rejected(self) -> tuple[Token, ...]:
        return tuple(token for token in self.tokens if not token.accepted)

    @property
    def reliable(self) -> bool:
        """Whether the recording is clean enough for the formants to mean anything."""
        return self.snr_db_min is not None and self.snr_db_min >= SNR_UNRELIABLE_DB

    @property
    def marginal(self) -> bool:
        return (
            self.snr_db_min is not None
            and SNR_UNRELIABLE_DB <= self.snr_db_min < SNR_MARGINAL_DB
        )

    def quality_note(self) -> str:
        """What to say about this recording's quality, in one sentence, or "" when it is fine."""
        if self.snr_db_min is None:
            return (
                "Azure reported no signal-to-noise ratio for this recording, so there is no "
                "way to say whether the vowel measurements are trustworthy."
            )
        if not self.reliable:
            return (
                f"The worst part of this recording measured {self.snr_db_min:.1f} dB "
                f"signal-to-noise. Below about {SNR_UNRELIABLE_DB:.0f} dB, formant estimates "
                f"are measuring the room rather than the speaker — treat these numbers as "
                f"unreliable rather than as a result."
            )
        if self.marginal:
            return (
                f"The worst part of this recording measured {self.snr_db_min:.1f} dB "
                f"signal-to-noise, which is usable but not clean. A quieter room or a closer "
                f"microphone would tighten every number below."
            )
        return ""


# --- Extraction ------------------------------------------------------------------------------


def _vowel_segments(words: Sequence[Mapping[str, object]]) -> list[tuple[int, str, str, Segment]]:
    """(word index, word, vowel, span) for every timed vocalic phoneme, in time order.

    Offsets are ticks from the start of the **audio stream**, which for the file-backed
    recognition this project runs is the start of the file. `speech_analyzer._timing` flags
    that as the thing a slicing chunk must not assume, so it is not assumed: `alignment_db`
    below checks it against the audio itself on every measurement.
    """
    found: list[tuple[int, str, str, Segment]] = []
    for index, word in enumerate(words):
        text = str(word.get("word") or "")
        for phoneme in word.get("phonemes") or []:  # type: ignore[union-attr]
            if not isinstance(phoneme, dict):
                continue
            symbol = phoneme_reference.normalise(phoneme.get("phoneme"))
            entry = phoneme_reference.lookup(symbol)
            if entry is None or entry.kind not in VOCALIC_KINDS:
                continue
            offset, duration = phoneme.get("offset_ticks"), phoneme.get("duration_ticks")
            if offset is None or duration is None:
                continue
            start = float(offset) / _TICKS_PER_SECOND
            found.append(
                (index, text, symbol, Segment(symbol, start, start + float(duration) / _TICKS_PER_SECOND))
            )
    found.sort(key=lambda item: item[3].start_s)
    return found


def _produced_vowel(phoneme: Mapping[str, object]) -> str | None:
    """The vowel Azure's best alternate says was actually produced, when it differs.

    None when the best alternate agrees with the target, or when the alternate is not a vowel
    at all — that is a consonant confusion and belongs in the phoneme diagnosis, not here.
    """
    expected = phoneme_reference.normalise(phoneme.get("phoneme"))
    alternates = [
        alternate
        for alternate in (phoneme.get("nbest") or [])  # type: ignore[union-attr]
        if isinstance(alternate, dict) and alternate.get("phoneme")
    ]
    if not alternates:
        return None
    best = max(alternates, key=lambda alternate: alternate.get("score") or 0.0)
    produced = phoneme_reference.normalise(best.get("phoneme"))
    if produced == expected:
        return None
    entry = phoneme_reference.lookup(produced)
    if entry is None or entry.kind not in VOCALIC_KINDS:
        return None
    return produced


def _coda_voiceless(word: Mapping[str, object], position: int) -> bool | None:
    """Whether the consonant right after this vowel, inside the same word, is voiceless."""
    phonemes = word.get("phonemes") or []  # type: ignore[union-attr]
    for phoneme in list(phonemes)[position + 1 :]:
        if not isinstance(phoneme, dict):
            continue
        return phoneme_reference.is_voiceless(phoneme.get("phoneme"))
    return None


def alignment_db(analysis: acoustics.Analysis, segments: Sequence[Segment]) -> float | None:
    """How much louder the claimed vowel spans are than the rest of the recording, in dB.

    **This is the check that the phoneme offsets really point where they are believed to.**
    `speech_analyzer._timing` establishes that offsets are ticks from the start of the audio
    stream and warns that a slicing consumer must not simply treat them as file positions. The
    arithmetic supports the absolute reading — the drill fixture's payload spans 1.69 s to
    11.48 s inside a 12.82 s recording, i.e. leading and trailing silence — but arithmetic on
    one fixture is not proof, and a half-second error would shift every formant onto a
    neighbouring consonant while still producing plausible-looking numbers.

    So it is measured, every time: vowels are the loudest thing in speech, and if the slices
    are landing where they should this figure is comfortably positive. A value near zero or
    negative means the offsets are being read wrongly and nothing below should be believed.

    Measured against **the complement of the spans**, not against the whole recording. The
    whole includes the vowels, which on connected speech are most of it, so that comparison
    understates the contrast to the point of being useless — 2.5 dB where the honest figure
    was over 20.
    """
    if not segments:
        return None
    inside = [
        level
        for segment in segments
        if (level := acoustics.rms_dbfs(analysis.sound, segment.start_s, segment.end_s))
        is not None
    ]
    if not inside:
        return None
    outside = acoustics.rms_dbfs_excluding(
        analysis.sound, [(segment.start_s, segment.end_s) for segment in segments]
    )
    if outside is None:
        return None
    return statistics.fmean(inside) - outside


def extract(
    words: Sequence[Mapping[str, object]],
    wav_bytes: bytes,
    *,
    ceiling_hz: float | None = None,
    snr_db_min: float | None = None,
    style: str = "read",
) -> Measurement:
    """Measure every vowel in one recording.

    `ceiling_hz` is the stored baseline's ceiling. Passing None runs the sweep instead, which
    is what a calibration read does — the ceiling must match vocal tract length, and it is
    established once from calibration audio and then held still so later readings stay
    comparable.
    """
    sound = acoustics.load(wav_bytes)
    spans = _vowel_segments(words)
    segments = [segment for _, _, _, segment in spans]

    choice: acoustics.CeilingChoice | None = None
    if ceiling_hz is None:
        choice = acoustics.sweep_ceiling(sound, segments)
        ceiling_hz = choice.ceiling_hz

    analysis = acoustics.analyse(sound, ceiling_hz)
    alignment = alignment_db(analysis, segments)

    # Alignments are computed per word once, not per vowel: a five-vowel word would otherwise
    # be looked up five times, and the answer cannot differ between its own vowels.
    aligned: dict[int, stress_lexicon.Alignment] = {}
    vowel_position: dict[int, int] = {}

    tokens: list[Token] = []
    for word_index, word_text, symbol, segment in spans:
        word = words[word_index]
        if word_index not in aligned:
            aligned[word_index] = stress_lexicon.align_word(word)
        position = vowel_position.get(word_index, 0)
        vowel_position[word_index] = position + 1

        alignment_result = aligned[word_index]
        stress = (
            alignment_result.syllables[position].stress
            if alignment_result.aligned and position < len(alignment_result.syllables)
            else None
        )

        phoneme = _phoneme_at(word, symbol, position)
        score = phoneme.get("score") if phoneme else None
        produced = _produced_vowel(phoneme) if phoneme else None
        coda = _coda_voiceless(word, _phoneme_index(word, symbol, position)) if phoneme else None

        duration_ms = segment.duration_ms
        empty = FormantPoint(None, None, None, None, None, None)
        reason = ""

        if segment.end_s > analysis.duration_s or segment.start_s < 0:
            reason = REJECT_OUT_OF_RANGE
        elif duration_ms < MIN_VOWEL_MS:
            reason = REJECT_SHORT
        elif produced is not None:
            # A valid measurement of the wrong target. It belongs in the phoneme diagnosis,
            # which already reports it, and must never reach the vowel's category mean.
            reason = f"{REJECT_WRONG_VOWEL} (/{produced}/)"
        elif analysis.voiced_fraction(segment.start_s, segment.end_s) < MIN_VOICED_FRACTION:
            reason = REJECT_UNVOICED

        if reason:
            tokens.append(
                Token(
                    vowel=symbol, word=word_text, word_index=word_index,
                    start_s=segment.start_s, end_s=segment.end_s, duration_ms=duration_ms,
                    at20=empty, at50=empty, at80=empty, rms_dbfs=None, f0_hz=None,
                    stress=stress, azure_score=_as_float(score), coda_voiceless=coda,
                    accepted=False, rejected_reason=reason,
                )
            )
            continue

        at20, at50, at80 = analysis.measure(segment)
        _, middle, _ = segment.sample_times()
        accepted = at50.usable
        tokens.append(
            Token(
                vowel=symbol, word=word_text, word_index=word_index,
                start_s=segment.start_s, end_s=segment.end_s, duration_ms=duration_ms,
                at20=at20, at50=at50, at80=at80,
                rms_dbfs=acoustics.rms_dbfs(analysis.sound, segment.start_s, segment.end_s),
                f0_hz=analysis.f0_at(middle),
                stress=stress, azure_score=_as_float(score), coda_voiceless=coda,
                accepted=accepted, rejected_reason="" if accepted else REJECT_NO_FORMANTS,
            )
        )

    return Measurement(
        tokens=tuple(tokens), ceiling_hz=float(ceiling_hz), snr_db_min=snr_db_min,
        style=style, ceiling_choice=choice, alignment_db=alignment,
    )


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _phoneme_index(word: Mapping[str, object], symbol: str, position: int) -> int:
    """Index of the `position`-th vocalic phoneme matching `symbol` inside a word."""
    seen = -1
    for index, phoneme in enumerate(word.get("phonemes") or []):  # type: ignore[union-attr]
        if not isinstance(phoneme, dict):
            continue
        entry = phoneme_reference.lookup(phoneme.get("phoneme"))
        if entry is None or entry.kind not in VOCALIC_KINDS:
            continue
        seen += 1
        if seen == position:
            return index
    return -1


def _phoneme_at(word: Mapping[str, object], symbol: str, position: int) -> Mapping[str, object]:
    index = _phoneme_index(word, symbol, position)
    phonemes = list(word.get("phonemes") or [])  # type: ignore[union-attr]
    return phonemes[index] if 0 <= index < len(phonemes) else {}


# --- Normalisation ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Normaliser:
    """Lobanov z-scoring: per-speaker mean and SD across the vowel inventory.

    **Normalisation is not optional.** Formants scale with vocal tract length and raw hertz
    cannot be compared between speakers at all; setting a male speaker's F1/F2 against a female
    synthetic voice's without normalising produces a chart that is confidently and entirely
    wrong.

    **And the obvious implementation is the wrong one.** The mean and SD here are taken over
    PER-VOWEL-CATEGORY MEANS, never over the raw token pool. Any natural passage over-samples
    some vowels — the benchmark passage yields 50 tokens of one and 5 of another — and a
    token-weighted centroid is dragged toward whichever vowel happened to occur most, tilting
    every z-score in the inventory. The error is invisible on inspection: the chart still looks
    like a vowel chart. `tests/test_vowel_measure.py` asserts it against a deliberately
    unbalanced token set.
    """

    f1_mean: float
    f1_sd: float
    f2_mean: float
    f2_sd: float
    f3_mean: float | None
    f3_sd: float | None
    categories: tuple[str, ...]

    def z(self, point: FormantPoint) -> tuple[float | None, float | None, float | None]:
        """One measurement point in z-units."""
        return (
            _z(point.f1, self.f1_mean, self.f1_sd),
            _z(point.f2, self.f2_mean, self.f2_sd),
            _z(point.f3, self.f3_mean, self.f3_sd),
        )


def _z(value: float | None, mean: float | None, sd: float | None) -> float | None:
    if value is None or mean is None or sd is None or sd <= 0:
        return None
    return (value - mean) / sd


class TooFewTokens(ValueError):
    """There is not enough evidence to normalise. Message is safe to show in the UI."""


def category_means(
    tokens: Iterable[Token], *, minimum: int = MIN_TOKENS_PER_CATEGORY
) -> dict[str, FormantPoint]:
    """Mean F1/F2/F3 at the 50% point for each vowel with enough accepted tokens."""
    grouped: dict[str, list[FormantPoint]] = {}
    for token in tokens:
        if token.accepted and token.at50.usable:
            grouped.setdefault(token.vowel, []).append(token.at50)

    means: dict[str, FormantPoint] = {}
    for vowel, points in grouped.items():
        if len(points) < minimum:
            continue
        means[vowel] = FormantPoint(
            f1=_mean_of(point.f1 for point in points),
            f2=_mean_of(point.f2 for point in points),
            f3=_mean_of(point.f3 for point in points),
            b1=None, b2=None, b3=None,
        )
    return means


def _mean_of(values: Iterable[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return statistics.fmean(usable) if usable else None


def lobanov(
    tokens: Sequence[Token],
    *,
    categories: Collection[str] | None = None,
    min_categories: int = MIN_CATEGORIES,
) -> Normaliser:
    """Build the speaker's normaliser, or refuse.

    `categories` restricts which vowels form the centroid, and passing the reference table's
    own category set is not an optimisation — it is a correctness requirement. A z-score is
    relative to the centroid of whatever inventory produced it, so a speaker normalised over 22
    categories and a reference normalised over 12 are in **different spaces** and their numbers
    are not comparable. `reference_normaliser` below covers 12 vowels, so a comparison against
    it must normalise the speaker over those same 12. Vowels outside the set are still placed
    in that space afterwards; they simply have nothing to be compared against.
    """
    means = category_means(tokens)
    if categories is not None:
        means = {vowel: point for vowel, point in means.items() if vowel in categories}
    if len(means) < min_categories:
        raise TooFewTokens(
            f"Only {len(means)} vowel category(ies) have at least {MIN_TOKENS_PER_CATEGORY} "
            f"usable tokens, and normalising needs {min_categories}. This is a refusal, not a "
            f"zero: a vowel chart built from a handful of categories is a picture of which "
            f"words happened to be said, not of a voice."
        )
    return _normaliser_from(means)


def _normaliser_from(means: Mapping[str, FormantPoint]) -> Normaliser:
    """Mean and SD across category means — the whole Lobanov subtlety, in one place."""
    f1 = [point.f1 for point in means.values() if point.f1 is not None]
    f2 = [point.f2 for point in means.values() if point.f2 is not None]
    f3 = [point.f3 for point in means.values() if point.f3 is not None]
    return Normaliser(
        f1_mean=statistics.fmean(f1),
        f1_sd=statistics.stdev(f1) if len(f1) > 1 else 0.0,
        f2_mean=statistics.fmean(f2),
        f2_sd=statistics.stdev(f2) if len(f2) > 1 else 0.0,
        f3_mean=statistics.fmean(f3) if f3 else None,
        f3_sd=statistics.stdev(f3) if len(f3) > 1 else None,
        categories=tuple(sorted(means)),
    )


def reference_normaliser(reference_set: str) -> Normaliser:
    """The published table's own normaliser, built the same way over its own 12 categories."""
    table = vowel_reference.REFERENCE_SETS.get(reference_set)
    if not table:
        raise TooFewTokens(
            f"{reference_set!r} is not a reference set. Choose 'men' or 'women' — and never "
            f"an average of the two, which describes nobody."
        )
    means = {
        symbol: FormantPoint(
            f1=entry.at50.f1, f2=entry.at50.f2, f3=entry.at50.f3, b1=None, b2=None, b3=None
        )
        for symbol, entry in table.items()
    }
    return _normaliser_from(means)


REFERENCE_CATEGORIES: frozenset[str] = frozenset(vowel_reference.MEN)


# --- Positions ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VowelPosition:
    """Where one vowel category sits, with the evidence behind it.

    `n` is not decoration. A point built from two tokens and one built from twenty must never
    look the same on a chart or in a table, so the count travels with the number everywhere.
    """

    vowel: str
    n: int
    f1_hz: float | None
    f2_hz: float | None
    f3_hz: float | None
    f1_z: float | None
    f2_z: float | None
    f3_z: float | None
    duration_ms: float | None
    f2_travel_hz: float | None
    f3_minus_f2_hz: float | None
    rms_dbfs: float | None

    @property
    def has_reference(self) -> bool:
        return self.vowel in REFERENCE_CATEGORIES


def positions(
    tokens: Sequence[Token], normaliser: Normaliser, *, minimum: int = MIN_TOKENS_PER_CATEGORY
) -> dict[str, VowelPosition]:
    """Per-vowel means in hertz and in z-units, from accepted tokens only."""
    grouped: dict[str, list[Token]] = {}
    for token in tokens:
        if token.accepted and token.at50.usable:
            grouped.setdefault(token.vowel, []).append(token)

    found: dict[str, VowelPosition] = {}
    for vowel, group in sorted(grouped.items()):
        if len(group) < minimum:
            continue
        mean = FormantPoint(
            f1=_mean_of(token.at50.f1 for token in group),
            f2=_mean_of(token.at50.f2 for token in group),
            f3=_mean_of(token.at50.f3 for token in group),
            b1=None, b2=None, b3=None,
        )
        f1_z, f2_z, f3_z = normaliser.z(mean)
        found[vowel] = VowelPosition(
            vowel=vowel,
            n=len(group),
            f1_hz=mean.f1, f2_hz=mean.f2, f3_hz=mean.f3,
            f1_z=f1_z, f2_z=f2_z, f3_z=f3_z,
            duration_ms=_mean_of(token.duration_ms for token in group),
            f2_travel_hz=_mean_of(token.f2_travel for token in group),
            f3_minus_f2_hz=_mean_of(token.f3_minus_f2 for token in group),
            rms_dbfs=_mean_of(token.rms_dbfs for token in group),
        )
    return found


def reference_positions(reference_set: str) -> dict[str, VowelPosition]:
    """The published means in the reference's own Lobanov space, for direct comparison."""
    table = vowel_reference.REFERENCE_SETS.get(reference_set, {})
    normaliser = reference_normaliser(reference_set)
    found: dict[str, VowelPosition] = {}
    for symbol, entry in table.items():
        point = FormantPoint(
            f1=entry.at50.f1, f2=entry.at50.f2, f3=entry.at50.f3, b1=None, b2=None, b3=None
        )
        f1_z, f2_z, f3_z = normaliser.z(point)
        found[symbol] = VowelPosition(
            vowel=symbol, n=entry.n,
            f1_hz=entry.at50.f1, f2_hz=entry.at50.f2, f3_hz=entry.at50.f3,
            f1_z=f1_z, f2_z=f2_z, f3_z=f3_z,
            duration_ms=entry.duration_ms,
            f2_travel_hz=entry.f2_travel,
            f3_minus_f2_hz=entry.at50.f3_minus_f2,
            rms_dbfs=None,
        )
    return found


# --- Reduction, duration ratios, stress ------------------------------------------------------


@dataclass(frozen=True)
class Reduction:
    """How far the speaker's unstressed vowels collapse toward their own schwa.

    Under-reduction — unstressed vowels held too peripheral, too long, too loud — is one of the
    strongest and most trainable accent markers in English, and it is invisible to every
    phoneme-level score Azure returns. The centroid is the SPEAKER'S OWN, computed from their
    unstressed tokens: the question is whether their reduced vowels are reduced relative to
    their own vowel space, not whether they match somebody else's schwa.
    """

    centroid_f1_z: float | None
    centroid_f2_z: float | None
    mean_distance_z: float | None
    n_unstressed: int
    n_stressed: int
    stressed_distance_z: float | None

    @property
    def measured(self) -> bool:
        return self.mean_distance_z is not None


def reduction(tokens: Sequence[Token], normaliser: Normaliser) -> Reduction:
    """The speaker's schwa centroid, and the mean distance of unstressed vowels from it."""
    unstressed: list[tuple[float, float]] = []
    stressed: list[tuple[float, float]] = []
    for token in tokens:
        if not token.accepted or token.stressed is None:
            continue
        f1_z, f2_z, _ = normaliser.z(token.at50)
        if f1_z is None or f2_z is None:
            continue
        (stressed if token.stressed else unstressed).append((f1_z, f2_z))

    if not unstressed:
        return Reduction(None, None, None, 0, len(stressed), None)

    centroid_f1 = statistics.fmean(point[0] for point in unstressed)
    centroid_f2 = statistics.fmean(point[1] for point in unstressed)

    def spread(points: Sequence[tuple[float, float]]) -> float | None:
        if not points:
            return None
        return statistics.fmean(
            math.hypot(f1 - centroid_f1, f2 - centroid_f2) for f1, f2 in points
        )

    return Reduction(
        centroid_f1_z=centroid_f1,
        centroid_f2_z=centroid_f2,
        mean_distance_z=spread(unstressed),
        n_unstressed=len(unstressed),
        n_stressed=len(stressed),
        stressed_distance_z=spread(stressed),
    )


@dataclass(frozen=True)
class DurationRatio:
    """One duration contrast, as a ratio. Never as absolute milliseconds — see below."""

    label: str
    numerator_ms: float | None
    denominator_ms: float | None
    n_numerator: int
    n_denominator: int
    target: float | None
    target_source: str

    @property
    def ratio(self) -> float | None:
        if not self.numerator_ms or not self.denominator_ms:
            return None
        return self.numerator_ms / self.denominator_ms


def tense_lax_ratios(tokens: Sequence[Token], reference_set: str) -> list[DurationRatio]:
    """The tense/lax duration contrasts: /i/:/ɪ/, /u/:/ʊ/, /eɪ/:/ɛ/.

    **Ratios, never absolute milliseconds.** The published durations are citation-form /hVd/
    words read in isolation — /i/ averages 244 ms for men — and connected speech is far
    shorter, so an absolute comparison would report every speaker alive as catastrophically
    clipped. The ratio survives the difference; the absolute number does not.

    In General American the contrast is carried by quality AND length together, so a learner
    who gets the formants right and the length wrong still sounds wrong.
    """
    lengths = _mean_durations(tokens)
    counts = _counts(tokens)
    found: list[DurationRatio] = []
    for tense, lax in vowel_reference.TENSE_LAX_PAIRS:
        found.append(
            DurationRatio(
                label=f"/{tense}/ : /{lax}/",
                numerator_ms=lengths.get(tense),
                denominator_ms=lengths.get(lax),
                n_numerator=counts.get(tense, 0),
                n_denominator=counts.get(lax, 0),
                target=vowel_reference.tense_lax_ratio(tense, lax, reference_set),
                target_source=f"Hillenbrand 1995, {reference_set}",
            )
        )
    return found


def pre_fortis_ratios(tokens: Sequence[Token]) -> list[DurationRatio]:
    """Pre-fortis clipping: the same vowel before a voiced coda against a voiceless one.

    The same vowel is markedly shorter before a voiceless coda than before a voiced one, and
    **that length difference — not the consonant's own voicing — is the main cue that separates
    "bat" from "bad" in American English.** A learner producing no clipping produces minimal
    pairs that do not land, however cleanly they articulate the final consonant.

    **This one has no published target**, and the reason is worth knowing rather than
    discovering: every Hillenbrand stimulus is an /hVd/ word — *had, hod, hawed, head, heard,
    haid, hid, heed, hoed, hood, hud, who'd* — so all twelve end in a voiced /d/ and the table
    contains no pre-fortis data at all. The comparison available instead is the TTS voice
    through this same pipeline, and a ratio near 1.0 is a finding on its own terms: it means no
    clipping is being produced.
    """
    grouped: dict[tuple[str, bool], list[float]] = {}
    for token in tokens:
        if token.accepted and token.coda_voiceless is not None:
            grouped.setdefault((token.vowel, token.coda_voiceless), []).append(token.duration_ms)

    found: list[DurationRatio] = []
    vowels = sorted({vowel for vowel, _ in grouped})
    for vowel in vowels:
        voiced = grouped.get((vowel, False), [])
        voiceless = grouped.get((vowel, True), [])
        if not voiced or not voiceless:
            continue
        found.append(
            DurationRatio(
                label=f"/{vowel}/ before voiced : before voiceless",
                numerator_ms=statistics.fmean(voiced),
                denominator_ms=statistics.fmean(voiceless),
                n_numerator=len(voiced),
                n_denominator=len(voiceless),
                target=None,
                target_source="TTS voice, same pipeline",
            )
        )
    return found


def _mean_durations(tokens: Sequence[Token]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for token in tokens:
        if token.accepted:
            grouped.setdefault(token.vowel, []).append(token.duration_ms)
    return {
        vowel: statistics.fmean(values)
        for vowel, values in grouped.items()
        if len(values) >= MIN_TOKENS_PER_CATEGORY
    }


def _counts(tokens: Sequence[Token]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for token in tokens:
        if token.accepted:
            counts[token.vowel] = counts.get(token.vowel, 0) + 1
    return counts


@dataclass(frozen=True)
class StressContrast:
    """One word's stressed syllable against its reduced ones, on all four cues separately.

    An English stressed syllable is longer, louder, higher or more pitch-moved, and has an
    unreduced vowel. Reporting that as one number is exactly the vague advice this project
    exists to delete, so all four travel separately and every one of them carries its unit.

    Intensity is dBFS and therefore only meaningful WITHIN one recording — which is what this
    is, a comparison between two syllables of the same word. It must never be carried across
    recordings, where a gain change is indistinguishable from a change in delivery.
    """

    word: str
    duration_ms_delta: float | None
    intensity_db_delta: float | None
    f0_semitone_delta: float | None
    reduction_z_delta: float | None

    @property
    def measured(self) -> bool:
        return any(
            value is not None
            for value in (
                self.duration_ms_delta,
                self.intensity_db_delta,
                self.f0_semitone_delta,
                self.reduction_z_delta,
            )
        )


def stress_contrasts(
    tokens: Sequence[Token], normaliser: Normaliser, centroid: Reduction
) -> list[StressContrast]:
    """Per-word stressed-versus-reduced deltas, for every alignable multisyllabic word."""
    by_word: dict[int, list[Token]] = {}
    for token in tokens:
        if token.accepted and token.stress is not None:
            by_word.setdefault(token.word_index, []).append(token)

    found: list[StressContrast] = []
    for group in by_word.values():
        strong = [token for token in group if token.stress == stress_lexicon.PRIMARY]
        weak = [token for token in group if token.stress == stress_lexicon.REDUCED]
        if not strong or not weak:
            continue
        found.append(
            StressContrast(
                word=strong[0].word,
                duration_ms_delta=_delta(
                    _mean_of(t.duration_ms for t in strong), _mean_of(t.duration_ms for t in weak)
                ),
                intensity_db_delta=_delta(
                    _mean_of(t.rms_dbfs for t in strong), _mean_of(t.rms_dbfs for t in weak)
                ),
                f0_semitone_delta=_semitones(
                    _mean_of(t.f0_hz for t in strong), _mean_of(t.f0_hz for t in weak)
                ),
                reduction_z_delta=_delta(
                    _centroid_distance(strong, normaliser, centroid),
                    _centroid_distance(weak, normaliser, centroid),
                ),
            )
        )
    return found


def _delta(first: float | None, second: float | None) -> float | None:
    return None if first is None or second is None else first - second


def _semitones(first: float | None, second: float | None) -> float | None:
    """Pitch difference in semitones — the unit a pitch difference is actually heard in."""
    if not first or not second or first <= 0 or second <= 0:
        return None
    return 12.0 * math.log2(first / second)


def _centroid_distance(
    tokens: Sequence[Token], normaliser: Normaliser, centroid: Reduction
) -> float | None:
    if centroid.centroid_f1_z is None or centroid.centroid_f2_z is None:
        return None
    distances: list[float] = []
    for token in tokens:
        f1_z, f2_z, _ = normaliser.z(token.at50)
        if f1_z is None or f2_z is None:
            continue
        distances.append(
            math.hypot(f1_z - centroid.centroid_f1_z, f2_z - centroid.centroid_f2_z)
        )
    return statistics.fmean(distances) if distances else None


# --- The baseline and the noise floor --------------------------------------------------------


@dataclass(frozen=True)
class NoiseFloor:
    """How far a vowel moves between two readings with no learning in between.

    **This is why the calibration passage is recorded twice.** A vowel centroid shifts between
    sessions from microphone placement, room, posture, time of day and vocal warm-up, with no
    change in the speaker's ability whatsoever. Without knowing how big that movement is, the
    progress view will render noise as progress — against a brief whose entire goal is to see
    that drilling something worked.

    The per-vowel displacement between two reads of the same passage, taken in one sitting at
    least ten minutes apart on the same microphone in the same room, IS that number.

    **Thereafter no movement smaller than this may be reported as change** — including, and
    especially, when it is in the flattering direction.
    """

    per_vowel: Mapping[str, float]
    median_z: float | None
    vowels: int

    def band_for(self, vowel: str) -> float | None:
        """The noise band for one vowel: its own if measured, otherwise the median."""
        return self.per_vowel.get(vowel, self.median_z)

    def within_noise(self, vowel: str, movement_z: float | None) -> bool:
        """Whether a movement is too small to be called change."""
        if movement_z is None:
            return True
        band = self.band_for(vowel)
        return band is not None and abs(movement_z) < band


def noise_floor(
    first: Mapping[str, VowelPosition], second: Mapping[str, VowelPosition]
) -> NoiseFloor:
    """Per-vowel displacement between two calibration reads, in z-units."""
    displacements: dict[str, float] = {}
    for vowel, position in first.items():
        other = second.get(vowel)
        if other is None:
            continue
        if None in (position.f1_z, position.f2_z, other.f1_z, other.f2_z):
            continue
        displacements[vowel] = math.hypot(
            float(other.f1_z) - float(position.f1_z),  # type: ignore[arg-type]
            float(other.f2_z) - float(position.f2_z),  # type: ignore[arg-type]
        )
    median = statistics.median(displacements.values()) if displacements else None
    return NoiseFloor(per_vowel=displacements, median_z=median, vowels=len(displacements))


@dataclass(frozen=True)
class Baseline:
    """One calibrated speaker: where their vowels sit, and how much that wanders by itself."""

    positions: Mapping[str, VowelPosition]
    normaliser: Normaliser
    noise: NoiseFloor
    ceiling_hz: float
    reference_set: str
    style: str
    reduction: Reduction
    tokens: int
    attempt_ids: tuple[int, ...] = ()
    measured_at: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)


# --- The output contract ---------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One row of the four-column accent table.

    **Every accent surface in this project renders findings as this table, with exactly these
    four columns, in this order.** The rules that make it worth having, all enforced by how
    this dataclass is filled:

    - `feature` names the phoneme in **Azure's IPA**, plus the **Wells lexical-set keyword**,
      plus the metric — "/eɪ/ FACE — F2 travel 20→80%". IPA alone is unreadable at a glance,
      the keyword alone is imprecise, and the metric alone is not a sound.
    - `user` and `target` carry **numbers with units** — Hz, z-units, ms, dB, semitones — and
      `user` carries its token count. Never a score, never a percentage, never a verdict.
    - `delta` carries the **signed delta AND the articulatory instruction it implies**. A delta
      with no instruction is a measurement; an instruction with no delta is the vague advice
      this project exists to delete. Both, in every row.
    """

    feature: str
    user: str
    target: str
    delta: str


COLUMNS: tuple[str, str, str, str] = (
    "Acoustic Feature",
    "User Realization",
    "Target Realization",
    "Delta / Adjustment Needed",
)

# Which reference a surface is measured against. The two do NOT coincide, and imitating the
# voice can move a token AWAY from the published mean while sounding better — so every surface
# says which one it used, and they are never averaged together.
REFERENCE_PUBLISHED = "Hillenbrand 1995"
REFERENCE_VOICE = "TTS voice, same pipeline"
REFERENCE_SELF = "your own speech"

WITHIN_NOISE = "Within measurement noise"


def _feature(vowel: str, metric: str) -> str:
    keyword = phoneme_reference.keyword_for(vowel)
    return f"/{vowel}/ {keyword} — {metric}" if keyword else f"/{vowel}/ — {metric}"


def _signed(value: float | None, unit: str, places: int = 0) -> str:
    """A signed number with its unit. Uses a real minus sign, not a hyphen."""
    if value is None:
        return "—"
    text = f"{value:+.{places}f}".replace("-", "−")
    return f"{text} {unit}"


def _plain(value: float | None, unit: str, places: int = 0) -> str:
    return "—" if value is None else f"{value:.{places}f} {unit}"


def _with_count(text: str, count: int) -> str:
    return f"{text} (n={count})"


def _position_findings(
    speaker: Mapping[str, VowelPosition],
    reference: Mapping[str, VowelPosition],
    noise: NoiseFloor | None,
) -> list[Finding]:
    """F1 and F2 position, per vowel, in the shared Lobanov space."""
    found: list[Finding] = []
    for vowel, position in sorted(speaker.items()):
        target = reference.get(vowel)
        if target is None:
            # Ten of the categories the benchmark passage carries have no published mean at
            # all. Reporting the speaker's own position with an honest blank target is the
            # correct answer; inventing a number would not be.
            found.append(
                Finding(
                    feature=_feature(vowel, "F1/F2 (Lobanov z)"),
                    user=_with_count(
                        f"F1 {_signed(position.f1_z, 'z', 2)}, F2 {_signed(position.f2_z, 'z', 2)}",
                        position.n,
                    ),
                    target="no published GA reference",
                    delta=(
                        "Hillenbrand 1995 measured 12 vowels and this is not one of them. "
                        "Position is recorded, not scored."
                    ),
                )
            )
            continue
        for label, user_z, target_z in (
            ("F1 (Lobanov z)", position.f1_z, target.f1_z),
            ("F2 (Lobanov z)", position.f2_z, target.f2_z),
        ):
            delta = _delta(target_z, user_z)
            found.append(
                Finding(
                    feature=_feature(vowel, label),
                    user=_with_count(_signed(user_z, "z", 2), position.n),
                    target=_signed(target_z, "z", 2),
                    delta=_position_instruction(vowel, label, delta, noise),
                )
            )
    return found


def _position_instruction(
    vowel: str, label: str, delta: float | None, noise: NoiseFloor | None
) -> str:
    if delta is None:
        return "Not measurable from this recording."
    if noise is not None and noise.within_noise(vowel, delta):
        band = noise.band_for(vowel)
        return (
            f"{WITHIN_NOISE} — {_signed(delta, 'z', 2)} against a "
            f"{band:.2f} z band. Not reported as change."
        )
    widened = vowel_reference.TOLERANCE_MULTIPLIER.get(vowel)
    caveat = ""
    if widened:
        caveat = (
            f" (band widened ×{widened:g}: the 1995 reference predates the low-back merger "
            f"and GOOSE-fronting, so a deviation here may be a change it did not see)"
        )
    if label.startswith("F1"):
        # Higher F1 means a more open vowel: the jaw is lower and the tongue further from the
        # palate. So a positive delta — target above the speaker — asks for more openness.
        move = "open the jaw further, tongue lower" if delta > 0 else "close the jaw, tongue higher"
    else:
        # Higher F2 means a fronter vowel with spread lips.
        move = (
            "tongue further front, lips spread" if delta > 0 else "tongue further back, lips rounder"
        )
    return f"{_signed(delta, 'z', 2)} → {move}{caveat}"


def _trajectory_findings(
    speaker: Mapping[str, VowelPosition], reference: Mapping[str, VowelPosition]
) -> list[Finding]:
    """F2 travel from 20% to 80% — whether a diphthong is a diphthong."""
    found: list[Finding] = []
    for vowel, position in sorted(speaker.items()):
        entry = phoneme_reference.lookup(vowel)
        if entry is None or entry.kind != "diphthong":
            continue
        target = reference.get(vowel)
        target_travel = target.f2_travel_hz if target else None
        delta = _delta(target_travel, position.f2_travel_hz)
        if target_travel is None:
            instruction = (
                "No published GA reference for this diphthong. Travel is recorded, not scored."
            )
        elif delta is None:
            instruction = "Not measurable from this recording."
        elif abs(position.f2_travel_hz or 0.0) < abs(target_travel) * 0.5:
            instruction = f"{_signed(delta, 'Hz')} → monophthongised; glide, do not hold"
        else:
            instruction = f"{_signed(delta, 'Hz')} → widen the glide"
        found.append(
            Finding(
                feature=_feature(vowel, "F2 travel 20→80%"),
                user=_with_count(_signed(position.f2_travel_hz, "Hz"), position.n),
                target=_signed(target_travel, "Hz") if target_travel is not None else "—",
                delta=instruction,
            )
        )
    return found


def _rhoticity_findings(
    speaker: Mapping[str, VowelPosition], reference: Mapping[str, VowelPosition]
) -> list[Finding]:
    """F3−F2 for every r-coloured vowel. The highest-value single number in the chunk.

    /ɝ/ has a published mean. /ɚ/ and the /Vɹ/ sequences do not — but r-colouring is one
    articulatory gesture, so they are measured against /ɝ/'s target and the row says so
    rather than pretending the table covers them.
    """
    nurse = reference.get("ɝ")
    found: list[Finding] = []
    for vowel, position in sorted(speaker.items()):
        entry = phoneme_reference.lookup(vowel)
        rhotic = vowel in {"ɝ", "ɚ"} or (entry is not None and entry.kind == "r-coloured")
        if not rhotic:
            continue
        own = reference.get(vowel)
        target_value = (own or nurse).f3_minus_f2_hz if (own or nurse) else None
        source = "" if own else " (/ɝ/ target — no published mean for this vowel)"
        delta = _delta(target_value, position.f3_minus_f2_hz)
        if delta is None:
            instruction = "Not measurable from this recording."
        elif delta < -150:
            instruction = f"{_signed(delta, 'Hz')} → r-colouring is strong enough"
        else:
            instruction = (
                f"{_signed(delta, 'Hz')} → F3 is sitting too high above F2: not enough "
                f"r-colouring. Bunch the tongue body up and back, or curl the tip."
            )
        found.append(
            Finding(
                feature=_feature(vowel, "F3−F2"),
                user=_with_count(_plain(position.f3_minus_f2_hz, "Hz"), position.n),
                target=(_plain(target_value, "Hz") + source) if target_value else "—",
                delta=instruction,
            )
        )
    return found


def _duration_findings(ratios: Sequence[DurationRatio], kind: str) -> list[Finding]:
    found: list[Finding] = []
    for ratio in ratios:
        value = ratio.ratio
        delta = _delta(ratio.target, value)
        if value is None:
            instruction = "Not enough tokens on both sides of the contrast."
        elif ratio.target is None:
            instruction = (
                f"Ratio {value:.2f}× — no published target exists for this contrast; "
                f"a ratio near 1.00 means the contrast is not being produced at all."
                if kind == "clipping"
                else f"Ratio {value:.2f}×."
            )
        elif delta is not None and delta > 0.1:
            instruction = (
                f"{_signed(delta, '×', 2)} → hold the first vowel longer, or cut the second "
                f"shorter. The length carries the contrast as much as the quality does."
            )
        else:
            instruction = f"{_signed(delta, '×', 2)} → length contrast is being produced"
        found.append(
            Finding(
                feature=(
                    f"{ratio.label} — tense/lax duration ratio"
                    if kind == "tense"
                    else f"{ratio.label} — pre-fortis clipping"
                ),
                user=_with_count(
                    "—" if value is None else f"{value:.2f}×",
                    min(ratio.n_numerator, ratio.n_denominator),
                ),
                target=(
                    f"{ratio.target:.2f}× ({ratio.target_source})"
                    if ratio.target is not None
                    else ratio.target_source
                ),
                delta=instruction,
            )
        )
    return found


def _reduction_finding(result: Reduction) -> Finding:
    """Unstressed spread against the speaker's own stressed spread.

    The target here is deliberately **internal**. There is no published "distance from schwa"
    figure to compare against, and inventing one would be worse than useless. What is available
    and genuinely diagnostic is the speaker's own stressed vowels: if the unstressed ones sit as
    far from the schwa centroid as the stressed ones do, no reduction is happening at all. That
    comparison needs no external reference and cannot be wrong about a dialect.
    """
    if not result.measured:
        return Finding(
            feature="/ə/ commA — unstressed distance from own schwa centroid",
            user="—",
            target=REFERENCE_SELF,
            delta=(
                "No unstressed vowels could be aligned to CMUdict in this recording, so "
                "reduction was not measured."
            ),
        )
    unstressed = float(result.mean_distance_z or 0.0)
    stressed = result.stressed_distance_z
    if stressed is None:
        instruction = "No stressed vowels to compare against in this recording."
    else:
        ratio = unstressed / stressed if stressed else None
        if ratio is None:
            instruction = "Not measurable from this recording."
        elif ratio > 0.8:
            instruction = (
                f"{_signed(unstressed - stressed, 'z', 2)} → under-reduced. The unstressed "
                f"vowels are sitting almost as far out as the stressed ones; let them collapse "
                f"toward schwa instead of giving each its spelling vowel."
            )
        else:
            instruction = f"{_signed(unstressed - stressed, 'z', 2)} → reduction is happening"
    return Finding(
        feature="/ə/ commA — unstressed distance from own schwa centroid",
        user=_with_count(f"{unstressed:.2f} z", result.n_unstressed),
        target=_with_count(
            f"< {stressed:.2f} z (own stressed vowels)" if stressed is not None else "—",
            result.n_stressed,
        ),
        delta=instruction,
    )


def _stress_findings(contrasts: Sequence[StressContrast], limit: int = 5) -> list[Finding]:
    """The four stress cues, reported separately, for the worst-produced words.

    Ranked by how little duration contrast the word carries, because that is the cue English
    leans on hardest and the one a learner most often flattens. Capped, because a 196-word
    passage would otherwise produce a hundred rows nobody reads.
    """
    ranked = sorted(
        (contrast for contrast in contrasts if contrast.measured),
        key=lambda contrast: (
            contrast.duration_ms_delta if contrast.duration_ms_delta is not None else 1e9
        ),
    )
    found: list[Finding] = []
    for contrast in ranked[:limit]:
        parts = [
            f"length {_signed(contrast.duration_ms_delta, 'ms')}",
            f"loudness {_signed(contrast.intensity_db_delta, 'dB', 1)}",
            f"pitch {_signed(contrast.f0_semitone_delta, 'st', 1)}",
            f"vowel openness {_signed(contrast.reduction_z_delta, 'z', 2)}",
        ]
        weak = [
            name
            for name, value in (
                ("longer", contrast.duration_ms_delta),
                ("louder", contrast.intensity_db_delta),
                ("higher in pitch", contrast.f0_semitone_delta),
                ("less reduced", contrast.reduction_z_delta),
            )
            if value is not None and value <= 0
        ]
        instruction = (
            f"The stressed syllable is not {', not '.join(weak)} than the reduced one. "
            f"English marks stress with all four together."
            if weak
            else "All four cues point the right way."
        )
        found.append(
            Finding(
                feature=f'"{contrast.word}" — stressed vs reduced syllable (4 cues)',
                user="; ".join(parts),
                target="stressed syllable longer, louder, higher, less reduced",
                delta=instruction,
            )
        )
    return found


def _rejection_findings(tokens: Sequence[Token]) -> list[Finding]:
    """One row per (vowel, reason), carrying the count.

    Grouped rather than one row per token: a 90-second read can reject fifty tokens, and fifty
    near-identical rows is a table nobody finishes reading. The grouping keeps what the rule is
    for — **a thin table is visibly thin rather than silently short** — while staying legible.
    """
    grouped: dict[tuple[str, str], int] = {}
    for token in tokens:
        if not token.accepted:
            key = (token.vowel, token.rejected_reason)
            grouped[key] = grouped.get(key, 0) + 1
    return [
        Finding(
            feature=_feature(vowel, "rejected token(s)"),
            user=_with_count("not measured", count),
            target="—",
            delta=f"Rejected: {reason}. Refused rather than guessed.",
        )
        for (vowel, reason), count in sorted(grouped.items(), key=lambda item: -item[1])
    ]


def findings(
    measurement: Measurement,
    normaliser: Normaliser,
    *,
    reference_set: str,
    noise: NoiseFloor | None = None,
) -> list[Finding]:
    """Every four-column row for one measurement, in reading order.

    Position and trajectory first, because they are what the chart shows; then rhoticity, which
    is the single most correctable marker; then the duration and reduction measures; then
    stress; then the rejections, last, so the table ends by admitting what it could not do.
    """
    accepted = measurement.accepted
    speaker = positions(accepted, normaliser)
    reference = reference_positions(reference_set)
    centroid = reduction(accepted, normaliser)

    rows: list[Finding] = []
    rows += _position_findings(speaker, reference, noise)
    rows += _trajectory_findings(speaker, reference)
    rows += _rhoticity_findings(speaker, reference)
    rows += _duration_findings(tense_lax_ratios(accepted, reference_set), "tense")
    rows += _duration_findings(pre_fortis_ratios(accepted), "clipping")
    rows.append(_reduction_finding(centroid))
    rows += _stress_findings(stress_contrasts(accepted, normaliser, centroid))
    rows += _rejection_findings(measurement.tokens)
    return rows
