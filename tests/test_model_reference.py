"""The General American reference this project measured for itself.

`src/model_reference.py` is a GENERATED file bought with real Azure allowance, so these tests
do two jobs that ordinary unit tests do not: they check the generator did not silently produce
a plausible-looking table, and they make it impossible to ship the bootstrap placeholder.
"""

from __future__ import annotations

import math

import pytest

import model_reference
import phoneme_reference
import vowel_measure
import vowel_reference
from native_model import MEN, MIN_VOICES_PER_SET, WOMEN

INVENTORY = sorted(phoneme_reference.LEXICAL_SET)
SETS = (MEN, WOMEN)


# --- It exists, and it is not the placeholder --------------------------------------------------


@pytest.mark.parametrize("reference_set", SETS)
def test_the_reference_is_populated_and_not_the_bootstrap_placeholder(reference_set: str) -> None:
    """`model_reference.py` has to exist before the builder that writes it can import it.

    That bootstrap is committed, so this is the test that stops the empty version reaching a
    release and silently removing every target from every chart.
    """
    table = model_reference.REFERENCE_SETS[reference_set]
    assert len(table) >= 12, f"{reference_set} has only {len(table)} categories"


@pytest.mark.parametrize("reference_set", SETS)
def test_every_published_entry_rests_on_enough_talkers(reference_set: str) -> None:
    """A between-voice SD computed from two voices is not a spread, it is an anecdote."""
    for symbol, entry in model_reference.REFERENCE_SETS[reference_set].items():
        assert entry.voices >= MIN_VOICES_PER_SET, f"{reference_set}/{symbol}: {entry.voices}"
        assert entry.n >= entry.voices, f"{reference_set}/{symbol} has fewer tokens than voices"


@pytest.mark.parametrize("reference_set", SETS)
def test_a_category_below_the_floor_is_an_explicit_absence_with_its_count(
    reference_set: str,
) -> None:
    """A thin reference has to LOOK thin — the same rule the four-column table follows.

    A surface asked for a vowel nobody produced can then say "two voices managed it, and a
    reference needs four" rather than rendering the same blank a typo would produce.
    """
    coverage = model_reference.VOICE_COVERAGE[reference_set]
    assert set(coverage) == set(INVENTORY), "coverage must cover the whole inventory"
    for symbol in INVENTORY:
        published = model_reference.has_reference(symbol, reference_set)
        assert published == (coverage[symbol] >= MIN_VOICES_PER_SET), symbol
        assert model_reference.voices_behind(symbol, reference_set) == coverage[symbol]


# --- The reason it was worth buying ------------------------------------------------------------


def test_it_covers_categories_hillenbrand_has_no_mean_for() -> None:
    """The whole point. Ten of the passage's 22 categories have no published mean at all.

    Six of those ten are r-coloured, on the marker the brief calls the loudest and most
    correctable available for a General American target — where Hillenbrand offers /ɝ/ alone.
    """
    published = set(vowel_reference.MEN)
    measured = set(model_reference.MEN)
    gained = measured - published
    assert gained, "the measured reference adds nothing the published one did not have"

    rhotics = {"ɚ", "ɑɹ", "ɔɹ", "ɛɹ", "ɪɹ", "ʊɹ"}
    assert rhotics & gained, f"no new r-coloured categories; gained only {sorted(gained)}"


def test_the_two_sets_are_never_pooled() -> None:
    """Formants scale with vocal tract length, so a mean of the two describes nobody.

    Checked as a real numeric claim rather than a structural one: the women's set must sit
    measurably higher in F1 than the men's, because it is made of shorter vocal tracts. If the
    two came out indistinguishable, the stratification did not happen.
    """
    shared = set(model_reference.MEN) & set(model_reference.WOMEN)
    assert len(shared) >= 8, "too few shared categories to compare the two sets"
    higher: list[bool] = []
    for symbol in shared:
        women_f1 = model_reference.WOMEN[symbol].at50.f1
        men_f1 = model_reference.MEN[symbol].at50.f1
        if women_f1 is not None and men_f1 is not None:
            higher.append(women_f1 > men_f1)
    assert sum(higher) / len(higher) > 0.7, "the women's set is not higher in F1 than the men's"


