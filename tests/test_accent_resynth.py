"""Changing one thing about the user's own recording, and only that thing.

Tested against synthesised signals rather than a committed recording, and that is the stronger
choice rather than a compromise. `.gitignore` and `tests/conftest.py` both hold that no
recording is ever committed — but more to the point, a synthesised signal has a **known** F0
and known formants, so "did PSOLA move the pitch to the target and leave the formants alone"
is a real assertion instead of a comparison against another estimate of the same unknown.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

import accent_resynth

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import SAMPLE_RATE, synth_vowel, to_wav_bytes

# A steady vowel with known formants and a known, flat pitch. Every assertion below is against
# one of those two knowns.
STEADY_F0 = 120.0
VOWEL_FORMANTS = (500.0, 1500.0, 2500.0)
SECONDS = 1.2


@pytest.fixture(scope="module")
def steady() -> bytes:
    return to_wav_bytes(synth_vowel(VOWEL_FORMANTS, SECONDS, f0=STEADY_F0))


def measured_f0(wav_bytes: bytes) -> list[float]:
    return [hz for _, hz in accent_resynth.pitch_track(wav_bytes)]


def measured_formants(wav_bytes: bytes) -> tuple[float | None, float | None]:
    import acoustics

    sound = acoustics.load(wav_bytes)
    analysis = acoustics.analyse(sound, 5000.0)
    point = analysis.formants_at(sound.duration / 2)
    return point.f1, point.f2


def seconds_of(wav_bytes: bytes) -> float:
    import acoustics

    return float(acoustics.load(wav_bytes).duration)


# --- The signal really is what the tests assume ------------------------------------------------


def test_the_fixture_has_the_flat_pitch_and_formants_the_tests_rest_on(steady) -> None:
    track = measured_f0(steady)
    assert track, "no pitch was tracked in the fixture at all"
    assert abs(float(np.median(track)) - STEADY_F0) < 3.0
    f1, f2 = measured_formants(steady)
    assert f1 is not None and f2 is not None
    assert abs(f1 - 500.0) / 500.0 < 0.06
    assert abs(f2 - 1500.0) / 1500.0 < 0.06


# --- Corrected pitch ------------------------------------------------------------------------------


def rising_contour(seconds: float = SECONDS, span: float = 4.0):
    """A contour rising `span` semitones across the clip, in the target's own semitone space."""
    steps = int(seconds / 0.02)
    return [(index * 0.02, span * index / max(steps - 1, 1)) for index in range(steps)]


def test_corrected_pitch_moves_the_contour_and_keeps_the_voice(steady) -> None:
    """The whole claim of this surface: same voice, different intonation.

    "Same voice" is not a figure of speech here — it is testable. The formants are what carry
    vocal identity, so if they survive the manipulation unchanged the listener really is
    hearing themselves, which is the entire reason this beats playing a TTS clip alongside.
    """
    result = accent_resynth.corrected_pitch(steady, rising_contour())
    assert not result.capped
    assert "your voice" in result.changed

    track = accent_resynth.pitch_track(result.audio)
    assert len(track) > 20
    early = [hz for time, hz in track if time < 0.3]
    late = [hz for time, hz in track if time > SECONDS - 0.4]
    assert early and late
    rise = accent_resynth.semitones(float(np.median(late)), float(np.median(early)))
    assert rise > 2.0, f"the contour only rose {rise:.2f} semitones; it should follow the target"

    # The identity survives: formants within the tracker's own tolerance of the original.
    before_f1, before_f2 = measured_formants(steady)
    after_f1, after_f2 = measured_formants(result.audio)
    assert None not in (before_f1, before_f2, after_f1, after_f2)
    # A new voice is exactly what this surface must NOT produce.
    assert abs(after_f1 - before_f1) / before_f1 < 0.08, "PSOLA moved F1"  # type: ignore[operator]
    assert abs(after_f2 - before_f2) / before_f2 < 0.08, "PSOLA moved F2"  # type: ignore[operator]

    # And it is still the same length. Pitch is the ONE variable.
    assert abs(seconds_of(result.audio) - seconds_of(steady)) < 0.02


def test_an_absurd_target_is_capped_rather_than_rendered_as_a_robot(steady) -> None:
    """Past a certain excursion PSOLA buzzes, and a buzzing clip teaches the wrong lesson.

    A learner who concludes that native intonation sounds robotic has been taught the exact
    opposite of the thing this surface exists to demonstrate.
    """
    absurd = [(index * 0.02, 40.0) for index in range(int(SECONDS / 0.02))]
    result = accent_resynth.corrected_pitch(steady, absurd)
    assert result.capped
    assert "capped" in result.note.lower()

    moved = accent_resynth.semitones(float(np.median(measured_f0(result.audio))), STEADY_F0)
    assert moved <= accent_resynth.MAX_PITCH_SHIFT_SEMITONES + 1.0, (
        f"the cap did not hold: the clip moved {moved:.1f} semitones"
    )


