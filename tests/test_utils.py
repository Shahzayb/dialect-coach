"""Config resolution, hashing, retry policy, and secret redaction."""

from __future__ import annotations

import logging

import pytest

import utils
from utils import Mode, PermanentError, TransientError


def test_get_prefers_environment_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_MODEL", "some-other-model")
    assert utils.get("GEMINI_MODEL") == "some-other-model"


def test_get_falls_back_to_declared_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert utils.get("GEMINI_MODEL") == "gemini-3.6-flash"


def test_require_names_the_missing_key_without_a_value() -> None:
    with pytest.raises(utils.ConfigError) as excinfo:
        utils.require("AZURE_SPEECH_KEY")
    assert "AZURE_SPEECH_KEY" in str(excinfo.value)


def test_check_required_is_empty_offline() -> None:
    # Offline needs no credentials at all — that is the whole point of the mode.
    assert utils.offline_mode() is True
    assert utils.check_required() == []


def test_check_required_reports_missing_keys_when_online(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OFFLINE_MODE", "false")
    assert set(utils.check_required()) == {"AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"}


def test_max_duration_is_per_mode() -> None:
    assert utils.max_duration_seconds(Mode.DRILL) == 30
    assert utils.max_duration_seconds(Mode.PARAGRAPH) == 120
    assert utils.max_duration_seconds(Mode.UNSCRIPTED) == 300


def test_attempt_hash_separates_text_from_audio() -> None:
    # Without a delimiter, ("ab", b"c") and ("a", b"bc") would collide.
    assert utils.attempt_hash("ab", b"c", Mode.DRILL) != utils.attempt_hash("a", b"bc", Mode.DRILL)


def test_attempt_hash_is_stable() -> None:
    assert (
        utils.attempt_hash("hello", b"\x01\x02", Mode.DRILL)
        == utils.attempt_hash("hello", b"\x01\x02", Mode.DRILL)
    )


def test_attempt_hash_separates_mode_from_the_rest() -> None:
    """The same text read into the same recording is assessed differently per mode —

    Drill is single-shot, Paragraph is continuous — so the same (text, audio) pair must
    not collide across modes. A collision would silently serve the other mode's cached
    result on Assess: no error, no re-assessment, just the wrong report on screen.
    """
    assert (
        utils.attempt_hash("hello", b"\x01\x02", Mode.DRILL)
        != utils.attempt_hash("hello", b"\x01\x02", Mode.PARAGRAPH)
    )


def test_normalise_words_strips_punctuation_and_case() -> None:
    assert utils.normalise_words("The cat's hat, indeed!") == ["the", "cat's", "hat", "indeed"]


def test_retry_transient_retries_then_succeeds() -> None:
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("busy")
        return "ok"

    assert utils.retry_transient(flaky, sleep=lambda _: None) == "ok"
    assert calls["n"] == 3


def test_retry_transient_gives_up_after_attempts() -> None:
    calls = {"n": 0}

    def always_busy() -> None:
        calls["n"] += 1
        raise TransientError("busy")

    with pytest.raises(TransientError):
        utils.retry_transient(always_busy, attempts=3, sleep=lambda _: None)
    assert calls["n"] == 3


def test_retry_transient_does_not_retry_permanent_errors() -> None:
    calls = {"n": 0}

    def bad_key() -> None:
        calls["n"] += 1
        raise PermanentError("401")

    with pytest.raises(PermanentError):
        utils.retry_transient(bad_key, sleep=lambda _: None)
    assert calls["n"] == 1, "a bad key must not be retried"


# --- Secret redaction (master plan acceptance criterion 7) --------------------------------

FAKE_KEY = "sk-not-a-real-key-0123456789abcdef"


def test_redact_removes_a_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SPEECH_KEY", FAKE_KEY)
    assert FAKE_KEY not in utils.redact(f"request failed for key {FAKE_KEY}")


def test_log_record_never_carries_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AZURE_SPEECH_KEY", FAKE_KEY)
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="auth failed with %s", args=(FAKE_KEY,), exc_info=None,
    )
    utils.SecretRedactingFilter([FAKE_KEY]).filter(record)
    assert FAKE_KEY not in record.getMessage()
    assert utils.SecretRedactingFilter.PLACEHOLDER in record.getMessage()


def test_redaction_filter_ignores_short_values() -> None:
    # A 3-character "secret" would redact ordinary words out of every log line.
    scrubbed = utils.SecretRedactingFilter(["abc"])._scrub("abc def")
    assert scrubbed == "abc def"


def test_a_key_inside_a_traceback_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gap a filter alone leaves: filters run before the formatter renders exc_text.

    An SDK exception whose message embeds the subscription key would otherwise reach the
    log in full, through the traceback rather than through the message.
    """
    monkeypatch.setenv("AZURE_SPEECH_KEY", FAKE_KEY)
    record = logging.LogRecord(
        name="test", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="assessment failed", args=(), exc_info=None,
    )
    try:
        raise RuntimeError(f"azure rejected subscription {FAKE_KEY}")
    except RuntimeError:
        import sys
        record.exc_info = sys.exc_info()

    rendered = utils.SecretRedactingFormatter("%(message)s").format(record)
    assert FAKE_KEY not in rendered
    assert utils.SecretRedactingFilter.PLACEHOLDER in rendered


def test_the_formatter_leaves_ordinary_text_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_SPEECH_KEY", FAKE_KEY)
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="recorded attempt 4", args=(), exc_info=None,
    )
    assert utils.SecretRedactingFormatter("%(message)s").format(record) == "recorded attempt 4"


def test_retry_reports_every_attempt_it_made() -> None:
    """Callers pay per call: a retry re-sends the audio and can still consume allowance."""
    seen: list[int] = []
    calls = {"n": 0}

    def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("busy")
        return "ok"

    utils.retry_transient(flaky, sleep=lambda _: None, on_attempt=seen.append)
    assert seen == [1, 2, 3]
