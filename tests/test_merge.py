"""Merge arithmetic and miscue diffing.

The payloads here are hand-built on purpose: these tests are about the arithmetic, and a
hand-built pair with known durations is the only way to assert the weighting is right.
Schema fidelity is tested separately in test_parsing.py, against a real captured payload.
"""

from __future__ import annotations

import pytest

import speech_analyzer as sa
from utils import Mode


def utterance(
    duration_ticks: int,
    *,
    pron=None,
    accuracy=None,
    fluency=None,
    prosody=None,
    completeness=None,
    words=(),
    display="",
) -> dict:
    scores = {}
    for key, value in (
        ("PronScore", pron),
        ("AccuracyScore", accuracy),
        ("FluencyScore", fluency),
        ("ProsodyScore", prosody),
        ("CompletenessScore", completeness),
    ):
        if value is not None:
            scores[key] = value
    return {
        "Duration": duration_ticks,
        "DisplayText": display,
        "NBest": [
            {
                "Display": display,
                "PronunciationAssessment": scores,
                "Words": [
                    {
                        "Word": w,
                        "PronunciationAssessment": {"AccuracyScore": 90.0, "ErrorType": "None"},
                    }
                    for w in words
                ],
            }
        ],
    }


# 1 second = 10,000,000 ticks (100-ns units).
SECOND = 10_000_000


def test_merge_is_duration_weighted_not_a_naive_mean() -> None:
    """A 9 s utterance and a 1 s one do not carry equal evidence."""
    payloads = [
        utterance(9 * SECOND, accuracy=90.0, words=["a"]),
        utterance(1 * SECOND, accuracy=50.0, words=["b"]),
    ]
    overall, _, _ = sa.normalise(payloads, "a b", Mode.PARAGRAPH)
    assert overall["accuracy"] == pytest.approx(86.0)  # (90*9 + 50*1) / 10
    assert overall["accuracy"] != pytest.approx(70.0)  # the naive mean


def test_merge_weights_every_overall_score() -> None:
    payloads = [
        utterance(3 * SECOND, pron=80.0, accuracy=80.0, fluency=60.0, prosody=40.0, words=["a"]),
        utterance(1 * SECOND, pron=40.0, accuracy=40.0, fluency=100.0, prosody=80.0, words=["b"]),
    ]
    overall, _, _ = sa.normalise(payloads, "a b", Mode.PARAGRAPH)
    assert overall["pron_score"] == pytest.approx(70.0)
    assert overall["fluency"] == pytest.approx(70.0)
    assert overall["prosody"] == pytest.approx(50.0)


def test_prosody_is_weighted_over_only_the_utterances_that_have_it() -> None:
    payloads = [
        utterance(1 * SECOND, accuracy=90.0, prosody=60.0, words=["a"]),
        utterance(9 * SECOND, accuracy=90.0, words=["b"]),  # no ProsodyScore at all
    ]
    overall, _, _ = sa.normalise(payloads, "a b", Mode.PARAGRAPH)
    assert overall["prosody"] == pytest.approx(60.0)


def test_prosody_is_none_not_zero_when_absent_everywhere() -> None:
    payloads = [
        utterance(SECOND, accuracy=90.0, words=["a"]),
        utterance(SECOND, accuracy=90.0, words=["b"]),
    ]
    overall, _, _ = sa.normalise(payloads, "a b", Mode.PARAGRAPH)
    assert overall["prosody"] is None


def test_missing_durations_fall_back_to_equal_weighting() -> None:
    payloads = [utterance(0, accuracy=100.0, words=["a"]), utterance(0, accuracy=50.0, words=["b"])]
    overall, _, _ = sa.normalise(payloads, "a b", Mode.PARAGRAPH)
    assert overall["accuracy"] == pytest.approx(75.0)


def test_completeness_is_recomputed_globally_not_averaged() -> None:
    """Azure scores each utterance against the whole reference; averaging is meaningless."""
    payloads = [
        utterance(SECOND, accuracy=90.0, completeness=25.0, words=["one", "two"]),
        utterance(SECOND, accuracy=90.0, completeness=25.0, words=["three", "four"]),
    ]
    overall, _, _ = sa.normalise(payloads, "one two three four", Mode.PARAGRAPH)
    assert overall["completeness"] == pytest.approx(100.0)


def test_completeness_drops_when_words_are_omitted() -> None:
    payloads = [utterance(SECOND, accuracy=90.0, words=["one", "two"])]
    overall, _, words = sa.normalise(payloads, "one two three four", Mode.PARAGRAPH)
    assert overall["completeness"] == pytest.approx(50.0)
    assert [w["word"] for w in words if w["error_type"] == "Omission"] == ["three", "four"]


def test_recognised_text_is_joined_across_utterances() -> None:
    payloads = [
        utterance(SECOND, accuracy=90.0, words=["one"], display="One."),
        utterance(SECOND, accuracy=90.0, words=["two"], display="Two."),
    ]
    _, recognised, _ = sa.normalise(payloads, "one two", Mode.PARAGRAPH)
    assert recognised == "One. Two."


# --- Local miscue diffing ------------------------------------------------------------------


def test_local_diff_marks_its_own_findings_as_not_azures() -> None:
    payloads = [utterance(SECOND, accuracy=90.0, words=["one", "two"])]
    _, _, words = sa.normalise(payloads, "one two three", Mode.PARAGRAPH)
    omissions = [w for w in words if w["error_type"] == "Omission"]
    assert omissions and all(w["error_source"] == "local_diff" for w in omissions)
    assert all(w["error_source"] == "azure" for w in words if w["error_type"] == "None")


def test_local_diff_flags_insertions() -> None:
    payloads = [utterance(SECOND, accuracy=90.0, words=["one", "and", "two"])]
    _, _, words = sa.normalise(payloads, "one two", Mode.PARAGRAPH)
    assert [w["word"] for w in words if w["error_type"] == "Insertion"] == ["and"]


def test_drill_mode_leaves_miscue_to_azure() -> None:
    """Single-shot had enableMiscue on, so re-deriving it locally would double-count."""
    payloads = [utterance(SECOND, accuracy=90.0, words=["one"])]
    _, _, words = sa.normalise(payloads, "one two three", Mode.DRILL)
    assert all(w["error_source"] == "azure" for w in words)
    assert not any(w["error_type"] == "Omission" for w in words)


def test_normalise_rejects_an_empty_payload_list() -> None:
    with pytest.raises(sa.AssessmentError):
        sa.normalise([], "one", Mode.DRILL)


# --- Assessment config -----------------------------------------------------------------------


def test_miscue_is_on_for_drill_and_off_for_paragraph() -> None:
    """enableMiscue is only honoured single-shot; True in continuous mode may be rejected."""
    import json

    assert json.loads(sa.assessment_config_json("x", Mode.DRILL))["enableMiscue"] is True
    assert json.loads(sa.assessment_config_json("x", Mode.PARAGRAPH))["enableMiscue"] is False


def test_assessment_config_requests_prosody_ipa_and_nbest() -> None:
    import json

    config = json.loads(sa.assessment_config_json("hello", Mode.DRILL))
    assert config["enableProsodyAssessment"] is True  # else ProsodyScore never appears
    assert config["phonemeAlphabet"] == "IPA"
    assert config["nBestPhonemeCount"] == 5  # what was actually produced
    assert config["granularity"] == "Phoneme"
    assert config["gradingSystem"] == "HundredMark"
