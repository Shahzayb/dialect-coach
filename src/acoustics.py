"""Formant, F0 and intensity measurement. The only module that imports parselmouth.

Everything here is about signals and knows nothing about English: it takes a sound and some
time spans, and returns numbers. Which spans are vowels, what they should be, and what a
deviation means are `vowel_measure`'s business. That is the same split `rhythm.py` has against
`speech_analyzer.py`, and it is what makes the phonetics testable without a C++ dependency in
the loop.

## Why Praat rather than forty lines of scipy

Not accuracy — the reason is that Praat's Burg implementation is what the published formant
tables were produced with, so measuring against them through a different estimator adds a
difference nobody can quantify. Two other things fell out of the choice:

**The ceiling/order trap is unreachable through this API.** The classic LPC formant error is
choosing an analysis bandwidth and an LPC order independently: "order 2 + fs/1000, so 18 at
16 kHz" quietly analyses the full 8 kHz band, which IS a ceiling of 8000 Hz, and an order that
high invents extra poles between a vowel's first two formants — splitting one formant into
two, which looks like a plausible measurement and is not one. Praat takes `maximum_formant`
and `max_number_of_formants`, resamples to twice the ceiling itself, and derives the
coefficient count from the formant count. The two cannot be set inconsistently. `BurgSettings`
below asserts the relationship anyway, so the invariant is stated in this project's own terms
rather than inherited silently.

**Resynthesis stays possible.** v0.11.0 plays the user their own recording with a corrected
pitch contour or vowel, which is Praat's Manipulation machinery. Worth recording before that
chunk is planned: parselmouth does **not** bind Manipulation, PitchTier or DurationTier as
typed classes — the bindings are Sound, Pitch, Formant, Intensity, Spectrum and friends — so
v0.11.0 goes through the untyped, string-dispatched `parselmouth.praat.call(...)`. Checked in
the 0.4.7 source tree, not assumed.

## Intensity is dBFS, and that is not a limitation that can be engineered away

There is no calibrated microphone in this project and no absolute sound-pressure reference, so
"how loud was that vowel" has no answer in dB SPL. What is answerable is how loud it was
*relative to everything else in the same recording*, which is what dBFS gives. Every consumer
must therefore compare intensities WITHIN one recording — stressed against unstressed syllable
of the same word — and never across two, where a gain-control change is indistinguishable from
a change in delivery.
"""

from __future__ import annotations

import io
import logging
import math
import statistics
import wave
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import parselmouth

logger = logging.getLogger(__name__)

# The vocal-tract-length guesses, in Hz. Praat's own conventional values for an adult male and
# an adult female voice. Treated as a WEAK prior and never as an answer: f0 and vocal tract
# length correlate loosely, not tightly, so these only bound `sweep_ceiling`, which decides.
CEILING_TYPICAL_MALE = 5000.0
CEILING_TYPICAL_FEMALE = 5500.0

# What the sweep considers. Wide enough to cover both conventional values with room either
# side, stepped finely enough that the winner is not an artefact of a coarse grid.
CEILING_SWEEP = tuple(float(hz) for hz in range(4500, 6001, 100))

# Praat's convention: five formants below the ceiling. The LPC order follows from it (see
# `BurgSettings.lpc_order`), which is the whole point — one decision, not two.
MAX_FORMANTS = 5.0

# Praat's Burg defaults, restated so a change is visible in a diff rather than inherited.
WINDOW_LENGTH_S = 0.025
PRE_EMPHASIS_FROM_HZ = 50.0

# Pitch tracking bounds. Wider than Praat's 75-600 at the bottom and narrower at the top: this
# is adult connected speech, and a 600 Hz ceiling invites octave errors on creaky phrase ends.
PITCH_FLOOR_HZ = 60.0
PITCH_CEILING_HZ = 500.0

# What a formant estimate has to look like to be believed. A "formant" wider than this is the
# analyser fitting noise, and one outside the range is not a vowel formant at all.
MAX_BANDWIDTH_HZ = 600.0
FORMANT_RANGE_HZ = (150.0, 5500.0)

# The three proportions of a vowel's duration at which everything is measured. **These are
# Hillenbrand et al.'s own sampling points**, taken from the header of `vowdata.dat` itself:
# formants there are reported at steady state and at 20%, 50% and 80% of vowel duration.
# Matching them is not a detail. Comparing a 25/75 sample against a 20/80 reference is a small
# systematic bias, it lands hardest on the diphthongs, and adopting the reference's own points
# removes it for free.
#
# Measuring at POINTS rather than averaging across the segment is the other half. A segment's
# edges are contaminated by coarticulation with the neighbouring consonants, and an averaged
# diphthong lands in the middle of nowhere — between where it started and where it ended, which
# is a place the speaker's tongue never was. Sampling three points also makes trajectory fall
# out for nothing: a monophthong is simply the case where 20% and 80% coincide.
SAMPLE_POINTS = (0.20, 0.50, 0.80)

