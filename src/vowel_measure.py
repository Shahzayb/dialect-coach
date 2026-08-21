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
import re
import statistics
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import acoustics
import model_reference
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

# --- And a HIGHER floor for the trajectory, which is a different measurement -----------------
#
# **Found by running the model-reference capture, not by reasoning about it.** The measured
# /eɪ/ FACE came out gliding -225 Hz, backwards, where General American glides about +140. The
# per-token dump said why: on "same" the 80% sample read F2 1285 Hz and F1 240 Hz — those are
# the following /m/, not the vowel.
#
# The arithmetic is forced. `acoustics.WINDOW_LENGTH_S` is 25 ms, so a sample at 80% of the
# duration is analysed over a window reaching 12.5 ms past it. To keep that window inside the
# vowel:
#
#     0.8 * d + 12.5 <= d   ->   d >= 62.5 ms
#
# and the 20% point needs the same by symmetry. Azure's timing sits on a 10 ms grid, so a
# boundary can be out by that much at each end; 90 ms is 62.5 plus that margin, rounded.
#
# **A short token is still fine for POSITION.** The 50% window fits inside anything past 25 ms,
# so a brief vowel still says where the tongue was — it just cannot say where the tongue WENT.
# Only the trajectory is refused, which is why this is a property of the token rather than a
# rejection: throwing the token away would cost the position measurement to protect the
# trajectory one, and connected speech is full of 60 ms vowels.
MIN_TRAJECTORY_MS = 90.0

# How much of a token's middle the pitch tracker must call voiced. Formant analysis of an
# unvoiced span measures a whisper, or the frication of the consonant next door.
MIN_VOICED_FRACTION = 0.6

# Below this many tokens a vowel category has no usable mean, and below this many categories
# there is no usable speaker centroid — Lobanov is a statement about a whole inventory.
MIN_TOKENS_PER_CATEGORY = 3

# The speech-style tags. **They live here, not in `app.py`, because they are a property of a
# measurement rather than of a page**: every token row, every baseline and every gate decision
# is scoped by one of them, and this module is the Streamlit-free one all three go through.
# Read speech and spontaneous speech are never pooled — not into one baseline, one centroid or
# one trend line.
STYLE_READ = "read"
STYLE_SPONTANEOUS = "spontaneous"
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

# --- Which reference a surface is measured against -------------------------------------------
# Declared up here rather than beside the table renderer because they are DEFAULT ARGUMENT
# VALUES for `reference_positions` and friends, and a default is evaluated at def time.
#
# The three do not coincide and are never averaged together. `REFERENCE_PUBLISHED` is
# Hillenbrand et al. (1995) — real humans, peer-reviewed, twelve vowels of citation-form /hVd/
# speech. `REFERENCE_VOICE` is `model_reference.py` — the whole 22-vowel inventory in connected
# speech, measured through this project's own pipeline from sixteen current en-US neural
# voices. `REFERENCE_SELF` is the speaker, used where no external target exists at all (how far
# their unstressed vowels sit from their OWN schwa centroid).
#
# Imitating a synthesised voice can move a token AWAY from the published mean while sounding
# better to a listener, which is exactly why every surface names the one it used.
REFERENCE_PUBLISHED = "Hillenbrand 1995"
REFERENCE_VOICE = "TTS voice, same pipeline"
REFERENCE_SELF = "your own speech"


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
    # Which speech population this token came from, when it was rebuilt from a stored row.
    #
    # Empty for a token `extract` just produced: within one recording the style is uniform and
    # lives on the `Measurement` that wraps it, so duplicating it per token there would be a
    # second copy to disagree. It matters on the way BACK, where tokens from two different
    # attempts are combined into one baseline and there is no longer a single Measurement to
    # ask — which is exactly where mixing read and spontaneous speech has to be refused.
    style: str = ""

    @property
    def trajectory_usable(self) -> bool:
        """Whether this token is long enough for its 20% and 80% windows to fit inside it.

        See `MIN_TRAJECTORY_MS`. Below it the edge samples analyse the neighbouring consonants
        and the "glide" they report is co-articulation, measured confidently and pointing
        wherever the neighbours happen to sit.
        """
        return (
            self.duration_ms >= MIN_TRAJECTORY_MS
            and self.at20.f2 is not None
            and self.at80.f2 is not None
        )

    @property
    def f2_travel(self) -> float | None:
        """Signed F2 movement from 20% to 80%. A monophthong sits near zero.

        None for a token too short to measure a glide in — refusing, rather than returning a
        number that describes the consonants on either side.
        """
        if not self.trajectory_usable:
            return None
        return self.at80.f2 - self.at20.f2  # type: ignore[operator]

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
            self.snr_db_min is not None and SNR_UNRELIABLE_DB <= self.snr_db_min < SNR_MARGINAL_DB
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


def _vowel_segments(words: Sequence[Mapping[str, Any]]) -> list[tuple[int, str, str, Segment]]:
    """(word index, word, vowel, span) for every timed vocalic phoneme, in time order.

    Offsets are ticks from the start of the **audio stream**, which for the file-backed
    recognition this project runs is the start of the file. `speech_analyzer._timing` flags
    that as the thing a slicing chunk must not assume, so it is not assumed: `alignment_db`
    below checks it against the audio itself on every measurement.
    """
    found: list[tuple[int, str, str, Segment]] = []
    for index, word in enumerate(words):
        text = str(word.get("word") or "")
        for phoneme in word.get("phonemes") or []:
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
                (
                    index,
                    text,
                    symbol,
                    Segment(symbol, start, start + float(duration) / _TICKS_PER_SECOND),
                )
            )
    found.sort(key=lambda item: item[3].start_s)
    return found


def _produced_vowel(phoneme: Mapping[str, Any]) -> str | None:
    """The vowel Azure's best alternate says was actually produced, when it differs.

    None when the best alternate agrees with the target, or when the alternate is not a vowel
    at all — that is a consonant confusion and belongs in the phoneme diagnosis, not here.
    """
    expected = phoneme_reference.normalise(phoneme.get("phoneme"))
    alternates = [
        alternate
        for alternate in (phoneme.get("nbest") or [])
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


def _coda_voiceless(word: Mapping[str, Any], position: int) -> bool | None:
    """Whether the consonant right after this vowel, inside the same word, is voiceless."""
    phonemes = word.get("phonemes") or []
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
        if (level := acoustics.rms_dbfs(analysis.sound, segment.start_s, segment.end_s)) is not None
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
    words: Sequence[Mapping[str, Any]],
    wav_bytes: bytes,
    *,
    ceiling_hz: float | None = None,
    snr_db_min: float | None = None,
    style: str = STYLE_READ,
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
                    vowel=symbol,
                    word=word_text,
                    word_index=word_index,
                    start_s=segment.start_s,
                    end_s=segment.end_s,
                    duration_ms=duration_ms,
                    at20=empty,
                    at50=empty,
                    at80=empty,
                    rms_dbfs=None,
                    f0_hz=None,
                    stress=stress,
                    azure_score=_as_float(score),
                    coda_voiceless=coda,
                    accepted=False,
                    rejected_reason=reason,
                )
            )
            continue

        at20, at50, at80 = analysis.measure(segment)
        _, middle, _ = segment.sample_times()
        accepted = at50.usable
        tokens.append(
            Token(
                vowel=symbol,
                word=word_text,
                word_index=word_index,
                start_s=segment.start_s,
                end_s=segment.end_s,
                duration_ms=duration_ms,
                at20=at20,
                at50=at50,
                at80=at80,
                rms_dbfs=acoustics.rms_dbfs(analysis.sound, segment.start_s, segment.end_s),
                f0_hz=analysis.f0_at(middle),
                stress=stress,
                azure_score=_as_float(score),
                coda_voiceless=coda,
                accepted=accepted,
                rejected_reason="" if accepted else REJECT_NO_FORMANTS,
            )
        )

    return Measurement(
        tokens=tuple(tokens),
        ceiling_hz=float(ceiling_hz),
        snr_db_min=snr_db_min,
        style=style,
        ceiling_choice=choice,
        alignment_db=alignment,
    )


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _phoneme_index(word: Mapping[str, Any], symbol: str, position: int) -> int:
    """Index of the `position`-th vocalic phoneme matching `symbol` inside a word."""
    seen = -1
    for index, phoneme in enumerate(word.get("phonemes") or []):
        if not isinstance(phoneme, dict):
            continue
        entry = phoneme_reference.lookup(phoneme.get("phoneme"))
        if entry is None or entry.kind not in VOCALIC_KINDS:
            continue
        seen += 1
        if seen == position:
            return index
    return -1


