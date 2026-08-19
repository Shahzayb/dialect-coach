"""Stopping an assessment part-way, and what that must not leave behind.

The rule under test throughout: **a cancelled run is never recorded and never metered.**
Whether it reached Azure changes only what the user is told, never what is stored — and
since the usage meter is derived from the attempts table, "no row" is also "no charge".

Nothing here sleeps or races. `_assess_continuous` is driven against a fake recognizer
whose events fire when the test says so, which is what lets a cancellation be tested at an
exact point rather than by hoping a real one lands in the right millisecond.
"""

from __future__ import annotations

import threading

import pytest

import app as app_module
import speech_analyzer as sa
from utils import Mode

REFERENCE = "Thursday brought thunder and thick clouds."


class FakeAsync:
    """What the SDK's `*_async()` calls return: something with `.get()`."""

    def __init__(self, on_get=None) -> None:
        self._on_get = on_get

    def get(self):
        if self._on_get is not None:
            self._on_get()
        return


class FakeSignal:
    """One of the recognizer's `.connect()`-able event slots."""

    def __init__(self) -> None:
        self.handler = None

    def connect(self, handler) -> None:
        self.handler = handler


class FakeRecognizer:
    """A recognizer whose session ends only when the test says so.

    `finish_on_start` is what separates the two cases under test: left False the session
    hangs, so the only way out of the wait is a cancellation; set True it completes
    normally, which is how the polling loop is checked for not having broken the happy path.
    """

    def __init__(self, finish_on_start: bool = False) -> None:
        self.recognized = FakeSignal()
        self.canceled = FakeSignal()
        self.session_stopped = FakeSignal()
        self.stopped = False
        self.finish_on_start = finish_on_start

    def start_continuous_recognition_async(self) -> FakeAsync:
        def maybe_finish() -> None:
            if self.finish_on_start and self.session_stopped.handler is not None:
                # Fired inline, after the handlers are connected: no timer, no race.
                self.session_stopped.handler(None)

        return FakeAsync(maybe_finish)

    def stop_continuous_recognition_async(self) -> FakeAsync:
        def mark() -> None:
            self.stopped = True

        return FakeAsync(mark)


@pytest.fixture
def recognizer_factory(monkeypatch: pytest.MonkeyPatch):
    """Swap the real recognizer out on the installed SDK module.

    The SDK is genuinely importable in this container, so the module itself is left alone
    and only the two classes `_assess_continuous` constructs are replaced. Faking the whole
    module in `sys.modules` does not work: `import azure.cognitiveservices.speech` resolves
    the package tree rather than reading the key.
    """
    import azure.cognitiveservices.speech as speechsdk

    monkeypatch.setattr(sa, "_speech_config", lambda: None)
    monkeypatch.setattr(
        sa,
        "_pron_config",
        lambda *a, **k: type("C", (), {"apply_to": lambda self, r: None})(),
    )
    monkeypatch.setattr(sa, "CANCEL_POLL_SECONDS", 0.01)
    monkeypatch.setattr(speechsdk, "AudioConfig", lambda **kwargs: None)

    def build(finish_on_start: bool = False) -> FakeRecognizer:
        recognizer = FakeRecognizer(finish_on_start)
        monkeypatch.setattr(speechsdk, "SpeechRecognizer", lambda **kwargs: recognizer)
        return recognizer

    return build


# --- The cancellation points themselves ---------------------------------------------------


def test_a_cancel_set_before_the_call_never_reaches_azure() -> None:
    event = threading.Event()
    event.set()

    with pytest.raises(sa.Cancelled) as caught:
        sa.recognise("unused.wav", REFERENCE, Mode.DRILL, cancel_event=event)

    # The distinction that drives the message: nothing was sent, so nothing was spent.
    assert caught.value.reached_azure is False


def test_a_cancel_during_continuous_recognition_stops_the_session(
    recognizer_factory,
) -> None:
    recognizer = recognizer_factory()  # never finishes on its own
    event = threading.Event()
    event.set()  # already stopping when the wait begins

    with pytest.raises(sa.Cancelled) as caught:
        sa._assess_continuous("unused.wav", REFERENCE, event)

    assert caught.value.reached_azure is True
    # The session is closed rather than left running against the account.
    assert recognizer.stopped is True


def test_an_uncancelled_continuous_run_still_finishes_normally(
    recognizer_factory,
) -> None:
    """The polling loop must not have broken the path where nobody cancels."""
    recognizer = recognizer_factory(finish_on_start=True)

    # No speech was recognised, so this ends the way an empty session does.
    with pytest.raises(sa.NoSpeechDetected):
        sa._assess_continuous("unused.wav", REFERENCE, None)

    assert recognizer.stopped is True