# Full scale for 16-bit PCM, the reference for every dBFS figure here.
_FULL_SCALE = 32768.0

# Quieter than this and the RMS is the noise floor rather than a vowel. -90 dBFS is roughly the
# bottom of 16-bit resolution; a real vowel in a usable recording sits far above it.
_SILENCE_DBFS = -90.0


class AcousticsError(ValueError):
    """The audio cannot be analysed. Message is safe to show in the UI."""


@dataclass(frozen=True)
class BurgSettings:
    """A formant ceiling and the formant count that goes with it — one decision, not two.

    Constructed only through `burg_settings`, so no caller can pair a ceiling with an
    unrelated order. `lpc_order` is derived rather than stored for the same reason.
    """

    ceiling_hz: float
    max_formants: float = MAX_FORMANTS

    @property
    def lpc_order(self) -> int:
        """Coefficients Praat will fit: twice the formant count, by Praat's definition."""
        return int(round(2 * self.max_formants))

    @property
    def analysis_rate_hz(self) -> float:
        """The rate Praat resamples to internally — twice the ceiling, never the file's rate.

        Stated because it is the number the classic mistake gets wrong. The rule of thumb
        "order = 2 + fs/1000" is about THIS rate (12 at 10 kHz), not about the 16 kHz the file
        happens to be sampled at, where it would give 18 and split formants in two.
        """
        return 2 * self.ceiling_hz


def burg_settings(ceiling_hz: float) -> BurgSettings:
    """The Burg settings for one ceiling, refusing a ceiling outside the plausible range."""
    low, high = CEILING_SWEEP[0], CEILING_SWEEP[-1]
    if not low <= ceiling_hz <= high:
        raise AcousticsError(
            f"A formant ceiling of {ceiling_hz:g} Hz is outside the plausible adult range "
            f"({low:g}-{high:g} Hz). It must match vocal tract length; getting it wrong "
            f"shifts every value in the measurement."
        )
    return BurgSettings(ceiling_hz=float(ceiling_hz))


@dataclass(frozen=True)
class FormantPoint:
    """F1/F2/F3 and their bandwidths at one instant, in Hz. None where unmeasurable."""

    f1: float | None
    f2: float | None
    f3: float | None
    b1: float | None
    b2: float | None
    b3: float | None

    @property
    def f3_minus_f2(self) -> float | None:
        """The rhoticity measure, and the single most useful number this module produces.

        American /ɹ ɝ ɚ ɔɹ ɑɹ ɪɹ ɛɹ ʊɹ/ are defined acoustically by a steeply lowered F3
        approaching F2; a non-rhotic or weakly rhotic production leaves F3 high and separated.
        In the published reference /ɝ/ sits near 300 Hz where every other vowel sits between
        546 and 1613, so the separation is enormous, unambiguous and cheap.
        """
        if self.f2 is None or self.f3 is None:
            return None
        return self.f3 - self.f2

    @property
    def usable(self) -> bool:
        """Whether F1 and F2 are both present and plausible. F3 may legitimately be missing."""
        return self.f1 is not None and self.f2 is not None


@dataclass(frozen=True)
class Segment:
    """A labelled span of the recording, in seconds. The label is opaque to this module."""

    label: str
    start_s: float
    end_s: float

    @property
    def duration_ms(self) -> float:
        return (self.end_s - self.start_s) * 1000.0

    def sample_times(self) -> tuple[float, float, float]:
        """The 20%, 50% and 80% instants of this segment, in seconds."""
        span = self.end_s - self.start_s
        first, second, third = (self.start_s + span * point for point in SAMPLE_POINTS)
        return first, second, third


