"""The queue's policy: what gets promoted, what graduates, and when things come back.

The load-bearing property is negative: the queue must never offer a target the recordings
did not produce. Most of what follows checks a refusal rather than a result.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import practice_queue as pq
import utils

NOW = datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


def phoneme_row(expected: str, produced: str, attempts: int = 3, **extra):
    row = {
        "label": f"/{expected}/ → /{produced}/", "expected": expected,
        "produced": produced, "attempts": attempts, "benchmark_attempts": 1,
        "tokens": attempts * 2,
    }
    row.update(extra)
    return row


def syllable_row(word: str, attempts: int = 3, syllable: str = "ɚ"):
    return {"word": word, "syllable": syllable, "attempts": attempts,
            "benchmark_attempts": 0, "tokens": attempts}


def target(item: str, kind: str = pq.CONTRAST, state: str = pq.ACTIVE, **extra):
    row = {"id": 1, "item": item, "kind": kind, "state": state, "reviews_passed": 0,
           "added": "2026-08-01T00:00:00Z", "next_due": "2026-08-01T00:00:00Z",
           "evidence": "{}", "last_seen": None}
    row.update(extra)
    return row


def block(correct: int, total: int = 20, *, review: bool = False, when: str = "2026-08-10",
          planned: int | None = None):
    return pq.BlockSummary(
        block_id=f"b{when}{correct}", created_at=f"{when}T00:00:00Z", correct=correct,
        total=total, planned=planned if planned is not None else total, novel=total,
        alternatives=2, review=review,
    )


# --- Candidates: only what the recordings produced -------------------------------------------


def test_a_substitution_below_the_recurrence_floor_is_not_offered() -> None:
    """One bad reading is not a pattern."""
    found = pq.candidates([phoneme_row("θ", "s", attempts=1)])
    assert found == []


def test_a_recurring_substitution_becomes_a_candidate() -> None:
    found = pq.candidates([phoneme_row("θ", "s")])
    assert [c.item for c in found] == ["/θ/ → /s/"]
    assert found[0].kind == pq.CONTRAST


def test_an_unclear_phoneme_is_dropped_rather_than_offered() -> None:
    """'/θ/ → (unclear)' has no second word to put it against."""
    found = pq.candidates([phoneme_row("θ", "(unclear)")])
    assert found == []


def test_a_substitution_with_too_few_pairs_is_dropped() -> None:
    assert pq.candidates([phoneme_row("ʒ", "z")]) == []


def test_a_vowel_substitution_is_a_vowel_gap() -> None:
    found = pq.candidates([phoneme_row("æ", "ɛ")])
    assert found[0].kind == pq.VOWEL


def test_a_weak_syllable_becomes_a_stress_candidate() -> None:
    found = pq.candidates([], [syllable_row("weather")])
    assert found[0].kind == pq.STRESS
    assert found[0].item == "weather"


def test_candidates_rank_by_how_many_attempts_they_recurred_in() -> None:
    found = pq.candidates([phoneme_row("θ", "s", attempts=2),
                           phoneme_row("v", "w", attempts=9)])
    assert [c.item for c in found] == ["/v/ → /w/", "/θ/ → /s/"]


def test_no_history_means_no_candidates_rather_than_a_guess() -> None:
    """The cold-start contract: nothing is seeded from a first language or a default list."""
    assert pq.candidates([], []) == []


def test_why_carries_the_real_counts() -> None:
    found = pq.candidates([phoneme_row("θ", "s", attempts=4)])
    why = found[0].why
    assert "4 separate attempts" in why and "8 times" in why and "benchmark" in why


def test_why_for_a_stress_item_names_the_syllable() -> None:
    found = pq.candidates([], [syllable_row("weather", syllable="ɚ")])
    assert "/ɚ/" in found[0].why and "weather" in found[0].why


# --- Promotion ------------------------------------------------------------------------------


def test_promotion_stops_at_the_cap() -> None:
    found = pq.candidates([phoneme_row(e, p) for e, p in
                           (("θ", "s"), ("v", "w"), ("ð", "d"), ("l", "ɹ"))])
    assert len(pq.promote([], found)) == utils.MAX_ACTIVE_TARGETS


def test_one_of_each_kind_before_a_second_of_any() -> None:
    """Three consonants would crowd out a vowel gap flagged just as often."""
    found = pq.candidates(
        [phoneme_row("θ", "s", attempts=9), phoneme_row("v", "w", attempts=8),
         phoneme_row("ð", "d", attempts=7), phoneme_row("æ", "ɛ", attempts=2)],
        [syllable_row("weather", attempts=2)],
    )
    kinds = {c.kind for c in pq.promote([], found)}
    assert kinds == {pq.CONTRAST, pq.VOWEL, pq.STRESS}


def test_a_kind_with_no_candidate_does_not_hold_a_slot_empty() -> None:
    found = pq.candidates([phoneme_row("θ", "s"), phoneme_row("v", "w"),
                           phoneme_row("ð", "d")])
    assert len(pq.promote([], found)) == 3


def test_an_existing_target_is_never_promoted_twice() -> None:
    found = pq.candidates([phoneme_row("θ", "s")])
    assert pq.promote([target("/θ/ → /s/")], found) == []


def test_a_graduated_target_is_not_re_promoted() -> None:
    found = pq.candidates([phoneme_row("θ", "s")])
    assert pq.promote([target("/θ/ → /s/", state=pq.GRADUATED)], found) == []


def test_a_full_active_list_promotes_nothing() -> None:
    existing = [target(f"item-{n}", id=n) for n in range(utils.MAX_ACTIVE_TARGETS)]
    found = pq.candidates([phoneme_row("θ", "s")])
    assert pq.promote(existing, found) == []


def test_a_graduated_target_frees_its_slot() -> None:
    existing = [target(f"item-{n}", id=n) for n in range(2)]
    existing.append(target("old", state=pq.GRADUATED, id=9))
    found = pq.candidates([phoneme_row("θ", "s")])
    assert len(pq.promote(existing, found)) == 1


# --- Blocks ---------------------------------------------------------------------------------


def test_blocks_are_grouped_by_id_and_ordered_by_time() -> None:
    trials = [
        {"block_id": "b", "created_at": "2026-08-11T00:00:00Z", "correct": 1,
         "novel": 1, "alternatives": 2, "review": 0},
        {"block_id": "a", "created_at": "2026-08-10T00:00:00Z", "correct": 0,
         "novel": 1, "alternatives": 2, "review": 0},
    ]
    assert [b.block_id for b in pq.summarise_blocks(trials)] == ["a", "b"]


def test_a_part_finished_block_is_incomplete() -> None:
    trials = [{"block_id": "a", "created_at": "2026-08-10T00:00:00Z", "correct": 1,
               "novel": 1, "alternatives": 2, "review": 0}] * 5
    assert not pq.summarise_blocks(trials)[0].complete


# --- Grading --------------------------------------------------------------------------------


def test_nothing_graduates_on_an_incomplete_block() -> None:
    """Evidence is kept; a verdict is not earned."""
    decision = pq.grade(target("/θ/ → /s/"), [block(5, 5, planned=20)])
    assert decision.state == pq.ACTIVE
    assert "stopped at 5 of 20" in decision.reason


def test_two_blocks_at_the_criterion_graduate() -> None:
    decision = pq.grade(target("/θ/ → /s/"), [block(19), block(20)])
    assert decision.state == pq.GRADUATED
    assert "95%" in decision.reason and "50%" in decision.reason


def test_one_good_block_is_not_enough() -> None:
    decision = pq.grade(target("/θ/ → /s/"), [block(20)])
    assert decision.state == pq.ACTIVE


def test_a_dip_below_the_criterion_stops_graduation() -> None:
    decision = pq.grade(target("/θ/ → /s/"), [block(20), block(14)])
    assert decision.state == pq.ACTIVE
    assert "70%" in decision.reason


def test_a_failed_review_returns_the_item_to_rotation() -> None:
    decision = pq.grade(
        target("/θ/ → /s/", state=pq.GRADUATED, reviews_passed=2),
        [block(20, when="2026-08-01"), block(4, 10, review=True, when="2026-08-15")],
    )
    assert decision.state == pq.ACTIVE
    assert decision.regressed and decision.reviews_passed == 0


def test_a_passed_review_advances_the_schedule() -> None:
    decision = pq.grade(
        target("/θ/ → /s/", state=pq.GRADUATED, reviews_passed=1),
        [block(9, 10, review=True, when="2026-08-15")],
    )
    assert decision.state == pq.GRADUATED and decision.reviews_passed == 2


def test_every_decision_carries_the_numbers_behind_it() -> None:
    """The rules have to be visible, not implicit."""
    for blocks in ([], [block(20)], [block(20), block(20)], [block(2, 10, review=True)]):
        assert pq.grade(target("/θ/ → /s/"), blocks).reason.strip()


def test_the_graduation_rule_states_its_own_thresholds() -> None:
    rule = pq.graduation_rule(pq.CONTRAST)
    assert "90%" in rule and "2 completed blocks" in rule and "50%" in rule


# --- Stress grades on evidence, not on a score ------------------------------------------------


def test_a_stress_item_stays_while_the_word_is_still_flagged() -> None:
    decision = pq.grade(target("weather", kind=pq.STRESS), still_flagged=True)
    assert decision.state == pq.ACTIVE


def test_a_stress_item_graduates_when_the_evidence_dries_up() -> None:
    decision = pq.grade(target("weather", kind=pq.STRESS), still_flagged=False)
    assert decision.state == pq.GRADUATED


def test_a_stress_item_with_no_new_attempt_is_left_alone() -> None:
    decision = pq.grade(target("weather", kind=pq.STRESS))
    assert decision.state == pq.ACTIVE and "Not re-checked" in decision.reason


def test_the_stress_rule_says_why_there_is_no_quiz() -> None:
    rule = pq.graduation_rule(pq.STRESS)
    assert "no stress marks" in rule


# --- Scheduling -------------------------------------------------------------------------------


def test_an_active_item_is_due_now() -> None:
    when = pq.next_due(pq.Decision(pq.ACTIVE, ""), now=NOW)
    assert when == "2026-08-19T12:00:00Z"


def test_the_review_gaps_widen() -> None:
    days = []
    for passed in range(len(utils.REVIEW_INTERVAL_DAYS)):
        when = pq.next_due(pq.Decision(pq.GRADUATED, "", reviews_passed=passed), now=NOW)
        days.append((datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ") - NOW.replace(tzinfo=None)).days)
    assert days == list(utils.REVIEW_INTERVAL_DAYS)


def test_past_the_last_interval_the_schedule_stops_widening() -> None:
    when = pq.next_due(pq.Decision(pq.GRADUATED, "", reviews_passed=99), now=NOW)
    expected = NOW + timedelta(days=utils.REVIEW_INTERVAL_DAYS[-1])
    assert when == expected.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_review_horizon_is_honest_once_the_schedule_runs_out() -> None:
    assert "not re-checked again" in pq.review_horizon(len(utils.REVIEW_INTERVAL_DAYS))


def test_due_returns_what_has_come_round_active_first() -> None:
    rows = [
        target("later", state=pq.GRADUATED, next_due="2026-09-01T00:00:00Z", id=1),
        target("review", state=pq.GRADUATED, next_due="2026-08-01T00:00:00Z", id=2),
        target("active", next_due="2026-08-02T00:00:00Z", id=3),
    ]
    assert [r["item"] for r in pq.due(rows, now=NOW)] == ["active", "review"]


def test_a_target_with_no_due_date_counts_as_due() -> None:
    assert pq.due([target("x", next_due=None)], now=NOW)


# --- Evidence ----------------------------------------------------------------------------------


def test_unreadable_evidence_does_not_break_the_page() -> None:
    assert pq.evidence_of(target("x", evidence="not json")) == {}


def test_evidence_round_trips() -> None:
    assert pq.evidence_of(target("x", evidence='{"expected": "θ"}')) == {"expected": "θ"}


def test_a_reason_never_restates_the_rule_rendered_beside_it() -> None:
    """The rule is on the line above in the UI; repeating it three inches later is padding."""
    rule = pq.graduation_rule(pq.CONTRAST)
    for blocks in ([], [block(20)], [block(20), block(20)]):
        assert rule not in pq.grade(target("/θ/ → /s/"), blocks).reason
    stress = pq.grade(target("weather", kind=pq.STRESS), still_flagged=True)
    assert pq.graduation_rule(pq.STRESS) not in stress.reason


# --- The shadowing kind -------------------------------------------------------------------------
# A shadowing passage lives in the same table only because that table is where "what am I doing
# today?" is answered. It is never promoted from evidence and it never graduates, so almost
# every rule above has to leave it alone.


def test_a_shadow_row_does_not_consume_a_promotion_slot() -> None:
    """The failure this guards is silent: adding a standing practice would otherwise retire a
    sound the recordings are still flagging."""
    existing = [target("Benchmark", kind=pq.SHADOW)]
    found = pq.candidates([phoneme_row(e, p) for e, p in
                           (("θ", "s"), ("v", "w"), ("ð", "d"), ("l", "ɹ"))])
    assert len(pq.promote(existing, found)) == utils.MAX_ACTIVE_TARGETS


def test_shadow_is_not_a_promotable_kind() -> None:
    assert not pq.promotable(pq.SHADOW)
    assert all(pq.promotable(kind) for kind in pq.KIND_ORDER)
    assert pq.SHADOW not in pq.KIND_ORDER


def test_shadow_has_a_label_on_screen() -> None:
    assert pq.KIND_LABELS[pq.SHADOW]


def test_grading_a_shadow_target_changes_nothing() -> None:
    """`app.apply_decisions` skips a target whose state is unchanged and which did not
    regress, which is exactly what keeps a shadow row's schedule out of the grader's hands."""
    decision = pq.grade(target("Benchmark", kind=pq.SHADOW))
    assert decision.state == pq.ACTIVE
    assert not decision.regressed


def test_a_shadow_target_is_due_on_a_fixed_gap_not_immediately() -> None:
    """"Active means due now" would make it due on every render, since it is always active."""
    decision = pq.grade(target("Benchmark", kind=pq.SHADOW))
    assert pq.next_due(decision, now=NOW, kind=pq.SHADOW) == pq._iso(
        NOW + timedelta(days=utils.SHADOW_INTERVAL_DAYS)
    )


def test_the_shadow_gap_never_widens() -> None:
    """Unlike REVIEW_INTERVAL_DAYS: there is no graduation for a widening schedule to grow
    confident about."""
    passed = pq.Decision(pq.ACTIVE, "", reviews_passed=3)
    assert pq.next_due(passed, now=NOW, kind=pq.SHADOW) == pq.next_due(
        pq.Decision(pq.ACTIVE, ""), now=NOW, kind=pq.SHADOW
    )


def test_the_shadow_rule_says_nothing_takes_it_off() -> None:
    rule = pq.graduation_rule(pq.SHADOW)
    assert "Nothing takes this off the list" in rule
    assert str(utils.SHADOW_INTERVAL_DAYS) in rule


def test_a_due_shadow_target_sorts_with_the_others() -> None:
    rows = [target("Benchmark", kind=pq.SHADOW, next_due="2026-08-01T00:00:00Z")]
    assert [row["item"] for row in pq.due(rows, now=NOW)] == ["Benchmark"]
