"""Formant, F0 and intensity measurement, against signals whose answers are known."""

from __future__ import annotations

import math

import pytest
from conftest import SAMPLE_RATE, synth_noise, synth_vowel, to_wav_bytes

import acoustics

# Hillenbrand's adult-male F1/F2/F3 means at the 50% point, which is what the pipeline is
# ultimately asked to recover. Copied here as TEST INPUT, not as reference data — the real
# table is generated into `vowel_reference.py` and a separate test checks these agree with it.
MEN_F123 = {
    "i": (340.0, 2338.0, 2994.0),
    "ɪ": (459.0, 1941.0, 2641.0),
    "ɛ": (592.0, 1774.0, 2602.0),
    "æ": (613.0, 1863.0, 2566.0),
    "ɑ": (757.0, 1326.0, 2523.0),
    "ɔ": (670.0, 1046.0, 2509.0),
    "ʌ": (618.0, 1243.0, 2549.0),
    "ʊ": (483.0, 1208.0, 2438.0),
    "u": (375.0, 971.0, 2359.0),
    "ɝ": (460.0, 1406.0, 1704.0),
}

# Measured across all ten vowels when this test was written: worst case 4.0%, most under 2%.
# The gate is set at 6% so ordinary variation does not fail the build, while a real regression
# — a wrong ceiling, a broken resampling, a slipped formant slot — moves things far more.
TOLERANCE = 0.06


def _analysis(formants: tuple[float, ...], seconds: float = 0.30, ceiling: float = 5000.0):
    signal = synth_vowel(formants, seconds)
    return acoustics.analyse(acoustics.load(to_wav_bytes(signal)), ceiling)


# --- The ceiling and the order are one decision ------------------------------------------


def test_lpc_order_follows_from_the_formant_count() -> None:
    """The classic LPC formant error is setting bandwidth and order independently."""
    settings = acoustics.burg_settings(5000.0)
    assert settings.lpc_order == 2 * settings.max_formants
    # And the order belongs to the RESAMPLED rate, not the file's. This is the number the
    # mistake gets wrong: "2 + fs/1000" is 12 at 10 kHz and 18 at the 16 kHz of the file.
    assert settings.analysis_rate_hz == 2 * settings.ceiling_hz
    assert settings.analysis_rate_hz == 10_000.0


@pytest.mark.parametrize("ceiling", [3000.0, 8000.0, 0.0])
def test_an_implausible_ceiling_is_refused(ceiling: float) -> None:
    with pytest.raises(acoustics.AcousticsError, match="vocal tract length"):
        acoustics.burg_settings(ceiling)


def test_the_f0_guess_is_only_a_guess() -> None:
    assert acoustics.suggested_ceiling(110.0) == acoustics.CEILING_TYPICAL_MALE
    assert acoustics.suggested_ceiling(210.0) == acoustics.CEILING_TYPICAL_FEMALE
    # No f0 at all still has to return something usable rather than raise.
    assert acoustics.suggested_ceiling(None) == acoustics.CEILING_TYPICAL_MALE


# --- Ground truth ----------------------------------------------------------------------------


@pytest.mark.parametrize(("vowel", "truth"), sorted(MEN_F123.items()))
def test_burg_recovers_known_formants(vowel: str, truth: tuple[float, float, float]) -> None:
    """The only honest test of a formant tracker: a signal whose formants are known."""
    point = _analysis(truth).formants_at(0.15)
    assert point.usable, f"/{vowel}/ produced no usable F1/F2"
    for measured, expected, name in zip((point.f1, point.f2, point.f3), truth, "123"):
        assert measured is not None, f"/{vowel}/ F{name} was not measurable"
        assert abs(measured - expected) / expected < TOLERANCE, (
            f"/{vowel}/ F{name}: {measured:.0f} Hz against a true {expected:.0f} Hz"
        )


def test_f3_minus_f2_separates_r_coloured_from_everything_else() -> None:
    """The rhoticity instrument, on signals where the answer is not in doubt.

    /ɝ/'s F3 sits barely 300 Hz above its F2 while every other vowel's sits far higher. This
    is the single most useful number the module produces, so it gets its own assertion rather
    than being covered incidentally by the formant test above.
    """
    nurse = _analysis(MEN_F123["ɝ"]).formants_at(0.15).f3_minus_f2
    assert nurse is not None and nurse < 400.0

    for vowel, truth in MEN_F123.items():
        if vowel == "ɝ":
            continue
        other = _analysis(truth).formants_at(0.15).f3_minus_f2
        assert other is not None and other > nurse + 200.0, f"/{vowel}/ looked r-coloured"


def test_f0_is_tracked_and_unvoiced_audio_reports_none() -> None:
    voiced = _analysis(MEN_F123["ɑ"])
    assert voiced.f0_at(0.15) == pytest.approx(120.0, abs=5.0)
    assert voiced.f0_median() == pytest.approx(120.0, abs=5.0)
    assert voiced.voiced_fraction(0.05, 0.25) == 1.0

    silence = acoustics.analyse(acoustics.load(to_wav_bytes(synth_noise(0.3))), 5000.0)
    assert silence.f0_at(0.15) is None
    assert silence.voiced_fraction(0.05, 0.25) == 0.0


# --- Refusing rather than guessing -----------------------------------------------------------