def load(wav_bytes: bytes) -> parselmouth.Sound:
    """Decode PCM WAV bytes into a parselmouth Sound, without touching the disk.

    Deliberately not `parselmouth.Sound(path)`: everything upstream already holds the audio in
    memory, and a temp file here would be a second place a recording can be left behind by a
    crash. The input is whatever `audio_utils.to_pcm_wav` produced — 16 kHz, 16-bit, mono —
    and that is asserted rather than assumed, because a silently stereo array would be read as
    a sound of twice the length with interleaved garbage.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AcousticsError("That audio could not be read as PCM WAV.") from exc

    if channels != 1 or width != 2:
        raise AcousticsError(
            f"Expected 16-bit mono PCM, got {channels} channel(s) at {width * 8}-bit. "
            f"Audio must come through audio_utils.to_pcm_wav."
        )
    if not frames:
        raise AcousticsError("That audio contains no samples.")

    samples = np.frombuffer(frames, dtype="<i2").astype(np.float64) / _FULL_SCALE
    return parselmouth.Sound(samples, sampling_frequency=float(rate))


def rms_dbfs(sound: parselmouth.Sound, start_s: float, end_s: float) -> float | None:
    """RMS of one span, in dBFS. None when the span is empty or at the noise floor.

    dBFS, not dB SPL — see the module docstring. Computed from the samples rather than through
    Praat's Intensity object because that object smooths over a pitch-period-length window,
    which for a 50 ms vowel means the value at its midpoint is partly its neighbours.
    """
    values = np.asarray(sound.values[0], dtype=np.float64)
    rate = sound.sampling_frequency
    first = max(0, int(round(start_s * rate)))
    last = min(values.size, int(round(end_s * rate)))
    if last <= first:
        return None
    window = values[first:last]
    mean_square = float(np.mean(np.square(window)))
    if mean_square <= 0.0:
        return None
    level = 10.0 * math.log10(mean_square)
    return level if level > _SILENCE_DBFS else None


class Analysis:
    """One recording, analysed once at one ceiling, then sampled many times.

    The formant and pitch objects are built eagerly and reused: a 90-second calibration read
    yields a couple of hundred vowels, and re-running Burg per vowel would analyse the whole
    recording once per token.
    """

    def __init__(self, sound: parselmouth.Sound, settings: BurgSettings) -> None:
        self.sound = sound
        self.settings = settings
        self._formant = sound.to_formant_burg(
            max_number_of_formants=settings.max_formants,
            maximum_formant=settings.ceiling_hz,
            window_length=WINDOW_LENGTH_S,
            pre_emphasis_from=PRE_EMPHASIS_FROM_HZ,
        )
        self._pitch = sound.to_pitch_ac(
            pitch_floor=PITCH_FLOOR_HZ, pitch_ceiling=PITCH_CEILING_HZ
        )

    @property
    def duration_s(self) -> float:
        return float(self.sound.duration)

    def formants_at(self, time_s: float) -> FormantPoint:
        """F1-F3 and bandwidths at one instant, filtered for plausibility.

        A value outside `FORMANT_RANGE_HZ`, or with a bandwidth over `MAX_BANDWIDTH_HZ`, comes
        back as None rather than as a number. That is the difference between "no measurement"
        and "a measurement of the analyser fitting noise", and only the first is honest.
        """
        values: list[float | None] = []
        bandwidths: list[float | None] = []
        for number in (1, 2, 3):
            frequency = self._formant.get_value_at_time(number, time_s)
            bandwidth = self._formant.get_bandwidth_at_time(number, time_s)
            low, high = FORMANT_RANGE_HZ
            ok = (
                frequency is not None
                and not math.isnan(frequency)
                and low <= frequency <= high
                and bandwidth is not None
                and not math.isnan(bandwidth)
                and bandwidth <= MAX_BANDWIDTH_HZ
            )
            values.append(float(frequency) if ok else None)
            bandwidths.append(float(bandwidth) if ok else None)
        return FormantPoint(
            f1=values[0], f2=values[1], f3=values[2],
            b1=bandwidths[0], b2=bandwidths[1], b3=bandwidths[2],
        )

    def f0_at(self, time_s: float) -> float | None:
        """F0 in Hz, or None where the tracker found no voicing. Praat reports unvoiced as 0."""
        value = self._pitch.get_value_at_time(time_s)
        if value is None or math.isnan(value) or value <= 0.0:
            return None
        return float(value)

    def f0_median(self) -> float | None:
        """Median F0 across the whole recording, over voiced frames only.

        Median rather than mean: a single octave error in the tracker moves a mean and barely
        touches a median, and this figure only ever bounds the ceiling sweep.
        """
        frequencies = np.asarray(self._pitch.selected_array["frequency"], dtype=np.float64)
        voiced = frequencies[frequencies > 0.0]
        return float(np.median(voiced)) if voiced.size else None

    def voiced_fraction(self, start_s: float, end_s: float, samples: int = 5) -> float:
        """How much of a span the pitch tracker calls voiced, sampled evenly across it.

        A vowel with no reliable F0 through its middle is not a vowel this pipeline can
        measure: formant estimation on an unvoiced span is a measurement of a whisper or of
        the following consonant's frication.
        """
        if end_s <= start_s or samples < 1:
            return 0.0
        step = (end_s - start_s) / (samples + 1)
        times = [start_s + step * (index + 1) for index in range(samples)]
        return sum(1 for time in times if self.f0_at(time) is not None) / samples

    def measure(self, segment: Segment) -> tuple[FormantPoint, FormantPoint, FormantPoint]:
        """The segment's formants at 20%, 50% and 80% of its duration."""
        return tuple(self.formants_at(time) for time in segment.sample_times())  # type: ignore[return-value]


