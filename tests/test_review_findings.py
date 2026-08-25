"""The confirmed code-review findings against the coaching layer.

One test per finding, each written to fail against the code as it was released in v0.1.0.
They are grouped here rather than scattered into the module test files because what they
have in common is why they matter — each one is a way the coaching layer could quietly
mislead or overspend — and keeping them together makes that legible.

**Three of the original eight went on 2026-08-25.** Findings 1, 5&6 and 7 were about the
Gemini *coaching* path — the always-returns-a-report fall-through, the anti-fabrication
validator, and re-reading a stored `CoachingReport`. Gemini writes only the prosody
annotation now, and the guarantees that replaced them are asserted where they now live:
the word-sequence contract in `test_ai_coach`, and the coach's own never-raises path in
`test_app`. The findings are recorded here rather than silently dropped.
"""

from __future__ import annotations

import pytest

import ai_coach
import fallback_coach as fc
import speech_analyzer as sa
import utils

REFERENCE = "Thursday brought thunder and thick clouds."


def phoneme(symbol: str, score: float, *nbest: tuple[str, float]) -> dict:
    return {
        "phoneme": symbol,
        "score": score,
        "is_mispronounced": score < 60,
        "nbest": [{"phoneme": p, "score": s} for p, s in nbest],
    }


@pytest.fixture
def attempt() -> sa.Assessment:
    return sa.Assessment(
        raw=[],
        overall_scores={"pron_score": 62.0, "accuracy": 70.0},
        recognised_text="sursday brought thunder and thick clouds",
        words=[
            {
                "word": "thursday",
                "accuracy": 34.0,
                "error_type": "Mispronunciation",
                "error_source": "azure",
                "delivery_error_types": [],
                "syllables": [{"syllable": "θɝz", "score": 26.0}],
                "phonemes": [phoneme("θ", 41.0, ("s", 100.0))],
            }
        ],
    )


# --- 1. The "always returns a report" guarantee -------------------------------------------


def test_the_emergency_report_is_schema_valid_and_self_contained() -> None:
    """It must not depend on any of the machinery that just failed."""
    report = fc.emergency_report("something broke")

    assert report.overall_comment
    assert report.practice_plan
    assert report.stress_and_rhythm.drill
    assert report.priority_fixes == []


# --- 2. final_cluster on a smeared merge ---------------------------------------------------


def test_a_merged_substitution_takes_the_final_clusters_position() -> None:
    """Azure smears one produced sound across two targets; the merge keeps the worse one.

    When the *earlier* entry is kept (it scored worse), it now stands at the later
    position, so it has to inherit that position's final-cluster status — otherwise the
    swallowed-final-cluster note never fires for a word that ends in a consonant cluster.
    """
    word = {
        "word": "asked",
        "accuracy": 30.0,
        "error_type": "Mispronunciation",
        "error_source": "azure",
        "delivery_error_types": [],
        "syllables": [],
        # /k/ and /t/ both come back as /d/; the /k/ scores worse and is kept, but the
        # cluster is only final at the /t/.
        "phonemes": [
            phoneme("æ", 95.0),
            phoneme("s", 90.0),
            phoneme("k", 20.0, ("d", 100.0)),
            phoneme("t", 40.0, ("d", 100.0)),
        ],
    }

    found = fc._substitutions(word)

    assert len(found) == 1, "the smeared pair must collapse to one entry"
    assert found[0]["expected"] == "k", "the worse-scoring entry is the one kept"
    assert found[0]["final_cluster"] is True, "it now sits at the word-final position"


# --- 3. Transport failures are retryable ---------------------------------------------------


def test_httpx_transport_failures_are_classified_as_transient() -> None:
    """httpx's errors do not subclass the builtins, so testing those alone missed them."""
    import httpx

    for exc in (
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectTimeout("slow"),
    ):
        assert isinstance(ai_coach._classify(exc), utils.TransientError), type(exc).__name__


def test_builtin_transport_failures_are_still_transient() -> None:
    assert isinstance(ai_coach._classify(TimeoutError()), utils.TransientError)
    assert isinstance(ai_coach._classify(ConnectionError()), utils.TransientError)


# --- 5, 6 & 7. Gone with the coaching path ---------------------------------------------------
# Findings 5 and 6 were `ai_coach.validated` refusing a report that named a phoneme Azure never
# reported, in a fix or in the prose. Finding 7 was re-reading a stored `CoachingReport` out of
# either the response envelope or the flat shape. Neither function exists in that form any more:
# Gemini no longer writes coaching, and `fallback_coach` cannot fabricate a phoneme because it
# only ever reads the ones in the payload. `ai_coach.validated` now checks the word sequence of
# an annotation, and `test_ai_coach` asserts that contract in full.


# --- 8. The practice-plan allocation --------------------------------------------------------


def _fix(symbol: str) -> fc.PriorityFix:
    return fc.PriorityFix(
        expected_phoneme=symbol,
        produced_phoneme="s",
        affected_words=["thursday"],
        why_it_matters="",
        articulation="",
        minimal_pairs=[],
    )


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_the_practice_plan_survives_any_number_of_fixes(count: int) -> None:
    """The old lookup table was a KeyError waiting for MAX_PRIORITY_FIXES to change."""
    fixes = [_fix(s) for s in "θðszʃʒ"[:count]]

    plan = fc._practice_plan({"flagged_words": []}, fixes)

    assert plan
    assert "0 minute" not in plan, "a step allotted no time is not a step"


def test_the_existing_minute_split_is_unchanged() -> None:
    """Three fixes still get 2/1/1, the split the released version produced."""
    plan = fc._practice_plan({"flagged_words": []}, [_fix("θ"), _fix("ð"), _fix("s")])

    assert "2 minutes" in plan
    assert "1 minute" in plan