def _phoneme_at(word: Mapping[str, Any], symbol: str, position: int) -> Mapping[str, Any]:
    index = _phoneme_index(word, symbol, position)
    phonemes = list(word.get("phonemes") or [])
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

    def hz(self, f1_z: float | None, f2_z: float | None) -> tuple[float | None, float | None]:
        """`z` inverted: where a normalised position sits in THIS speaker's own hertz.

        What it is for: a reference target is a position in z, and anything that has to act on
        the audio — shifting a formant, naming a frequency — needs hertz. Reading the target's
        hertz straight off the reference table would import the reference talker's vocal tract
        along with the target, which is the error normalisation exists to prevent. Mapping the
        target z back through the SPEAKER's own mean and SD asks the right question instead:
        where would this speaker's F2 be if the vowel sat where the target sits.
        """
        return (
            None if f1_z is None else f1_z * self.f1_sd + self.f1_mean,
            None if f2_z is None else f2_z * self.f2_sd + self.f2_mean,
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
            b1=None,
            b2=None,
            b3=None,
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


def _reference_table(
    reference_set: str, source: str
) -> Mapping[str, vowel_reference.ReferenceVowel]:
    """One of the two General American tables. **Never a blend of them.**

    `REFERENCE_PUBLISHED` is Hillenbrand et al. (1995): real humans, peer-reviewed, twelve
    vowels of citation-form /hVd/ speech recorded in the early 1990s. `REFERENCE_VOICE` is
    `model_reference.py`: the whole 22-vowel inventory in connected speech, measured through
    this project's own pipeline, from sixteen current en-US neural voices.

    They answer different questions and they are never averaged — a mean of a human corpus and
    a synthesiser set describes nothing at all. Every surface names which one it used, which is
    what `REFERENCE_PUBLISHED` and `REFERENCE_VOICE` have existed for since v0.10.0.
    """
    if source == REFERENCE_VOICE:
        return model_reference.REFERENCE_SETS.get(reference_set, {})
    return vowel_reference.REFERENCE_SETS.get(reference_set, {})


def reference_normaliser(reference_set: str, *, source: str = REFERENCE_PUBLISHED) -> Normaliser:
    """A reference table's own normaliser, built the same way over its own categories.

    Each table is normalised over ITS OWN inventory, not over a shared subset. A z-score is
    relative to whatever inventory produced it, so a speaker normalised over twelve categories
    and a reference normalised over twenty-two are not comparable — which is why
    `REFERENCE_CATEGORIES` pins the speaker's side to the published twelve.
    """
    table = _reference_table(reference_set, source)
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
    # How many of `n` were long enough to measure a GLIDE in, which is a stricter test than
    # being long enough to measure a position in — see `MIN_TRAJECTORY_MS`. Carried so a
    # trajectory row can say "0 of 14 tokens were long enough" instead of a bare "not
    # measurable", which reads like a bug rather than a fact about connected speech.
    n_trajectory: int = 0

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
            b1=None,
            b2=None,
            b3=None,
        )
        f1_z, f2_z, f3_z = normaliser.z(mean)
        found[vowel] = VowelPosition(
            vowel=vowel,
            n=len(group),
            f1_hz=mean.f1,
            f2_hz=mean.f2,
            f3_hz=mean.f3,
            f1_z=f1_z,
            f2_z=f2_z,
            f3_z=f3_z,
            duration_ms=_mean_of(token.duration_ms for token in group),
            f2_travel_hz=_mean_of(token.f2_travel for token in group),
            f3_minus_f2_hz=_mean_of(token.f3_minus_f2 for token in group),
            rms_dbfs=_mean_of(token.rms_dbfs for token in group),
            n_trajectory=sum(1 for token in group if token.trajectory_usable),
        )
    return found


def reference_positions(
    reference_set: str, *, source: str = REFERENCE_PUBLISHED
) -> dict[str, VowelPosition]:
    """A reference table's means in its own Lobanov space, for direct comparison."""
    table = _reference_table(reference_set, source)
    normaliser = reference_normaliser(reference_set, source=source)
    found: dict[str, VowelPosition] = {}
    for symbol, entry in table.items():
        point = FormantPoint(
            f1=entry.at50.f1, f2=entry.at50.f2, f3=entry.at50.f3, b1=None, b2=None, b3=None
        )
        f1_z, f2_z, f3_z = normaliser.z(point)
        found[symbol] = VowelPosition(
            vowel=symbol,
            n=entry.n,
            f1_hz=entry.at50.f1,
            f2_hz=entry.at50.f2,
            f3_hz=entry.at50.f3,
            f1_z=f1_z,
            f2_z=f2_z,
            f3_z=f3_z,
            duration_ms=entry.duration_ms,
            f2_travel_hz=entry.f2_travel,
            f3_minus_f2_hz=entry.at50.f3_minus_f2,
            rms_dbfs=None,
            n_trajectory=entry.n,
        )
    return found


# --- Trajectories ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Trajectory:
    """Where one vowel STARTS and where it ENDS, in the shared normalised space.

    `VowelPosition` carries the 50% point and a scalar `f2_travel_hz`, which is enough for a
    table row and not enough for a chart: a scalar says how far the tongue moved and not in
    which direction. A diphthong is a gesture, and the only honest way to draw one is as the
    stroke it actually is — from the 20% point to the 80% point, in the same space the
    monophthongs are plotted in.

    **20% and 80%, not 25% and 75%.** `acoustics.SAMPLE_POINTS` uses the proportions
    Hillenbrand's own file sampled at, so the speaker and the reference are measured at the
    same places. A 25/75 sample against a 20/80 reference is a small systematic bias that lands
    hardest on exactly these vowels.
    """

    vowel: str
    n: int
    start_f1_z: float | None
    start_f2_z: float | None
    end_f1_z: float | None
    end_f2_z: float | None
    travel_hz: float | None  # signed F2 movement, the number the table reports

    @property
    def length_z(self) -> float | None:
        """How long the stroke is. A monophthongised diphthong renders as a dot."""
        if None in (self.start_f1_z, self.start_f2_z, self.end_f1_z, self.end_f2_z):
            return None
        return math.hypot(
            float(self.end_f1_z) - float(self.start_f1_z),  # type: ignore[arg-type]
            float(self.end_f2_z) - float(self.start_f2_z),  # type: ignore[arg-type]
        )


