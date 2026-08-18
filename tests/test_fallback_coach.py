"""The offline coach: the primary path, tested against the real captured payload.

The free Gemini tier runs out. What this module produces is what the app is on that day,
so it is held to the same standard as the model path: never a substitution Azure did not
report, never a fabricated articulation note, and the same bytes out for the same bytes in.
"""

from __future__ import annotations

import json

import pytest

import fallback_coach as fc
import phoneme_reference as pr
import speech_analyzer as sa
from utils import Mode

REFERENCE = (
    "The weather this month has been rather unpredictable. Thursday brought thunder "
    "and thick clouds, while Wednesday stayed warm and clear."
)


@pytest.fixture
def drill() -> sa.Assessment:
    """The committed drill payload, replayed offline exactly as the app replays it."""
    return sa.analyse("", REFERENCE, Mode.DRILL)


def phoneme(symbol: str, score: float, *nbest: tuple[str, float]) -> dict:
    return {
        "phoneme": symbol,
        "score": score,
        "is_mispronounced": score < 60,
        "nbest": [{"phoneme": p, "score": s} for p, s in nbest],
    }


def word(text: str, accuracy=None, error_type="None", phonemes=None, syllables=None,
         delivery=None) -> dict:
    return {
        "word": text,
        "accuracy": accuracy,
        "error_type": error_type,
        "error_source": "azure",
        "delivery_error_types": delivery or [],
        "syllables": syllables or [],
        "phonemes": phonemes or [],
    }


def assessment(words: list[dict], **scores) -> sa.Assessment:
    return sa.Assessment(raw=[], overall_scores=scores, recognised_text="", words=words)


# --- Compaction ------------------------------------------------------------------------------


def test_the_payload_sent_on_is_a_fraction_of_the_raw_response(drill: sa.Assessment) -> None:
    """Raw Azure JSON is mostly offsets and scores for words that were fine."""
    compacted = len(json.dumps(fc.compact(drill, Mode.DRILL)))
    raw = len(json.dumps(drill.raw))
    assert compacted < raw / 10, f"{compacted} vs {raw}"


def test_only_flagged_words_survive_compaction(drill: sa.Assessment) -> None:
    kept = {entry["word"] for entry in fc.compact(drill, Mode.DRILL)["flagged_words"]}
    assert kept == {w["word"] for w in drill.words if sa.is_flagged(w)}
    assert len(kept) < len(drill.words)


def test_no_substitution_is_claimed_when_azures_own_best_guess_is_the_target() -> None:
    """"brought" in the fixture: /b/ scores 37 but the top alternate is /b/ itself."""
    subject = word("brought", 41.0, phonemes=[phoneme("b", 37.0, ("b", 100.0), ("ə", 53.0))])
    compacted = fc.compact(assessment([subject]), Mode.DRILL)
    assert compacted["flagged_words"][0]["substitutions"] == []
    assert compacted["observed_pairs"] == []


def test_one_produced_sound_smeared_over_two_targets_counts_once() -> None:
    """The fixture's "thursday" returns /tʃ/ at 100 for both its /z/ and its /d/."""
    subject = word("thursday", 34.0, phonemes=[
        phoneme("z", 2.0, ("tʃ", 100.0)),
        phoneme("d", 0.0, ("tʃ", 100.0)),
    ])
    substitutions = fc.compact(assessment([subject]), Mode.DRILL)["flagged_words"][0]["substitutions"]
    assert len(substitutions) == 1
    assert substitutions[0]["score"] == 0.0, "the worse of the run is the one kept"


def test_two_different_substitutions_in_one_word_both_survive() -> None:
    subject = word("thursday", 34.0, phonemes=[
        phoneme("θ", 41.0, ("s", 100.0)),
        phoneme("ɝ", 0.0, ("æ", 100.0)),
    ])
    substitutions = fc.compact(assessment([subject]), Mode.DRILL)["flagged_words"][0]["substitutions"]
    assert [(s["expected"], s["produced"]) for s in substitutions] == [("θ", "s"), ("ɝ", "æ")]


# --- The report against the real payload --------------------------------------------------------


def test_the_report_is_complete_and_valid(drill: sa.Assessment) -> None:
    report = fc.build(drill, Mode.DRILL)
    assert fc.CoachingReport.model_validate(report.model_dump())
    assert report.overall_comment.strip()
    assert report.practice_plan.strip()
    assert report.stress_and_rhythm.drill.strip()


def test_at_most_three_fixes_are_reported(drill: sa.Assessment) -> None:
    assert len(fc.build(drill, Mode.DRILL).priority_fixes) <= fc.MAX_PRIORITY_FIXES


def test_the_flagship_substitution_is_reported(drill: sa.Assessment) -> None:
    """/θ/ → /s/ on "thursday" is the substitution this whole project exists to catch."""
    fixes = fc.build(drill, Mode.DRILL).priority_fixes
    assert ("θ", "s") in [(f.expected_phoneme, f.produced_phoneme) for f in fixes]


def test_no_fix_names_a_pair_azure_did_not_report(drill: sa.Assessment) -> None:
    compacted = fc.compact(drill, Mode.DRILL)
    observed = {tuple(pair) for pair in compacted["observed_pairs"]}
    for fix in fc.build_from_compacted(compacted).priority_fixes:
        assert (fix.expected_phoneme, fix.produced_phoneme) in observed