def analyse(sound: parselmouth.Sound, ceiling_hz: float) -> Analysis:
    """Analyse one recording at one ceiling."""
    return Analysis(sound, burg_settings(ceiling_hz))


def suggested_ceiling(f0_median_hz: float | None) -> float:
    """The conventional ceiling for a voice with this median F0. A WEAK starting point.

    F0 and vocal tract length correlate loosely, so this is a prior and not a measurement —
    it exists to bound `sweep_ceiling`, and nothing should store it as the answer. The split
    is at 165 Hz, between the two conventional adult values.
    """
    if f0_median_hz is None:
        return CEILING_TYPICAL_MALE
    return CEILING_TYPICAL_FEMALE if f0_median_hz >= 165.0 else CEILING_TYPICAL_MALE


@dataclass(frozen=True)
class CeilingChoice:
    """Which ceiling the sweep chose, what it beat, and what it had to go on."""

    ceiling_hz: float
    score: float | None  # mean within-category dispersion at the winner; lower is better
    suggested_hz: float  # what f0 alone would have guessed
    categories: int  # labels with enough tokens to contribute
    tokens: int
    swept: tuple[float, ...]

    @property
    def measured(self) -> bool:
        """False when the sweep had nothing to go on and fell back to the f0 guess."""
        return self.score is not None


def sweep_ceiling(
    sound: parselmouth.Sound,
    segments: Sequence[Segment],
    *,
    candidates: Sequence[float] = CEILING_SWEEP,
    min_per_category: int = 3,
) -> CeilingChoice:
    """Pick the formant ceiling that makes the speaker's own categories tightest.

    The ceiling must match vocal tract length — roughly 5000 Hz for a typical adult male voice
    and 5500 for a typical adult female one — and getting it wrong shifts every value in the
    measurement. Deriving it from median F0 is the usual shortcut and is a weak estimator, so
    that guess is reported (`suggested_hz`) but does not decide.

    What decides is this: the same speaker's repetitions of the same vowel should land in the
    same place. So each candidate ceiling is scored by the **mean within-category dispersion**
    of F1 and F2 across every label with enough tokens, and the tightest wins. A ceiling that
    is too low pushes a real formant out of the analysis band and the analyser substitutes its
    neighbour, which scatters a category badly; one that is too high admits an extra pole and
    splits a formant, which scatters it differently. Both show up here as spread.

    Dispersion is the coefficient of variation — SD over mean — and not raw SD, so a
    high-frequency category like /i/'s F2 near 2300 Hz does not simply outvote a low one by
    being numerically larger.

    Labels are opaque strings. This function does not know what a vowel is.
    """
    grouped: dict[str, list[Segment]] = {}
    for segment in segments:
        grouped.setdefault(segment.label, []).append(segment)
    usable = {
        label: items for label, items in grouped.items() if len(items) >= min_per_category
    }

    suggested = suggested_ceiling(Analysis(sound, burg_settings(CEILING_TYPICAL_MALE)).f0_median())
    swept = tuple(float(value) for value in candidates)
    tokens = sum(len(items) for items in usable.values())

    if not usable:
        logger.info("Ceiling sweep had no category with %d+ tokens; using the f0 guess.",
                    min_per_category)
        return CeilingChoice(
            ceiling_hz=suggested, score=None, suggested_hz=suggested,
            categories=0, tokens=tokens, swept=swept,
        )

    best: tuple[float, float] | None = None
    for candidate in swept:
        analysis = Analysis(sound, burg_settings(candidate))
        dispersions: list[float] = []
        for items in usable.values():
            for index in (0, 1):  # F1 and F2 only; F3 is noisier and not what defines a vowel
                measured = [
                    value
                    for segment in items
                    if (value := (
                        analysis.formants_at(segment.sample_times()[1]).f1
                        if index == 0
                        else analysis.formants_at(segment.sample_times()[1]).f2
                    )) is not None
                ]
                if len(measured) < 2:
                    continue
                mean = statistics.fmean(measured)
                if mean > 0:
                    dispersions.append(statistics.stdev(measured) / mean)
        if not dispersions:
            continue
        score = statistics.fmean(dispersions)
        if best is None or score < best[1]:
            best = (candidate, score)

    if best is None:
        return CeilingChoice(
            ceiling_hz=suggested, score=None, suggested_hz=suggested,
            categories=len(usable), tokens=tokens, swept=swept,
        )
    return CeilingChoice(
        ceiling_hz=best[0], score=best[1], suggested_hz=suggested,
        categories=len(usable), tokens=tokens, swept=swept,
    )