def test_a_slow_session_is_not_cancelled_by_an_unset_event(recognizer_factory) -> None:
    """An event that exists but is never set must not end the wait early."""
    recognizer = recognizer_factory(finish_on_start=True)

    with pytest.raises(sa.NoSpeechDetected):
        sa._assess_continuous("unused.wav", REFERENCE, threading.Event())

    assert recognizer.stopped is True


def test_cancellation_is_an_assessment_error_so_existing_handlers_see_it() -> None:
    """`Cancelled` has to be catchable as the family the app already handles."""
    assert issubclass(sa.Cancelled, sa.AssessmentError)


# --- What a cancelled run must not leave behind -------------------------------------------


def _wav_and_conn(tmp_path):
    import db

    conn = db.connect(str(tmp_path / "coach.db"))
    return conn


def test_a_cancelled_run_writes_no_attempt_row(tmp_path, monkeypatch) -> None:
    """The exit criterion: no half-written attempt row after a stop."""
    conn = _wav_and_conn(tmp_path)
    event = threading.Event()
    event.set()

    outcome = app_module.run_assessment_job(conn, b"RIFFfake", 5.0, REFERENCE, Mode.DRILL, event)

    assert outcome.cancelled is True
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_a_cancelled_run_leaves_the_meter_at_zero(tmp_path) -> None:
    """No row means no metered seconds — the meter is derived from that same table."""
    import db

    conn = _wav_and_conn(tmp_path)
    event = threading.Event()
    event.set()

    app_module.run_assessment_job(conn, b"RIFFfake", 12.0, REFERENCE, Mode.DRILL, event)

    assert db.monthly_stt_seconds(conn) == 0.0


def test_a_result_that_arrives_after_a_stop_is_discarded(tmp_path, monkeypatch) -> None:
    """Drill cannot be interrupted mid-call, so the late result is dropped instead."""
    conn = _wav_and_conn(tmp_path)
    event = threading.Event()

    def analyse_then_stop(*args, **kwargs):
        # The user clicks Stop while Azure is answering.
        event.set()
        return sa.Assessment(
            raw=[{}],
            overall_scores={"pron_score": 80.0},
            recognised_text="thursday",
            words=[],
            offline=True,
            attempts=0,
        )

    monkeypatch.setattr(sa, "analyse", analyse_then_stop)

    outcome = app_module.run_assessment_job(conn, b"RIFFfake", 5.0, REFERENCE, Mode.DRILL, event)

    assert outcome.cancelled is True
    assert outcome.assessment is None
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_a_completed_run_does_write_its_row(tmp_path, monkeypatch) -> None:
    """The control case: without a cancel, the row lands exactly as before."""
    conn = _wav_and_conn(tmp_path)

    monkeypatch.setattr(
        sa,
        "analyse",
        lambda *a, **k: sa.Assessment(
            raw=[{"ok": True}],
            overall_scores={"pron_score": 83.0},
            recognised_text="thursday",
            words=[],
            offline=True,
            attempts=0,
        ),
    )

    outcome = app_module.run_assessment_job(
        conn, b"RIFFfake", 5.0, REFERENCE, Mode.DRILL, threading.Event()
    )

    assert outcome.cancelled is False
    assert outcome.attempt_id is not None
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 1


def test_the_worker_never_raises_even_on_an_unexpected_bug(tmp_path, monkeypatch) -> None:
    """An exception on a worker thread is invisible, so every exit must be an outcome."""
    conn = _wav_and_conn(tmp_path)

    def explode(*args, **kwargs):
        raise ValueError("something nobody predicted")

    monkeypatch.setattr(sa, "analyse", explode)

    outcome = app_module.run_assessment_job(
        conn, b"RIFFfake", 5.0, REFERENCE, Mode.DRILL, threading.Event()
    )

    assert outcome.error is not None
    assert outcome.assessment is None
    assert conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0


def test_a_worker_failure_is_returned_not_rendered(tmp_path, monkeypatch) -> None:
    """The worker cannot call st.error: it returns an (icon, message) pair instead."""
    conn = _wav_and_conn(tmp_path)
    monkeypatch.setattr(
        sa,
        "analyse",
        lambda *a, **k: (_ for _ in ()).throw(
            sa.NoSpeechDetected("Azure heard no speech in that recording.")
        ),
    )

    outcome = app_module.run_assessment_job(
        conn, b"RIFFfake", 5.0, REFERENCE, Mode.DRILL, threading.Event()
    )

    assert outcome.error is not None
    icon, message = outcome.error
    assert icon and "no speech" in message.lower()
