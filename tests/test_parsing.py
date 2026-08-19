"""Parsing against the real captured Azure payloads.

These fixtures are verbatim responses from a live pronunciation assessment of a genuine
recording, captured once with scripts/capture_fixture.py. Everything here therefore tests
the schema Azure actually returns, not the one the docs describe — the two differ (see
test_error_type_is_read_from_inside_the_assessment_block).
"""

from __future__ import annotations

import json

import pytest

import speech_analyzer as sa
import utils
from utils import Mode


@pytest.fixture
def drill_payload(fixtures_dir) -> dict:
    return json.loads((fixtures_dir / "sample_azure_response.json").read_text())


@pytest.fixture
def continuous_payloads(fixtures_dir) -> list[dict]:
    return json.loads((fixtures_dir / "sample_azure_continuous.json").read_text())


@pytest.fixture
def reference() -> str:
    return (
        "The weather this month has been rather unpredictable. Thursday brought thunder "
        "and thick clouds, while Wednesday stayed warm and clear."
    )


# --- Acceptance criterion 3: apply_to was verifiably called -------------------------------


def test_the_payload_contains_a_pronunciation_assessment_block(drill_payload: dict) -> None:
    """Proof that pron_config.apply_to(recognizer) ran.

    Without it recognition still succeeds and still returns a transcript — this block is
    simply absent. Its presence in a captured response is the only real evidence.
    """
    assert "PronunciationAssessment" in drill_payload["NBest"][0]


def test_prosody_is_actually_populated(drill_payload: dict, reference: str) -> None:
    """Criterion 3: prosody must be enabled explicitly or ProsodyScore never appears."""
    overall, _, _ = sa.normalise([drill_payload], reference, Mode.DRILL)
    assert overall["prosody"] is not None
    assert 0 < overall["prosody"] <= 100


def test_every_overall_score_is_present(drill_payload: dict, reference: str) -> None:
    overall, _, _ = sa.normalise([drill_payload], reference, Mode.DRILL)
    for key in ("pron_score", "accuracy", "fluency", "completeness", "prosody"):
        assert isinstance(overall[key], float), f"{key} missing from the normalised result"


# --- Acceptance criterion 4: what was produced, not only what was expected ------------------


def test_flagged_phonemes_report_what_was_actually_produced(
    drill_payload: dict, reference: str
) -> None:
    """The difference between 'your /θ/ scored 41' and 'you produced /t/ where /θ/ was
    expected'. Only the second is actionable, and it needs NBestPhonemes."""
    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    scored_phonemes = [p for w in words for p in w["phonemes"] if p["score"] is not None]
    assert scored_phonemes, "no phoneme-level scores parsed at all"
    assert all(p["nbest"] for p in scored_phonemes), \
        "every scored phoneme must carry its produced alternates"
    assert all(
        isinstance(alt["phoneme"], str) and isinstance(alt["score"], float)
        for p in scored_phonemes for alt in p["nbest"]
    )


def test_nbest_gives_five_alternates(drill_payload: dict, reference: str) -> None:
    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    counts = {len(p["nbest"]) for w in words for p in w["phonemes"] if p["nbest"]}
    assert max(counts) == sa.NBEST_PHONEME_COUNT


def test_syllables_are_parsed(drill_payload: dict, reference: str) -> None:
    """Misplaced lexical stress is invisible at the phoneme level and is a common failure."""
    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    syllables = [s for w in words for s in w["syllables"]]
    assert syllables
    assert all(s["syllable"] and s["score"] is not None for s in syllables)


# --- Schema specifics the docs get wrong ----------------------------------------------------


def test_error_type_is_read_from_inside_the_assessment_block(
    drill_payload: dict, reference: str
) -> None:
    """In the SDK's JSON, ErrorType is nested under the word's PronunciationAssessment.

    Reading word["ErrorType"] returns nothing and every word looks clean — which is how
    this got caught in the first place.
    """
    word = drill_payload["NBest"][0]["Words"][0]
    assert "ErrorType" not in word, "fixture shape changed; re-check the parser"
    assert "ErrorType" in word["PronunciationAssessment"]

    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    assert {w["error_type"] for w in words} != {"None"}, \
        "a real attempt with mispronunciations must not parse as entirely clean"


def test_mispronunciations_are_surfaced(drill_payload: dict, reference: str) -> None:
    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    assert [w for w in words if w["error_type"] == "Mispronunciation"]


