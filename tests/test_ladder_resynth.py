"""Corrections applied to one rung's span rather than to a whole recording.

Against synthesised signals with a KNOWN pitch and known formants, the same choice
`test_accent_resynth.py` makes and for the same reason: "did it correct the right part and
leave the rest alone" is a real assertion against a known, not a comparison of two estimates.

The property that matters most here is **containment** — a rung-scale correction must return
the rung, not the recording. Getting that wrong produces audio that still sounds plausible,
which is exactly the failure that has to be caught by a test rather than by an ear.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

import accent_resynth
import ladder
import ladder_practice

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import synth_vowel, to_wav_bytes

STEADY_F0 = 120.0
VOWEL_FORMANTS = (500.0, 1500.0, 2500.0)
SECONDS = 3.0


@pytest.fixture(scope="module")
def steady() -> bytes:
    return to_wav_bytes(synth_vowel(VOWEL_FORMANTS, SECONDS, f0=STEADY_F0))


def _span(start: float, end: float, rung: ladder.Rung = ladder.Rung.SENTENCE) -> ladder.Span:
    return ladder.Span(rung=rung, label="unit", start_s=start, end_s=end, word_indices=(0,))


def seconds_of(wav_bytes: bytes) -> float:
    import acoustics

    return float(acoustics.load(wav_bytes).duration)


def median_f0(wav_bytes: bytes) -> float:
    return float(np.median([hz for _, hz in accent_resynth.pitch_track(wav_bytes)]))


# --- Containment: the correction returns the unit, not the recording ------------------------------


def test_a_pitch_correction_returns_the_span_not_the_whole_recording(steady) -> None:
    span = _span(1.0, 1.8)
    target = [(t / 100.0, 4.0) for t in range(100, 181)]
    result = ladder_practice.corrected_pitch_in(steady, span, target)
    assert seconds_of(result.audio) == pytest.approx(0.8 + 2 * ladder.PAD_S, abs=0.05)
    assert seconds_of(result.audio) < SECONDS


def test_a_span_at_the_very_start_is_still_bounded(steady) -> None:
    """The Extract part trap in its usual disguise: a plausible-sounding whole recording."""
    span = _span(0.0, 0.6)
    target = [(t / 100.0, 4.0) for t in range(0, 61)]
    result = ladder_practice.corrected_pitch_in(steady, span, target)
    assert seconds_of(result.audio) < SECONDS / 2


def test_a_span_at_the_very_end_is_still_bounded(steady) -> None:
    span = _span(SECONDS - 0.6, SECONDS)
    target = [(t / 100.0, 4.0) for t in range(240, 301)]
    result = ladder_practice.corrected_pitch_in(steady, span, target)
    assert seconds_of(result.audio) < SECONDS / 2


def test_a_timing_correction_returns_the_span_not_the_recording(steady) -> None:
    span = _span(1.0, 2.0)
    result = ladder_practice.corrected_timing_in(steady, span, [(1.2, 1.6, 1.3)])
    # Stretching 0.4s by 1.3 adds 0.12s to a ~1.04s cut, not to the 3s recording.
    assert seconds_of(result.audio) < SECONDS


def test_a_vowel_correction_returns_the_span_not_the_recording(steady) -> None:
    span = _span(1.0, 2.0, ladder.Rung.WORD)
    result = ladder_practice.corrected_vowel_in(steady, span, 1.3, 1.6, 1500.0, 1800.0)
    assert seconds_of(result.audio) == pytest.approx(1.0 + 2 * ladder.PAD_S, abs=0.05)


# --- The correction lands on the right part of the clock -----------------------------------------


def test_the_target_contour_is_rebased_onto_the_cut(steady) -> None:
    """Full-clock times against a one-second cut would all fall past its end.

    That failure surfaces as "the model contour does not overlap this recording", which reads
    like a missing capture rather than the arithmetic error it actually is.
    """
    span = _span(1.0, 2.0)
    target = [(t / 100.0, 6.0) for t in range(100, 201)]
    result = ladder_practice.corrected_pitch_in(steady, span, target)
    # +6 semitones from a flat 120 Hz is about 170 Hz. If the rebase were wrong, no point
    # would have applied and the pitch would still read 120.
    assert median_f0(result.audio) > STEADY_F0 * 1.2


def test_a_contour_that_misses_the_span_entirely_refuses(steady) -> None:
    span = _span(2.0, 2.8)
    target = [(t / 100.0, 4.0) for t in range(0, 51)]  # all before the span
    with pytest.raises(accent_resynth.ResynthesisError):
        ladder_practice.corrected_pitch_in(steady, span, target)


def test_a_stretch_outside_the_span_is_dropped_rather_than_clipped(steady) -> None:
    """Stretching half a vowel would claim a correction the listener never hears."""
    span = _span(1.0, 1.5)
    with pytest.raises(accent_resynth.ResynthesisError):
        ladder_practice.corrected_timing_in(steady, span, [(2.2, 2.6, 1.3)])


def test_a_vowel_outside_the_unit_refuses_rather_than_correcting_the_wrong_sound(steady) -> None:
    span = _span(1.0, 1.5, ladder.Rung.WORD)
    with pytest.raises(accent_resynth.ResynthesisError):
        ladder_practice.corrected_vowel_in(steady, span, 2.2, 2.4, 1500.0, 1800.0)


# --- Still one thing at a time -------------------------------------------------------------------


def test_each_correction_names_exactly_what_it_changed(steady) -> None:
    """'Your voice, one thing changed' has to stay literally true — there is no stacking."""
    span = _span(1.0, 2.0)
    pitch = ladder_practice.corrected_pitch_in(
        steady, span, [(t / 100.0, 4.0) for t in range(100, 201)]
    )
    timing = ladder_practice.corrected_timing_in(steady, span, [(1.2, 1.6, 1.3)])
    assert pitch.changed != timing.changed
    assert "intonation" in pitch.changed


def test_the_caps_still_apply_at_rung_scale(steady) -> None:
    """A span-scale correction must not become a way around the caps."""
    span = _span(1.0, 2.0)
    absurd = [(t / 100.0, 40.0) for t in range(100, 201)]
    result = ladder_practice.corrected_pitch_in(steady, span, absurd)
    assert result.capped
    assert result.note
