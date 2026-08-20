"""The arrow-to-instruction mapping: the one place this project can be confidently wrong.

A wrong articulatory instruction, delivered confidently, is the exact failure the project
exists to eliminate — worse than saying nothing, because the learner acts on it. These tests
are what stop `vowel_measure` regrowing a generated instruction.
"""

from __future__ import annotations

import pytest

import phoneme_reference
import vowel_measure
import vowel_reference
from vowel_reference import BACK_ROUNDED, FRONT_UNROUNDED, RHOTIC

INVENTORY = sorted(phoneme_reference.LEXICAL_SET)


# --- Coverage --------------------------------------------------------------------------------


@pytest.mark.parametrize("symbol", INVENTORY)
def test_every_vowel_in_the_inventory_is_classified(symbol: str) -> None:
    """A vowel with no class would fall through to a generated instruction, which is the bug."""
    assert vowel_reference.vowel_class(symbol), f"/{symbol}/ has no articulatory class"


@pytest.mark.parametrize("symbol", INVENTORY)
def test_every_vowel_in_the_inventory_has_an_instruction(symbol: str) -> None:
    entry = vowel_reference.ARTICULATION.get(symbol)
    assert entry is not None, f"/{symbol}/ has no ARTICULATION entry"
    for field in ("f1_raise", "f1_lower", "f2_raise", "f2_lower"):
        assert getattr(entry, field).strip(), f"/{symbol}/ has an empty {field}"


def test_the_mapping_covers_the_inventory_and_nothing_else() -> None:
    """An entry for a symbol Azure never emits is dead text nobody will ever read or check."""
    assert set(vowel_reference.ARTICULATION) == set(INVENTORY)
    assert set(vowel_reference.VOWEL_CLASS) == set(INVENTORY)


# --- The rule the whole table exists for ------------------------------------------------------


@pytest.mark.parametrize(
    "symbol", sorted(s for s in INVENTORY if vowel_reference.vowel_class(s) == BACK_ROUNDED)
)
def test_a_back_rounded_vowel_never_gets_a_tongue_instruction_for_f2(symbol: str) -> None:
    """F2 responds to lip posture as strongly as to tongue advancement.

    A learner whose /u/ sits too high in F2 has almost always under-rounded rather than
    fronted the tongue. "Move your tongue back" sends them to the wrong articulator and makes
    the vowel worse — so for these vowels the F2 instruction talks about lips, or it is wrong.
    """
    entry = vowel_reference.ARTICULATION[symbol]
    for direction, text in (("raise", entry.f2_raise), ("lower", entry.f2_lower)):
        assert "tongue" not in text.lower(), (
            f"/{symbol}/ f2_{direction} tells a back ROUNDED vowel to move its tongue: {text!r}"
        )
        assert "lip" in text.lower() or "round" in text.lower(), (
            f"/{symbol}/ f2_{direction} says nothing about lip posture: {text!r}"
        )


@pytest.mark.parametrize(
    "symbol", sorted(s for s in INVENTORY if vowel_reference.vowel_class(s) == FRONT_UNROUNDED)
)
def test_a_front_unrounded_vowel_is_never_told_to_round_its_lips(symbol: str) -> None:
    """Rounding a front vowel does lower F2 — and produces a vowel English does not use."""
    entry = vowel_reference.ARTICULATION[symbol]
    for direction, text in (("raise", entry.f2_raise), ("lower", entry.f2_lower)):
        assert "round the lips" not in text.lower(), (
            f"/{symbol}/ f2_{direction} rounds a FRONT vowel: {text!r}"
        )


@pytest.mark.parametrize(
    "symbol", sorted(s for s in INVENTORY if vowel_reference.vowel_class(s) == RHOTIC)
)
def test_a_rhotic_redirects_to_f3_rather_than_instructing_height_or_frontness(
    symbol: str,
) -> None:
    """For an r-coloured vowel F3 is the measure and F1/F2 are secondary.

    The instruction is about tongue bunching or retraction plus lip rounding — never about
    height or frontness. So the F1/F2 fields say exactly that and nothing else: they are the
    shared redirect string, not a per-vowel instruction that could drift into one.
    """
    entry = vowel_reference.ARTICULATION[symbol]
    for field in ("f1_raise", "f1_lower", "f2_raise", "f2_lower"):
        assert getattr(entry, field) == vowel_reference.RHOTIC_SECONDARY, (
            f"/{symbol}/ {field} instructs height or frontness on an r-coloured vowel"
        )
    assert entry.f3_raise and entry.f3_lower, f"/{symbol}/ has no F3 instruction"
    assert any(word in entry.f3_lower.lower() for word in ("bunch", "curl", "back")), (
        f"/{symbol}/ f3_lower does not name the gesture: {entry.f3_lower!r}"
    )


@pytest.mark.parametrize(
    "symbol", sorted(s for s in INVENTORY if vowel_reference.vowel_class(s) != RHOTIC)
)
def test_only_rhotics_carry_an_f3_instruction(symbol: str) -> None:
    """F3 is a finding only where r-colouring is what is being measured."""
    entry = vowel_reference.ARTICULATION[symbol]
    assert not entry.f3_raise and not entry.f3_lower
    assert vowel_reference.instruction_for(symbol, "F3", 1.0) == ""