def test_no_model_contour_is_refused_rather_than_invented(steady) -> None:
    with pytest.raises(accent_resynth.ResynthesisError, match="no model contour"):
        accent_resynth.corrected_pitch(steady, [])


def test_a_contour_that_does_not_overlap_the_recording_is_refused(steady) -> None:
    with pytest.raises(accent_resynth.ResynthesisError, match="overlap"):
        accent_resynth.corrected_pitch(steady, [(99.0, 2.0), (100.0, 3.0)])


# --- Corrected timing ----------------------------------------------------------------------------


def test_corrected_timing_stretches_only_the_span_it_was_given(steady) -> None:
    """A DurationTier interpolates, so a naive two-point tier ramps the WHOLE utterance.

    Four points per span is what makes it a step. The check is arithmetic: stretching 0.4s of a
    1.2s clip by 1.4x should add about 0.16s and nothing else should move.
    """
    result = accent_resynth.corrected_timing(steady, [(0.4, 0.8, 1.4)])
    assert not result.capped
    grew = seconds_of(result.audio) - seconds_of(steady)
    assert 0.10 < grew < 0.22, f"the clip grew {grew:.3f}s; 0.4s at 1.4x should add ~0.16s"


def test_a_compression_shortens_it(steady) -> None:
    result = accent_resynth.corrected_timing(steady, [(0.4, 0.8, 0.7)])
    assert seconds_of(result.audio) < seconds_of(steady)


def test_an_extreme_stretch_is_capped(steady) -> None:
    """Beyond ~1.5x the overlap-add seams warble, and that is heard as the speaker's defect."""
    result = accent_resynth.corrected_timing(steady, [(0.2, 1.0, 6.0)])
    assert result.capped
    grew = seconds_of(result.audio) / seconds_of(steady)
    assert grew < accent_resynth.MAX_DURATION_SCALE, f"the cap did not hold: {grew:.2f}x"


def test_nothing_to_correct_is_reported_as_a_result_not_a_failure(steady) -> None:
    """ "Every vowel is already on target" is the outcome the user is working toward."""
    with pytest.raises(accent_resynth.ResynthesisError, match="not a failure"):
        accent_resynth.corrected_timing(steady, [(0.4, 0.8, 1.0)])
    with pytest.raises(accent_resynth.ResynthesisError, match=r"(?i)no vowel"):
        accent_resynth.corrected_timing(steady, [])


# --- Corrected vowel -----------------------------------------------------------------------------


def test_corrected_vowel_changes_the_span_and_leaves_the_rest_bit_identical(steady) -> None:
    """The narrowest surface, and its claim is exact: everything outside the span is untouched.

    Asserted on the samples themselves, not on a measurement. If the audio either side is
    bit-identical then whatever the listener hears change can only be that one vowel — which is
    the entire argument for this surface over playing a whole synthesised word.
    """
    import acoustics

    result = accent_resynth.corrected_vowel(steady, 0.4, 0.8, 1500.0, 1900.0)
    original = np.asarray(acoustics.load(steady).values)[0]
    modified = np.asarray(acoustics.load(result.audio).values)[0]
    assert len(original) == len(modified)

    edge = int(0.35 * SAMPLE_RATE)
    assert np.allclose(original[:edge], modified[:edge], atol=1e-4), "audio before the vowel moved"
    tail = int(0.85 * SAMPLE_RATE)
    assert np.allclose(original[tail:], modified[tail:], atol=1e-4), "audio after the vowel moved"
    assert not np.allclose(
        original[int(0.5 * SAMPLE_RATE) : int(0.7 * SAMPLE_RATE)],
        modified[int(0.5 * SAMPLE_RATE) : int(0.7 * SAMPLE_RATE)],
        atol=1e-4,
    ), "the vowel itself did not change"


def test_a_realistic_correction_lands_a_third_of_the_way_toward_the_target(steady) -> None:
    """The audible half of the claim, at a shift size a real correction actually asks for.

    1500 -> 1800 Hz is a large F2 correction by the standards of the four-column table. A third
    of it is ~100 Hz, which is audible as a direction and nowhere near enough to sound like
    somebody else.
    """
    result = accent_resynth.corrected_vowel(steady, 0.3, 0.9, 1500.0, 1800.0)
    assert result.applied_ratio == pytest.approx(1.0 + 0.2 / 3.0, rel=1e-6)

    _, f2 = measured_formants(result.audio)
    assert f2 is not None
    assert 1530 < f2 < 1700, f"F2 landed at {f2:.0f} Hz; a third of the way is ~1600 Hz"


