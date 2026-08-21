"""Ladder targets: measured-only resolution, the level-above check, and the automatic reopen.

The three exits are **measured**, **bailed** and **reopened**. Marking something resolved by
hand is deliberately not one of them, and several tests below exist to keep it that way — a
target can only leave the list because an instrument said so.
"""

from __future__ import annotations

import ladder
import practice_queue

BANDS = {
    "npvi": ladder.Band("npvi", mean=55.0, sd=4.0, voices=16),
    "pitch_range_st": ladder.Band("pitch_range_st", mean=10.0, sd=2.0, voices=16),
    "terminal_slope_st": ladder.Band("terminal_slope_st", mean=-4.0, sd=1.5, voices=16),
}
FLOOR = ladder.MetricFloor(per_metric={"npvi": 2.0}, units=6)


def _span(rung: ladder.Rung) -> ladder.Span:
    return ladder.Span(rung=rung, label="x", start_s=0.0, end_s=1.0, word_indices=(0,))


def _verdict(rung: ladder.Rung, npvi: float, previous: float | None = None) -> ladder.Verdict:
    return ladder.verdict(
        _span(rung),
        {"npvi": npvi},
        BANDS,
        previous=None if previous is None else {"npvi": previous},
        floor=FLOOR,
    )


def _target(kind: str, state: str = practice_queue.ACTIVE) -> dict[str, object]:
    return {"item": "the cat sat", "kind": kind, "state": state, "reviews_passed": 0}


RESOLVED = 56.0  # inside the band
FAR = 75.0  # well outside it
BEFORE = 70.0  # far enough from RESOLVED to clear the floor


# --- The kinds ------------------------------------------------------------------------------


def test_the_three_measured_rungs_are_ladder_kinds() -> None:
    assert {"word", "sentence", "paragraph"} == practice_queue.LADDER_KINDS


def test_the_sound_rung_is_not_a_ladder_kind() -> None:
    """It keeps the contrast and vowel machinery, which already works."""
    assert not practice_queue.is_ladder(practice_queue.CONTRAST)
    assert not practice_queue.is_ladder(practice_queue.VOWEL)
    assert "sound" not in practice_queue.LADDER_KINDS


# --- Resolution needs both bars AND the rung above --------------------------------------------


def test_clearing_both_bars_alone_does_not_graduate_a_sentence() -> None:
    """#42: in isolation you hyperarticulate. The paragraph check is not optional."""
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE), _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE)
    )
    assert decision.state == practice_queue.ACTIVE
    assert "survives inside its paragraph" in decision.reason


def test_a_sentence_graduates_once_it_survives_inside_its_paragraph() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE),
        _verdict(ladder.Rung.PARAGRAPH, RESOLVED, BEFORE),
    )
    assert decision.state == practice_queue.GRADUATED


def test_right_on_its_own_but_wrong_in_context_stays_on_the_list() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE),
        _verdict(ladder.Rung.PARAGRAPH, FAR, BEFORE),
    )
    assert decision.state == practice_queue.ACTIVE
    assert "not inside its paragraph yet" in decision.reason


def test_the_paragraph_is_checked_against_itself_and_needs_nothing_above() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.PARAGRAPH), _verdict(ladder.Rung.PARAGRAPH, RESOLVED, BEFORE)
    )
    assert decision.state == practice_queue.GRADUATED
    assert ladder.TOP_RUNG_NOTE in decision.reason


def test_a_target_that_has_not_been_measured_is_neither_resolved_nor_failing() -> None:
    decision = practice_queue.grade_ladder(_target(practice_queue.SENTENCE), None)
    assert decision.state == practice_queue.ACTIVE
    assert "Not measured yet" in decision.reason


def test_a_verdict_that_clears_neither_bar_keeps_the_target_and_says_why() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE), _verdict(ladder.Rung.SENTENCE, FAR, BEFORE)
    )
    assert decision.state == practice_queue.ACTIVE
    assert "not yet inside the native range" in decision.reason
    assert "SD outside" in decision.reason


def test_arrival_without_real_movement_says_so_in_words() -> None:
    """The reason has to name which bar failed, or the card asserts rather than explains."""
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, RESOLVED + 0.5),
    )
    assert "smaller than your own session-to-session variation" in decision.reason


# --- The automatic reopen ---------------------------------------------------------------------


def test_a_graduated_rung_reopens_when_it_stops_surviving_above() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE, practice_queue.GRADUATED),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE),
        _verdict(ladder.Rung.PARAGRAPH, FAR, BEFORE),
    )
    assert decision.state == practice_queue.ACTIVE
    assert decision.regressed
    assert decision.reviews_passed == 0


def test_the_reopen_reads_as_information_not_punishment() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE, practice_queue.GRADUATED),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE),
        _verdict(ladder.Rung.PARAGRAPH, FAR, BEFORE),
    )
    assert "not a step backwards" in decision.reason
    assert "no longer survives inside the paragraph" in decision.reason


def test_a_graduated_rung_that_still_survives_stays_graduated() -> None:
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE, practice_queue.GRADUATED),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE),
        _verdict(ladder.Rung.PARAGRAPH, RESOLVED, BEFORE),
    )
    assert decision.state == practice_queue.GRADUATED
    assert not decision.regressed


def test_a_graduated_rung_is_not_reopened_by_an_unmeasured_level_above() -> None:
    """Absence of a check is not evidence of failure; it must not silently demote."""
    decision = practice_queue.grade_ladder(
        _target(practice_queue.SENTENCE, practice_queue.GRADUATED),
        _verdict(ladder.Rung.SENTENCE, RESOLVED, BEFORE),
        None,
    )
    assert decision.state == practice_queue.GRADUATED


# --- The rule the card renders verbatim --------------------------------------------------------


def test_the_rule_states_both_bars_the_level_above_and_the_automatic_reopen() -> None:
    rule = practice_queue.graduation_rule(practice_queue.SENTENCE)
    assert "no way to mark this done by hand" in rule
    assert "session-to-session variation" in rule
    assert "survive inside its paragraph" in rule
    assert "come back on its own" in rule


def test_the_paragraph_rule_does_not_promise_a_level_above_it_does_not_have() -> None:
    rule = practice_queue.graduation_rule(practice_queue.PARAGRAPH)
    assert ladder.TOP_RUNG_NOTE in rule