def test_durations_are_connected_speech_and_not_citation_form() -> None:
    """The one caveat this table LIFTS, and the reason it was worth real allowance.

    `vowel_reference` caveat 3 forbids comparing absolute milliseconds against Hillenbrand,
    whose /hVd/ words were read in isolation — /i/ averages 244 ms there. The same vowel inside
    running speech is far shorter, and these are measured inside running speech, so they can be
    compared in milliseconds. If they came out anywhere near the citation-form figures, this
    table would be measuring the wrong thing.
    """
    for symbol in set(model_reference.MEN) & set(vowel_reference.MEN):
        measured = model_reference.MEN[symbol].duration_ms
        citation = vowel_reference.MEN[symbol].duration_ms
        if measured is None or citation is None:
            continue
        assert measured < citation, (
            f"/{symbol}/ measured {measured:.0f} ms in connected speech against a "
            f"{citation:.0f} ms citation form — connected speech cannot be the longer one"
        )


# --- Did the capture measure SPEECH, or an artefact? -------------------------------------------


@pytest.mark.parametrize("reference_set", SETS)
def test_it_agrees_in_shape_with_hillenbrand_where_the_two_overlap(reference_set: str) -> None:
    """The check that separates "a new reference" from "a bug in the capture".

    Two independent measurements of General American, forty years and one synthesiser apart,
    must place the same twelve vowels in roughly the same arrangement — /i/ high and front,
    /ɑ/ low and back. Normalised, so the comparison is about SHAPE and not about hertz.

    A set that does not correlate is not a finding about English; it is a formant tracker
    measuring the wrong thing, and it must never reach a chart.
    """
    published = vowel_measure.reference_positions(
        reference_set, source=vowel_measure.REFERENCE_PUBLISHED
    )
    measured = vowel_measure.reference_positions(
        reference_set, source=vowel_measure.REFERENCE_VOICE
    )
    shared = sorted(set(published) & set(measured))
    assert len(shared) >= 10, f"only {len(shared)} shared categories to compare"

    for axis in ("f1_z", "f2_z"):
        one = [getattr(published[s], axis) for s in shared]
        two = [getattr(measured[s], axis) for s in shared]
        assert all(v is not None for v in one + two)
        correlation = _pearson([float(v) for v in one], [float(v) for v in two])
        assert correlation > 0.8, (
            f"{reference_set} {axis}: the measured reference correlates {correlation:.2f} with "
            f"Hillenbrand. Two measurements of General American must agree in shape — this "
            f"says the capture measured something else."
        )


def _pearson(one: list[float], two: list[float]) -> float:
    mean_one, mean_two = sum(one) / len(one), sum(two) / len(two)
    covariance = sum((a - mean_one) * (b - mean_two) for a, b in zip(one, two))
    spread = math.sqrt(
        sum((a - mean_one) ** 2 for a in one) * sum((b - mean_two) ** 2 for b in two)
    )
    return covariance / spread if spread else 0.0


def test_the_rhotics_really_are_r_coloured() -> None:
    """F3 collapsing toward F2 is the acoustic signature. If it is absent, so is the finding.

    /ɝ/ sits near 300 Hz in Hillenbrand where every other vowel sits between 546 and 1613. A
    measured r-coloured category that does not show the same collapse was not measured — and
    the rhoticity chart is the surface most likely to be read as an instruction.
    """
    table = model_reference.MEN
    rhotics = [s for s in table if vowel_reference.vowel_class(s) == vowel_reference.RHOTIC]
    plain = [s for s in table if vowel_reference.vowel_class(s) != vowel_reference.RHOTIC]
    assert rhotics and plain

    def gap(symbol: str) -> float | None:
        return table[symbol].at50.f3_minus_f2

    rhotic_gaps = [g for g in (gap(s) for s in rhotics) if g is not None]
    plain_gaps = [g for g in (gap(s) for s in plain) if g is not None]
    assert sum(rhotic_gaps) / len(rhotic_gaps) < sum(plain_gaps) / len(plain_gaps), (
        "the r-coloured vowels do not sit lower in F3−F2 than the plain ones"
    )


