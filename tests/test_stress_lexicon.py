"""CMUdict stress, and whether it really aligns onto the phonemes Azure returns."""

from __future__ import annotations

import json

import pytest

import phoneme_reference
import speech_analyzer
import stress_lexicon
from utils import Mode

FIXTURE_MODES = {
    "sample_azure_response.json": Mode.PARAGRAPH,
    "sample_azure_continuous.json": Mode.PARAGRAPH,
    "bad_delivery_capture.json": Mode.PARAGRAPH,
}


def _words(fixtures_dir, name: str):
    payload = json.loads((fixtures_dir / name).read_text(encoding="utf-8"))
    payloads = payload if isinstance(payload, list) else [payload]
    # Reference text is deliberately empty: the miscue diff would splice in omissions that
    # carry no phonemes, and this test is about the words Azure actually returned.
    _, _, words = speech_analyzer.normalise(payloads, "", FIXTURE_MODES[name])
    return words


# --- The mapping -----------------------------------------------------------------------------


def test_cmudict_has_no_schwa_so_the_stress_digit_carries_it() -> None:
    """AH0 is /ə/ and AH1 is /ʌ/; ER0 is /ɚ/ and ER1 is /ɝ/.

    Stripping the digit before mapping would merge each pair and destroy exactly the reduction
    signal the dictionary was added to supply. This is the assertion that keeps that from
    being quietly refactored away.
    """
    assert phoneme_reference.from_arpabet("AH0") == "ə"
    assert phoneme_reference.from_arpabet("AH1") == "ʌ"
    assert phoneme_reference.from_arpabet("AH2") == "ʌ"
    assert phoneme_reference.from_arpabet("ER0") == "ɚ"
    assert phoneme_reference.from_arpabet("ER1") == "ɝ"


def test_consonants_carry_no_stress_and_map_to_nothing() -> None:
    """`R` having no digit is what makes the two notations' vowel counts line up."""
    for consonant in ("R", "DH", "N", "HH", "ZH"):
        assert not phoneme_reference.is_arpabet_vowel(consonant)
        assert phoneme_reference.arpabet_stress(consonant) is None
        assert phoneme_reference.from_arpabet(consonant) == ""


def test_every_arpabet_vowel_maps_to_a_symbol_the_phoneme_table_knows() -> None:
    for base in phoneme_reference._ARPABET:
        for digit in "012":
            symbol = phoneme_reference.from_arpabet(f"{base}{digit}")
            assert symbol, f"{base}{digit} mapped to nothing"
            assert phoneme_reference.lookup(symbol) is not None, symbol


def test_every_vowel_has_a_wells_keyword_and_it_agrees_with_its_label() -> None:
    """The keyword table and the prose label must not drift apart in silence."""
    for symbol, entry in phoneme_reference.PHONEMES.items():
        if entry.kind not in stress_lexicon.VOCALIC_KINDS:
            continue
        keyword = phoneme_reference.keyword_for(symbol)
        assert keyword, f"/{symbol}/ has no lexical-set keyword"
        if "(" in entry.label:
            inside = entry.label[entry.label.index("(") + 1 : entry.label.rindex(")")]
            named = [part.strip().lower() for part in inside.split(",")]
            assert keyword.lower() in named, f"/{symbol}/: {keyword} not in {entry.label!r}"


# --- Coverage, measured rather than assumed --------------------------------------------------
# The two passage-coverage tests that lived here were deleted on 2026-08-25 with the benchmark
# and the calibration reads they measured. They asserted that CMUdict covered every word of
# `progress_view.BENCHMARK_PASSAGE` and yielded enough unstressed vowels for a schwa centroid;
# neither the passage nor the centroid exists any more, and retargeting them at an arbitrary
# preset would have been a new claim wearing an old test's name.


@pytest.mark.parametrize("name", sorted(FIXTURE_MODES))
def test_alignment_succeeds_on_almost_every_word_of_every_committed_fixture(
    fixtures_dir, name: str
) -> None:
    """101/101 measured when this was written. The gate is 95%, to leave room for new fixtures."""
    words = _words(fixtures_dir, name)
    results = [stress_lexicon.align_word(word) for word in words]
    aligned = [result for result in results if result.aligned]
    assert len(words) > 0
    assert len(aligned) / len(words) >= 0.95, [
        (result.word, result.reason) for result in results if not result.aligned
    ]