def test_recognised_text_is_captured(drill_payload: dict, reference: str) -> None:
    """What Azure heard is itself the most useful signal when it differs from the script."""
    _, recognised, _ = sa.normalise([drill_payload], reference, Mode.DRILL)
    assert "weather" in recognised.lower()


def test_delivery_error_types_ignore_the_none_marker(
    drill_payload: dict, reference: str
) -> None:
    """Azure reports Break.ErrorTypes: ["None"] for clean words — not a delivery problem."""
    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    assert all("None" not in w["delivery_error_types"] for w in words)


def test_delivery_error_types_are_extracted_when_present(reference: str) -> None:
    """Synthetic, deliberately: the captured recording had no break or intonation faults.

    The payload below is hand-built to the shape the real fixture confirmed, so the
    aggregation path is covered without doctoring a captured response.
    """
    payload = {
        "Duration": 10_000_000,
        "DisplayText": "weather",
        "NBest": [{
            "Display": "weather",
            "PronunciationAssessment": {"AccuracyScore": 80.0, "PronScore": 80.0},
            "Words": [{
                "Word": "weather",
                "PronunciationAssessment": {
                    "AccuracyScore": 70.0,
                    "ErrorType": "None",
                    "Feedback": {"Prosody": {
                        "Break": {"ErrorTypes": ["UnexpectedBreak"]},
                        "Intonation": {"ErrorTypes": ["Monotone"]},
                    }},
                },
            }],
        }],
    }
    _, _, words = sa.normalise([payload], reference, Mode.DRILL)
    assert words[0]["delivery_error_types"] == ["UnexpectedBreak", "Monotone"]


# --- Continuous mode -------------------------------------------------------------------------


def test_continuous_fixture_parses_into_the_same_shape(
    continuous_payloads: list[dict], reference: str
) -> None:
    overall, recognised, words = sa.normalise(continuous_payloads, reference, Mode.PARAGRAPH)
    assert overall["prosody"] is not None
    assert recognised
    assert words and all("phonemes" in w and "syllables" in w for w in words)


def test_continuous_completeness_is_locally_recomputed(
    continuous_payloads: list[dict], reference: str
) -> None:
    """Not Azure's CompletenessScore — enableMiscue is off in continuous mode."""
    overall, _, _ = sa.normalise(continuous_payloads, reference, Mode.PARAGRAPH)
    azure_completeness = (
        continuous_payloads[0]["NBest"][0]["PronunciationAssessment"]["CompletenessScore"]
    )
    assert overall["completeness"] != azure_completeness
    assert 0 <= overall["completeness"] <= 100


# --- Offline replay ---------------------------------------------------------------------------


def test_offline_mode_replays_the_fixture_without_a_network_call(reference: str) -> None:
    """conftest forces OFFLINE_MODE, and no credentials are set — this must still work."""
    payloads, offline, attempts = sa.recognise("/nonexistent.wav", reference, Mode.DRILL)
    assert offline is True
    assert attempts == 0, "a fixture replay never reaches Azure, so it charges nothing"
    assert payloads and "NBest" in payloads[0]


def test_offline_analyse_produces_a_complete_result(reference: str) -> None:
    assessment = sa.analyse("/nonexistent.wav", reference, Mode.DRILL)
    assert assessment.offline is True
    assert assessment.overall_scores["pron_score"] is not None
    assert assessment.words
    assert assessment.raw, "the verbatim payload must survive for storage"


def test_offline_paragraph_replays_the_continuous_fixture(reference: str) -> None:
    payloads, _, _ = sa.recognise("/nonexistent.wav", reference, Mode.PARAGRAPH)
    assert isinstance(payloads, list) and payloads


def test_unscripted_mode_is_refused_rather_than_half_working(
    monkeypatch: pytest.MonkeyPatch, reference: str
) -> None:
    """Mode C needs Azure content assessment, which is a separate chunk.

    Offline replays the drill fixture for any mode, so the guard has to be checked with
    OFFLINE_MODE off. No network call happens: `recognise` raises on the mode before it
    touches the SDK or the credentials.
    """
    monkeypatch.setenv("OFFLINE_MODE", "false")
    with pytest.raises(sa.AssessmentError, match="not implemented"):
        sa.recognise("/nonexistent.wav", reference, Mode.UNSCRIPTED)


def test_quota_exhaustion_is_a_type_not_a_marker_string() -> None:
    """The 403 signal must not ride along in the message the user is shown."""
    error = sa.QuotaExhausted("Azure returned 403. The monthly free allowance is used up.")
    assert sa.is_quota_exhausted(error)
    assert "QUOTA_EXHAUSTED" not in str(error), "internal markers must not reach the UI"
    assert not sa.is_quota_exhausted(sa.AssessmentError("something else"))


