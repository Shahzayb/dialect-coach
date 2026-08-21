"""Mode C: the two-pass flow, what it refuses to report, and what it charges.

The expensive claim in this chunk is that a Mode C recording is sent to Azure twice and that
the meter knows it. That is asserted here without a network call, by driving `recognise`
against stubbed passes and counting what they were asked to do.
"""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest

import budget
import db
import speech_analyzer as sa
from utils import Mode

PROMPT = "Explain a technical decision you made recently."


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Offline replays a fixture for any mode, so the two-pass wiring needs it off.

    No socket is opened: both passes are stubbed, and `conftest.no_network` would fail the
    test loudly if one ever were.
    """
    monkeypatch.setenv("OFFLINE_MODE", "false")


def _payload(text: str) -> dict[str, Any]:
    return {"DisplayText": text, "Duration": 10_000_000, "NBest": [{"Display": text}]}


def _stub_passes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    transcript: str = "what standard stt heard",
    assessed: str = "what the assessed pass heard",
) -> dict[str, list[Any]]:
    """Replace both Azure calls and record what each was handed."""
    seen: dict[str, list[Any]] = {"transcribe": [], "assess": []}

    def fake_transcribe(wav_path: str, cancel_event: Any = None) -> list[dict[str, Any]]:
        seen["transcribe"].append(wav_path)
        return [_payload(transcript)]

    def fake_assess(
        wav_path: str,
        reference_text: str,
        cancel_event: Any = None,
        *,
        mode: Mode = Mode.PARAGRAPH,
        topic: str = "",
    ) -> list[dict[str, Any]]:
        seen["assess"].append((reference_text, mode, topic))
        return [_payload(assessed)]

    monkeypatch.setattr(sa, "_transcribe_continuous", fake_transcribe)
    monkeypatch.setattr(sa, "_assess_continuous", fake_assess)
    return seen


# --- The two passes -----------------------------------------------------------------------


def test_standard_stt_runs_first_and_its_transcript_becomes_the_reference(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """Microsoft's own recommendation, and the reason the audio is sent twice.

    Unscripted assessment runs on a weaker recogniser than standard Azure STT. A phoneme
    diagnosis against a wrong transcript is worse than none, because it confidently blames the
    wrong sounds — so the accurate transcript is bought first and then scored against.
    """
    seen = _stub_passes(monkeypatch, transcript="the accurate transcript")

    result = sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT)

    assert seen["transcribe"] == ["clip.wav"], "pass 1 must be plain STT on the same audio"
    reference, mode, _topic = seen["assess"][0]
    assert reference == "the accurate transcript"
    assert mode is Mode.UNSCRIPTED
    assert result.scored_against == "the accurate transcript"


def test_the_prompt_is_never_sent_as_a_reference_text(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """Nothing is scored against a prompt. Sending it would make Mode C a scripted read of it."""
    seen = _stub_passes(monkeypatch)
    sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT)
    reference, _mode, _topic = seen["assess"][0]
    assert PROMPT not in reference


def test_both_passes_are_counted_into_one_attempt_total(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """**The claim the budget depends on.**

    The usage meter is derived from `attempts.audio_seconds`, which `app.run_assessment_job`
    writes as `seconds * attempts`. A two-pass recording metered at one pass would understate
    the month by the length of every Mode C attempt, and every later pre-flight would then be
    computed against an understated total.
    """
    _stub_passes(monkeypatch)
    result = sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT)
    assert result.attempts == 2


def test_a_retry_inside_a_pass_is_counted_on_top_of_the_other_pass(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """A retry re-uploads the audio and can consume allowance even when it fails."""
    _stub_passes(monkeypatch)
    calls = {"n": 0}
    real = sa._assess_continuous

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sa.TransientError("Azure was busy")
        return real(*args, **kwargs)

    monkeypatch.setattr(sa, "_assess_continuous", flaky)
    # `retry_transient` sleeps ~0.5 s before its second try. Left alone rather than patched
    # out: patching it would replace the very mechanism whose attempt counting is under test.
    result = sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT)
    assert result.attempts == 3, "one transcription plus two assessment attempts"


def test_one_pass_when_two_pass_is_switched_off(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """The flag is real, and the single pass is genuinely unscripted — an empty referenceText."""
    monkeypatch.setenv("UNSCRIPTED_TWO_PASS", "false")
    seen = _stub_passes(monkeypatch)

    result = sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT)

    assert seen["transcribe"] == [], "pass 1 must not run when two-pass is off"
    reference, mode, topic = seen["assess"][0]
    assert reference == "", "an empty referenceText is what makes it unscripted"
    assert mode is Mode.UNSCRIPTED and topic == PROMPT
    assert result.attempts == 1


def test_a_recording_with_no_words_stops_before_the_second_pass(
    monkeypatch: pytest.MonkeyPatch, online: None
) -> None:
    """Nothing to score against means nothing to buy a second call for."""
    seen = _stub_passes(monkeypatch, transcript="")
    with pytest.raises(sa.NoSpeechDetected):
        sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT)
    assert seen["assess"] == [], "the assessed pass must not be spent on an empty transcript"


# --- The assessment config -------------------------------------------------------------------


def test_an_empty_reference_text_is_what_switches_azure_to_unscripted() -> None:
    config = json.loads(sa.assessment_config_json("", Mode.UNSCRIPTED))
    assert config["referenceText"] == ""
    assert config["enableProsodyAssessment"] is True
    # A miscue against a machine transcript is two recognisers disagreeing, not a speaker error.
    assert config["enableMiscue"] is False


def test_the_retired_content_fields_are_off_by_default() -> None:
    """Azure retired content assessment at Speech SDK 1.46.0; this project pins 1.51.1."""
    config = json.loads(sa.assessment_config_json("", Mode.UNSCRIPTED, topic=PROMPT))
    assert "enableContentAssessment" not in config
    assert "contentTopic" not in config


def test_the_probe_flag_puts_the_retired_fields_on_the_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The JSON config passes unknown keys through untouched, which is the only route left.

    Whether the service still answers is a live-call question. That the client can still ask is
    this test.
    """
    monkeypatch.setenv("UNSCRIPTED_CONTENT_PROBE", "true")
    config = json.loads(sa.assessment_config_json("", Mode.UNSCRIPTED, topic=PROMPT))
    assert config["enableContentAssessment"] is True
    assert config["contentTopic"] == PROMPT


