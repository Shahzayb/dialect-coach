"""The eight confirmed code-review findings against the coaching layer.

One test per finding, each written to fail against the code as it was released in v0.1.0.
They are grouped here rather than scattered into the module test files because what they
have in common is why they matter — each one is a way the coaching layer could quietly
mislead or overspend — and keeping them together makes that legible.
"""

from __future__ import annotations

import pytest

import ai_coach
import fallback_coach as fc
import speech_analyzer as sa
import utils
from utils import Mode

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


def test_a_broken_compaction_still_produces_a_report(attempt, monkeypatch) -> None:
    """The guarantee has to hold on the free path too, not only Gemini's."""
    monkeypatch.setattr(
        fc,
        "compact",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("a bug in the compaction pipeline")),
    )

    result = ai_coach.coach(attempt, REFERENCE, Mode.DRILL)

    assert result.source == fc.SOURCE_FALLBACK
    assert result.report.overall_comment  # a real, renderable report
    assert result.report.priority_fixes == []


def test_a_broken_offline_build_still_produces_a_report(attempt, monkeypatch) -> None:
    monkeypatch.setattr(
        fc,
        "build_from_compacted",
        lambda *a, **k: (_ for _ in ()).throw(KeyError("observed_pairs")),
    )

    result = ai_coach.coach(attempt, REFERENCE, Mode.DRILL)

    assert result.source == fc.SOURCE_FALLBACK
    assert result.report.overall_comment


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


# --- 5 & 6. validated() ---------------------------------------------------------------------


def _report(**overrides):
    body = {
        "overall_comment": "The /θ/ came out as /s/.",
        "priority_fixes": [
            {
                "expected_phoneme": "θ",
                "produced_phoneme": "s",
                "affected_words": ["thursday"],
                "why_it_matters": "A listener hears a different word.",
                "articulation": "Tongue tip between the teeth.",
                "minimal_pairs": [{"a": "thin", "b": "sin"}],
            }
        ],
        "delivery_drills": [],
        "stress_and_rhythm": {"issues": [], "drill": "Read it twice."},
        "practice_plan": "Five minutes on thursday.",
    }
    body.update(overrides)
    return fc.CoachingReport.model_validate(body)


COMPACTED = {"observed_pairs": [["θ", "s"]]}


def test_a_fabricated_phoneme_in_the_prose_rejects_the_report() -> None:
    """The UI claims every unsupported sound was removed, so prose must be checked too."""
    report = _report(practice_plan="Two minutes on /ð/ versus /z/, then read it back.")

    assert ai_coach.validated(report, COMPACTED) is None


def test_a_fabricated_phoneme_in_a_stress_issue_rejects_the_report() -> None:
    report = _report(
        stress_and_rhythm={
            "issues": ["Your /ŋ/ endings are dropped."],
            "drill": "Read it twice.",
        }
    )

    assert ai_coach.validated(report, COMPACTED) is None


def test_prose_naming_only_supported_sounds_survives() -> None:
    report = _report(practice_plan="Two minutes on /θ/, holding it against /s/.")

    checked = ai_coach.validated(report, COMPACTED)

    assert checked is not None
    assert len(checked.priority_fixes) == 1


def test_fabricated_fixes_are_rejected_even_when_nothing_was_observed() -> None:
    """The old guard only degraded correctly when observed_pairs was non-empty."""
    report = _report()

    assert ai_coach.validated(report, {"observed_pairs": []}) is None


def test_a_report_with_no_fixes_and_no_evidence_is_still_usable() -> None:
    """Claiming nothing when there is nothing to claim is a correct answer, not a failure."""
    report = _report(priority_fixes=[], overall_comment="Nothing stood out.")

    checked = ai_coach.validated(report, {"observed_pairs": []})

    assert checked is not None
    assert checked.priority_fixes == []


# --- 7. Re-reading a stored row -------------------------------------------------------------


def test_a_stored_flat_report_is_recoverable_under_the_gemini_source() -> None:
    """coach() stores the flat report when the response will not serialise.

    The row is still marked `gemini`, so re-reading it has to cope with either shape or the
    report is silently lost.
    """
    flat = _report().model_dump()

    recovered = ai_coach.report_from_raw(flat, fc.SOURCE_GEMINI)

    assert recovered is not None
    assert recovered.overall_comment == flat["overall_comment"]


def test_a_stored_response_envelope_is_still_recoverable() -> None:
    """The normal shape must keep working."""
    envelope = {"candidates": [{"content": {"parts": [{"text": _report().model_dump_json()}]}}]}

    recovered = ai_coach.report_from_raw(envelope, fc.SOURCE_GEMINI)

    assert recovered is not None
    assert recovered.priority_fixes[0].expected_phoneme == "θ"


def test_an_unreadable_row_returns_none_rather_than_raising() -> None:
    assert ai_coach.report_from_raw({"nonsense": True}, fc.SOURCE_GEMINI) is None


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
