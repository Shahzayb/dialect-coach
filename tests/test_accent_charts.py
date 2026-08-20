"""The accent charts, and the one assertion the whole feature is judged on.

Every chart is built as a frame first and a chart spec second, so both halves can be asserted
without driving a Streamlit page — the boundary `progress_view` and `accent_view` already sit
on. A chart whose *encoding* is wrong renders happily and teaches the opposite of the truth,
which is why the specs are inspected here and not only the numbers.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

import accent_charts
import speech_analyzer
import vowel_measure

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_vowel_measure import INVENTORY_SPEC, build_recording

from conftest import synth_glide, synth_noise, synth_vowel, to_wav_bytes

TICKS = speech_analyzer.TICKS_PER_SECOND

# Hillenbrand's adult-male FACE, at the two points the pipeline samples. Used as test INPUT.
FACE_20 = (478.8, 2089.0, 2698.5)
FACE_50 = (437.2, 2180.6, 2727.0)
FACE_80 = (399.6, 2229.2, 2741.7)


# --- THE EXIT CONDITION ------------------------------------------------------------------------


def face_recording(*, gliding: bool, repeats: int = 4):
    """A reading of "hayed" several times over, with FACE either moving or held still.

    Everything else is identical between the two: same word, same duration, same consonants,
    same lead-in. One variable.
    """
    lead_in = 1.0
    pieces = [synth_noise(lead_in)]
    cursor = lead_in
    words = []
    for _ in range(repeats):
        entries = []
        word_start = cursor
        for symbol, milliseconds in (("h", 60), ("eɪ", 240), ("d", 70)):
            seconds = milliseconds / 1000.0
            if symbol == "eɪ":
                pieces.append(
                    synth_glide(FACE_20, FACE_80, seconds)
                    if gliding
                    else synth_vowel(FACE_50, seconds)
                )
            else:
                pieces.append(synth_noise(seconds))
            entries.append(
                {
                    "phoneme": symbol,
                    "score": 95.0,
                    "nbest": [{"phoneme": symbol, "score": 95.0}],
                    "offset_ticks": round(cursor * TICKS),
                    "duration_ticks": round(seconds * TICKS),
                    "start_s": cursor,
                    "end_s": cursor + seconds,
                }
            )
            cursor += seconds
        words.append(
            {
                "word": "hayed",
                "accuracy": 95.0,
                "error_type": "None",
                "phonemes": entries,
                "syllables": [],
                "offset_ticks": round(word_start * TICKS),
                "duration_ticks": round((cursor - word_start) * TICKS),
                "start_s": word_start,
                "end_s": cursor,
            }
        )
    import numpy as np

    pieces.append(synth_noise(0.2))
    return to_wav_bytes(np.concatenate(pieces)), words


def face_trajectory(*, gliding: bool):
    wav, words = face_recording(gliding=gliding)
    measurement = vowel_measure.extract(words, wav, ceiling_hz=5000.0, snr_db_min=30.0)
    identity = vowel_measure.Normaliser(
        f1_mean=500.0,
        f1_sd=100.0,
        f2_mean=1500.0,
        f2_sd=300.0,
        f3_mean=2500.0,
        f3_sd=300.0,
        categories=("eɪ",),
    )
    found = vowel_measure.trajectories(measurement.accepted, identity, minimum=1)
    assert "eɪ" in found, "the FACE tokens were not measured at all"
    return found["eɪ"]


def test_a_monophthongised_face_vowel_is_visibly_different_from_a_diphthongised_one() -> None:
    """**The exit condition for the whole chunk.**

    If this fails the pipeline is wrong regardless of what else works: the trajectory chart's
    entire claim is that a flattened diphthong renders as a dot where a native rendering
    renders as a stroke. Nothing else in this feature is worth shipping if that is not true.
    """
    flat = face_trajectory(gliding=False)
    native = face_trajectory(gliding=True)

    assert flat.travel_hz is not None and native.travel_hz is not None
    # The monophthong genuinely does not move: single-digit hertz, i.e. tracker noise.
    assert abs(flat.travel_hz) < 10.0, f"a held FACE vowel moved {flat.travel_hz:.1f} Hz"
    # The glide does, by a margin no reader could mistake for the same picture.
    assert native.travel_hz > 50.0, f"a gliding FACE vowel moved only {native.travel_hz:.1f} Hz"
    assert abs(native.travel_hz) > 10 * abs(flat.travel_hz)

    # And it survives into the drawn stroke, which is what the reader actually sees.
    assert flat.length_z is not None and native.length_z is not None
    assert native.length_z > 3 * flat.length_z, (
        f"stroke lengths {native.length_z:.3f} z against {flat.length_z:.3f} z — "
        f"a reader could not tell these two vowels apart on the chart"
    )


def test_the_exit_condition_reaches_the_frame_the_chart_is_drawn_from() -> None:
    """Not just the measurement — the frame, with two rows per vowel making a real stroke."""
    frames = {}
    for name, gliding in (("flat", False), ("native", True)):
        frames[name] = accent_charts.trajectory_frame({"eɪ": face_trajectory(gliding=gliding)})

    for name, frame in frames.items():
        assert len(frame) == 2, f"{name}: a stroke needs a start row and an end row"
        assert list(frame["order"]) == [0, 1]

    def span(frame):
        return math.hypot(
            frame["f1_z"].iloc[1] - frame["f1_z"].iloc[0],
            frame["f2_z"].iloc[1] - frame["f2_z"].iloc[0],
        )

    assert span(frames["native"]) > 3 * span(frames["flat"])


# --- The quadrant ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def inventory():
    wav, words = build_recording(INVENTORY_SPEC * 3)
    measurement = vowel_measure.extract(words, wav, ceiling_hz=5000.0, snr_db_min=30.0)
    normaliser = vowel_measure.lobanov(
        measurement.accepted, categories=vowel_measure.REFERENCE_CATEGORIES
    )
    return measurement, normaliser


@pytest.fixture(scope="module")
def quadrant(inventory):
    measurement, normaliser = inventory
    return accent_charts.quadrant_frame(
        vowel_measure.positions(measurement.accepted, normaliser),
        vowel_measure.reference_positions("men"),
    )


def test_both_axes_are_reversed_because_a_vowel_chart_is_a_map_of_the_mouth(quadrant) -> None:
    """High-F2 front vowels belong on the left and low-F1 close vowels on top.

    Not a style choice: an unreversed scatter of the same numbers is upside down and back to
    front, and unreadable to anyone who has ever seen a vowel chart.
    """
    spec = accent_charts.quadrant_chart(quadrant).to_dict()
    encodings = [layer["encoding"] for layer in spec["layer"] if "encoding" in layer]
    axes = [(e["x"], e["y"]) for e in encodings if "x" in e and "y" in e]
    assert axes, "the quadrant drew no x/y layers at all"
    for x, y in axes:
        assert x["scale"]["reverse"] is True
        assert y["scale"]["reverse"] is True


def test_the_arrow_points_where_the_instruction_says(quadrant) -> None:
    """The arrow's DIRECTION is the instruction, so the reversal must be undone in the angle.

    Vega measures `angle` clockwise from straight up. Front is on the left of a vowel chart, so
    a target that is purely fronter than the speaker must come back pointing left — 270°.
    Getting this wrong renders every arrow backwards while the chart still looks correct.
    """
    assert accent_charts.screen_angle(0.0, 1.0) == pytest.approx(270.0)  # fronter → left
    assert accent_charts.screen_angle(0.0, -1.0) == pytest.approx(90.0)  # backer → right
    assert accent_charts.screen_angle(1.0, 0.0) == pytest.approx(180.0)  # more open → down
    assert accent_charts.screen_angle(-1.0, 0.0) == pytest.approx(0.0)  # closer → up
    assert 0.0 <= accent_charts.screen_angle(0.4, 0.9) < 360.0


def test_an_arrow_inside_the_noise_band_is_not_drawn_as_a_finding(inventory) -> None:
    """A vowel moves this much between two reads with no learning at all."""
    measurement, normaliser = inventory
    speaker = vowel_measure.positions(measurement.accepted, normaliser)
    reference = vowel_measure.reference_positions("men")

    wide = vowel_measure.NoiseFloor(per_vowel={}, median_z=99.0, vowels=0)
    frame = accent_charts.quadrant_frame(speaker, reference, wide)
    assert not frame["real"].any(), "an arrow was drawn inside a band wider than the chart"
    assert all(vowel_measure.WITHIN_NOISE in v for v in frame[frame["has_target"]]["verdict"])

    # The points still render — the vowel is not dropped, only its arrow.
    assert len(frame) > 0
    spec = accent_charts.quadrant_chart(frame).to_dict()
    assert spec["layer"], "suppressing the arrows removed the whole chart"


def test_a_vowel_with_no_reference_keeps_its_point_and_loses_its_arrow() -> None:
    """Ten categories have no published mean. Dropping them under-reports the inventory."""
    speaker = {
        "aʊ": vowel_measure.VowelPosition(
            vowel="aʊ",
            n=4,
            f1_hz=700.0,
            f2_hz=1200.0,
            f3_hz=2500.0,
            f1_z=0.5,
            f2_z=-0.5,
            f3_z=0.0,
            duration_ms=200.0,
            f2_travel_hz=300.0,
            f3_minus_f2_hz=1300.0,
            rms_dbfs=-20.0,
        )
    }
    frame = accent_charts.quadrant_frame(speaker, {})
    assert len(frame) == 1
    assert not bool(frame["has_target"].iloc[0])
    assert frame["arrow_z"].iloc[0] is None
    assert "not scored" in frame["verdict"].iloc[0]


def test_the_noise_ring_is_drawn_in_data_units_not_in_screen_area(quadrant) -> None:
    """A ring encoded as mark `size` would rescale against the arrows when the chart resizes.

    The band exists to be compared against the arrow length, so the two must live in the same
    units or the comparison the reader makes by eye is meaningless.
    """
    frame = quadrant.copy()
    frame["band_z"] = 0.5
    rings = accent_charts.noise_ring_frame(frame, points=24)
    assert not rings.empty
    for vowel, group in rings.groupby("vowel"):
        row = frame[frame["vowel"] == vowel].iloc[0]
        radii = [
            math.hypot(r["f1_z"] - row["f1_z"], r["f2_z"] - row["f2_z"])
            for _, r in group.iterrows()
        ]
        assert all(abs(radius - 0.5) < 1e-9 for radius in radii)


def test_thin_evidence_looks_thin(quadrant) -> None:
    """A point from two tokens and one from twenty must never look the same."""
    spec = accent_charts.quadrant_chart(quadrant).to_dict()
    sizes = [
        layer["encoding"]["size"]
        for layer in spec["layer"]
        if "encoding" in layer and "size" in layer["encoding"]
    ]
    assert sizes and sizes[0]["field"] == "n"


# --- Rhoticity -----------------------------------------------------------------------------------


def test_rhoticity_plots_every_token_rather_than_a_category_mean(inventory) -> None:
    """A mean is exactly the statistic that hides inconsistent r-colouring.

    R-colouring is the marker most likely to arrive on a stressed NURSE and vanish on an
    unstressed lettER, and one averaged dot cannot show that.
    """
    measurement, _ = inventory
    frame = accent_charts.rhoticity_frame(
        measurement.accepted, vowel_measure.reference_positions("men")
    )
    rhotic_tokens = [t for t in measurement.accepted if accent_charts.is_rhotic(t.vowel)]
    assert len(frame) == len([t for t in rhotic_tokens if t.f3_minus_f2 is not None])
    assert len(frame) > 1, "the fixture has only one rhotic token; the rule cannot be shown"
    assert frame["target_hz"].notna().all()


def test_the_rhoticity_axis_says_which_direction_is_more_r_coloured(inventory) -> None:
    """F3 dropping toward F2 IS the signature. Leaving the reader to infer it is a trap."""
    measurement, _ = inventory
    frame = accent_charts.rhoticity_frame(
        measurement.accepted, vowel_measure.reference_positions("men")
    )
    spec = accent_charts.rhoticity_chart(frame).to_dict()
    titles = [
        layer["encoding"]["x"].get("title")
        for layer in spec["layer"]
        if "encoding" in layer and "x" in layer["encoding"]
    ]
    assert any(title and "more r-coloured" in title for title in titles)


def test_only_r_coloured_vowels_reach_the_rhoticity_chart(inventory) -> None:
    measurement, _ = inventory
    frame = accent_charts.rhoticity_frame(
        measurement.accepted, vowel_measure.reference_positions("men")
    )
    assert set(frame["vowel"]) <= {"ɝ", "ɚ", "ɑɹ", "ɔɹ", "ɛɹ", "ɪɹ", "ʊɹ"}


# --- What no accent surface may say -----------------------------------------------------------


def test_no_chart_caption_offers_a_percentage_or_a_verdict(quadrant) -> None:
    """Native-likeness is a direction with a distance, never a checkbox or a score.

    A surface that can only say "not yet native" is uninformative for the entire period during
    which it is true — which is the whole time the tool is useful.
    """
    banned = ("% native", "native-like", "pass", "fail", "score:", "grade")
    for verdict in quadrant["verdict"]:
        lowered = str(verdict).lower()
        assert not any(word in lowered for word in banned), verdict
    assert "post-hoc" in accent_charts.POST_HOC.lower()


# --- The alignment decision --------------------------------------------------------------------


def words_at(starts: list[tuple[str, float]]) -> list[dict[str, object]]:
    return [{"word": text, "start_s": start} for text, start in starts]


def test_anchors_match_the_two_readings_word_by_word() -> None:
    user = words_at([("the", 0.0), ("quick", 0.5), ("fox", 1.2)])
    model = words_at([("the", 0.0), ("quick", 0.3), ("fox", 0.7)])
    anchors = accent_charts.word_anchors(user, model)
    assert [a.word for a in anchors] == ["the", "quick", "fox"]
    assert anchors[1].user_s == 0.5 and anchors[1].model_s == 0.3


def test_an_omitted_word_drops_out_rather_than_shifting_everything_after_it() -> None:
    """A word with no timing was never spoken, and a word that was never spoken cannot anchor."""
    user = words_at([("the", 0.0), ("quick", 0.5)])
    user.insert(1, {"word": "brown", "start_s": None})
    model = words_at([("the", 0.0), ("brown", 0.2), ("quick", 0.4)])
    anchors = accent_charts.word_anchors(user, model)
    assert [a.word for a in anchors] == ["the"]


def test_alignment_stops_where_the_readings_diverge() -> None:
    """Anchoring past a substitution would misalign the rest while still looking aligned."""
    user = words_at([("the", 0.0), ("sick", 0.5), ("fox", 1.0)])
    model = words_at([("the", 0.0), ("quick", 0.3), ("fox", 0.7)])
    assert [a.word for a in accent_charts.word_anchors(user, model)] == ["the"]


def test_the_warp_is_piecewise_linear_between_word_starts() -> None:
    user = words_at([("a", 0.0), ("b", 2.0)])
    model = words_at([("a", 0.0), ("b", 1.0)])
    anchors = accent_charts.word_anchors(user, model)
    assert accent_charts.to_user_clock(0.0, anchors) == pytest.approx(0.0)
    assert accent_charts.to_user_clock(0.5, anchors) == pytest.approx(1.0)
    assert accent_charts.to_user_clock(1.0, anchors) == pytest.approx(2.0)


def test_the_alignment_cannot_hide_a_timing_error() -> None:
    """**The reason this is word offsets and not DTW.**

    DTW finds the warp that minimises distance between two contours, so it would happily
    stretch the time axis until a timing error disappeared — and timing error is one of the
    things being measured. Anchoring on word starts is a warp constrained to linguistic units:
    it lines the same word up with the same word, and it CANNOT line up two syllables that
    took different amounts of time.

    Here the user takes 2.0s over a word the model takes 0.5s. After alignment that word must
    still occupy four times as much of the user's axis, or the chart is lying.
    """
    user = words_at([("slow", 0.0), ("next", 2.0), ("end", 2.5)])
    model = words_at([("slow", 0.0), ("next", 0.5), ("end", 1.0)])
    anchors = accent_charts.word_anchors(user, model)

    def at(model_time: float) -> float:
        mapped = accent_charts.to_user_clock(model_time, anchors)
        assert mapped is not None
        return mapped

    slow_span = at(0.5) - at(0.0)
    next_span = at(1.0) - at(0.5)
    assert slow_span == pytest.approx(2.0)
    assert next_span == pytest.approx(0.5)
    assert slow_span > 3 * next_span, "the warp flattened a 4:1 timing difference"


def test_a_single_anchor_cannot_define_a_warp() -> None:
    user = words_at([("only", 0.0)])
    model = words_at([("only", 0.0)])
    assert accent_charts.to_user_clock(0.5, accent_charts.word_anchors(user, model)) is None


# --- Pitch, in semitones ------------------------------------------------------------------------


def track(points: list[tuple[float, float]]):
    return points


def test_two_voices_an_octave_apart_with_the_same_shape_overlay_exactly() -> None:
    """**Why the axis is semitones and not hertz.**

    In hertz these two contours are nowhere near each other and the chart shows the trivial
    fact that one speaker has a deeper voice. In semitones relative to each speaker's own
    median they are the same line — which is the thing that can actually be imitated.
    """
    low = [(index * 0.1, 100.0 * (1.0 + 0.05 * index)) for index in range(10)]
    high = [(index * 0.1, 200.0 * (1.0 + 0.05 * index)) for index in range(10)]
    anchors = accent_charts.word_anchors(
        words_at([("a", 0.0), ("b", 0.9)]), words_at([("a", 0.0), ("b", 0.9)])
    )
    frame = accent_charts.pitch_frame(low, [high], anchors)

    mine = frame[frame["series"] == "You"].reset_index(drop=True)
    theirs = frame[frame["series"] != "You"].reset_index(drop=True)
    assert len(mine) == len(theirs) == 10
    for a, b in zip(mine["semitones"], theirs["semitones"]):
        assert abs(a - b) < 1e-9, "the same contour shape came out different in semitones"
    # And in hertz they are an octave apart, which is exactly what the axis suppresses.
    assert theirs["hz"].iloc[0] == pytest.approx(2 * mine["hz"].iloc[0])


def test_the_pitch_chart_axis_names_the_speakers_own_median() -> None:
    frame = accent_charts.pitch_frame([(0.0, 100.0), (0.5, 120.0)], [], [])
    spec = accent_charts.pitch_chart(frame).to_dict()
    titles = [
        layer["encoding"]["y"].get("title")
        for layer in spec["layer"]
        if "encoding" in layer and "y" in layer["encoding"] and "title" in layer["encoding"]["y"]
    ]
    assert any(title and "own median" in title for title in titles)


def test_pitch_range_and_terminal_slope_are_reported_in_semitones() -> None:
    flat = [(index * 0.05, 120.0) for index in range(30)]
    assert accent_charts.pitch_range_semitones(flat) == pytest.approx(0.0, abs=1e-9)
    assert accent_charts.terminal_slope_semitones(flat) == pytest.approx(0.0, abs=1e-9)

    falling = [(index * 0.05, 160.0 - 2.0 * index) for index in range(30)]
    slope = accent_charts.terminal_slope_semitones(falling)
    spread = accent_charts.pitch_range_semitones(falling)
    assert slope is not None and spread is not None
    assert slope < -1.0, "a falling terminal must come out negative"
    assert spread > 3.0

    assert accent_charts.pitch_range_semitones([(0.0, 100.0)]) is None
    assert accent_charts.terminal_slope_semitones([]) is None


# --- Duration -------------------------------------------------------------------------------------


def position(vowel: str, duration_ms: float, n: int = 5):
    return vowel_measure.VowelPosition(
        vowel=vowel,
        n=n,
        f1_hz=500.0,
        f2_hz=1500.0,
        f3_hz=2500.0,
        f1_z=0.0,
        f2_z=0.0,
        f3_z=0.0,
        duration_ms=duration_ms,
        f2_travel_hz=None,
        f3_minus_f2_hz=1000.0,
        rms_dbfs=-20.0,
    )


def test_the_duration_chart_carries_three_bars_and_the_third_is_the_usable_one() -> None:
    """Hillenbrand's /i/ is 244 ms of citation form; connected speech is nowhere near that.

    Both references are shown, because the brief asked for the published one and honesty asks
    for the other. A learner comparing only against citation form would read a 150 ms shortfall
    that is the reference's artefact rather than their accent.
    """
    frame = accent_charts.duration_frame(
        {"i": position("i", 95.0)},
        {"i": position("i", 244.0)},
        {"i": position("i", 101.0)},
    )
    assert set(frame["series"]) == {
        "You",
        "Hillenbrand 1995 (citation form)",
        "Model voices (connected speech)",
    }
    values = dict(zip(frame["series"], frame["duration_ms"]))
    assert values["Hillenbrand 1995 (citation form)"] > 2 * values["You"]
    assert abs(values["Model voices (connected speech)"] - values["You"]) < 20


def test_the_duration_chart_only_shows_vowels_the_speaker_produced() -> None:
    """A bar chart of targets with no measurement beside them is a chart of the reference."""
    frame = accent_charts.duration_frame(
        {"i": position("i", 95.0)},
        {"i": position("i", 244.0), "u": position("u", 237.0)},
        {"i": position("i", 101.0), "u": position("u", 87.0)},
    )
    assert set(frame["vowel"]) == {"i"}


# --- Rhythm ---------------------------------------------------------------------------------------


def test_the_rhythm_chart_keeps_the_pause_boundaries() -> None:
    """nPVI is computed within unbroken stretches and never across a pause.

    Running the bars together would imply a comparison the number itself refuses to make.
    """
    frame = accent_charts.rhythm_frame([[100.0, 60.0, 120.0], [80.0, 90.0]])
    assert len(frame) == 5
    assert list(frame["run"]) == [0, 0, 0, 1, 1]
    assert list(frame["index"]) == [0, 1, 2, 3, 4]


def test_a_flat_rhythm_and_a_jagged_one_are_different_pictures() -> None:
    """The chart's whole claim: syllable-timed reads as a row of equal bars."""
    flat = accent_charts.rhythm_frame([[90.0] * 8])
    jagged = accent_charts.rhythm_frame([[40.0, 140.0] * 4])
    assert flat["duration_ms"].std() == pytest.approx(0.0)
    assert jagged["duration_ms"].std() > 40.0