def test_a_spurious_wide_pole_is_refused_rather_than_reported() -> None:
    """A model with more poles than the signal has resonances invents one. Catch it.

    Praat fits five poles below a 5 kHz ceiling. Give it a vowel with only three resonances
    and it spends a spare pole on a wide, shallow, meaningless peak between F1 and F2 — this
    was measured at a 1692 Hz bandwidth while real formants sat between 56 and 190. A number
    like that is not a weak measurement, it is a measurement of nothing, and reporting it
    would silently shift every higher formant into the wrong slot.
    """
    import numpy as np

    from conftest import _resonate

    rate = SAMPLE_RATE
    source = np.zeros(int(0.3 * rate))
    source[:: int(rate / 120)] = 1.0
    signal = _resonate(source, 100.0, 100.0, rate)
    for index, frequency in enumerate((300.0, 2300.0, 3000.0)):  # three formants, not five
        signal = _resonate(signal, frequency, 60.0 + 30.0 * index, rate)
    signal = signal / float(np.max(np.abs(signal))) * 0.5

    point = acoustics.analyse(acoustics.load(to_wav_bytes(signal)), 5000.0).formants_at(0.15)
    assert point.f2 is None, "the spurious wide pole was reported as F2"
    assert not point.usable, "a token with no trustworthy F2 must not count as usable"


def test_load_refuses_anything_that_is_not_16_bit_mono() -> None:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(b"\x00\x00" * 400)
    with pytest.raises(acoustics.AcousticsError, match="16-bit mono"):
        acoustics.load(buffer.getvalue())

    with pytest.raises(acoustics.AcousticsError):
        acoustics.load(b"not a wav at all")


def test_intensity_is_dbfs_and_silence_reports_none() -> None:
    sound = acoustics.load(to_wav_bytes(synth_vowel(MEN_F123["ɑ"], 0.3)))
    level = acoustics.rms_dbfs(sound, 0.05, 0.25)
    assert level is not None and -30.0 < level < 0.0, "a vowel at half scale should sit here"

    import numpy as np

    digital_silence = acoustics.load(to_wav_bytes(np.zeros(4800)))
    assert acoustics.rms_dbfs(digital_silence, 0.0, 0.3) is None
    # An empty span is not a quiet span.
    assert acoustics.rms_dbfs(sound, 0.2, 0.2) is None


# --- The sweep -------------------------------------------------------------------------------


def test_the_sweep_prefers_the_ceiling_that_tightens_the_speaker_s_own_categories() -> None:
    """The ceiling must match vocal tract length; f0 is a weak estimator of it, so sweep.

    Two vowel categories are repeated with small jitter, exactly as a real speaker's
    repetitions would be. The sweep should land somewhere sane rather than at an extreme, and
    should report that it actually had evidence.
    """
    import numpy as np

    from conftest import to_wav_bytes as encode

    pieces: list[np.ndarray] = []
    segments: list[acoustics.Segment] = []
    cursor = 0.0
    for repeat in range(4):
        for label in ("i", "ɑ"):
            f1, f2, f3 = MEN_F123[label]
            jitter = 1.0 + 0.02 * (repeat - 1.5)
            pieces.append(synth_vowel((f1 * jitter, f2 * jitter, f3), 0.20))
            segments.append(acoustics.Segment(label, cursor + 0.02, cursor + 0.18))
            cursor += 0.20
            pieces.append(synth_noise(0.05))
            cursor += 0.05

    sound = acoustics.load(encode(np.concatenate(pieces)))
    choice = acoustics.sweep_ceiling(sound, segments)

    assert choice.measured, "the sweep reported no evidence at all"
    assert choice.categories == 2
    assert choice.ceiling_hz in acoustics.CEILING_SWEEP
    assert acoustics.CEILING_SWEEP[0] < choice.ceiling_hz < acoustics.CEILING_SWEEP[-1]


def test_the_sweep_falls_back_to_the_f0_guess_with_nothing_to_go_on() -> None:
    sound = acoustics.load(to_wav_bytes(synth_vowel(MEN_F123["ɑ"], 0.3)))
    choice = acoustics.sweep_ceiling(sound, [])
    assert not choice.measured
    assert choice.ceiling_hz == choice.suggested_hz
    assert choice.categories == 0


def test_sample_points_match_the_reference_s_own_sampling() -> None:
    """20/50/80, because that is where Hillenbrand et al. measured. Not 25/50/75."""
    assert acoustics.SAMPLE_POINTS == (0.20, 0.50, 0.80)
    segment = acoustics.Segment("æ", 1.0, 2.0)
    assert segment.sample_times() == (1.2, 1.5, 1.8)
    assert segment.duration_ms == pytest.approx(1000.0)


def test_measure_returns_three_points() -> None:
    analysis = _analysis(MEN_F123["eɪ"] if "eɪ" in MEN_F123 else MEN_F123["ɛ"])
    points = analysis.measure(acoustics.Segment("ɛ", 0.05, 0.25))
    assert len(points) == 3
    assert all(isinstance(point, acoustics.FormantPoint) for point in points)
    # A monophthong is the case where 20% and 80% coincide — that is what makes trajectory
    # fall out of the same three samples for free.
    assert points[0].f2 is not None and points[2].f2 is not None
    assert abs(points[2].f2 - points[0].f2) < 120.0


def test_a_formant_outside_the_plausible_range_is_not_reported() -> None:
    assert acoustics.FORMANT_RANGE_HZ == (150.0, 5500.0)
    assert acoustics.MAX_BANDWIDTH_HZ == 600.0
    empty = acoustics.FormantPoint(None, None, None, None, None, None)
    assert empty.f3_minus_f2 is None
    assert not empty.usable
    assert math.isclose(
        acoustics.FormantPoint(300.0, 1000.0, 2500.0, 50.0, 60.0, 70.0).f3_minus_f2 or 0.0, 1500.0
    )
