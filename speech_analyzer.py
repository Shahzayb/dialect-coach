"""Azure pronunciation assessment: recognition, raw-JSON capture, and normalisation.

The hard module. Three things here are the difference between working code and code that
silently returns nothing useful:

1. `pron_config.apply_to(recognizer)` — omit it and recognition still succeeds, still
   returns a transcript, and simply has no `PronunciationAssessment` block. It looks like
   an Azure bug and is a client-side mistake.
2. Prosody must be enabled explicitly, or `ProsodyScore` never appears at all.
3. `enableMiscue` is only honoured by single-shot recognition. In continuous mode it does
   not produce Omission/Insertion, so those are diffed locally instead (see `_diff_miscue`).

The assessment config is built from JSON rather than constructor kwargs: `phoneme_alphabet`
and `nbest_phoneme_count` are properties, not constructor parameters, and `grading_system`
/ `granularity` take enum members rather than strings. JSON is the one form that sets every
field the same way across SDK versions.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import utils
from utils import Mode, PermanentError, TransientError

logger = logging.getLogger(__name__)

LOCALE = "en-US"

# The whole point of the tool: 5 alternates per phoneme means the report can say "you
# produced /t/ where /θ/ was expected" rather than "your /θ/ scored 41".
NBEST_PHONEME_COUNT = 5

FIXTURE_DIR = Path(__file__).resolve().parent / "tests" / "fixtures"
FIXTURES: dict[Mode, str] = {
    Mode.DRILL: "sample_azure_response.json",
    Mode.PARAGRAPH: "sample_azure_continuous.json",
}

# How long to wait for continuous recognition to report session_stopped. Generous: it is a
# backstop against a hung SDK callback, not a processing budget.
CONTINUOUS_TIMEOUT_SECONDS = 300.0


class AssessmentError(RuntimeError):
    """Assessment failed in a way worth showing the user. Never carries a key."""


class NoSpeechDetected(AssessmentError):
    """Azure connected fine and heard nothing. Distinct from a failure — see §10."""


@dataclass
class Assessment:
    """One assessed recording: the verbatim payloads plus the normalised view of them."""

    raw: list[dict[str, Any]] = field(default_factory=list)
    overall_scores: dict[str, Any] = field(default_factory=dict)
    recognised_text: str = ""
    words: list[dict[str, Any]] = field(default_factory=list)
    offline: bool = False

    def as_normalised(self) -> dict[str, Any]:
        """The shape master plan §4 specifies."""
        return {
            "overall_scores": self.overall_scores,
            "recognised_text": self.recognised_text,
            "words": self.words,
        }


# --- Configuration -------------------------------------------------------------------------


def _speech_config():
    import azure.cognitiveservices.speech as speechsdk

    config = speechsdk.SpeechConfig(
        subscription=utils.require("AZURE_SPEECH_KEY"),
        region=utils.require("AZURE_SPEECH_REGION"),
    )
    # Must be set explicitly. Prosody assessment supports en-US and nothing else.
    config.speech_recognition_language = LOCALE
    return config


def assessment_config_json(reference_text: str, mode: Mode) -> str:
    """The assessment config as JSON. Separated out so a test can assert its contents."""
    return json.dumps(
        {
            "referenceText": reference_text or "",
            "gradingSystem": "HundredMark",
            "granularity": "Phoneme",
            "phonemeAlphabet": "IPA",
            "nBestPhonemeCount": NBEST_PHONEME_COUNT,
            # Only honoured single-shot. True alongside continuous recognition is
            # unsupported and may be rejected outright.
            "enableMiscue": mode is Mode.DRILL,
            "enableProsodyAssessment": True,
        }
    )


def _pron_config(reference_text: str, mode: Mode):
    import azure.cognitiveservices.speech as speechsdk

    return speechsdk.PronunciationAssessmentConfig(
        json_string=assessment_config_json(reference_text, mode)
    )


# --- Error mapping ---------------------------------------------------------------------------


def _classify_cancellation(details) -> Exception:
    """Turn an Azure cancellation into a retryable or a terminal error, with a real message.

    Retrying a 401 only burns time; retrying a 403 quota response can consume more
    allowance for nothing. Both must be distinguishable in the message — "your key is
    wrong" and "your month is gone" need different actions from the user.
    """
    import azure.cognitiveservices.speech as speechsdk

    code = getattr(details, "error_code", None)
    # error_details can echo request context; scrub before it reaches a log or the UI.
    raw_detail = utils.redact(str(getattr(details, "error_details", "") or ""))

    transient = {
        speechsdk.CancellationErrorCode.ServiceUnavailable,
        speechsdk.CancellationErrorCode.ServiceTimeout,
        speechsdk.CancellationErrorCode.ConnectionFailure,
        speechsdk.CancellationErrorCode.TooManyRequests,
    }
    if code in transient:
        return TransientError(f"Azure was temporarily unavailable ({code}). {raw_detail}")

    if code == speechsdk.CancellationErrorCode.AuthenticationFailure:
        return PermanentError(
            "Azure rejected the credentials. AZURE_SPEECH_KEY or AZURE_SPEECH_REGION is "
            "wrong — note the key must match the region it was issued for."
        )
    if code == speechsdk.CancellationErrorCode.Forbidden:
        return PermanentError(
            "Azure returned 403. On an F0 resource this means the monthly free allowance "
            "is used up; it resets at the start of the next billing month. "
            "QUOTA_EXHAUSTED"
        )
    if code == speechsdk.CancellationErrorCode.BadRequest:
        return PermanentError(
            f"Azure rejected the request as malformed. Check the reference text and the "
            f"audio format. {raw_detail}"
        )
    return PermanentError(f"Azure cancelled the request ({code}). {raw_detail}")


def is_quota_exhausted(error: BaseException) -> bool:
    """True when the error is Azure's own 'the month is gone' signal."""
    return "QUOTA_EXHAUSTED" in str(error)


