"""What may be charted, and what the geometry says to practise next.

Two rules with an asymmetric cost, which is why they get their own file. Refusing to plot a
drill throws away the measure-drill-remeasure loop the accent surfaces exist for; plotting
before a baseline exists draws a confident dot from a normalisation that does not exist.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import vowel_measure

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_vowel_measure import INVENTORY_SPEC, build_recording


@pytest.fixture(scope="module")
def inventory_measurement():
    wav, words = build_recording(INVENTORY_SPEC * 3)
    return vowel_measure.extract(words, wav, ceiling_hz=5000.0, snr_db_min=30.0)


@pytest.fixture(scope="module")
def inventory_normaliser(inventory_measurement):
    return vowel_measure.lobanov(
        inventory_measurement.accepted, categories=vowel_measure.REFERENCE_CATEGORIES
    )


@pytest.fixture(scope="module")
def drill_measurement():
    """Three words. Far too few categories to normalise against itself, and that is the point."""
    wav, words = build_recording(
        [
            ("heed", [("h", 60), ("i", 200), ("d", 70)]),
            ("hoed", [("h", 60), ("oʊ", 220), ("d", 70)]),
            ("heard", [("h", 60), ("ɝ", 220), ("d", 70)]),
        ]
    )
    return vowel_measure.extract(words, wav, ceiling_hz=5000.0, snr_db_min=30.0)


# --- The gate ---------------------------------------------------------------------------------


def test_without_a_baseline_nothing_may_be_charted(drill_measurement) -> None:
    gate = vowel_measure.plot_gate(drill_measurement, baseline_normaliser=None)
    assert not gate.ok
    assert "calibration passage" in gate.reason
    assert gate.normaliser is None


def test_a_drill_is_chartable_once_a_baseline_exists(
    drill_measurement, inventory_normaliser
) -> None:
    """The rule this whole file exists for.

    Establishing the vowel space needs a full inventory from one speaker; a three-word drill
    cannot supply one. But USING an already-stored space needs only the token being measured.
    Refusing here would delete the measure-drill-remeasure loop — the entire point of drawing
    any of this.
    """
    # It genuinely cannot normalise against itself: three categories, and the floor is eight.
    with pytest.raises(vowel_measure.TooFewTokens):
        vowel_measure.lobanov(
            drill_measurement.accepted, categories=vowel_measure.REFERENCE_CATEGORIES
        )

    gate = vowel_measure.plot_gate(drill_measurement, baseline_normaliser=inventory_normaliser)
    assert gate.ok, gate.reason
    assert gate.normaliser is inventory_normaliser
    # And a single token per category is enough, because the SPACE came from somewhere else.
    assert gate.minimum_tokens == 1


def test_a_drill_actually_produces_rows_through_the_gate(
    drill_measurement, inventory_normaliser
) -> None:
    """Not just permitted — it has to yield something, with the token count attached."""
    gate = vowel_measure.plot_gate(drill_measurement, baseline_normaliser=inventory_normaliser)
    assert gate.normaliser is not None
    rows = vowel_measure.findings(
        drill_measurement,
        gate.normaliser,
        reference_set="men",
        minimum=gate.minimum_tokens,
    )
    assert rows, "a drill measured against a stored baseline produced no findings at all"
    # Thin evidence must look thin: every user cell built from a position carries its count.
    assert any("(n=1)" in row.user for row in rows)


def test_a_measurement_with_nothing_usable_is_refused_even_with_a_baseline(
    inventory_normaliser,
) -> None:
    empty = vowel_measure.Measurement(tokens=(), ceiling_hz=5000.0, snr_db_min=30.0, style="read")
    gate = vowel_measure.plot_gate(empty, baseline_normaliser=inventory_normaliser)
    assert not gate.ok
    assert "could be measured" in gate.reason


def test_a_style_mismatch_is_a_caveat_and_not_a_refusal(
    drill_measurement, inventory_normaliser
) -> None:
    """A read-speech baseline normalises read speech. Silently mixing populations is the bug."""
    spontaneous = vowel_measure.Measurement(
        tokens=drill_measurement.tokens,
        ceiling_hz=drill_measurement.ceiling_hz,
        snr_db_min=drill_measurement.snr_db_min,
        style="spontaneous",
    )
    gate = vowel_measure.plot_gate(
        spontaneous, baseline_normaliser=inventory_normaliser, baseline_style="read"
    )
    assert gate.ok, "a style mismatch must not refuse the plot"
    assert "spontaneous" in gate.style_warning and "read" in gate.style_warning


def test_matching_styles_carry_no_warning(drill_measurement, inventory_normaliser) -> None:
    gate = vowel_measure.plot_gate(
        drill_measurement, baseline_normaliser=inventory_normaliser, baseline_style="read"
    )
    assert gate.style_warning == ""


# --- Slicing the findings ---------------------------------------------------------------------


def test_the_instrument_split_loses_nothing_and_invents_nothing(
    inventory_measurement, inventory_normaliser
) -> None:
    """The chart tables and the whole table must be the SAME rows, not two derivations."""
    grouped = vowel_measure.findings_by_instrument(
        inventory_measurement, inventory_normaliser, reference_set="men"
    )
    whole = vowel_measure.findings(inventory_measurement, inventory_normaliser, reference_set="men")
    flattened = [row for key in vowel_measure.INSTRUMENT_ORDER for row in grouped.get(key, [])]
    assert flattened == whole
    assert set(grouped) == set(vowel_measure.MEASUREMENT_INSTRUMENTS)


def test_rhoticity_leads_the_reading_order() -> None:
    """The loudest, most correctable marker goes above the quadrant, not below it."""
    assert vowel_measure.INSTRUMENT_ORDER[0] == vowel_measure.RHOTICITY
    assert vowel_measure.INSTRUMENT_ORDER[-1] == vowel_measure.REJECTED


# --- The ranking ------------------------------------------------------------------------------


def test_gaps_are_ranked_worst_first_within_each_metric(
    inventory_measurement, inventory_normaliser
) -> None:
    gaps = vowel_measure.ranked_gaps(
        inventory_measurement, inventory_normaliser, reference_set="men"
    )
    assert gaps, "a full inventory against the published means produced no gaps at all"
    by_metric: dict[str, list[float]] = {}
    for gap in gaps:
        by_metric.setdefault(gap.metric, []).append(gap.magnitude)
    for metric, magnitudes in by_metric.items():
        assert magnitudes == sorted(magnitudes, reverse=True), metric
        assert len(magnitudes) <= vowel_measure.GAPS_PER_METRIC
        assert all(value > 0 for value in magnitudes), metric


def test_a_gap_smaller_than_the_noise_floor_never_becomes_a_drill(
    inventory_measurement, inventory_normaliser
) -> None:
    """A vowel moves this much between two reads with no learning at all.

    So it is not a finding, and a finding that is not real must never become something the
    user is told to practise. Dropped before ranking rather than flagged afterwards.
    """
    without = vowel_measure.ranked_gaps(
        inventory_measurement, inventory_normaliser, reference_set="men"
    )
    positions = [gap for gap in without if gap.metric == vowel_measure.POSITION]
    assert positions, "no position gaps to suppress — the fixture cannot prove the rule"

    # A band wider than any arrow in the reading. Nothing may survive it.
    huge = vowel_measure.NoiseFloor(per_vowel={}, median_z=99.0, vowels=0)
    with_floor = vowel_measure.ranked_gaps(
        inventory_measurement, inventory_normaliser, reference_set="men", noise=huge
    )
    assert not [gap for gap in with_floor if gap.metric == vowel_measure.POSITION]


def test_every_gap_carries_the_evidence_it_was_ranked_on(
    inventory_measurement, inventory_normaliser
) -> None:
    """ "Why is this here" is answered with numbers or it is not answered."""
    for gap in vowel_measure.ranked_gaps(
        inventory_measurement, inventory_normaliser, reference_set="men"
    ):
        assert gap.evidence, gap
        assert gap.unit
        assert gap.detail
        if gap.metric != vowel_measure.RHYTHM:
            assert gap.label.startswith("/")


def test_the_rhythm_gap_names_the_direction_rather_than_only_the_size() -> None:
    """A lower nPVI is a syllable-timed rhythm carried into English. That is the finding."""
    flatter = vowel_measure.rhythm_gap(40.0, 55.0)
    assert flatter is not None
    assert flatter.metric == vowel_measure.RHYTHM
    assert flatter.magnitude == pytest.approx(15.0)
    assert "syllable-timed" in flatter.detail

    assert vowel_measure.rhythm_gap(55.4, 55.0) is None, "sub-point noise is not a finding"
    assert vowel_measure.rhythm_gap(None, 55.0) is None
    assert vowel_measure.rhythm_gap(55.0, None) is None


# --- The direction of the advice ---------------------------------------------------------------
# The one place `vowel_measure` can produce confidently wrong advice, and the failure mode is
# silent: an inverted instruction renders exactly like a correct one. Both directions of both
# instruments are named here so a sign flip cannot pass review again.


def position_at(vowel: str, f3_minus_f2: float) -> vowel_measure.VowelPosition:
    """A minimal position carrying one number — the one the rhoticity row is built from."""
    return vowel_measure.VowelPosition(
        vowel=vowel,
        n=4,
        f1_hz=500.0,
        f2_hz=1500.0,
        f3_hz=1500.0 + f3_minus_f2,
        f1_z=0.0,
        f2_z=0.0,
        f3_z=0.0,
        duration_ms=140.0,
        f2_travel_hz=0.0,
        f3_minus_f2_hz=f3_minus_f2,
        rms_dbfs=-20.0,
        n_trajectory=4,
    )


def rhoticity_row(produced: float, target: float = 400.0) -> str:
    rows = vowel_measure._rhoticity_findings(
        {"ɝ": position_at("ɝ", produced)}, {"ɝ": position_at("ɝ", target)}
    )
    assert len(rows) == 1
    return rows[0].delta


def test_an_under_rhotic_speaker_is_told_to_bunch_and_never_to_release() -> None:
    """F3 sitting well ABOVE F2 is r-colouring that has not arrived. Bunch the tongue.

    The delta is `target − produced` on F3−F2, so a speaker wider than the target reads
    negative. Negating it before the lookup — which is easy to talk yourself into, because
    F3−F2 is a difference rather than a formant — selects `f3_raise` and tells exactly the
    speaker who needs more r-colouring to release the bunching they have not got.
    """
    instruction = rhoticity_row(produced=900.0)
    assert "more r-colouring" in instruction, instruction
    assert "less r-colouring" not in instruction, instruction
    assert "bunch" in instruction.lower(), instruction


def test_an_over_rhotic_speaker_is_told_to_release_and_never_to_bunch() -> None:
    """The mirror image, so the test cannot pass on a lookup that is constant either way."""
    instruction = rhoticity_row(produced=100.0)
    assert "less r-colouring" in instruction, instruction
    assert "more r-colouring" not in instruction, instruction


def test_r_colouring_near_the_target_is_reported_as_being_in_the_band() -> None:
    """Neither direction is an instruction inside the tolerance, in EITHER direction."""
    for produced in (400.0 - vowel_measure.RHOTICITY_TOLERANCE_HZ + 1.0, 400.0, 500.0):
        instruction = rhoticity_row(produced=produced)
        assert "tolerance band" in instruction, (produced, instruction)
        assert "bunch" not in instruction.lower(), instruction
        assert "release" not in instruction.lower(), instruction


def test_a_glide_that_was_never_measurable_does_not_become_a_monophthong_gap(
    inventory_normaliser,
) -> None:
    """`or 0.0` on a missing travel manufactures the worst finding the chart can report.

    A vowel every one of whose tokens was too short for the 20% and 80% windows has NO travel.
    `_trajectory_findings` refuses to instruct on that and says so in the table; the ranking
    has to agree, because a gap that is not real must not become a drill.
    """
    short = _face_tokens(duration_ms=60.0)
    measurement = vowel_measure.Measurement(
        tokens=tuple(short), ceiling_hz=5000.0, snr_db_min=30.0, style="read"
    )
    position = vowel_measure.positions(short, inventory_normaliser, minimum=1)["eɪ"]
    assert position.n_trajectory == 0, "fixture is not exercising the refused-glide case"
    assert position.f2_travel_hz is None

    gaps = vowel_measure.ranked_gaps(
        measurement, inventory_normaliser, reference_set="men", minimum=1
    )
    assert not [gap for gap in gaps if gap.metric == vowel_measure.TRAJECTORY], (
        "an unmeasured glide was ranked as a flattened one"
    )

    rows = vowel_measure.findings_by_instrument(
        measurement, inventory_normaliser, reference_set="men", minimum=1
    )[vowel_measure.TRAJECTORY]
    assert any("reached" in row.delta and "ms" in row.delta for row in rows), rows


def test_a_backwards_glide_is_not_ranked_as_a_shortfall(inventory_normaliser) -> None:
    """The capture found FACE gliding −225 Hz where General American glides +140.

    That is the 80% window landing in the following nasal, not a small glide, and "widen the
    glide" is the wrong thing to say about it. The table refuses; so does the ranking.
    """
    # Travelling backwards by LESS than the target travels forwards, so the guard is the
    # only thing standing between this and a ranked "the glide is 90 Hz short" gap.
    backwards = _face_tokens(duration_ms=200.0, f2_start=2140.0, f2_end=2089.0)
    measurement = vowel_measure.Measurement(
        tokens=tuple(backwards), ceiling_hz=5000.0, snr_db_min=30.0, style="read"
    )
    position = vowel_measure.positions(backwards, inventory_normaliser, minimum=1)["eɪ"]
    assert position.f2_travel_hz is not None and position.f2_travel_hz < 0

    gaps = vowel_measure.ranked_gaps(
        measurement, inventory_normaliser, reference_set="men", minimum=1
    )
    assert not [gap for gap in gaps if gap.metric == vowel_measure.TRAJECTORY]


def _face_tokens(
    *, duration_ms: float, f2_start: float = 2089.0, f2_end: float = 2089.0, count: int = 6
) -> list[vowel_measure.Token]:
    """FACE tokens with the travel and the length dialled in directly.

    Built rather than synthesised because what is under test is the arithmetic on top of a
    measurement, not the measurement — and a synthesised signal cannot be given a 60 ms
    duration and a clean 20%/80% pair at the same time, which is the whole point of the floor.
    """

    def point(f2: float) -> vowel_measure.FormantPoint:
        return vowel_measure.FormantPoint(f1=460.0, f2=f2, f3=2700.0, b1=50.0, b2=80.0, b3=120.0)

    return [
        vowel_measure.Token(
            vowel="eɪ",
            word="say",
            word_index=index,
            start_s=index * 0.5,
            end_s=index * 0.5 + duration_ms / 1000.0,
            duration_ms=duration_ms,
            at20=point(f2_start),
            at50=point((f2_start + f2_end) / 2),
            at80=point(f2_end),
            rms_dbfs=-20.0,
            f0_hz=120.0,
            stress=1,
            azure_score=90.0,
            coda_voiceless=False,
            accepted=True,
        )
        for index in range(count)
    ]
