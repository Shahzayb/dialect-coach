"""Text-to-speech: SSML construction, refusals, billing, and error mapping.

Every test here runs offline with no key. The SDK is reached through exactly one seam —
`tts._speak` — so the logic wrapped around it (retry accounting, character counting,
cancellation mapping) is testable without a network call or a synthesised byte.
"""

from __future__ import annotations

import pytest

import speech_analyzer as sa
import tts
import utils


@pytest.fixture
def online(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's global OFFLINE_MODE for the tests that exercise the calling path.

    No network results: `_speak` is always substituted in these tests. This only turns off
    the early refusal so the code after it can be reached.
    """
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k" * 32)
    monkeypatch.setenv("AZURE_SPEECH_REGION", "westeurope")


# --- SSML ----------------------------------------------------------------------------------


def test_slow_ssml_carries_the_rate_and_the_voice() -> None:
    ssml = tts.slow_ssml("hello", "en-US-BrianNeural")
    assert 'rate="-35%"' in ssml
    assert 'name="en-US-BrianNeural"' in ssml
    assert "hello" in ssml


def test_slow_ssml_escapes_text_that_would_break_the_request() -> None:
    """An unescaped & or < is rejected as malformed — the whole phrase fails, not one word."""
    ssml = tts.slow_ssml("Tom & Jerry <fast>", "en-US-BrianNeural")
    assert "&amp;" in ssml
    assert "&lt;fast&gt;" in ssml
    assert "<fast>" not in ssml


def test_slow_ssml_quotes_the_voice_attribute_safely() -> None:
    """quoteattr may switch to single quotes rather than entity-escape; both are safe.

    What matters is that the value cannot terminate the attribute and inject markup, not
    which of the two legal quoting styles it happened to pick.
    """
    ssml = tts.slow_ssml("hi", 'ev"il')
    assert "name='ev\"il'" in ssml
    assert ssml.count("<voice") == 1


# --- Refusals ------------------------------------------------------------------------------


def test_empty_text_is_refused_before_any_call() -> None:
    with pytest.raises(tts.SynthesisError, match="nothing to say"):
        tts.synthesise("   ")


def test_offline_mode_refuses_to_synthesise(monkeypatch: pytest.MonkeyPatch) -> None:
    """OFFLINE_MODE means no network call, ever. There is no audio fixture to replay."""
    called = False

    def explode(*a, **k):
        nonlocal called
        called = True
        raise AssertionError("must not reach the SDK")

    monkeypatch.setattr(tts, "_speak", explode)
    with pytest.raises(tts.SynthesisError, match="OFFLINE_MODE"):
        tts.synthesise("hello")
    assert not called


# --- The calling path ----------------------------------------------------------------------


def test_normal_speed_sends_plain_text_not_ssml(online, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(payload, voice, *, is_ssml):
        seen.update(payload=payload, voice=voice, is_ssml=is_ssml)
        return b"AUDIO"

    monkeypatch.setattr(tts, "_speak", fake)
    result = tts.synthesise("weather")

    assert result.audio == b"AUDIO"
    assert seen["payload"] == "weather"
    assert seen["is_ssml"] is False


def test_slow_sends_ssml(online, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake(payload, voice, *, is_ssml):
        seen.update(payload=payload, is_ssml=is_ssml)
        return b"AUDIO"

    monkeypatch.setattr(tts, "_speak", fake)
    tts.synthesise("weather", slow=True)

    assert seen["is_ssml"] is True
    assert "<prosody" in seen["payload"]


def test_the_voice_comes_from_the_environment(online, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_TTS_VOICE", "en-US-BrianNeural")
    monkeypatch.setattr(tts, "_speak", lambda *a, **k: b"AUDIO")
    assert tts.synthesise("hi").voice == "en-US-BrianNeural"


def test_billing_counts_the_whole_payload_including_markup(
    online, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over-counting is the right direction for a guard; under-counting is not."""
    monkeypatch.setattr(tts, "_speak", lambda *a, **k: b"AUDIO")

    plain = tts.synthesise("weather")
    slow = tts.synthesise("weather", slow=True)

    assert plain.characters == len("weather")
    assert slow.characters > plain.characters, "SSML markup is billed, conservatively"


def test_every_attempt_is_counted_not_just_the_successful_one(
    online, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A retry re-sends the text and can consume allowance even when it fails."""
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise utils.TransientError("Azure was busy")
        return b"AUDIO"

    monkeypatch.setattr(tts, "_speak", flaky)
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)

    result = tts.synthesise("weather")
    assert result.attempts == 3, "the meter has to charge for all three uploads"


def test_a_permanent_failure_is_not_retried(online, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def bad_key(*a, **k):
        calls["n"] += 1
        raise utils.PermanentError("Azure rejected the credentials.")

    monkeypatch.setattr(tts, "_speak", bad_key)
    with pytest.raises(utils.PermanentError):
        tts.synthesise("weather")
    assert calls["n"] == 1, "retrying a bad key only burns time"


# --- Error mapping -------------------------------------------------------------------------


class _Details:
    """Stands in for a cancellation details object. Both SDK variants have this shape."""

    def __init__(self, error_code, error_details: str = "") -> None:
        self.error_code = error_code
        self.error_details = error_details


def test_a_synthesis_bad_request_blames_the_voice_not_the_audio() -> None:
    """The shared classifier's one genuinely TTS-specific branch."""
    import azure.cognitiveservices.speech as speechsdk

    error = sa.classify_cancellation(
        _Details(speechsdk.CancellationErrorCode.BadRequest),
        bad_request_hint=tts.BAD_REQUEST_HINT,
    )
    assert isinstance(error, utils.PermanentError)
    assert "AZURE_TTS_VOICE" in str(error)
    assert "audio format" not in str(error)


def test_recognition_keeps_its_own_bad_request_message() -> None:
    import azure.cognitiveservices.speech as speechsdk

    error = sa.classify_cancellation(_Details(speechsdk.CancellationErrorCode.BadRequest))
    assert "audio format" in str(error)


def test_a_synthesis_403_is_the_same_quota_type_the_budget_guard_watches() -> None:
    """One QuotaExhausted type is what lets a TTS 403 block the month like an STT one."""
    import azure.cognitiveservices.speech as speechsdk

    error = sa.classify_cancellation(
        _Details(speechsdk.CancellationErrorCode.Forbidden),
        bad_request_hint=tts.BAD_REQUEST_HINT,
    )
    assert sa.is_quota_exhausted(error)


def test_a_busy_service_is_classified_as_retryable() -> None:
    import azure.cognitiveservices.speech as speechsdk

    error = sa.classify_cancellation(
        _Details(speechsdk.CancellationErrorCode.ServiceUnavailable),
        bad_request_hint=tts.BAD_REQUEST_HINT,
    )
    assert isinstance(error, utils.TransientError)


def test_a_synthesis_403_is_caught_by_the_handler_that_acts_on_it() -> None:
    """QuotaExhausted is an AssessmentError, not a PermanentError or a SynthesisError.

    `play()` marks the month exhausted on a 403, but only if the exception reaches its
    handler. Leaving AssessmentError out of that tuple made the branch unreachable and let
    the error escape into an uncaught Streamlit traceback instead.
    """
    import speech_analyzer as sa

    handled = (utils.PermanentError, utils.TransientError, tts.SynthesisError, sa.AssessmentError)
    assert issubclass(sa.QuotaExhausted, handled)
    assert not issubclass(
        sa.QuotaExhausted, (utils.PermanentError, utils.TransientError, tts.SynthesisError)
    )


def test_the_caller_sees_attempts_even_when_every_one_fails(
    online, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failing path is the one that needs this: the exception carries no count.

    Three transient failures mean the text reached Azure three times and may have been
    charged three times, but `synthesise` raises rather than returning a Synthesis, so
    without `on_attempt` the caller has nothing to meter.
    """
    seen: list[int] = []
    monkeypatch.setattr(
        tts, "_speak", lambda *a, **k: (_ for _ in ()).throw(utils.TransientError("busy"))
    )
    monkeypatch.setattr(utils.time, "sleep", lambda _s: None)

    with pytest.raises(utils.TransientError):
        tts.synthesise("weather", on_attempt=seen.append)

    assert seen == [1, 2, 3]
    assert len(seen) == utils.MAX_SYNTHESIS_ATTEMPTS


def test_on_attempt_is_optional() -> None:
    """Every existing caller omits it; adding the hook must not make it mandatory."""
    import inspect

    assert inspect.signature(tts.synthesise).parameters["on_attempt"].default is None


# --- The disk cache ----------------------------------------------------------------------
# It holds synthesised audio only. The no-stored-audio rule covers the user's recordings and
# nothing here ever writes one; a neural voice reading "think" carries no personal data.


def test_the_key_changes_with_every_part_of_the_identity() -> None:
    base = tts.cache_key("en-US-AvaNeural", "think")
    assert base != tts.cache_key("en-US-BrianNeural", "think")
    assert base != tts.cache_key("en-US-AvaNeural", "sink")
    assert base != tts.cache_key("en-US-AvaNeural", "think", "-35%")


def test_the_key_is_stable_across_calls() -> None:
    assert tts.cache_key("v", "think") == tts.cache_key("v", "think")


def test_the_key_does_not_collide_on_a_shifted_separator() -> None:
    """Concatenating without a separator would make ("ab","c") and ("a","bc") one clip."""
    assert tts.cache_key("ab", "c") != tts.cache_key("a", "bc")


def test_a_miss_is_none_rather_than_an_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    assert tts.cached_audio("en-US-AvaNeural", "think") is None


def test_a_stored_clip_reads_back(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    tts.store_audio("en-US-AvaNeural", "think", b"RIFFfake")
    assert tts.cached_audio("en-US-AvaNeural", "think") == b"RIFFfake"


def test_a_different_rate_is_a_different_entry(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    tts.store_audio("v", "think", b"normal")
    tts.store_audio("v", "think", b"slow", rate=tts.SLOW_RATE)
    assert tts.cached_audio("v", "think") == b"normal"
    assert tts.cached_audio("v", "think", rate=tts.SLOW_RATE) == b"slow"


def test_the_cache_creates_its_directory(tmp_path, monkeypatch) -> None:
    nested = tmp_path / "data" / "tts_cache"
    monkeypatch.setenv("TTS_CACHE_DIR", str(nested))
    tts.store_audio("v", "think", b"bytes")
    assert nested.is_dir()
    assert tts.cached_audio("v", "think") == b"bytes"


def test_an_interrupted_write_leaves_no_partial_hit(tmp_path, monkeypatch) -> None:
    """A truncated WAV that reads as a valid hit would be worse than no cache at all."""
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path))
    tts.store_audio("v", "think", b"bytes")
    assert not list(tmp_path.glob("*.part"))


def test_an_unwritable_cache_is_a_warning_not_a_failure(tmp_path, monkeypatch) -> None:
    """Failing to cache costs quota next time; it must never break playback."""
    monkeypatch.setenv("TTS_CACHE_DIR", str(tmp_path / "file"))
    (tmp_path / "file").write_text("not a directory")
    tts.store_audio("v", "think", b"bytes")  # no raise
    assert tts.cached_audio("v", "think") is None


def test_a_word_is_billed_as_plain_text_not_as_ssml() -> None:
    """The meter charges the payload actually sent, and SSML bills its full markup."""
    plain = tts.payload_for("thursday", slow=False, voice="en-US-AvaNeural")
    wrapped = tts.payload_for("thursday", slow=True, voice="en-US-AvaNeural")
    assert len(plain) == 8
    assert len(wrapped) > 100
