"""Block planning: variability, novelty, and the chance floor.

The properties asserted here are the ones that make a block *training* rather than a quiz.
A block that cycles one voice, or that presents the same word-and-voice pairing every time,
or that always puts the answer on the left, would still look correct on screen.
"""

from __future__ import annotations

import itertools
import random
from typing import Any

import pytest

import perception_trainer as pt
import phoneme_reference
import utils

# A flagship contrast with five pairs written up — the one the project was built to catch.
EXPECTED, PRODUCED = "θ", "s"
ITEM = "/θ/ → /s/"


def build(**kwargs: Any) -> pt.Block:
    defaults: dict[str, Any] = {
        "item": ITEM,
        "expected": EXPECTED,
        "produced": PRODUCED,
        "rng": random.Random(7),
    }
    defaults.update(kwargs)
    return pt.build_block(**defaults)


# --- The chance floor -----------------------------------------------------------------------


def test_chance_floor_is_derived_not_assumed() -> None:
    assert pt.chance_floor(2) == 0.5
    assert pt.chance_floor(4) == 0.25


def test_chance_floor_rejects_a_choice_that_is_not_one() -> None:
    with pytest.raises(ValueError):
        pt.chance_floor(1)


def test_chance_caption_names_the_floor_and_the_number_of_choices() -> None:
    caption = pt.chance_caption(2)
    assert "50%" in caption and "guessing" in caption
    assert "25%" in pt.chance_caption(4)


# --- Preconditions --------------------------------------------------------------------------


def test_a_block_refuses_to_run_under_four_voices() -> None:
    """Degrading to fewer voices would look like training while training the wrong thing."""
    with pytest.raises(pt.BlockError, match="at least 4 distinct voices"):
        build(voices=("en-US-AvaNeural", "en-US-BrianNeural"))


def test_repeated_voice_names_do_not_count_toward_the_minimum() -> None:
    with pytest.raises(pt.BlockError):
        build(voices=("a", "a", "b", "b"))


def test_a_contrast_with_too_few_pairs_is_refused() -> None:
    # /ʒ/ → /z/ has a single pair written up.
    assert not pt.trainable("ʒ", "z")
    with pytest.raises(pt.BlockError, match="minimal pairs"):
        build(expected="ʒ", produced="z")


def test_an_unwritten_pair_is_not_trainable_rather_than_invented() -> None:
    assert pt.pairs_for("θ", "ŋ") == []
    assert not pt.trainable("θ", "ŋ")


def test_the_flagship_contrasts_are_all_trainable() -> None:
    for expected, produced in (("θ", "s"), ("θ", "t"), ("ð", "d"), ("v", "w"), ("l", "ɹ")):
        assert pt.trainable(expected, produced), f"/{expected}/ → /{produced}/"


# --- Kinds ---------------------------------------------------------------------------------


def test_consonants_and_vowels_are_told_apart_by_the_reference_table() -> None:
    assert pt.kind_for("θ") == pt.CONTRAST
    assert pt.kind_for("v") == pt.CONTRAST
    assert pt.kind_for("æ") == pt.VOWEL
    assert pt.kind_for("eɪ") == pt.VOWEL  # diphthong
    assert pt.kind_for("ɝ") == pt.VOWEL  # r-coloured


def test_every_vowel_kind_in_the_reference_table_reads_as_a_vowel_gap() -> None:
    for symbol, entry in phoneme_reference.PHONEMES.items():
        if entry.kind in {"vowel", "diphthong", "r-coloured"}:
            assert pt.kind_for(symbol) == pt.VOWEL, symbol


# --- Variability, which is the active ingredient ---------------------------------------------


def test_a_block_uses_every_voice() -> None:
    block = build()
    assert {t.voice for t in block.trials} == set(pt.VOICES)


def test_consecutive_trials_never_repeat_a_voice() -> None:
    """Five trials of one voice then five of the next is four single-talker blocks."""
    for seed in range(20):
        block = build(rng=random.Random(seed))
        voices = [t.voice for t in block.trials]
        assert all(a != b for a, b in itertools.pairwise(voices)), seed


def test_consecutive_trials_avoid_repeating_a_pair() -> None:
    block = build()
    pairs = [frozenset({t.word, t.other}) for t in block.trials]
    assert all(a != b for a, b in itertools.pairwise(pairs))


def test_both_words_of_a_pair_come_up_as_the_played_word() -> None:
    """If only the expected word were ever played, the answer is the question."""
    block = build(trials=40)
    played = {t.word for t in block.trials}
    for first, second in pt.pairs_for(EXPECTED, PRODUCED):
        assert first in played and second in played


def test_the_answer_is_not_always_in_the_same_position() -> None:
    block = build(trials=40)
    firsts = [t.alternatives[0] == t.word for t in block.trials]
    assert any(firsts) and not all(firsts)