def word(text: str, accuracy=None, error_type="None", error_source="azure",
         delivery=None, phonemes=None, syllables=None,
         break_length=None, monotone_confidence=None) -> dict:
    """One normalised word, hand-built. The captured fixtures carry no delivery faults."""
    return {
        "word": text,
        "accuracy": accuracy,
        "error_type": error_type,
        "error_source": error_source,
        "delivery_error_types": delivery or [],
        "prosody_detail": {
            "break_length": break_length, "monotone_confidence": monotone_confidence,
        },
        "syllables": syllables or [],
        "phonemes": phonemes or [],
    }


# --- Reading the normalised shape ---------------------------------------------------------
# These read the structure `normalise` produces and are shared with the coaching layer,
# which cannot import `app.py`. Their tests live beside the parser for the same reason.


def test_the_produced_phoneme_is_the_best_alternate_that_differs() -> None:
    """'/ð/ → /d/' is actionable; '/ð/ scored 80' is not. This is the tool's whole point."""
    subject = word("this", 97.0, phonemes=[
        {"phoneme": "ð", "score": 80.0, "is_mispronounced": False,
         "nbest": [{"phoneme": "d", "score": 100.0}, {"phoneme": "ð", "score": 92.0}]},
    ])
    assert sa.phoneme_pairs(subject) == [("ð", "d", 80.0)]


def test_no_substitution_is_reported_when_the_target_wins() -> None:
    subject = word("this", 97.0, phonemes=[
        {"phoneme": "ð", "score": 99.0, "is_mispronounced": False,
         "nbest": [{"phoneme": "ð", "score": 100.0}, {"phoneme": "d", "score": 20.0}]},
    ])
    assert sa.phoneme_pairs(subject) == [("ð", None, 99.0)]


def test_the_best_alternate_is_taken_by_score_not_by_position() -> None:
    subject = word("this", 50.0, phonemes=[
        {"phoneme": "θ", "score": 40.0, "is_mispronounced": True,
         "nbest": [{"phoneme": "s", "score": 30.0}, {"phoneme": "t", "score": 90.0}]},
    ])
    assert sa.phoneme_pairs(subject)[0][1] == "t"


def test_a_phoneme_with_no_symbol_is_not_rendered_as_the_word_none() -> None:
    """Showing "/None/" invents a target sound in a tool whose job is naming sounds."""
    subject = word("odd", 40.0, phonemes=[
        {"phoneme": None, "score": 30.0, "is_mispronounced": True, "nbest": []},
    ])
    assert sa.phoneme_pairs(subject) == [(None, None, 30.0)]


def test_delivery_faults_are_aggregated_with_the_words_involved() -> None:
    """Synthetic payload: the captured fixture contains no delivery faults at all.

    Noted in memory-bank/progress.md — the real 12.8s recording came back clean on
    Break and Intonation, so this path can only be covered by a hand-built payload.
    """
    words = [
        word("the", 90.0, delivery=["UnexpectedBreak"]),
        word("weather", 88.0, delivery=["UnexpectedBreak", "Monotone"]),
        word("today", 95.0),
    ]
    summary = sa.delivery_summary(words)
    assert summary["UnexpectedBreak"] == ["the", "weather"]
    assert summary["Monotone"] == ["weather"]
    assert "MissingBreak" not in summary


def test_a_clean_attempt_has_no_delivery_entries() -> None:
    assert sa.delivery_summary([word("the", 99.0)]) == {}


# --- The measurements beside the faults ------------------------------------------------------


def test_the_prosody_measurements_are_read_off_the_captured_payload(
    drill_payload: dict, reference: str
) -> None:
    """Proven, not constructed: these two numbers are in the committed capture.

    Every `BreakLength` in it is 0 and every `SyllablePitchDeltaConfidence` is 0.17783079,
    on words Azure flagged with nothing. That is the whole reason the parser keeps the
    values regardless of whether a fault was reported, and the reason no unit is claimed
    for the break length anywhere.
    """
    _, _, words = sa.normalise([drill_payload], reference, Mode.DRILL)
    detail = words[1]["prosody_detail"]
    assert detail["break_length"] == 0.0
    assert detail["monotone_confidence"] == pytest.approx(0.17783079)
    assert all(w["delivery_error_types"] == [] for w in words), (
        "the capture is clean on Break and Intonation — if this fails the fixture changed"
    )