def trajectories(
    tokens: Sequence[Token], normaliser: Normaliser, *, minimum: int = MIN_TOKENS_PER_CATEGORY
) -> dict[str, Trajectory]:
    """Per-vowel 20%→80% strokes, in z-units. Every vowel, not only the diphthongs.

    Monophthongs are included deliberately: a chart that only draws the diphthongs cannot show
    that they are the ones that move. A steady vowel's stroke is its own noise floor, and
    seeing /i/ sit still next to a flattened /eɪ/ is what makes the flattening legible.
    """
    grouped: dict[str, list[Token]] = {}
    for token in tokens:
        # `trajectory_usable`, not `at50.usable`: a stroke drawn from a token too short to
        # measure a glide in is a picture of the neighbouring consonants.
        if token.accepted and token.trajectory_usable:
            grouped.setdefault(token.vowel, []).append(token)

    found: dict[str, Trajectory] = {}
    for vowel, group in sorted(grouped.items()):
        if len(group) < minimum:
            continue
        start = FormantPoint(
            f1=_mean_of(token.at20.f1 for token in group),
            f2=_mean_of(token.at20.f2 for token in group),
            f3=None,
            b1=None,
            b2=None,
            b3=None,
        )
        end = FormantPoint(
            f1=_mean_of(token.at80.f1 for token in group),
            f2=_mean_of(token.at80.f2 for token in group),
            f3=None,
            b1=None,
            b2=None,
            b3=None,
        )
        start_f1, start_f2, _ = normaliser.z(start)
        end_f1, end_f2, _ = normaliser.z(end)
        found[vowel] = Trajectory(
            vowel=vowel,
            n=len(group),
            start_f1_z=start_f1,
            start_f2_z=start_f2,
            end_f1_z=end_f1,
            end_f2_z=end_f2,
            travel_hz=_mean_of(token.f2_travel for token in group),
        )
    return found


