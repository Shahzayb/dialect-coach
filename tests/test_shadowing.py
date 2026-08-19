"""Shadowing: phrase splitting, passage identity, and the mode that is deliberately unscored.

The load-bearing property here is also negative. A shadowed read that reaches the database
untagged is indistinguishable from a cold one afterwards, and it lands on the exact trajectory
the tag exists to keep it off — so most of what follows checks that the two are kept apart
rather than that either one works.
"""

from __future__ import annotations

import pytest

import progress_view
import shadowing


# --- Passage identity -------------------------------------------------------------------------
# The comparison pairs a shadowed read to a cold read BY MATCHING normalised reference text,
# so anything that splits one passage into two keys silently empties the whole feature.


def test_the_passage_key_survives_casing_punctuation_and_line_breaks() -> None:
    assert shadowing.passage_key("The cat sat.  It slept.") == shadowing.passage_key(
        "the cat sat\n\nit slept"
    )


def test_the_passage_key_matches_the_benchmark_key() -> None:
    """One normaliser, or a shadowed benchmark read pairs against nothing."""
    assert shadowing.passage_key(
        progress_view.BENCHMARK_PASSAGE
    ) == progress_view.benchmark_key(progress_view.BENCHMARK_PASSAGE)


def test_an_empty_passage_has_an_empty_key() -> None:
    assert shadowing.passage_key(None) == ""
    assert shadowing.passage_key("   ") == ""


def test_title_for_matches_by_key_not_by_string() -> None:
    passages = {"Mine": "The cat sat. It slept."}
    assert shadowing.title_for(passages, "the CAT sat.  it slept.") == "Mine"
    assert shadowing.title_for(passages, "something else entirely") is None


# --- Phrases ----------------------------------------------------------------------------------


def test_phrases_split_on_sentence_ends() -> None:
    found = shadowing.phrases("One thing happened. Then another thing did! Did it really?")
    assert found == [
        "One thing happened.", "Then another thing did!", "Did it really?",
    ]


def test_a_paragraph_break_is_not_a_phrase_boundary() -> None:
    """A blank line is a visual convenience in the passage text, not a place the reader stops."""
    found = shadowing.phrases("The first thing here.\n\nThe second thing here.")
    assert found == ["The first thing here.", "The second thing here."]


def test_a_short_fragment_merges_into_its_neighbour() -> None:
    """A one-word clip followed by a one-word silence is not shadowing practice."""
    found = shadowing.phrases("A long enough opening sentence. No. Another long one here.")
    assert found == ["A long enough opening sentence. No.", "Another long one here."]


def test_a_leading_fragment_merges_forward() -> None:
    found = shadowing.phrases("Yes. A long enough sentence follows it.")
    assert found == ["Yes. A long enough sentence follows it."]


def test_an_unsplittable_passage_comes_back_as_one_phrase() -> None:
    assert shadowing.phrases("no punctuation at all here") == ["no punctuation at all here"]


def test_an_empty_passage_has_no_phrases() -> None:
    assert shadowing.phrases("") == []
    assert shadowing.phrases(None) == []


def test_the_benchmark_passage_splits_into_real_phrases() -> None:
    found = shadowing.phrases(progress_view.BENCHMARK_PASSAGE)
    assert len(found) > 5
    assert all(len(phrase) >= shadowing.MIN_PHRASE_CHARS for phrase in found)
    # Nothing is lost or invented: the words come back in the same order.
    assert " ".join(found).split() == progress_view.BENCHMARK_PASSAGE.split()


# --- The echo gap ------------------------------------------------------------------------------


def test_the_echo_track_leaves_a_gap_as_long_as_each_phrase() -> None:
    """A fixed pause is too short for the long sentences and dead air after the short ones."""
    assert shadowing.echo_seconds([2.0, 4.0], tail_ms=0) == pytest.approx(12.0)


def test_the_echo_tail_is_added_once_per_phrase() -> None:
    assert shadowing.echo_seconds([2.0], tail_ms=400) == pytest.approx(4.4)


# --- Only one mode is assessed -----------------------------------------------------------------


def test_only_a_simultaneous_read_is_assessable() -> None:
    """An echo recording pauses between every phrase, so its fluency measures the format."""
    passage = "The cat sat. It slept."
    assert shadowing.Session("t", passage, shadowing.SIMULTANEOUS, False).assessable
    assert not shadowing.Session("t", passage, shadowing.ECHO, False).assessable


def test_both_modes_have_a_label_on_screen() -> None:
    assert set(shadowing.MODE_LABELS) == {shadowing.SIMULTANEOUS, shadowing.ECHO}


def test_the_echo_copy_says_it_is_not_assessed() -> None:
    """The reason has to be on screen, not only in a docstring — it looks like an omission."""
    assert "not assessed" in shadowing.ECHO_STEPS


def test_the_copy_names_headphones_and_says_why() -> None:
    assert "Headphones" in shadowing.HEADPHONES
    assert "mixture" in shadowing.HEADPHONES


def test_the_surface_says_nothing_is_scored_while_shadowing() -> None:
    assert "not another reading you get marked on" in shadowing.NOT_A_MEASUREMENT


# --- Evidence ----------------------------------------------------------------------------------


def test_shadow_evidence_says_it_was_not_promoted() -> None:
    """A standing practice makes no claim about the user's flagged history and must not look
    as though it does."""
    session = shadowing.Session("Benchmark", "The cat sat.", shadowing.SIMULTANEOUS, False)
    evidence = shadowing.evidence_for(session)
    assert evidence["source"] == "shadowing"
    assert "nothing promoted it" in str(evidence["why"])
    assert evidence["passage_key"] == shadowing.passage_key("The cat sat.")