def test_a_word_with_no_feedback_block_measures_nothing(reference: str) -> None:
    """Absent is None, never 0.0 — a break of zero and no break reported are not the same."""
    payload = {
        "Duration": 10_000_000,
        "DisplayText": "weather",
        "NBest": [{
            "Display": "weather",
            "PronunciationAssessment": {"AccuracyScore": 80.0, "PronScore": 80.0},
            "Words": [{
                "Word": "weather",
                "PronunciationAssessment": {"AccuracyScore": 70.0, "ErrorType": "None"},
            }],
        }],
    }
    _, _, words = sa.normalise([payload], reference, Mode.DRILL)
    assert words[0]["prosody_detail"] == {"break_length": None, "monotone_confidence": None}


def test_an_omitted_word_carries_the_key_with_nothing_in_it(reference: str) -> None:
    """It was never spoken, so there is nothing to measure — but the key is still there."""
    omitted = sa._omission("thursday")
    assert omitted["prosody_detail"] == {"break_length": None, "monotone_confidence": None}


def test_delivery_faults_carry_the_span_and_its_measurements() -> None:
    """Synthetic: the captured recording came back clean, so this shape is hand-built."""
    words = [
        word("the", 90.0, delivery=["UnexpectedBreak"], break_length=1200.0),
        word("weather", 88.0, delivery=["UnexpectedBreak"], break_length=800.0),
        word("today", 95.0, delivery=["Monotone"], monotone_confidence=0.9),
    ]
    faults = sa.delivery_faults(words)

    assert [f["fault"] for f in faults] == ["UnexpectedBreak", "Monotone"]
    unexpected, monotone = faults
    assert unexpected["words"] == ["the", "weather"]
    assert unexpected["break_length_max"] == 1200.0
    assert unexpected["break_length_mean"] == 1000.0
    assert unexpected["monotone_confidence_mean"] is None, (
        "BreakLength is reported on the Break block; a pitch number there would be noise"
    )
    assert monotone["words"] == ["today"]
    assert monotone["monotone_confidence_mean"] == 0.9
    assert monotone["break_length_max"] is None


def test_the_monotone_average_ignores_words_that_were_not_flagged() -> None:
    """Synthetic. Azure reports a pitch confidence on clean words too — the captured
    fixture reports 0.178 on every one of them. Averaging across the attempt rather than
    across the span would hand every reader a monotone number for a clean reading."""
    words = [
        word("the", 99.0, monotone_confidence=0.1),
        word("weather", 88.0, delivery=["Monotone"], monotone_confidence=0.9),
    ]
    faults = sa.delivery_faults(words)
    assert len(faults) == 1
    assert faults[0]["monotone_confidence_mean"] == 0.9


def test_delivery_faults_are_ordered_by_span_then_by_precedence() -> None:
    """Synthetic. Deterministic order matters: this list is rendered straight onto a page."""
    words = [
        word("the", 90.0, delivery=["Monotone"]),
        word("weather", 88.0, delivery=["Monotone"]),
        word("today", 95.0, delivery=["MissingBreak"]),
        word("is", 95.0, delivery=["UnexpectedBreak"]),
    ]
    faults = [f["fault"] for f in sa.delivery_faults(words)]
    assert faults == ["Monotone", "UnexpectedBreak", "MissingBreak"], (
        "two words beats one; between two one-word spans the precedence decides"
    )
    assert faults == [f["fault"] for f in sa.delivery_faults(words)]


def test_a_clean_attempt_reports_no_delivery_faults() -> None:
    assert sa.delivery_faults([word("the", 99.0, monotone_confidence=0.2)]) == []


def test_mispronounced_words_reads_the_errortype_not_the_accuracy() -> None:
    """A word can be badly scored without being ErrorType Mispronunciation, and vice versa —
    the headline count in #10/#12 is specifically what Azure classified this way."""
    words = [
        word("thursday", 41.0, error_type="Mispronunciation"),
        word("weather", 99.0),
        word("today", None, error_type="Omission"),
    ]
    assert sa.mispronounced_words(words) == ["thursday"]


def test_a_clean_attempt_has_no_mispronounced_words() -> None:
    assert sa.mispronounced_words([word("the", 99.0)]) == []


def test_a_clean_high_scoring_word_is_not_flagged() -> None:
    assert not sa.is_flagged(word("weather", 99.0))


def test_a_delivery_only_fault_still_flags_the_word() -> None:
    assert sa.is_flagged(word("weather", 99.0, delivery=["Monotone"]))


def test_a_word_below_the_amber_cut_is_flagged() -> None:
    assert sa.is_flagged(word("weather", utils.WORD_AMBER - 0.1))
