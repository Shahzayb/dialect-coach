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