def test_the_probe_flag_never_leaks_into_a_scripted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNSCRIPTED_CONTENT_PROBE", "true")
    for mode in (Mode.DRILL, Mode.PARAGRAPH):
        assert "enableContentAssessment" not in json.loads(
            sa.assessment_config_json("hello", mode, topic=PROMPT)
        )


# --- What Mode C refuses to report ------------------------------------------------------------


def _unscripted_payload(words: list[str]) -> dict[str, Any]:
    text = " ".join(words)
    return {
        "DisplayText": text,
        "Duration": 30_000_000,
        "NBest": [
            {
                "Display": text,
                "PronunciationAssessment": {
                    "AccuracyScore": 90.0,
                    "FluencyScore": 85.0,
                    "ProsodyScore": 80.0,
                    "PronScore": 86.0,
                },
                "Words": [
                    {"Word": word, "PronunciationAssessment": {"AccuracyScore": 90.0}}
                    for word in words
                ],
            }
        ],
    }


def test_there_is_no_completeness_score_in_mode_c() -> None:
    """Nothing to be complete against.

    Azure's own unscripted results table carries no CompletenessScore, and the composite for
    the speaking scenario is defined without it. Against a machine transcript it would come out
    ~100 by construction — a number measuring the recogniser agreeing with itself.
    """
    payloads = [_unscripted_payload(["one", "two", "three"])]
    overall, _text, _words = sa.normalise(payloads, "one two three", Mode.UNSCRIPTED)
    assert overall["completeness"] is None, "None, never 0.0 or 100.0"
    assert overall["pron_score"] is not None, "the scores that DO exist must survive"


def test_no_miscue_diff_runs_against_the_machines_own_transcript() -> None:
    """A diff here reports one recogniser disagreeing with another as a speaker error."""
    payloads = [_unscripted_payload(["alpha", "bravo"])]
    _overall, _text, words = sa.normalise(
        payloads, "alpha bravo charlie delta echo", Mode.UNSCRIPTED
    )
    assert [w["word"] for w in words] == ["alpha", "bravo"]
    assert not any(w["error_type"] == "Omission" for w in words)