def test_the_formant_shift_is_capped_at_a_third_of_the_distance(steady) -> None:
    """The most conservative of the three caps, because it is the most fragile manipulation.

    Asserted on the ratio actually applied rather than on a re-measured formant, and that is
    not a dodge — it is the same effect the cap exists for. Shifting the envelope by a third
    of a 2x request moves F5 past the analysis ceiling, at which point the tracker loses a
    slot and re-measurement stops answering the question. A shift big enough to be hard to
    measure is a shift big enough to sound synthetic.
    """
    result = accent_resynth.corrected_vowel(steady, 0.3, 0.9, 1500.0, 3000.0)
    assert result.capped
    assert f"{accent_resynth.MAX_FORMANT_FRACTION:.0%}" in result.note
    # A third of the way from 1500 toward 3000 is 2000 Hz, i.e. a ratio of 4/3 — not 2.0.
    assert result.applied_ratio == pytest.approx(4.0 / 3.0, rel=1e-6)


def test_an_exact_target_needs_no_cap(steady) -> None:
    """`capped` must mean "we held back", not "we did something"."""
    result = accent_resynth.corrected_vowel(steady, 0.3, 0.9, 1500.0, 1500.0)
    assert not result.capped
    assert result.applied_ratio == pytest.approx(1.0)


def test_a_span_outside_the_recording_is_refused(steady) -> None:
    with pytest.raises(accent_resynth.ResynthesisError, match="outside the recording"):
        accent_resynth.corrected_vowel(steady, 5.0, 6.0, 1500.0, 1900.0)


def test_a_vowel_with_no_formant_measurement_is_refused(steady) -> None:
    with pytest.raises(accent_resynth.ResynthesisError, match="no usable formant"):
        accent_resynth.corrected_vowel(steady, 0.3, 0.6, 0.0, 1900.0)


# --- What the surface must always say -----------------------------------------------------------


def test_every_result_names_the_one_thing_it_changed(steady) -> None:
    """A clip labelled only "modified" leaves the listener guessing what to listen for."""
    results = [
        accent_resynth.corrected_pitch(steady, rising_contour()),
        accent_resynth.corrected_timing(steady, [(0.4, 0.8, 1.3)]),
        accent_resynth.corrected_vowel(steady, 0.4, 0.8, 1500.0, 1700.0),
    ]
    for result in results:
        assert result.changed.strip()
        assert result.label.startswith("Modified — ")
        assert result.audio.startswith(b"RIFF")


def test_the_own_voice_notice_says_it_is_the_user_and_not_a_native_speaker() -> None:
    """A synthetic-sounding clip the user believes is a native model is actively misleading."""
    notice = accent_resynth.OWN_VOICE_NOTICE.lower()
    assert "your own recording" in notice
    assert "not a native speaker" in notice
    assert "modified" in notice


def test_semitones_are_relative_and_hertz_free() -> None:
    """The only honest unit for two voices: an octave is 12 semitones whoever is speaking."""
    assert accent_resynth.semitones(240.0, 120.0) == pytest.approx(12.0)
    assert accent_resynth.semitones(120.0, 240.0) == pytest.approx(-12.0)
    assert accent_resynth.semitones(100.0, 100.0) == 0.0
    # A low voice and a high voice with the SAME contour shape come out identical.
    assert accent_resynth.semitones(110.0, 100.0) == pytest.approx(
        accent_resynth.semitones(220.0, 200.0)
    )


# --- The ordering rule, as the app enforces it -------------------------------------------------


def test_the_app_plays_the_original_before_the_modified_clip() -> None:
    """A modified clip heard alone teaches nothing — there is nothing to difference it against.

    Asserted against the source rather than a rendered page because Streamlit's `AppTest` does
    not expose audio widgets. What matters is the ORDER of the two `st.audio` calls and that
    both are labelled, and that is visible right here.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "src" / "app.py").read_text("utf-8")
    body = source[source.index("def render_resynthesis(") :]
    body = body[: body.index("\ndef _duration_stretches")]

    original = body.index("ORIGINAL_LABEL")
    modified = body.index("result.label")
    assert original < modified, "the modified clip is offered before the original"
    assert body.index("st.audio(wav_bytes") < body.index("st.audio(result.audio")
    assert "OWN_VOICE_NOTICE" in body, "the surface does not say it is the user's own voice"