def test_the_model_voices_actually_collapse_onto_one_line() -> None:
    """The chart says "averaged across voices at each instant". It has to be true.

    Each voice is tracked on its own 10 ms grid and then warped onto the user's clock, which
    lands its frames on arbitrary floats. Without a shared bucket the chart's
    `median(semitones)` groups by an x value that is unique per voice per frame, aggregates
    nothing, and draws a line zigzagging between eight voices — which reads as a wildly
    unstable reference contour rather than as the tendency it claims to be.
    """
    anchors = [accent_charts.Anchor("one", 0.0, 0.0), accent_charts.Anchor("two", 1.0, 1.0)]
    # Three voices on the same grid, offset the way three real trackers are, and an octave
    # apart so a failure to average is visible in the values and not only in the row count.
    tracks = [
        [(index * 0.01 + offset, hz) for index in range(50)]
        for offset, hz in ((0.000, 100.0), (0.003, 200.0), (0.007, 150.0))
    ]
    frame = accent_charts.pitch_frame(
        [(index * 0.01, 120.0) for index in range(50)], tracks, anchors
    )
    model = frame[frame["series"] == "General American model"]

    assert len(model) == 150
    assert model["time_s"].nunique() <= 51, (
        f"{model['time_s'].nunique()} distinct x values for 150 rows — the voices never "
        f"share a group, so nothing is averaged"
    )
    biggest = model.groupby("time_s").size().max()
    assert biggest == 3, f"at most {biggest} voice(s) landed in one group"