def _raw_json(result) -> dict[str, Any]:
    import azure.cognitiveservices.speech as speechsdk

    payload = result.properties.get(speechsdk.PropertyId.SpeechServiceResponse_JsonResult)
    if not payload:
        raise AssessmentError(
            "Azure returned a result with no JSON body, so there is nothing to assess."
        )
    return json.loads(payload)


# --- Recognition ------------------------------------------------------------------------------


def _assess_single_shot(wav_path: str, reference_text: str) -> list[dict[str, Any]]:
    """Mode A. `recognize_once_async` caps at roughly 15 s, so drills stay one utterance."""
    import azure.cognitiveservices.speech as speechsdk

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=_speech_config(),
        audio_config=speechsdk.AudioConfig(filename=wav_path),
    )
    # Mandatory. Without it the transcript comes back and the assessment block does not.
    _pron_config(reference_text, Mode.DRILL).apply_to(recognizer)

    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.RecognizedSpeech:
        return [_raw_json(result)]
    if result.reason == speechsdk.ResultReason.NoMatch:
        raise NoSpeechDetected(
            "Azure heard no speech in that recording. Check the microphone picked you up, "
            "and that the clip is not silence."
        )
    if result.reason == speechsdk.ResultReason.Canceled:
        raise _classify_cancellation(result.cancellation_details)
    raise AssessmentError(f"Unexpected recognition result: {result.reason}")


def _assess_continuous(wav_path: str, reference_text: str) -> list[dict[str, Any]]:
    """Mode B. Accumulate `recognized` events, stop on session_stopped or canceled.

    Returns one payload per utterance; merging them is `_merge_overall` further down.
    """
    import azure.cognitiveservices.speech as speechsdk

    recognizer = speechsdk.SpeechRecognizer(
        speech_config=_speech_config(),
        audio_config=speechsdk.AudioConfig(filename=wav_path),
    )
    _pron_config(reference_text, Mode.PARAGRAPH).apply_to(recognizer)

    payloads: list[dict[str, Any]] = []
    failure: list[Exception] = []
    done = threading.Event()

    def on_recognized(evt) -> None:
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            try:
                payloads.append(_raw_json(evt.result))
            except Exception as exc:  # a malformed utterance must not kill the session
                logger.warning("Skipped an unparseable utterance: %s", utils.redact(str(exc)))

    def on_canceled(evt) -> None:
        # EndOfStream is the normal way a file-backed session finishes, not a failure.
        if evt.reason != speechsdk.CancellationReason.EndOfStream:
            failure.append(_classify_cancellation(evt))
        done.set()

    def on_stopped(_evt) -> None:
        done.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_stopped)

    recognizer.start_continuous_recognition_async().get()
    try:
        if not done.wait(timeout=CONTINUOUS_TIMEOUT_SECONDS):
            raise AssessmentError(
                "Azure did not finish assessing that recording in time. Try a shorter one."
            )
    finally:
        recognizer.stop_continuous_recognition_async().get()

    if failure:
        raise failure[0]
    if not payloads:
        raise NoSpeechDetected(
            "Azure heard no speech in that recording. Check the microphone picked you up, "
            "and that the clip is not silence."
        )
    return payloads


def _load_fixture(mode: Mode) -> list[dict[str, Any]]:
    """Replay a committed payload. The true zero-cost path — no network call at all."""
    name = FIXTURES.get(mode) or FIXTURES[Mode.DRILL]
    path = FIXTURE_DIR / name
    if not path.exists():
        raise AssessmentError(
            f"OFFLINE_MODE is on but the fixture {path.name} is missing, so there is "
            f"nothing to replay."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def recognise(wav_path: str, reference_text: str, mode: Mode) -> tuple[list[dict[str, Any]], bool]:
    """Run the assessment for `mode`. Returns (verbatim payloads, came_from_fixture)."""
    if utils.offline_mode():
        logger.info("OFFLINE_MODE: replaying the %s fixture instead of calling Azure", mode.value)
        return _load_fixture(mode), True

    if mode is Mode.UNSCRIPTED:
        raise AssessmentError(
            "Unscripted mode is not implemented yet — it needs Azure's content assessment, "
            "which is a separate chunk of work."
        )

    call = _assess_single_shot if mode is Mode.DRILL else _assess_continuous
    return utils.retry_transient(lambda: call(wav_path, reference_text)), False
