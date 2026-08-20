"""Change ONE thing about the user's own recording, and play it back.

This is the part that makes the arrow audible, and it is the highest-value surface in the
chunk. `projectbrief.md`'s founding problem is "I can't hear the difference between my
pronunciation and a native speaker's", and playing a TTS voice next to the user's recording
has never fully solved it: **two different voices differ in a hundred ways at once**, and the
listener cannot isolate the one that matters. Pitch, timbre, rate, room, microphone and accent
all change together, so "they sound different" carries no information about which difference
is the one to fix.

The fix is to change one dimension of the user's OWN recording and play that back. Same voice,
same room, same everything — one variable moved. What is left is the finding, and it is
audible without any training in phonetics.

Three surfaces, in descending order of value:

- **Corrected pitch.** The user's own voice with a native intonation contour. Nothing else in
  this project demonstrates a prosody error so immediately.
- **Corrected timing.** Under-reduction and missing pre-fortis clipping become audible rather
  than tabular.
- **Corrected vowel.** One flagged vowel shifted toward its target and the rest of the
  utterance untouched. The narrowest and most convincing, and the most fragile.

## The rules, and why each one is a rule

**Always play the ORIGINAL immediately before the modified version, in that order, labelled.**
A modified clip heard alone teaches nothing — the listener has nothing to difference it
against, and will hear whatever they expected to hear. `app.py` enforces the ordering and
`tests/test_accent.py` asserts it.

**Cap every manipulation.** Past a certain excursion PSOLA produces artefacts the ear reads as
"robot" rather than as "native" — and a learner who concludes that native intonation sounds
robotic has been taught the exact opposite of the lesson. Every function reports `capped` when
it hit its limit, and the surface says so.

**Say on the surface that this is the user's own voice, modified.** A synthetic-sounding clip
that the user believes is a native model is actively misleading, and would undo the entire
reason for resynthesising rather than synthesising.

**Transient.** Generated in the request, played, never written to disk — like every other
synthesised clip in this project.

## Built on `parselmouth.praat.call`, because there is no typed surface

`progress.md` records planning this against a typed `parselmouth.Manipulation` class as a dead
end: it does not exist. The bindings are Sound, Pitch, Formant, Intensity and Spectrum, and
PSOLA resynthesis, PitchTier replacement and DurationTier time-scaling are reachable only
through the untyped `call(...)`. Verified against the installed 0.4.7, not recalled.

No Streamlit, no network, no database.
"""

from __future__ import annotations

import io
import logging
import math
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import parselmouth
from parselmouth.praat import call

import acoustics

logger = logging.getLogger(__name__)

# PSOLA's analysis grid. 10 ms is Praat's own default and matches the pitch step used
# everywhere else in this project.
_TIME_STEP_S = 0.01

# How far a single pitch point may be moved from where the speaker actually put it. Six
# semitones is a musical fifth: far enough to carry any intonation contour English uses, and
# inside the range where overlap-add stays clean on a 16 kHz recording. Past roughly an octave
# PSOLA starts to buzz, and a buzzing clip teaches "native intonation sounds robotic".
MAX_PITCH_SHIFT_SEMITONES = 6.0

# Time-scaling limits. Beyond about 1.5x in either direction the overlap-add seams become
# audible as a warble, which is heard as a defect in the SPEAKER rather than as a correction.
MAX_DURATION_SCALE = 1.5
MIN_DURATION_SCALE = 1.0 / MAX_DURATION_SCALE

# How far toward the target a formant shift may go. The most conservative cap of the three,
# because it is the most fragile manipulation: `Change gender` resamples the spectral envelope,
# and a large shift sounds synthetic long before it sounds like a different accent. A third of
# the way is audible as a direction without ever sounding like somebody else.
MAX_FORMANT_FRACTION = 1.0 / 3.0

# What the surface must say. Not decoration — a synthetic-sounding clip the user believes is a
# native model is actively misleading, and this is the sentence that prevents it.
OWN_VOICE_NOTICE = (
    "**This is your own recording, modified** — not a native speaker and not a synthesised "
    "voice. Exactly one thing has been changed and everything else is untouched, which is what "
    "makes the difference you hear the finding rather than a difference between two people."
)