def reference_trajectories(
    reference_set: str, *, source: str = REFERENCE_PUBLISHED
) -> dict[str, Trajectory]:
    """The reference table's own strokes, in its own Lobanov space."""
    table = _reference_table(reference_set, source)
    normaliser = reference_normaliser(reference_set, source=source)
    found: dict[str, Trajectory] = {}
    for symbol, entry in table.items():
        start = FormantPoint(f1=entry.at20.f1, f2=entry.at20.f2, f3=None, b1=None, b2=None, b3=None)
        end = FormantPoint(f1=entry.at80.f1, f2=entry.at80.f2, f3=None, b1=None, b2=None, b3=None)
        start_f1, start_f2, _ = normaliser.z(start)
        end_f1, end_f2, _ = normaliser.z(end)
        found[symbol] = Trajectory(
            vowel=symbol,
            n=entry.n,
            start_f1_z=start_f1,
            start_f2_z=start_f2,
            end_f1_z=end_f1,
            end_f2_z=end_f2,
            travel_hz=entry.f2_travel,
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
        return statistics.fmean(math.hypot(f1 - centroid_f1, f2 - centroid_f2) for f1, f2 in points)

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
        distances.append(math.hypot(f1_z - centroid.centroid_f1_z, f2_z - centroid.centroid_f2_z))
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
        one_f1, one_f2 = position.f1_z, position.f2_z
        two_f1, two_f2 = other.f1_z, other.f2_z
        if one_f1 is None or one_f2 is None or two_f1 is None or two_f2 is None:
            continue
        displacements[vowel] = math.hypot(two_f1 - one_f1, two_f2 - one_f2)
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

WITHIN_NOISE = "Within measurement noise"

# Appended to any trajectory instruction scored against the published table. Hillenbrand's
# /hVd/ words were read in isolation and these are not, so the comparison is indicative rather
# than like-for-like — the same class of mismatch caveat 3 forbids outright for durations.
CITATION_CAVEAT = "(target is citation-form speech; treat as indicative)"

# How far F3−F2 may sit from its target before the gap is worth an instruction. Inherited from
# the threshold the pre-v0.11.0 code already used, now applied symmetrically: r-colouring
# measured within this much of the target — in EITHER direction — is reported as being in the
# band rather than converted into a gesture to change.
RHOTICITY_TOLERANCE_HZ = 150.0


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


# Finds the token count `_with_count` wrote, so the style can be appended inside the same
# parenthesis rather than in a second one beside it.
_COUNT_SUFFIX = re.compile(r"\(n=(\d+)\)$")


def _tag_style(findings: Sequence[Finding], style: str) -> list[Finding]:
    """Append the speech style to every User Realization cell that carries a token count.

    **The one addition the four-column contract takes**, and it is not decoration. Read speech
    and spontaneous speech are different populations, so a reader comparing this month's
    spontaneous vowel space against last month's read one and seeing it "get worse" has learned
    nothing except that they read aloud more carefully than they speak — which was never in
    doubt. In this mode the number cannot be interpreted without knowing which population
    produced it, and the table is the only place that can say so.

    Applied to the count, not to every cell: a row whose user column is "—" measured nothing,
    and a style tag on nothing is noise. Columns and their order are untouched.
    """
    if not style:
        return list(findings)
    return [replace(row, user=_COUNT_SUFFIX.sub(rf"(n=\1, {style})", row.user)) for row in findings]


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
    """The signed delta, plus the articulatory instruction it implies for THIS vowel.

    The instruction is **looked up** from `vowel_reference.ARTICULATION`, never composed here.
    Until v0.11.0 this function generated it from the sign alone — "tongue further front, lips
    spread" for a positive F2 delta and "tongue further back, lips rounder" for a negative one,
    for every vowel in the inventory. That is right for the front unrounded vowels and **wrong
    for the back rounded ones**: F2 responds to lip posture as strongly as to tongue
    advancement, so a learner whose /u/ sits too high in F2 has almost always under-rounded
    rather than fronted, and "move your tongue back" sends them to fix the wrong articulator.
    """
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
    if vowel_reference.is_merging(vowel):
        # Not an error class — a sound change in progress. Said before the instruction so a
        # reader does not start drilling a merger back apart.
        caveat = f" {vowel_reference.MERGING_NOTE}{caveat}"
    formant = "F1" if label.startswith("F1") else "F2"
    move = vowel_reference.instruction_for(vowel, formant, delta)
    if not move:
        # An inventory member with no entry, which a test forbids — but a missing instruction
        # must render as an honest blank rather than as a generated guess.
        return f"{_signed(delta, 'z', 2)} → no articulatory instruction for this vowel{caveat}"
    return f"{_signed(delta, 'z', 2)} → {move}{caveat}"


def _trajectory_findings(
    speaker: Mapping[str, VowelPosition], reference: Mapping[str, VowelPosition]
) -> list[Finding]:
    """F2 travel from 20% to 80% — whether a diphthong is a diphthong.

    **The sign of the travel is checked before any instruction is given**, and that guard was
    added after the model-reference capture showed what happens without it. In connected speech
    a diphthong's 80% window routinely lands in the following segment: measured across sixteen
    neural voices, FACE came out travelling backwards, because "same" ends in a nasal whose
    murmur reads as F1 240 Hz and "way" is followed by the word "I".

    A backwards travel is not a small glide, and "widen the glide" is the wrong thing to say
    about it — the vowel may be fine and the measurement contaminated. So a travel whose sign
    disagrees with the reference is reported as unmeasurable in this context, with the reason,
    rather than converted into an instruction.

    Note also that the only published targets available are Hillenbrand's, measured on
    citation-form /hVd/ words read in isolation. That is a gentler mismatch than it is for
    durations — a glide's EXTENT survives shortening better than its length does — but it is a
    mismatch, and the row says so instead of implying like-for-like.
    """
    found: list[Finding] = []
    for vowel, position in sorted(speaker.items()):
        entry = phoneme_reference.lookup(vowel)
        if entry is None or entry.kind != "diphthong":
            continue
        target = reference.get(vowel)
        target_travel = target.f2_travel_hz if target else None
        delta = _delta(target_travel, position.f2_travel_hz)
        if position.n_trajectory == 0:
            # Said plainly, because the alternative reads as a defect. A vowel can be measured
            # for POSITION and still be too short to measure a GLIDE in — connected speech is
            # full of 60 ms diphthongs, and at that length the edge windows analyse the
            # neighbouring consonants rather than the vowel.
            instruction = (
                f"None of the {position.n} token(s) reached {MIN_TRAJECTORY_MS:.0f} ms, the "
                f"length a glide needs before the 20% and 80% analysis windows fit inside the "
                f"vowel. Refused rather than measuring the consonants on either side."
            )
        elif target_travel is None:
            instruction = (
                "No published GA reference for this diphthong. Travel is recorded, not scored."
            )
        elif delta is None:
            instruction = "Not measurable from this recording."
        elif (position.f2_travel_hz or 0.0) * target_travel < 0:
            # The glide is measured running the OPPOSITE way to the reference. Almost always
            # the 80% window landing in the next segment rather than a real reversal, and
            # neither "widen the glide" nor "monophthongised" is a safe thing to say about it.
            instruction = (
                f"Measured travelling the opposite way to the target "
                f"({_signed(position.f2_travel_hz, 'Hz')} against "
                f"{_signed(target_travel, 'Hz')}). In connected speech that is usually the "
                f"80% sample landing in the following sound rather than a reversed glide, so "
                f"no instruction is given. Drill this vowel in a slower, longer word to "
                f"measure it cleanly."
            )
        elif abs(position.f2_travel_hz or 0.0) < abs(target_travel) * 0.5:
            instruction = (
                f"{_signed(delta, 'Hz')} → monophthongised; glide, do not hold {CITATION_CAVEAT}"
            )
        else:
            instruction = f"{_signed(delta, 'Hz')} → widen the glide {CITATION_CAVEAT}"
        found.append(
            Finding(
                feature=_feature(vowel, "F2 travel 20→80%"),
                user=_with_count(
                    _signed(position.f2_travel_hz, "Hz"),
                    position.n_trajectory,
                ),
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
        # /ɝ/ has a published mean; /ɚ/ and the /Vɹ/ sequences do not. Falling back to /ɝ/'s
        # target is defensible because r-colouring is one articulatory gesture, but the row
        # has to say that is what happened rather than imply the table covers the vowel.
        own = reference.get(vowel)
        against = own or nurse
        target_value = against.f3_minus_f2_hz if against is not None else None
        source = "" if own else " (/ɝ/ target — no published mean for this vowel)"
        delta = _delta(target_value, position.f3_minus_f2_hz)
        if delta is None:
            instruction = "Not measurable from this recording."
        elif abs(delta) <= RHOTICITY_TOLERANCE_HZ:
            instruction = f"{_signed(delta, 'Hz')} → r-colouring is within the tolerance band"
        else:
            # **`delta` is passed through UNCHANGED, and that is the whole point.** It is
            # `target − produced` on F3−F2, and `Instruction.f3_raise` / `f3_lower` are keyed
            # on the same convention: `f3_lower` is what to say when the target sits BELOW the
            # speaker. A negative delta means the speaker's F3 sits further above F2 than the
            # target's — r-colouring that has not arrived — and asks for a lower F3, which is
            # `f3_lower`, "bunch the tongue". Negating first looks like it corrects for F3−F2
            # being a difference rather than a formant. It does not: it inverts every
            # r-colouring instruction on the surface, telling an under-rhotic speaker to
            # release the bunching. Guarded by a test that names both directions.
            move = vowel_reference.instruction_for(vowel, "F3", delta)
            instruction = (
                f"{_signed(delta, 'Hz')} → {move}"
                if move
                else f"{_signed(delta, 'Hz')} → no r-colouring instruction for this vowel"
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


def rejection_findings(tokens: Sequence[Token]) -> list[Finding]:
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


# --- Which instrument a row belongs to --------------------------------------------------------
# Every chart in `accent_charts` renders its own table beside it, and that table must be the
# SAME rows the whole-measurement table already carries — not a second set built from the same
# numbers a slightly different way. So the findings are produced once, keyed by instrument, and
# `findings()` is the concatenation. One definition, two renderings.

POSITION = "position"
TRAJECTORY = "trajectory"
RHOTICITY = "rhoticity"
DURATION = "duration"
REDUCTION = "reduction"
STRESS = "stress"
PITCH = "pitch"
RHYTHM = "rhythm"
REJECTED = "rejected"

# Reading order, and the order the Accent page renders in. **Rhoticity leads**: it is the
# loudest, cleanest, most correctable marker available for a General American target, and on a
# non-rhotic-influenced accent it is routinely the largest single gap on the page. Rejections
# come last, so the table ends by admitting what it could not do.
INSTRUMENT_ORDER: tuple[str, ...] = (
    RHOTICITY,
    POSITION,
    TRAJECTORY,
    PITCH,
    DURATION,
    REDUCTION,
    RHYTHM,
    STRESS,
    REJECTED,
)

# The instruments that fall out of a `Measurement` alone. `PITCH` and `RHYTHM` need inputs a
# measurement does not carry — the model's rendering of the same text, and the assessment's own
# word timings — so they are built by their own functions and merged by the caller.
MEASUREMENT_INSTRUMENTS: tuple[str, ...] = (
    RHOTICITY,
    POSITION,
    TRAJECTORY,
    DURATION,
    REDUCTION,
    STRESS,
    REJECTED,
)


def _hertz_reference(
    reference_set: str, source: str, published: Mapping[str, VowelPosition]
) -> Mapping[str, VowelPosition]:
    """The table to score an instrument that compares HERTZ against, never z-units.

    **Only some instruments may switch tables, and which ones is not a preference.** A z-score
    is relative to the inventory that produced it: the speaker is normalised over
    `REFERENCE_CATEGORIES`, the published table over its own twelve, and `model_reference` over
    twenty-two. So position and trajectory — which compare z — have to stay on the published
    table or the arrows are measured in two different spaces and every one of them is wrong.

    F3−F2 is in hertz and carries no normalisation at all, so it can be scored against the
    better table, and the measured one is plainly better here: Hillenbrand published a mean for
    /ɝ/ alone, in citation-form /hVd/ words, while `model_reference` covers all seven r-coloured
    categories in connected speech through this same pipeline. Rhoticity is the largest and most
    correctable gap on the page for a General American target, and it was scored against a
    stand-in for six of its seven vowels until v0.11.0.

    Falls back to `published` when the measured table has nothing for this set, so a fresh clone
    with no capture behind it still gets the row it always got.
    """
    if source == REFERENCE_PUBLISHED:
        return published
    return reference_positions(reference_set, source=source) or published


def findings_by_instrument(
    measurement: Measurement,
    normaliser: Normaliser,
    *,
    reference_set: str,
    noise: NoiseFloor | None = None,
    minimum: int = MIN_TOKENS_PER_CATEGORY,
    rhoticity_source: str = REFERENCE_VOICE,
) -> dict[str, list[Finding]]:
    """The four-column rows for one measurement, split by which instrument produced them.

    `minimum` is the per-category token floor. It is `MIN_TOKENS_PER_CATEGORY` when the
    speaker is being normalised from their own reading, and **1** when a stored baseline is
    supplying the normalisation — see `plot_gate`. A single token is a legitimate point once
    the space it sits in was established elsewhere, provided its count travels with it, which
    `VowelPosition.n` guarantees.

    `rhoticity_source` picks the table the F3−F2 rows are scored against, and **only** those
    rows — see `_hertz_reference`.
    """
    accepted = measurement.accepted
    speaker = positions(accepted, normaliser, minimum=minimum)
    reference = reference_positions(reference_set)
    centroid = reduction(accepted, normaliser)

    grouped = {
        RHOTICITY: _rhoticity_findings(
            speaker, _hertz_reference(reference_set, rhoticity_source, reference)
        ),
        POSITION: _position_findings(speaker, reference, noise),
        TRAJECTORY: _trajectory_findings(speaker, reference),
        DURATION: (
            _duration_findings(tense_lax_ratios(accepted, reference_set), "tense")
            + _duration_findings(pre_fortis_ratios(accepted), "clipping")
        ),
        REDUCTION: [_reduction_finding(centroid)],
        STRESS: _stress_findings(stress_contrasts(accepted, normaliser, centroid)),
        REJECTED: rejection_findings(measurement.tokens),
    }
    return {instrument: _tag_style(rows, measurement.style) for instrument, rows in grouped.items()}


def findings(
    measurement: Measurement,
    normaliser: Normaliser,
    *,
    reference_set: str,
    noise: NoiseFloor | None = None,
    minimum: int = MIN_TOKENS_PER_CATEGORY,
    rhoticity_source: str = REFERENCE_VOICE,
) -> list[Finding]:
    """Every four-column row for one measurement, in reading order.

    The concatenation of `findings_by_instrument` in `INSTRUMENT_ORDER`. Kept as its own name
    because most callers want the whole table and should not have to know the keys.
    """
    grouped = findings_by_instrument(
        measurement,
        normaliser,
        reference_set=reference_set,
        noise=noise,
        minimum=minimum,
        rhoticity_source=rhoticity_source,
    )
    rows: list[Finding] = []
    for instrument in INSTRUMENT_ORDER:
        rows += grouped.get(instrument, [])
    return rows


# --- May this be drawn at all? ----------------------------------------------------------------


WRONG_STYLE_BASELINE = (
    "**Nothing here can be charted: there is no {measured} baseline yet.** The stored baseline "
    "was built from {baseline} speech, and it is not borrowed — read speech and spontaneous "
    "speech are different populations, not the same measurement made under harder conditions. "
    "Speakers hyperarticulate when reading and reduce far more when generating language, so "
    "vowels centralise, durations shorten and unstressed syllables collapse further toward "
    "schwa. Every one of those is something this page measures, and normalising {measured} "
    "speech against a {baseline} centroid would report that change of register as an accent "
    "finding. Build a {measured} baseline on the Calibration panel below — recording the same "
    "prompt a second time is part of what these first sessions are for."
)

NO_BASELINE = (
    "**No stored baseline, so nothing here can be charted.** Establishing the vowel space "
    "needs a full inventory from one speaker — the calibration passage, read twice. Until "
    "that exists there is no centroid to normalise against, and a point plotted without one "
    "is a confident dot drawn from a normalisation that does not exist."
)

NOTHING_MEASURABLE = (
    "**Nothing in this recording could be measured.** Every vowel token was refused — see the "
    "rejection table for what was refused and why."
)

STYLE_MISMATCH = (
    "**Nothing here can be charted: this is {measured} speech and the only stored baseline was "
    "built from {baseline} speech.** These are not the same measurement made under harder "
    "conditions, they are different populations — speakers hyperarticulate when reading and "
    "reduce far more when generating language, so vowels centralise, durations shorten and "
    "unstressed syllables collapse further toward schwa. Every one of those is something this "
    "page measures. Normalising {measured} speech against a {baseline} centroid would report "
    "that change of register as an accent finding. A {measured} reading is normalised against a "
    "{measured} baseline or it is not normalised at all, so establishing one is part of what "
    "these first sessions are for."
)

LABEL_MISMATCH = (
    "**The reading named above and the measurement below are not the same recording.** The "
    "label claims {labelled} accepted tokens; the tokens actually loaded number {loaded}. One "
    "of the two is describing a different attempt, so nothing here is drawn — a chart under the "
    "wrong label misattributes one reading's accent to another, which is worse than no chart."
)


def label_matches_measurement(labelled_tokens: int, measurement: Measurement) -> str:
    """Refuse when the label's token count and the loaded measurement's disagree.

    The one tell the 2026-08-20 mismatch left on screen was arithmetic: a label reading
    "138 tokens" above a table reporting n=2 and n=1 per category, which a 138-token read
    cannot produce. Both halves of that screen looked plausible on their own, so the check has
    to be made rather than seen.

    A tripwire, not the fix. The two counts agree by construction for one attempt id —
    `app.measured_attempts` filters on the same `accepted = 1` flag that `Measurement.accepted`
    reads back — so this fires only if the label and the measurement are ever resolved from
    different ids or different snapshots again. That is exactly the class of bug it is here to
    stop coming back silently, whatever the widget does next.

    Returns the reason to refuse with, or "" to draw.
    """
    loaded = len(measurement.accepted)
    if loaded == labelled_tokens:
        return ""
    return LABEL_MISMATCH.format(labelled=labelled_tokens, loaded=loaded)


def minimum_tokens_for(style: str, gate_minimum: int) -> int:
    """The per-vowel token floor for one reading, given the gate's own minimum.

    Spontaneous speech does not sample the vowel space evenly. Token counts per category come
    out wildly uneven and some categories get none at all, because free speech goes wherever the
    sentence goes rather than where a passage was written to send it. A single accidental token
    is therefore not the same object as a single drilled one: the drill token is a deliberate
    probe of a sound the speaker chose to work on, and the free-speech token is whichever vowel
    happened to fall out of a word they reached for.

    So read speech keeps the gate's minimum — 1, once a stored baseline supplies the space —
    and spontaneous speech is held to `MIN_TOKENS_PER_CATEGORY`. A vowel below the floor is
    refused rather than drawn: a lonely confident dot is worse than a gap, because a gap is
    visibly a gap.
    """
    if style == STYLE_SPONTANEOUS:
        return max(gate_minimum, MIN_TOKENS_PER_CATEGORY)
    return gate_minimum


@dataclass(frozen=True)
class PlotGate:
    """Whether this measurement may be drawn, and in whose vowel space.

    **The gate is "is there a stored baseline", never "which mode was this".** Establishing
    the normalisation reference needs a full vowel inventory from one speaker, which a
    three-word drill cannot supply. But USING an already-stored baseline needs only the token
    being measured — so once calibration has run, a drill token is a legitimate single point
    with its count shown beside it.

    Getting this wrong is costly in both directions. Refusing to plot drills throws away the
    measure-drill-remeasure loop, which is the entire purpose of the accent surfaces; plotting
    before a baseline exists draws a confident dot from a normalisation that does not exist.
    """

    ok: bool
    reason: str
    normaliser: Normaliser | None
    minimum_tokens: int
    tokens: int


def plot_gate(
    measurement: Measurement,
    *,
    baseline_normaliser: Normaliser | None,
    baseline_style: str = "",
) -> PlotGate:
    """Decide whether to chart this measurement, and which normalisation to chart it in.

    Takes the normaliser rather than a database row so this module stays SQL-free and the rule
    is testable without a connection — `app.py` does the `json.loads`.
    """
    accepted = measurement.accepted
    if baseline_normaliser is None:
        return PlotGate(
            ok=False,
            reason=NO_BASELINE,
            normaliser=None,
            minimum_tokens=MIN_TOKENS_PER_CATEGORY,
            tokens=len(accepted),
        )
    if not accepted:
        return PlotGate(
            ok=False,
            reason=NOTHING_MEASURABLE,
            normaliser=baseline_normaliser,
            minimum_tokens=1,
            tokens=0,
        )
    if baseline_style and measurement.style and measurement.style != baseline_style:
        # **A refusal, not a caveat.** This used to draw the chart with a warning above it, on
        # the reasoning that a stated caveat is honest. It is not enough: the numbers are still
        # rendered, still comparable-looking against last month's, and a caveat is the first
        # thing a reader skips. A read baseline normalises read speech, full stop.
        return PlotGate(
            ok=False,
            reason=STYLE_MISMATCH.format(measured=measurement.style, baseline=baseline_style),
            normaliser=None,
            minimum_tokens=MIN_TOKENS_PER_CATEGORY,
            tokens=len(accepted),
        )
    return PlotGate(
        ok=True,
        reason="",
        normaliser=baseline_normaliser,
        # One token is enough once the SPACE it sits in came from somewhere else. The count
        # travels on every point and every row, so thin evidence looks thin.
        #
        # That holds for a DELIBERATE token — a three-word drill aimed at one sound, which is
        # what makes the measure-drill-remeasure loop possible. It does not hold for free
        # speech, which samples the vowel space wherever the sentence happened to go; there the
        # caller raises the floor. See `minimum_tokens_for` and its call site.
        minimum_tokens=1,
        tokens=len(accepted),
    )


# --- Ranking what to practise next ------------------------------------------------------------


@dataclass(frozen=True)
class Gap:
    """One measured shortfall, big enough to be worth a drill.

    `magnitude` is in `unit`, always positive, and **only comparable within a metric**. There
    is deliberately no cross-metric severity score: 0.6 z of vowel displacement and 300 Hz of
    missing r-colouring are not commensurable, and inventing an exchange rate between them
    would be the kind of confident-and-unfounded number this project exists to delete. The
    ranking is therefore per metric, and `ranked_gaps` interleaves by metric priority.
    """

    vowel: str
    metric: str
    magnitude: float
    unit: str
    detail: str
    n: int
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        keyword = phoneme_reference.keyword_for(self.vowel)
        return f"/{self.vowel}/ {keyword}".strip()


# How many of each metric reach the coach. Three is enough to write a report around and few
# enough that the payload stays the fraction of the raw response `compact` exists to be.
GAPS_PER_METRIC = 3


def ranked_gaps(
    measurement: Measurement,
    normaliser: Normaliser,
    *,
    reference_set: str,
    noise: NoiseFloor | None = None,
    minimum: int = MIN_TOKENS_PER_CATEGORY,
    limit: int = GAPS_PER_METRIC,
    rhoticity_source: str = REFERENCE_VOICE,
) -> list[Gap]:
    """What the geometry says is worth practising, worst first within each metric.

    **Everything smaller than the noise floor is dropped before ranking**, not flagged after.
    A vowel that moved less than the band moves that much between two reads with no learning
    at all, so it is not a finding, and a finding that is not real must not become a drill.
    """
    accepted = measurement.accepted
    speaker = positions(accepted, normaliser, minimum=minimum)
    reference = reference_positions(reference_set)
    # Hertz-only, and only for the rhoticity gaps below — the position and trajectory gaps are
    # in z and must stay in the space the speaker was normalised into. See `_hertz_reference`.
    rhotic_reference = _hertz_reference(reference_set, rhoticity_source, reference)
    centroid = reduction(accepted, normaliser)

    found: dict[str, list[Gap]] = {RHOTICITY: [], POSITION: [], TRAJECTORY: [], REDUCTION: []}

    for vowel, position in speaker.items():
        # **Not `continue` on a missing published target.** Hillenbrand has a mean for /ɝ/ and
        # for none of the other six r-coloured categories, so skipping here meant the rhoticity
        # ranking could only ever fire on NURSE — while the measured table has all seven. The
        # position and trajectory blocks guard on `target` themselves instead.
        target = reference.get(vowel)
        entry = phoneme_reference.lookup(vowel)

        # Position — the arrow's length, net of the band it has to clear to be real.
        # Bound to locals rather than tested as a tuple: `None not in (...)` is true at
        # runtime and invisible to the type checker, and the ignore it would otherwise need is
        # exactly the kind that goes stale without anyone noticing.
        one, two = position.f1_z, position.f2_z
        aim_f1, aim_f2 = (target.f1_z, target.f2_z) if target is not None else (None, None)
        if one is not None and two is not None and aim_f1 is not None and aim_f2 is not None:
            f1_delta, f2_delta = aim_f1 - one, aim_f2 - two
            arrow = math.hypot(f1_delta, f2_delta)
            band = (noise.band_for(vowel) if noise else None) or 0.0
            if arrow > band:
                found[POSITION].append(
                    Gap(
                        vowel=vowel,
                        metric=POSITION,
                        magnitude=arrow - band,
                        unit="z",
                        detail=(
                            f"sits {arrow:.2f} z from the General American target, against a "
                            f"{band:.2f} z measurement band"
                        ),
                        n=position.n,
                        evidence={
                            "arrow_z": round(arrow, 3),
                            "noise_band_z": round(band, 3),
                            "f1_delta_z": round(f1_delta, 3),
                            "f2_delta_z": round(f2_delta, 3),
                            "tokens": position.n,
                        },
                    )
                )

        # Trajectory — how much of the glide is missing. A monophthongised diphthong is the
        # clearest single thing the charts show, so a shortfall here ranks on its own.
        # `n_trajectory` and an explicit None check, never `or 0.0`: a vowel whose tokens were
        # all too short to measure a glide in has NO travel, and reading that absence as a
        # 0 Hz glide manufactures the worst possible monophthongisation finding out of a
        # measurement that was refused. `_trajectory_findings` says exactly that in the table
        # and this has to agree with it — a gap that is not real must not become a drill. The
        # sign guard is the same one for the same reason: a glide measured running the
        # opposite way to the reference is the following segment leaking into the 80% window,
        # not a shortfall to practise.
        measured_travel = position.f2_travel_hz
        if (
            target is not None
            and entry is not None
            and entry.kind == "diphthong"
            and target.f2_travel_hz
            and position.n_trajectory > 0
            and measured_travel is not None
            and measured_travel * target.f2_travel_hz > 0
        ):
            produced_travel = abs(measured_travel)
            wanted = abs(target.f2_travel_hz)
            if produced_travel < wanted:
                found[TRAJECTORY].append(
                    Gap(
                        vowel=vowel,
                        metric=TRAJECTORY,
                        magnitude=wanted - produced_travel,
                        unit="Hz",
                        detail=(
                            f"glides {produced_travel:.0f} Hz where General American glides "
                            f"{wanted:.0f} Hz — the diphthong is flattening toward a monophthong"
                        ),
                        # The glide count, not the token count: only these tokens were long
                        # enough to contribute a travel, so only these are the evidence.
                        n=position.n_trajectory,
                        evidence={
                            "produced_travel_hz": round(produced_travel),
                            "target_travel_hz": round(wanted),
                            "tokens": position.n_trajectory,
                        },
                    )
                )

        # Rhoticity — F3 sitting too far above F2 is r-colouring that is not arriving. Scored
        # against `rhotic_reference`, which is the MEASURED table by default: F3−F2 is in hertz
        # and carries no normalisation, so it can use the table that actually covers all seven
        # r-coloured categories. /ɝ/ still stands in for anything the chosen table lacks, on the
        # same grounds `_rhoticity_findings` uses — r-colouring is one articulatory gesture
        # whatever vowel carries it.
        rhotic = vowel in {"ɝ", "ɚ"} or (entry is not None and entry.kind == "r-coloured")
        rhotic_target = rhotic_reference.get(vowel)
        nurse = rhotic_reference.get("ɝ")
        against = rhotic_target.f3_minus_f2_hz if rhotic_target is not None else None
        if against is None and nurse is not None:
            against = nurse.f3_minus_f2_hz
        if rhotic and position.f3_minus_f2_hz is not None and against is not None:
            excess = position.f3_minus_f2_hz - against
            if excess > 0:
                found[RHOTICITY].append(
                    Gap(
                        vowel=vowel,
                        metric=RHOTICITY,
                        magnitude=excess,
                        unit="Hz",
                        detail=(
                            f"F3 sits {position.f3_minus_f2_hz:.0f} Hz above F2 where the "
                            f"target is {against:.0f} Hz — {excess:.0f} Hz of missing "
                            f"r-colouring"
                        ),
                        n=position.n,
                        evidence={
                            "f3_minus_f2_hz": round(position.f3_minus_f2_hz),
                            "target_f3_minus_f2_hz": round(against),
                            "excess_hz": round(excess),
                            "tokens": position.n,
                        },
                    )
                )

    # Reduction — one gap, not one per vowel: it is a property of the whole reading.
    if centroid.measured and centroid.stressed_distance_z:
        unstressed = float(centroid.mean_distance_z or 0.0)
        stressed = float(centroid.stressed_distance_z)
        ratio = unstressed / stressed if stressed else 0.0
        if ratio > 0.8:
            found[REDUCTION].append(
                Gap(
                    vowel="ə",
                    metric=REDUCTION,
                    magnitude=unstressed - stressed,
                    unit="z",
                    detail=(
                        f"unstressed vowels sit {unstressed:.2f} z from your own schwa "
                        f"centroid against {stressed:.2f} z for the stressed ones — they are "
                        f"barely reducing at all"
                    ),
                    n=centroid.n_unstressed,
                    evidence={
                        "unstressed_distance_z": round(unstressed, 3),
                        "stressed_distance_z": round(stressed, 3),
                        "ratio": round(ratio, 3),
                        "tokens": centroid.n_unstressed,
                    },
                )
            )

    ranked: list[Gap] = []
    for metric in (RHOTICITY, POSITION, TRAJECTORY, REDUCTION):
        ranked += sorted(found[metric], key=lambda gap: -gap.magnitude)[:limit]
    return ranked


def rhythm_gap(measured_npvi: float | None, reference_npvi: float | None) -> Gap | None:
    """The nPVI deviation as a `Gap`, so rhythm reaches the coach the same way vowels do.

    Separate from `ranked_gaps` because nPVI is not a property of a vowel token — it is a
    property of the whole reading, and it comes from `rhythm.py` rather than from a
    `Measurement`. A LOWER nPVI than the reference means the vowels are closer to equal in
    length, which is what a syllable-timed rhythm carried into English sounds like.
    """
    if measured_npvi is None or reference_npvi is None:
        return None
    delta = measured_npvi - reference_npvi
    if abs(delta) < 1.0:
        return None
    direction = (
        "more even in length than the reference — the hallmark of a syllable-timed rhythm "
        "carried into English"
        if delta < 0
        else "more uneven in length than the reference"
    )
    return Gap(
        vowel="",
        metric=RHYTHM,
        magnitude=abs(delta),
        unit="nPVI",
        detail=(
            f"nPVI {measured_npvi:.1f} against {reference_npvi:.1f} — your vowels are {direction}"
        ),
        n=0,
        evidence={
            "npvi": round(measured_npvi, 2),
            "reference_npvi": round(reference_npvi, 2),
            "delta": round(delta, 2),
        },
    )


# --- Storage shapes ---------------------------------------------------------------------------
# Plain dicts, so `db.py` stays SQL-only and never imports this module's dataclasses. Round
# trips are asserted in the tests: a baseline that cannot be read back is a re-calibration.


def token_rows(measurement: Measurement) -> list[dict[str, Any]]:
    """One dict per token, matching `vowel_measurements`' columns.

    Rejected tokens are stored too. What was refused, and why, is evidence — it is what makes
    a thin measurement visibly thin rather than silently short, and it is the only record that
    a token existed at all once the reading is over.
    """
    return [
        {
            "vowel": token.vowel,
            "word": token.word,
            "word_index": token.word_index,
            "start_s": token.start_s,
            "duration_ms": token.duration_ms,
            "f1_20": token.at20.f1,
            "f2_20": token.at20.f2,
            "f3_20": token.at20.f3,
            "f1_50": token.at50.f1,
            "f2_50": token.at50.f2,
            "f3_50": token.at50.f3,
            "f1_80": token.at80.f1,
            "f2_80": token.at80.f2,
            "f3_80": token.at80.f3,
            "rms_dbfs": token.rms_dbfs,
            "f0_hz": token.f0_hz,
            "stressed": None if token.stressed is None else int(token.stressed),
            "stress_digit": token.stress,
            "azure_score": token.azure_score,
            "coda_voiceless": (None if token.coda_voiceless is None else int(token.coda_voiceless)),
            "snr_db_min": measurement.snr_db_min,
            "lpc_ceiling_hz": measurement.ceiling_hz,
            "style_tag": measurement.style,
            "accepted": int(token.accepted),
            "rejected_reason": token.rejected_reason,
        }
        for token in measurement.tokens
    ]


def positions_to_json(found: Mapping[str, VowelPosition]) -> dict[str, dict[str, Any]]:
    return {
        vowel: {
            "n": position.n,
            "f1_hz": position.f1_hz,
            "f2_hz": position.f2_hz,
            "f3_hz": position.f3_hz,
            "f1_z": position.f1_z,
            "f2_z": position.f2_z,
            "f3_z": position.f3_z,
            "duration_ms": position.duration_ms,
            "f2_travel_hz": position.f2_travel_hz,
            "f3_minus_f2_hz": position.f3_minus_f2_hz,
            "rms_dbfs": position.rms_dbfs,
        }
        for vowel, position in found.items()
    }


def positions_from_json(blob: Mapping[str, Mapping[str, Any]]) -> dict[str, VowelPosition]:
    return {
        vowel: VowelPosition(
            vowel=vowel,
            n=int(entry.get("n") or 0),
            f1_hz=_opt(entry.get("f1_hz")),
            f2_hz=_opt(entry.get("f2_hz")),
            f3_hz=_opt(entry.get("f3_hz")),
            f1_z=_opt(entry.get("f1_z")),
            f2_z=_opt(entry.get("f2_z")),
            f3_z=_opt(entry.get("f3_z")),
            duration_ms=_opt(entry.get("duration_ms")),
            f2_travel_hz=_opt(entry.get("f2_travel_hz")),
            f3_minus_f2_hz=_opt(entry.get("f3_minus_f2_hz")),
            rms_dbfs=_opt(entry.get("rms_dbfs")),
        )
        for vowel, entry in blob.items()
    }


def normaliser_to_json(normaliser: Normaliser) -> dict[str, Any]:
    return {
        "f1_mean": normaliser.f1_mean,
        "f1_sd": normaliser.f1_sd,
        "f2_mean": normaliser.f2_mean,
        "f2_sd": normaliser.f2_sd,
        "f3_mean": normaliser.f3_mean,
        "f3_sd": normaliser.f3_sd,
        "categories": list(normaliser.categories),
    }


def normaliser_from_json(blob: Mapping[str, Any]) -> Normaliser:
    return Normaliser(
        f1_mean=float(blob["f1_mean"]),
        f1_sd=float(blob["f1_sd"]),
        f2_mean=float(blob["f2_mean"]),
        f2_sd=float(blob["f2_sd"]),
        f3_mean=_opt(blob.get("f3_mean")),
        f3_sd=_opt(blob.get("f3_sd")),
        categories=tuple(blob.get("categories") or ()),
    )


def noise_to_json(noise: NoiseFloor) -> dict[str, Any]:
    return {
        "per_vowel": dict(noise.per_vowel),
        "median_z": noise.median_z,
        "vowels": noise.vowels,
    }


def noise_from_json(blob: Mapping[str, Any]) -> NoiseFloor:
    per_vowel = blob.get("per_vowel") or {}
    return NoiseFloor(
        per_vowel={str(k): float(v) for k, v in dict(per_vowel).items()},
        median_z=_opt(blob.get("median_z")),
        vowels=int(blob.get("vowels") or 0),
    )


def _opt(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _point_from_row(row: Mapping[str, Any], suffix: str) -> FormantPoint:
    """One stored measurement point. Bandwidths are not stored — they gated acceptance at
    measurement time and say nothing once a token has been accepted."""
    return FormantPoint(
        f1=_opt(row.get(f"f1_{suffix}")),
        f2=_opt(row.get(f"f2_{suffix}")),
        f3=_opt(row.get(f"f3_{suffix}")),
        b1=None,
        b2=None,
        b3=None,
    )


def tokens_from_rows(rows: Iterable[Mapping[str, Any]]) -> list[Token]:
    """Rebuild tokens from stored `vowel_measurements` rows.

    This is what makes the stored rows worth storing. A change to the normalisation scheme or
    the reference table is re-derived from here — the raw hertz, durations and stress flags are
    all on the row — without asking anybody to read the passage again.
    """
    rebuilt: list[Token] = []
    for row in rows:
        stress = row.get("stress_digit")
        coda = row.get("coda_voiceless")
        rebuilt.append(
            Token(
                vowel=str(row.get("vowel") or ""),
                word=str(row.get("word") or ""),
                word_index=int(row.get("word_index") or 0),
                start_s=float(row.get("start_s") or 0.0),
                end_s=float(row.get("start_s") or 0.0)
                + float(row.get("duration_ms") or 0.0) / 1000.0,
                duration_ms=float(row.get("duration_ms") or 0.0),
                at20=_point_from_row(row, "20"),
                at50=_point_from_row(row, "50"),
                at80=_point_from_row(row, "80"),
                rms_dbfs=_opt(row.get("rms_dbfs")),
                f0_hz=_opt(row.get("f0_hz")),
                stress=None if stress is None else int(stress),
                azure_score=_opt(row.get("azure_score")),
                coda_voiceless=None if coda is None else bool(coda),
                accepted=bool(row.get("accepted")),
                rejected_reason=str(row.get("rejected_reason") or ""),
                style=str(row.get("style_tag") or ""),
            )
        )
    return rebuilt


class CalibrationRefused(ValueError):
    """The two reads cannot produce an honest baseline. Message is safe to show in the UI."""


def calibrate(
    first: Sequence[Token],
    second: Sequence[Token],
    *,
    reference_set: str,
    ceiling_hz: float,
    style: str = STYLE_READ,
    attempt_ids: Sequence[int] = (),
    measured_at: str = "",
) -> Baseline:
    """Turn two reads of the calibration passage into a baseline and a noise floor.

    Both reads are normalised through the **first read's** centroid. That is the point: if
    each were normalised through its own, Lobanov would absorb most of the between-session
    movement into the normalisation itself and the noise floor would come out flatteringly
    small — which would then license reporting noise as progress, the exact failure the two
    reads exist to prevent.

    The centroid is built over the reference table's own twelve categories, because a z-score
    is relative to whatever inventory produced it and a speaker normalised over twenty-two
    would not be comparable to a reference normalised over twelve.
    """
    if not first or not second:
        raise CalibrationRefused(
            "A baseline needs two readings of the calibration passage. One of them has no "
            "usable vowel measurements."
        )

    # **Both readings must be the same speech style.** A baseline mixed from one read and one
    # spontaneous recording has a centroid that belongs to neither population, and the
    # displacement between them would be read as measurement noise when most of it is the
    # change of register. `style` is what the baseline is then stored and matched under, so a
    # mislabelled pair silently mis-normalises every later reading of both styles.
    styles = {token.style for token in list(first) + list(second) if token.style}
    if len(styles) > 1:
        raise CalibrationRefused(
            f"Those two readings are not the same speech style ({', '.join(sorted(styles))}). "
            f"Read speech and spontaneous speech are different populations, so a baseline "
            f"built across both describes neither. Calibrate each style from two readings of "
            f"its own."
        )
    if styles and style not in styles:
        raise CalibrationRefused(
            f"Those readings are {', '.join(sorted(styles))} speech but the baseline was asked "
            f"for as {style}. A baseline stored under the wrong style is applied to the wrong "
            f"readings, so nothing is stored."
        )

    normaliser = lobanov(list(first), categories=REFERENCE_CATEGORIES)
    first_positions = positions(first, normaliser)
    second_positions = positions(second, normaliser)

    floor = noise_floor(first_positions, second_positions)
    if floor.vowels < MIN_CATEGORIES:
        raise CalibrationRefused(
            f"Only {floor.vowels} vowel(s) could be compared across the two readings, and a "
            f"noise floor needs {MIN_CATEGORIES}. Without it there is no way to tell a real "
            f"change from a different microphone position, so no baseline is stored."
        )

    # The baseline's positions are the mean of the two reads, which is a better estimate of
    # where the speaker sits than either read alone — and it is the pair that defines the
    # band, so neither read gets to be "the" baseline.
    merged: dict[str, VowelPosition] = {}
    for vowel in sorted(set(first_positions) & set(second_positions)):
        one, other = first_positions[vowel], second_positions[vowel]
        merged[vowel] = VowelPosition(
            vowel=vowel,
            n=one.n + other.n,
            f1_hz=_mean_of([one.f1_hz, other.f1_hz]),
            f2_hz=_mean_of([one.f2_hz, other.f2_hz]),
            f3_hz=_mean_of([one.f3_hz, other.f3_hz]),
            f1_z=_mean_of([one.f1_z, other.f1_z]),
            f2_z=_mean_of([one.f2_z, other.f2_z]),
            f3_z=_mean_of([one.f3_z, other.f3_z]),
            duration_ms=_mean_of([one.duration_ms, other.duration_ms]),
            f2_travel_hz=_mean_of([one.f2_travel_hz, other.f2_travel_hz]),
            f3_minus_f2_hz=_mean_of([one.f3_minus_f2_hz, other.f3_minus_f2_hz]),
            rms_dbfs=_mean_of([one.rms_dbfs, other.rms_dbfs]),
        )

    combined = list(first) + list(second)
    return Baseline(
        positions=merged,
        normaliser=normaliser,
        noise=floor,
        ceiling_hz=ceiling_hz,
        reference_set=reference_set,
        style=style,
        reduction=reduction(combined, normaliser),
        tokens=len([token for token in combined if token.accepted]),
        attempt_ids=tuple(attempt_ids),
        measured_at=measured_at,
    )