def test_a_word_whose_vowel_counts_disagree_is_refused_with_a_reason() -> None:
    """Refused rather than paired off by position, which would mislabel stress silently.

    A word with one dictionary vowel and three reported ones cannot be aligned by any
    honest rule. The failure has to name what it saw on each side, because "could not
    align" with no numbers is not something a reader can act on.
    """
    result = stress_lexicon.align("cat", ["æ", "æ", "æ"])
    assert not result.aligned
    assert "3" in result.reason and "1" in result.reason


def test_multiple_pronunciations_rescue_words_a_single_variant_would_lose() -> None:
    """*our* is why variant selection is not a nicety.

    Azure returns it as one r-coloured /ɑɹ/. CMUdict's FIRST listed pronunciation is
    `AW1 ER0` — two vowels, which does not align — but it also lists `AW1 R` and `AA1 R`,
    both one vowel. Taking the first variant would refuse this word; considering all of
    them aligns it, and picks the variant that actually agrees with what was said.

    Measured across the three committed Azure fixtures: **101 of 101 words align.** The
    scratch analysis done while planning looked only at first variants and predicted
    100/101, so this behaviour is better than the plan assumed, not worse.
    """
    assert len(stress_lexicon.variants("our")) >= 3
    result = stress_lexicon.align("our", ["ɑɹ"])
    assert result.aligned
    assert len(result.syllables) == 1
    # The STRESS is what travels downstream, and every one-vowel variant agrees it is
    # stressed. Which of `AW1 R` and `AA1 R` wins the tie is not asserted: neither spells
    # Azure's `ɑɹ`, so the agreement tie-break cannot separate them, and it does not matter
    # — the vowel's identity comes from Azure and only the digit comes from the dictionary.
    assert result.syllables[0].stress == stress_lexicon.PRIMARY


def test_a_word_the_dictionary_does_not_have_says_so() -> None:
    result = stress_lexicon.align("zzzznotaword", ["ɪ"])
    assert not result.aligned
    assert result.reason == "not in CMUdict"


# --- Choosing between pronunciations ---------------------------------------------------------


def test_the_variant_chosen_is_the_one_that_agrees_with_what_was_said() -> None:
    """*the* is `DH AH0` or `DH IY0`; *a* is `AH0` or `EY1`.

    Taking whichever the dictionary lists first would label a full vowel as reduced roughly
    whenever the speaker was emphasising. The acoustics only break the tie between variants —
    the stress digits still come from the dictionary, or the measurement would be circular.
    """
    assert stress_lexicon.align("the", ["ə"]).syllables[0].vowel == "ə"
    assert stress_lexicon.align("the", ["i"]).syllables[0].vowel == "i"


def test_stress_flags_read_the_way_english_reduction_works() -> None:
    """Primary and secondary both count as stressed; only the reduced ones collapse."""
    syllables = stress_lexicon.align("computer", ["ə", "u", "ɚ"]).syllables
    assert len(syllables) == 3
    assert [syllable.stressed for syllable in syllables] == [False, True, False]
    assert [syllable.primary for syllable in syllables] == [False, True, False]


def test_apostrophes_survive_normalisation() -> None:
    """CMUdict keys contractions with them, so `utils.normalise_words` would lose the hit."""
    assert stress_lexicon.normalise_word("Don't") == "don't"
    assert stress_lexicon.variants("don't")


def test_azure_vowels_reads_only_the_vocalic_phonemes() -> None:
    word = {
        "word": "weather",
        "phonemes": [
            {"phoneme": "w"},
            {"phoneme": "ɛ"},
            {"phoneme": "ð"},
            {"phoneme": "ɚ"},
        ],
    }
    assert stress_lexicon.azure_vowels(word) == ["ɛ", "ɚ"]
    result = stress_lexicon.align_word(word)
    assert result.aligned
    assert [syllable.stress for syllable in result.syllables] == [1, 0]