def test_every_fix_names_the_words_it_came_from(drill: sa.Assessment) -> None:
    spoken = {str(w.get("word") or "") for w in drill.words}
    for fix in fc.build(drill, Mode.DRILL).priority_fixes:
        assert fix.affected_words
        assert set(fix.affected_words) <= spoken


def test_the_practice_plan_names_words_from_this_attempt(drill: sa.Assessment) -> None:
    report = fc.build(drill, Mode.DRILL)
    assert any(word in report.practice_plan
               for fix in report.priority_fixes for word in fix.affected_words)


def test_the_same_attempt_produces_the_same_report(drill: sa.Assessment) -> None:
    """No clock, no randomness, no set iteration order — a rerun cannot reshuffle it."""
    first = fc.build(drill, Mode.DRILL).model_dump()
    second = fc.build(sa.analyse("", REFERENCE, Mode.DRILL), Mode.DRILL).model_dump()
    assert first == second


# --- Ranking -----------------------------------------------------------------------------------


def test_a_substitution_spanning_more_words_outranks_a_worse_isolated_one() -> None:
    words = [
        word("think", 50.0, phonemes=[phoneme("θ", 55.0, ("s", 100.0))]),
        word("thick", 50.0, phonemes=[phoneme("θ", 55.0, ("s", 100.0))]),
        word("vine", 30.0, phonemes=[phoneme("v", 1.0, ("w", 100.0))]),
    ]
    fixes = fc.build(assessment(words), Mode.DRILL).priority_fixes
    assert (fixes[0].expected_phoneme, fixes[0].produced_phoneme) == ("θ", "s")


def test_a_coachable_pair_outranks_alignment_noise_at_the_same_spread() -> None:
    """An unwritten pair can only be named; a written-up one can be practised."""
    words = [
        word("thursday", 34.0, phonemes=[
            phoneme("d", 0.0, ("tʃ", 100.0)),      # no entry: alignment noise
            phoneme("θ", 41.0, ("s", 100.0)),      # written up, and worse for the listener
        ]),
    ]
    fixes = fc.build(assessment(words), Mode.DRILL).priority_fixes
    assert (fixes[0].expected_phoneme, fixes[0].produced_phoneme) == ("θ", "s")


# --- Degradation -------------------------------------------------------------------------------


def test_an_unwritten_pair_is_reported_without_inventing_advice() -> None:
    words = [word("thursday", 34.0, phonemes=[phoneme("d", 0.0, ("tʃ", 100.0))])]
    fix = fc.build(assessment(words), Mode.DRILL).priority_fixes[0]
    assert fix.articulation.startswith(pr.PHONEMES["d"].articulation)
    assert fix.minimal_pairs == []
    assert "/tʃ/" in fix.why_it_matters and "listener" not in fix.why_it_matters.lower()


def test_an_attempt_where_nothing_was_spoken_still_gets_a_usable_report() -> None:
    """Every word omitted: there is no phoneme evidence at all, and no fix can be honest."""
    words = [word(text, None, error_type="Omission") for text in ("thunder", "thick", "clouds")]
    report = fc.build(assessment(words, completeness=0.0), Mode.PARAGRAPH)
    assert report.priority_fixes == []
    assert "never spoken" in report.overall_comment
    assert "thunder" in report.overall_comment
    assert report.practice_plan.strip() and report.stress_and_rhythm.drill.strip()


def test_a_clean_attempt_says_so_rather_than_inventing_a_problem() -> None:
    report = fc.build(assessment([word("weather", 99.0)], pron_score=98.0), Mode.DRILL)
    assert report.priority_fixes == []
    assert "Nothing fell below" in report.overall_comment


# --- Stress, rhythm and clusters ------------------------------------------------------------------


def test_delivery_faults_become_issues_naming_the_words() -> None:
    """Synthetic: the captured recording came back clean on Break and Intonation."""
    words = [word("thursday", 88.0, delivery=["UnexpectedBreak"]),
             word("clouds", 90.0, delivery=["Monotone"])]
    issues = fc.build(assessment(words), Mode.PARAGRAPH).stress_and_rhythm.issues
    assert any("paused" in issue.lower() and "thursday" in issue for issue in issues)
    assert any("flat" in issue.lower() and "clouds" in issue for issue in issues)


def test_every_delivery_fault_azure_reports_has_a_sentence() -> None:
    for fault in ("UnexpectedBreak", "MissingBreak", "Monotone"):
        assert fault in fc._DELIVERY_SENTENCES


def test_a_weak_stressed_syllable_is_called_out(drill: sa.Assessment) -> None:
    issues = fc.build(drill, Mode.DRILL).stress_and_rhythm.issues
    assert any("thursday" in issue and "θɝz" in issue for issue in issues)


def test_a_swallowed_final_cluster_gets_the_cluster_note() -> None:
    subject = word("asked", 40.0, phonemes=[
        phoneme("æ", 90.0), phoneme("s", 90.0), phoneme("k", 90.0),
        phoneme("t", 10.0, ("d", 100.0)),
    ])
    fix = fc.build(assessment([subject]), Mode.DRILL).priority_fixes[0]
    assert pr.FINAL_CLUSTER_NOTE in fix.articulation


def test_a_word_initial_substitution_does_not_get_the_cluster_note() -> None:
    subject = word("think", 40.0, phonemes=[phoneme("θ", 10.0, ("s", 100.0)), phoneme("ɪ", 90.0)])
    fix = fc.build(assessment([subject]), Mode.DRILL).priority_fixes[0]
    assert pr.FINAL_CLUSTER_NOTE not in fix.articulation
