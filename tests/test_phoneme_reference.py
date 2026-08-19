"""The static IPA table: coverage against the real payloads, and honest degradation.

The coverage test here is deliberately the only *provable* claim about the inventory.
"Covers en-US" cannot be asserted from inside the repo; "resolves every symbol that
appears in the two committed Azure payloads" can, and it is the claim that actually
matters — a symbol Azure emits and the table misses produces a silently empty note.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import phoneme_reference as pr

FIXTURES = ("sample_azure_response.json", "sample_azure_continuous.json")


def _payload_symbols(fixtures_dir: Path) -> tuple[set[str], set[str]]:
    """(expected symbols, produced symbols) across both committed payloads."""
    expected: set[str] = set()
    produced: set[str] = set()
    for name in FIXTURES:
        data = json.loads((fixtures_dir / name).read_text(encoding="utf-8"))
        for utterance in data if isinstance(data, list) else [data]:
            for word in utterance["NBest"][0].get("Words", []):
                for phoneme in word.get("Phonemes", []):
                    expected.add(phoneme["Phoneme"])
                    assessment = phoneme.get("PronunciationAssessment", {})
                    for alternate in assessment.get("NBestPhonemes", []):
                        produced.add(alternate["Phoneme"])
    return expected, produced


# --- Coverage ---------------------------------------------------------------------------


def test_every_expected_phoneme_in_the_fixtures_resolves(fixtures_dir: Path) -> None:
    expected, _ = _payload_symbols(fixtures_dir)
    missing = sorted(symbol for symbol in expected if pr.lookup(symbol) is None)
    assert not missing, f"Azure emits these as targets and the table has no entry: {missing}"


def test_every_produced_alternate_in_the_fixtures_resolves(fixtures_dir: Path) -> None:
    """The produced side comes from `NBestPhonemes` and is what the report names."""
    _, produced = _payload_symbols(fixtures_dir)
    missing = sorted(symbol for symbol in produced if pr.lookup(symbol) is None)
    assert not missing, f"Azure offers these as alternates and the table has no entry: {missing}"


# --- Normalisation ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written, azure",
    [
        ("iː", "i"),
        ("ɜː", "ɝ"),
        ("ɑː", "ɑ"),
        ("ɔː", "ɔ"),
        ("uː", "u"),
        ("ɡ", "g"),
        ("r", "ɹ"),
        ("ʧ", "tʃ"),
        ("ʤ", "dʒ"),
        ("əʊ", "oʊ"),
        ("ˈɑ", "ɑ"),
    ],
)
def test_textbook_spellings_resolve_to_azures_symbols(written: str, azure: str) -> None:
    """Azure's IPA is rhotic and length-mark-free; sources and hands write it otherwise."""
    assert pr.normalise(written) == azure
    assert pr.lookup(written) is pr.lookup(azure)


def test_normalise_survives_nothing_at_all() -> None:
    assert pr.normalise(None) == ""
    assert pr.normalise("") == ""
    assert pr.lookup(None) is None


def test_case_is_not_folded() -> None:
    """IPA is case-significant; lowercasing would merge symbols rather than tidy them."""
    assert pr.normalise("I") == "I"


# --- Degradation --------------------------------------------------------------------------


def test_an_unknown_sound_degrades_to_no_note_never_to_a_wrong_one() -> None:
    assert pr.articulation_for("ǁ") == pr.NO_NOTE
    assert pr.minimal_pairs("ǁ", "s") == []


def test_an_unwritten_pair_between_two_known_sounds_still_degrades() -> None:
    """/θ/ and /ŋ/ both exist; that substitution has not been written up."""
    assert pr.contrast("θ", "ŋ") is None
    assert pr.minimal_pairs("θ", "ŋ") == []


def test_an_unwritten_pair_states_the_fact_rather_than_inventing_a_consequence() -> None:
    note = pr.why_it_matters("θ", "ŋ")
    assert "/ŋ/" in note and "/θ/" in note
    assert "listener" not in note.lower()


def test_articulation_is_the_targets_regardless_of_what_came_out() -> None:
    """Advice for making a /θ/ does not change with what was produced instead."""
    assert pr.articulation_for("θ", "s") == pr.articulation_for("θ", "t")
    assert pr.articulation_for("θ") == pr.articulation_for("θ", "f")


# --- Data invariants -------------------------------------------------------------------------


def test_every_entry_has_a_concrete_articulation_and_a_label() -> None:
    for symbol, entry in pr.PHONEMES.items():
        assert entry.articulation.strip(), f"/{symbol}/ has no articulation note"
        assert entry.label.strip(), f"/{symbol}/ has no label"
        assert entry.symbol == symbol


def test_every_contrast_is_keyed_by_the_symbol_azure_would_send() -> None:
    """A contrast keyed by a textbook spelling could never be found by a lookup."""
    for symbol, entry in pr.PHONEMES.items():
        for produced, contrast in entry.contrasts.items():
            assert pr.normalise(produced) == produced, f"/{symbol}/ -> /{produced}/"
            assert contrast.produced == produced
            assert produced in pr.PHONEMES, f"/{produced}/ has no entry of its own"
            assert produced != symbol, f"/{symbol}/ contrasts with itself"


def test_every_contrast_says_what_it_costs() -> None:
    for entry in pr.PHONEMES.values():
        for contrast in entry.contrasts.values():
            assert contrast.why_it_matters.strip()


def test_minimal_pairs_are_genuine_pairs() -> None:
    for entry in pr.PHONEMES.values():
        for contrast in entry.contrasts.values():
            for first, second in contrast.minimal_pairs:
                assert first and second
                assert first != second, f"/{entry.symbol}/ -> /{contrast.produced}/"


def test_the_seed_sounds_all_carry_pairs_to_drill() -> None:
    """The sounds this project was built to catch must be actionable, not just described."""
    for expected, produced in [
        ("θ", "s"),
        ("θ", "t"),
        ("ð", "d"),
        ("v", "w"),
        ("w", "v"),
        ("æ", "ɛ"),
        ("ɛ", "ɪ"),
        ("ɪ", "i"),
        ("i", "ɪ"),
        ("ʌ", "ɑ"),
        ("l", "ɹ"),
        ("z", "dʒ"),
        ("ʃ", "s"),
        ("f", "p"),
        ("t", "d"),
        ("d", "t"),
        ("ɝ", "æ"),
    ]:
        assert pr.minimal_pairs(expected, produced), f"/{expected}/ -> /{produced}/"


def test_a_duplicate_symbol_is_refused_rather_than_silently_overwriting() -> None:
    """A repeated key would drop the earlier entry's contrasts invisibly."""
    twice = (pr.PHONEMES["θ"], pr.PHONEMES["θ"])
    with pytest.raises(RuntimeError, match="Duplicate"):
        pr._build(twice)