# --- The switch between the two tables ---------------------------------------------------------


def test_the_two_references_are_selectable_and_never_blended() -> None:
    published = vowel_measure.reference_positions("men", source=vowel_measure.REFERENCE_PUBLISHED)
    measured = vowel_measure.reference_positions("men", source=vowel_measure.REFERENCE_VOICE)
    assert set(published) == set(vowel_reference.MEN)
    assert set(measured) == set(model_reference.MEN)
    assert len(measured) > len(published), "the measured table should cover more categories"


def test_only_the_measured_table_claims_a_between_talker_spread() -> None:
    """`voices` is how a surface tells which kind of SD it is holding."""
    assert all(entry.voices == 0 for entry in vowel_reference.MEN.values())
    assert all(entry.voices > 0 for entry in model_reference.MEN.values())


# --- The trajectory this table deliberately does NOT publish ------------------------------------


@pytest.mark.parametrize("reference_set", SETS)
def test_no_entry_publishes_a_diphthong_trajectory(reference_set: str) -> None:
    """Established by measuring, and encoded here so it cannot be quietly undone.

    The first build of this table had FACE gliding -225 Hz where General American glides about
    +140. The per-token dump said why: across all eight men's voices only twelve /eɪ/ tokens
    are long enough to sample at all, they come from three word types, and every one has a
    contaminating right context — "same" ends in a nasal whose murmur the 80% window reads as
    F1 240 Hz, and "way" is followed by the word "I".

    It is not an amplitude artefact and cannot be gated on one: that 80% window measures
    -17.4 dB against -16.4 dB at the midpoint. The velum is open at full voicing.

    So the number exists and does not mean what "F2 travel 20→80%" says — it describes what
    FOLLOWS each diphthong in this passage. Published as a target it would generate a confident
    "widen the glide" instruction derived from a following /m/.
    """
    for symbol, entry in model_reference.REFERENCE_SETS[reference_set].items():
        assert entry.at20.f2 is None, f"{reference_set}/{symbol} published a 20% point"
        assert entry.at80.f2 is None, f"{reference_set}/{symbol} published an 80% point"
        assert entry.f2_travel is None, f"{reference_set}/{symbol} published a travel target"


def test_the_midpoint_is_unaffected_and_is_what_every_entry_carries() -> None:
    """Only the EDGES are withheld. The 50% window sits in the middle of the vowel."""
    for reference_set in SETS:
        for symbol, entry in model_reference.REFERENCE_SETS[reference_set].items():
            assert entry.at50.f1 is not None, f"{reference_set}/{symbol} lost its midpoint"
            assert entry.at50.f2 is not None
            assert entry.duration_ms is not None


def test_a_trajectory_row_against_this_table_says_recorded_not_scored() -> None:
    """Withholding the target must reach the reader as a sentence, not as a blank cell."""
    speaker = {
        "eɪ": vowel_measure.VowelPosition(
            vowel="eɪ",
            n=6,
            f1_hz=450.0,
            f2_hz=2100.0,
            f3_hz=2700.0,
            f1_z=-0.4,
            f2_z=1.1,
            f3_z=0.2,
            duration_ms=95.0,
            f2_travel_hz=120.0,
            f3_minus_f2_hz=600.0,
            rms_dbfs=-18.0,
            n_trajectory=4,
        )
    }
    rows = vowel_measure._trajectory_findings(
        speaker, vowel_measure.reference_positions("men", source=vowel_measure.REFERENCE_VOICE)
    )
    assert rows, "the trajectory instrument produced no row at all"
    assert "recorded, not scored" in rows[0].delta