ORIGINAL_LABEL = "Original — what you said"
CAPPED_NOTICE = (
    "The correction was **capped**: the full distance to the target would have pushed the "
    "resynthesis into artefacts that sound robotic, which teaches the wrong lesson. What you "
    "are hearing is the largest change that still sounds like speech."
)


class ResynthesisError(RuntimeError):
    """The manipulation cannot be done. Message is safe to show in the UI."""


@dataclass(frozen=True)
class Resynthesis:
    """One modified clip, and the single thing that was modified."""

    audio: bytes
    changed: str  # the one variable moved, named for the surface
    capped: bool
    note: str
    # How much of the requested correction was actually applied, where that is a single
    # number: the formant shift ratio for `corrected_vowel`. None where the manipulation is a
    # whole contour and no one number describes it. Exposed rather than left implicit because
    # it is the only thing that makes a cap testable without re-measuring the audio — and
    # re-measuring a large shift runs into the formant tracker's own limits, which is a
    # different question from whether the cap held.
    applied_ratio: float | None = None

    @property
    def label(self) -> str:
        return f"Modified — {self.changed}"


# --- Plumbing ---------------------------------------------------------------------------------


def _sound(wav_bytes: bytes) -> parselmouth.Sound:
    return acoustics.load(wav_bytes)