def test_the_same_payload_in_paragraph_mode_does_diff() -> None:
    """The contrast that shows the Mode C rule is a rule and not a missing feature."""
    payloads = [_unscripted_payload(["alpha", "bravo"])]
    _overall, _text, words = sa.normalise(
        payloads, "alpha bravo charlie delta echo", Mode.PARAGRAPH
    )
    assert any(w["error_type"] == "Omission" for w in words)


def test_a_repeated_word_is_still_caught_without_an_aligner() -> None:
    """Free speech is where stumbles happen, so losing them in Mode C would be the wrong trade.

    `_mark_repetitions` needs one of the pair labelled `Insertion`, which only a miscue diff or
    `enableMiscue` produces. Adjacency is enough on its own, and both copies are marked because
    which one carries the badly scored phonemes cannot be known from the pair.
    """
    payloads = [_unscripted_payload(["we", "hit", "hit", "a", "problem"])]
    _overall, _text, words = sa.normalise(payloads, "", Mode.UNSCRIPTED)
    marked = [w["word"] for w in words if w["disfluency"] == sa.REPETITION]
    assert marked == ["hit", "hit"]


def test_a_word_repeated_far_apart_is_not_a_stumble() -> None:
    payloads = [_unscripted_payload(["hit", "a", "problem", "and", "hit", "another"])]
    _overall, _text, words = sa.normalise(payloads, "", Mode.UNSCRIPTED)
    assert not any(w["disfluency"] for w in words)


# --- Content scores out of the payload, if Azure ever sends them ------------------------------


def test_azure_content_scores_are_read_when_present() -> None:
    payload = _unscripted_payload(["one", "two"])
    payload["NBest"][0]["PronunciationAssessment"].update(
        {"VocabularyScore": 70.0, "GrammarScore": 80.0, "TopicScore": 90.0}
    )
    overall, _text, _words = sa.normalise([payload], "", Mode.UNSCRIPTED)
    assert overall["content"] == {"vocabulary": 70.0, "grammar": 80.0, "topic": 90.0}


def test_a_payload_without_them_carries_no_content_key() -> None:
    """The expected case, since the feature is retired. It must not fabricate an empty one."""
    overall, _text, _words = sa.normalise(
        [_unscripted_payload(["one", "two"])], "", Mode.UNSCRIPTED
    )
    assert "content" not in overall


# --- The meter ---------------------------------------------------------------------------------


def test_the_preflight_prices_both_passes_before_the_first_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exit criterion, at the unit level: the cost shown before pass 1 covers both passes."""
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_TIER_CONFIRMED_F0", "true")
    monkeypatch.setenv("UNSCRIPTED_TWO_PASS", "true")
    monkeypatch.setenv("AZURE_FREE_STT_SECONDS", "100")
    conn = db.connect(":memory:")
    budget.reset_exhausted_flag()

    # 60 s of audio: one pass fits inside a 100 s allowance, two do not.
    budget.preflight_stt(conn, 60.0, Mode.PARAGRAPH)
    with pytest.raises(budget.BudgetError, match="twice"):
        budget.preflight_stt(conn, 60.0, Mode.UNSCRIPTED)
    conn.close()


def test_the_meter_is_charged_for_both_passes_after_the_fact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-flight and post-hoc have to agree, or the month drifts low by every Mode C attempt."""
    conn = db.connect(":memory:")
    seconds, attempts = 90.0, 2
    db.record_attempt(
        conn,
        mode=Mode.UNSCRIPTED,
        reference_text=PROMPT,
        recognised_text="x",
        audio_seconds=seconds * attempts,
        audio_sha256="h",
        overall_scores={},
        azure_raw={},
    )
    assert db.monthly_stt_seconds(conn) == pytest.approx(180.0)
    conn.close()


# --- Cancellation still works through both passes ---------------------------------------------


def test_a_stop_before_anything_is_sent_costs_nothing(online: None) -> None:
    event = threading.Event()
    event.set()
    with pytest.raises(sa.Cancelled) as caught:
        sa.recognise("clip.wav", "", Mode.UNSCRIPTED, topic=PROMPT, cancel_event=event)
    assert caught.value.reached_azure is False