# --- The lookup itself -----------------------------------------------------------------------


def test_the_sign_selects_the_direction() -> None:
    """Delta is target minus produced, so a positive delta asks the speaker to move UP."""
    entry = vowel_reference.ARTICULATION["i"]
    assert vowel_reference.instruction_for("i", "F1", 0.4) == entry.f1_raise
    assert vowel_reference.instruction_for("i", "F1", -0.4) == entry.f1_lower
    assert vowel_reference.instruction_for("i", "F2", 0.4) == entry.f2_raise
    assert vowel_reference.instruction_for("i", "F2", -0.4) == entry.f2_lower


@pytest.mark.parametrize("delta", [None, 0.0])
def test_no_delta_produces_no_instruction(delta: float | None) -> None:
    """An empty string renders as "no instruction"; a guess would render as advice."""
    assert vowel_reference.instruction_for("i", "F1", delta) == ""


def test_an_unknown_symbol_produces_no_instruction() -> None:
    assert vowel_reference.instruction_for("ʉ", "F2", 1.0) == ""
    assert vowel_reference.instruction_for("", "F2", 1.0) == ""
    assert vowel_reference.instruction_for("i", "F9", 1.0) == ""


def test_the_low_back_pair_is_marked_as_merging_rather_than_wrong() -> None:
    assert vowel_reference.is_merging("ɑ")
    assert vowel_reference.is_merging("ɔ")
    assert not vowel_reference.is_merging("i")
    # And the two the tolerance table widens are exactly the merger plus GOOSE-fronting.
    assert set(vowel_reference.MERGING) <= set(vowel_reference.TOLERANCE_MULTIPLIER)


# --- What reaches the four-column table ------------------------------------------------------


def test_a_back_rounded_vowel_never_reaches_the_table_with_a_tongue_instruction() -> None:
    """The end-to-end version of the rule, through the renderer a surface actually calls.

    Written to fail against v0.10.0, where `_position_instruction` generated "tongue further
    back, lips rounder" from the sign alone for every vowel in the inventory.
    """
    rendered = vowel_measure._position_instruction("u", "F2 (Lobanov z)", -0.8, None)
    assert "tongue" not in rendered.lower(), rendered
    assert "round the lips" in rendered.lower(), rendered


def test_a_front_vowel_still_gets_its_tongue_instruction() -> None:
    rendered = vowel_measure._position_instruction("i", "F2 (Lobanov z)", 0.8, None)
    assert "tongue" in rendered.lower(), rendered
    assert "+0.80 z" in rendered


def test_a_merging_vowel_says_so_before_it_instructs() -> None:
    rendered = vowel_measure._position_instruction("ɔ", "F2 (Lobanov z)", -0.9, None)
    assert "merged or merging" in rendered.lower(), rendered


def test_the_noise_floor_still_wins_over_any_instruction() -> None:
    """Within the band there is no finding, so there is nothing to instruct."""
    noise = vowel_measure.NoiseFloor(per_vowel={"u": 0.5}, median_z=0.5, vowels=1)
    rendered = vowel_measure._position_instruction("u", "F2 (Lobanov z)", -0.2, noise)
    assert vowel_measure.WITHIN_NOISE in rendered
    assert "lips" not in rendered.lower()


# --- The offline half of the coaching loop ---------------------------------------------------


@pytest.mark.parametrize("symbol", INVENTORY)
def test_every_vowel_has_bridging_phrases(symbol: str) -> None:
    """The fallback coach must be able to answer any vowel the measurement can flag."""
    phrases = vowel_reference.bridging_phrases(symbol)
    assert len(phrases) >= 2, f"/{symbol}/ has {len(phrases)} bridging phrase(s)"
    for phrase in phrases:
        assert phrase.strip().endswith((".", "?", "!")), f"/{symbol}/: {phrase!r} is not a sentence"
        # A sentence, not a word list. The value is the co-articulation, and a comma-separated
        # list of citation-form words exercises none of it.
        assert len(phrase.split()) >= 6, f"/{symbol}/: {phrase!r} is too short to co-articulate"


def test_pre_fortis_pairs_are_real_pairs_on_known_vowels() -> None:
    for pair in vowel_reference.PRE_FORTIS_PAIRS:
        assert pair.vowel in phoneme_reference.LEXICAL_SET, pair
        assert pair.long != pair.short, pair


def test_stress_shift_pairs_carry_both_placements_and_a_sentence_using_both() -> None:
    """A stress pair that does not force both readings in one sentence is two flashcards."""
    for pair in vowel_reference.STRESS_SHIFT_PAIRS:
        assert pair.noun != pair.verb, pair
        assert pair.noun.lower() == pair.word, pair
        assert pair.verb.lower() == pair.word, pair
        assert pair.noun in pair.sentence and pair.verb in pair.sentence, pair