def test_alternatives_are_always_the_two_words_of_the_pair() -> None:
    for trial in build().trials:
        assert set(trial.alternatives) == {trial.word, trial.other}
        assert len(trial.alternatives) == 2


# --- Novelty ----------------------------------------------------------------------------------


def test_everything_is_novel_the_first_time() -> None:
    block = build()
    assert block.novel_count == len(block.trials)


def test_unheard_combinations_are_preferred_over_heard_ones() -> None:
    """The definition that makes 'unseen items' workable: an unheard (word, voice).

    Five pairs over six voices is a 60-stimulus pool, so a second block has plenty left
    that has never been played and must take from there first.
    """
    first = build()
    heard = {(t.word, t.voice): "2026-08-01T00:00:00Z" for t in first.trials}
    second = build(heard=heard, rng=random.Random(11))
    reused = [t for t in second.trials if not t.novel]
    assert reused == []
    assert not ({(t.word, t.voice) for t in second.trials} & set(heard))


def test_once_novelty_runs_out_the_least_recently_heard_comes_back() -> None:
    # The whole pool, so nothing unheard is left for the block to prefer.
    pool = [
        (word, voice)
        for first, second in pt.pairs_for(EXPECTED, PRODUCED)
        for word in (first, second)
        for voice in pt.VOICES
    ]
    half = len(pool) // 2
    old = dict.fromkeys(pool[:half], "2026-08-01T00:00:00Z")
    recent = dict.fromkeys(pool[half:], "2026-08-09T00:00:00Z")

    block = build(heard={**old, **recent}, rng=random.Random(3))
    assert block.novel_count == 0
    assert all((t.word, t.voice) in old for t in block.trials)


def test_a_short_pool_caps_the_block_rather_than_repeating_a_stimulus() -> None:
    """/ð/ → /z/ has three pairs, so the pool is 3 x 2 words x however many voices."""
    expected_pool = len(pt.pairs_for("ð", "z")) * 2 * len(pt.VOICES)
    block = pt.build_block(
        item="/ð/ → /z/", expected="ð", produced="z", trials=1000, rng=random.Random(1)
    )
    stimuli = [(t.word, t.voice) for t in block.trials]
    assert len(stimuli) == expected_pool
    assert len(set(stimuli)) == expected_pool, "a stimulus was played twice in one block"


# --- Determinism and length -------------------------------------------------------------------


def test_the_same_seed_plans_the_same_block() -> None:
    a = build(rng=random.Random(99))
    b = build(rng=random.Random(99))
    assert a.trials == b.trials


def test_block_and_review_lengths_come_from_the_conventions() -> None:
    assert len(build().trials) == utils.PERCEPTION_BLOCK_TRIALS
    assert len(build(review=True).trials) == utils.PERCEPTION_REVIEW_TRIALS


# --- What has to be synthesised ------------------------------------------------------------------


def test_stimuli_cover_both_words_of_every_pair_in_the_trial_voice() -> None:
    """ "Replay both" is on every reveal, so the other half cannot be missing."""
    block = build()
    needed = set(pt.stimuli(block))
    for trial in block.trials:
        assert (trial.word, trial.voice) in needed
        assert (trial.other, trial.voice) in needed


def test_stimuli_are_de_duplicated() -> None:
    block = build()
    assert len(pt.stimuli(block)) == len(set(pt.stimuli(block)))


# --- Scoring ---------------------------------------------------------------------------------


def test_score_reports_accuracy_against_its_own_chance_floor() -> None:
    result = pt.score([True] * 13 + [False] * 7, alternatives=2, novel=20, planned=20)
    assert result.accuracy == pytest.approx(0.65)
    assert result.chance == 0.5
    assert result.above_chance


def test_a_block_at_the_floor_is_not_above_it() -> None:
    result = pt.score([True] * 10 + [False] * 10, alternatives=2, novel=20, planned=20)
    assert not result.above_chance


def test_an_abandoned_block_is_incomplete_but_still_scored() -> None:
    result = pt.score([True] * 5, alternatives=2, novel=5, planned=20)
    assert not result.complete
    assert result.accuracy == 1.0


def test_a_finished_block_is_complete() -> None:
    assert pt.score([True] * 20, alternatives=2, novel=20, planned=20).complete


def test_novel_fraction_is_reported() -> None:
    result = pt.score([True] * 20, alternatives=2, novel=15, planned=20)
    assert result.novel_fraction == pytest.approx(0.75)


# --- The voice roster ---------------------------------------------------------------------------


def test_the_configured_roster_meets_its_own_minimum() -> None:
    assert len(set(pt.VOICES)) >= pt.MIN_VOICES


def test_the_roster_is_not_the_single_playback_voice() -> None:
    """AZURE_TTS_VOICE is one consistent model for imitation; this is variety for
    identification. Collapsing them would defeat both."""
    import tts

    assert len(set(pt.VOICES) - {tts.voice_name()}) >= pt.MIN_VOICES - 1