def to_wav_bytes(sound: parselmouth.Sound) -> bytes:
    """A parselmouth Sound as 16-bit PCM WAV, scaled so it cannot clip.

    Praat's own `Sound.save` warns and clips when a resynthesis overshoots full scale, which
    PSOLA routinely does at the seams. Scaling first is the difference between a clean clip and
    one whose "artefacts" are the writer's rather than the manipulation's.
    """
    samples = np.asarray(sound.values, dtype=np.float64)
    if samples.ndim > 1:
        samples = samples[0]
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 0.99:
        samples = samples * (0.99 / peak)

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(round(sound.sampling_frequency))
        handle.writeframes((np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def _manipulation(sound: parselmouth.Sound) -> Any:
    return call(
        sound,
        "To Manipulation",
        _TIME_STEP_S,
        acoustics.PITCH_FLOOR_HZ,
        acoustics.PITCH_CEILING_HZ,
    )


def _median_f0(sound: parselmouth.Sound) -> float | None:
    pitch = sound.to_pitch(
        time_step=_TIME_STEP_S,
        pitch_floor=acoustics.PITCH_FLOOR_HZ,
        pitch_ceiling=acoustics.PITCH_CEILING_HZ,
    )
    voiced = pitch.selected_array["frequency"]
    voiced = voiced[voiced > 0]
    return float(np.median(voiced)) if voiced.size else None


def pitch_track(wav_bytes: bytes) -> list[tuple[float, float]]:
    """(time, hertz) for every voiced frame. What both the overlay and the correction read."""
    sound = _sound(wav_bytes)
    pitch = sound.to_pitch(
        time_step=_TIME_STEP_S,
        pitch_floor=acoustics.PITCH_FLOOR_HZ,
        pitch_ceiling=acoustics.PITCH_CEILING_HZ,
    )
    frequencies = pitch.selected_array["frequency"]
    return [(float(time), float(hz)) for time, hz in zip(pitch.xs(), frequencies) if hz > 0]


def semitones(hz: float, reference_hz: float) -> float:
    """Hertz as semitones relative to a reference. The only honest unit for two voices.

    Hertz is a statement about a person's larynx; semitones relative to that person's OWN
    median is a statement about their intonation. Overlaying two speakers in hertz shows the
    trivial fact that they have different voices and hides the contour entirely.
    """
    if hz <= 0 or reference_hz <= 0:
        return 0.0
    return 12.0 * math.log2(hz / reference_hz)


# --- Corrected pitch ----------------------------------------------------------------------------


def corrected_pitch(
    wav_bytes: bytes,
    target: Sequence[tuple[float, float]],
    *,
    max_shift: float = MAX_PITCH_SHIFT_SEMITONES,
) -> Resynthesis:
    """The user's own voice carrying a native intonation contour.

    `target` is (time in seconds on the USER's clock, semitones relative to the model's own
    median) — already aligned by `accent_charts`, which anchors on word offsets rather than
    warping with DTW. Semitones, never hertz: the contour is re-expressed against the USER's
    median here, so what transfers is the SHAPE and not the model's larynx.

    The contour is replaced rather than blended a third of the way, because a third of an
    intonation correction is inaudible and an inaudible demonstration is not one. What is
    capped instead is how far any single point may move from where the speaker actually put it
    — `max_shift` semitones — which is what keeps overlap-add clean.
    """
    if not target:
        raise ResynthesisError(
            "There is no model contour to apply. Capture the model's reading of this text "
            "first — without it there is nothing to correct toward."
        )
    sound = _sound(wav_bytes)
    own_median = _median_f0(sound)
    if own_median is None:
        raise ResynthesisError(
            "No pitch could be tracked in this recording, so there is no contour to replace. "
            "A whispered or very quiet take will do this."
        )

    manipulation = _manipulation(sound)
    tier = call("Create PitchTier", "target", 0.0, sound.duration)
    capped = False
    points = 0

    for time_s, target_st in target:
        if not 0.0 <= time_s <= sound.duration:
            continue
        own_hz = _hz_at(sound, time_s)
        own_st = semitones(own_hz, own_median) if own_hz else 0.0
        shift = target_st - own_st
        if abs(shift) > max_shift:
            shift = math.copysign(max_shift, shift)
            capped = True
        new_hz = own_median * (2.0 ** ((own_st + shift) / 12.0))
        call(tier, "Add point", float(time_s), float(new_hz))
        points += 1

    if points == 0:
        raise ResynthesisError("The model contour does not overlap this recording in time.")

    call([tier, manipulation], "Replace pitch tier")
    resynthesised = call(manipulation, "Get resynthesis (overlap-add)")
    return Resynthesis(
        audio=to_wav_bytes(resynthesised),
        changed="native intonation, your voice",
        capped=capped,
        note=CAPPED_NOTICE if capped else "",
    )


def _hz_at(sound: parselmouth.Sound, time_s: float) -> float | None:
    pitch = sound.to_pitch(
        time_step=_TIME_STEP_S,
        pitch_floor=acoustics.PITCH_FLOOR_HZ,
        pitch_ceiling=acoustics.PITCH_CEILING_HZ,
    )
    value = pitch.get_value_at_time(time_s)
    return None if value is None or math.isnan(value) or value <= 0 else float(value)


# --- Corrected timing -------------------------------------------------------------------------


def corrected_timing(
    wav_bytes: bytes,
    stretches: Sequence[tuple[float, float, float]],
    *,
    minimum: float = MIN_DURATION_SCALE,
    maximum: float = MAX_DURATION_SCALE,
) -> Resynthesis:
    """Stretch the under-long vowels and compress the over-long ones toward the target.

    `stretches` is (start_s, end_s, ratio) per vowel — ratio above 1 lengthens. This is what
    turns under-reduction and missing pre-fortis clipping from a table row into something the
    ear catches: the numbers say a vowel is 40 ms too long, and 40 ms is meaningless until it
    is heard in place.

    A DurationTier interpolates linearly between its points, so each span gets **four** points
    — 1.0 just outside each edge and the ratio just inside — which is what makes a step rather
    than a ramp across the whole utterance.
    """
    if not stretches:
        raise ResynthesisError("No vowel in this recording has a duration target to move toward.")

    sound = _sound(wav_bytes)
    manipulation = _manipulation(sound)
    tier = call(manipulation, "Extract duration tier")
    edge = _TIME_STEP_S / 2.0
    capped = False
    applied = 0

    for start_s, end_s, ratio in sorted(stretches):
        if end_s <= start_s or start_s < 0 or end_s > sound.duration:
            continue
        clamped = min(max(ratio, minimum), maximum)
        if clamped != ratio:
            capped = True
        if abs(clamped - 1.0) < 1e-3:
            continue
        call(tier, "Add point", max(start_s - edge, 0.0), 1.0)
        call(tier, "Add point", start_s + edge, float(clamped))
        call(tier, "Add point", max(end_s - edge, start_s + edge), float(clamped))
        call(tier, "Add point", min(end_s + edge, sound.duration), 1.0)
        applied += 1

    if applied == 0:
        raise ResynthesisError(
            "Every vowel is already within its duration target, so there is nothing to "
            "demonstrate. That is a result, not a failure."
        )

    call([tier, manipulation], "Replace duration tier")
    resynthesised = call(manipulation, "Get resynthesis (overlap-add)")
    return Resynthesis(
        audio=to_wav_bytes(resynthesised),
        changed=f"General American vowel lengths ({applied} vowels), your voice",
        capped=capped,
        note=CAPPED_NOTICE if capped else "",
    )


# --- Corrected vowel --------------------------------------------------------------------------


def corrected_vowel(
    wav_bytes: bytes,
    start_s: float,
    end_s: float,
    produced_f2_hz: float,
    target_f2_hz: float,
    *,
    fraction: float = MAX_FORMANT_FRACTION,
) -> Resynthesis:
    """Shift ONE vowel's formants toward its target and leave the rest of the utterance alone.

    The narrowest and most convincing of the three, and the most fragile. The whole point is
    that the words on either side are bit-identical: whatever the listener hears change is the
    vowel, because nothing else could be.

    `Change gender` with a new pitch median of 0 keeps the speaker's pitch and moves only the
    spectral envelope, which is what makes this a vowel correction rather than a voice swap.
    The shift is capped at a third of the distance to the target — the most conservative of the
    three caps, because a large envelope shift sounds synthetic long before it sounds like a
    different accent.
    """
    sound = _sound(wav_bytes)
    if not 0.0 <= start_s < end_s <= sound.duration:
        raise ResynthesisError("That vowel's span falls outside the recording.")
    if produced_f2_hz <= 0 or target_f2_hz <= 0:
        raise ResynthesisError("That vowel has no usable formant measurement to shift.")

    full = target_f2_hz / produced_f2_hz
    ratio = 1.0 + (full - 1.0) * fraction
    capped = abs(full - 1.0) > abs(ratio - 1.0) + 1e-9

    # A second reason the cap is conservative, beyond "large shifts sound synthetic": shifting
    # the whole envelope moves the UPPER formants too, and a big enough ratio pushes F5 past
    # the analysis ceiling. Past that point the pole the tracker had been spending on F5 goes
    # somewhere else and every formant below it shifts a slot — so a large shift stops being
    # measurable at the same time as it stops being convincing.

    before = call(sound, "Extract part", 0.0, start_s, "rectangular", 1.0, "no")
    middle = call(sound, "Extract part", start_s, end_s, "rectangular", 1.0, "no")
    after = call(sound, "Extract part", end_s, sound.duration, "rectangular", 1.0, "no")
    shifted = call(
        middle,
        "Change gender",
        acoustics.PITCH_FLOOR_HZ,
        acoustics.PITCH_CEILING_HZ,
        float(ratio),  # formant shift ratio
        0.0,  # new pitch median: 0 keeps the speaker's own pitch untouched
        1.0,  # pitch range factor: unchanged
        1.0,  # duration factor: unchanged
    )
    joined = call([before, shifted, after], "Concatenate")

    direction = "further front" if ratio > 1 else "further back or more rounded"
    return Resynthesis(
        audio=to_wav_bytes(joined),
        changed=f"one vowel shifted {direction}, everything else untouched",
        capped=capped,
        applied_ratio=ratio,
        note=(
            f"Shifted {fraction:.0%} of the way to the target — the rest of the utterance is "
            f"bit-identical, so anything you hear change is that one vowel."
        ),
    )
