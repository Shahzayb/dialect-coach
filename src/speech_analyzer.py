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
import time
from collections.abc import Callable, Sequence
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

# Azure times everything in this payload in 100-ns ticks — Offset, Duration, and
# Break.BreakLength with them. See `_prosody_detail` for how the last of those was pinned
# down, since the SDK never names its unit.
TICKS_PER_MS = 10_000
TICKS_PER_SECOND = 10_000_000

# Everything Azure times in this payload lands on a 10 ms grid: every Offset and every
# Duration, at word, syllable and phoneme level, is a whole multiple of this. Verified across
# all four committed fixtures with no exceptions, and asserted in tests/test_parsing.py
# rather than trusted — `rhythm.vocalic_intervals` decides what counts as contiguous by
# comparing gaps against exactly one frame.
FRAME_TICKS = 100_000

# Two levels up: this module lives in src/, the fixtures live beside the tests at the
# repo root. Derived from __file__ rather than cwd so a script run from anywhere finds them.
FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
FIXTURES: dict[Mode, str] = {
    Mode.DRILL: "sample_azure_response.json",
    Mode.PARAGRAPH: "sample_azure_continuous.json",
}

# How long to wait for continuous recognition to report session_stopped. Generous: it is a
# backstop against a hung SDK callback, not a processing budget.
CONTINUOUS_TIMEOUT_SECONDS = 300.0

# How often the continuous wait wakes up to notice a cancellation. Short enough that Stop
# feels immediate, long enough that the wait is not a busy loop.
CANCEL_POLL_SECONDS = 0.2


class AssessmentError(RuntimeError):
    """Assessment failed in a way worth showing the user. Never carries a key."""


class QuotaExhausted(AssessmentError):
    """Azure itself reported the monthly allowance as gone (403). Authoritative."""


class NoSpeechDetected(AssessmentError):
    """Azure connected fine and heard nothing. Distinct from a failure — see §10."""


class Cancelled(AssessmentError):
    """The caller asked for this run to stop before it produced a result.

    `reached_azure` is the difference between "nothing was ever sent" and "audio was
    already on its way when the stop landed". It drives what the user is told, not what
    they are charged: a cancelled run is never recorded and never metered either way.
    """

    def __init__(self, message: str, *, reached_azure: bool) -> None:
        super().__init__(message)
        self.reached_azure = reached_azure


@dataclass
class Assessment:
    """One assessed recording: the verbatim payloads plus the normalised view of them."""

    raw: list[dict[str, Any]] = field(default_factory=list)
    overall_scores: dict[str, Any] = field(default_factory=dict)
    recognised_text: str = ""
    words: list[dict[str, Any]] = field(default_factory=list)
    offline: bool = False
    # How many times the audio was actually sent. Retries re-upload it and can consume
    # allowance even when they fail, so the meter multiplies by this rather than by 1.
    attempts: int = 1

    def as_normalised(self) -> dict[str, Any]:
        """The shape master plan §4 specifies."""
        return {
            "overall_scores": self.overall_scores,
            "recognised_text": self.recognised_text,
            "words": self.words,
        }


# --- Configuration -------------------------------------------------------------------------


def _speech_config() -> Any:
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


def _pron_config(reference_text: str, mode: Mode) -> Any:
    import azure.cognitiveservices.speech as speechsdk

    return speechsdk.PronunciationAssessmentConfig(
        json_string=assessment_config_json(reference_text, mode)
    )


# --- Error mapping ---------------------------------------------------------------------------


DEFAULT_BAD_REQUEST_HINT = "Check the reference text and the audio format."


def classify_cancellation(
    details: Any, *, bad_request_hint: str = DEFAULT_BAD_REQUEST_HINT
) -> Exception:
    """Turn an Azure cancellation into a retryable or a terminal error, with a real message.

    Retrying a 401 only burns time; retrying a 403 quota response can consume more
    allowance for nothing. Both must be distinguishable in the message — "your key is
    wrong" and "your month is gone" need different actions from the user.

    Public, and shared with `tts.py`: a synthesis cancellation carries the same
    `error_code` / `error_details` attributes and the same `CancellationErrorCode` enum, so
    one error map serves both. It also keeps `QuotaExhausted` a single type, which is what
    lets `is_quota_exhausted` drive the budget guard for TTS 403s as well as STT ones.
    `bad_request_hint` is the one branch that genuinely differs: a malformed recognition is
    about the audio, a malformed synthesis is about the SSML or the voice name.
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
        return QuotaExhausted(
            "Azure returned 403. On an F0 resource this means the monthly free allowance "
            "is used up; it resets at the start of the next billing month."
        )
    if code == speechsdk.CancellationErrorCode.BadRequest:
        return PermanentError(
            f"Azure rejected the request as malformed. {bad_request_hint} {raw_detail}"
        )
    return PermanentError(f"Azure cancelled the request ({code}). {raw_detail}")


def is_quota_exhausted(error: BaseException) -> bool:
    """True when the error is Azure's own 'the month is gone' signal.

    A dedicated exception type rather than a marker string in the message: the message is
    rendered to the user, and matching on its text breaks the moment it is reworded.
    """
    return isinstance(error, QuotaExhausted)


def _raw_json(result: Any) -> dict[str, Any]:
    import azure.cognitiveservices.speech as speechsdk

    payload = result.properties.get(speechsdk.PropertyId.SpeechServiceResponse_JsonResult)
    if not payload:
        raise AssessmentError(
            "Azure returned a result with no JSON body, so there is nothing to assess."
        )
    body: dict[str, Any] = json.loads(payload)
    return body


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
        raise classify_cancellation(result.cancellation_details)
    raise AssessmentError(f"Unexpected recognition result: {result.reason}")


def _assess_continuous(
    wav_path: str, reference_text: str, cancel_event: threading.Event | None = None
) -> list[dict[str, Any]]:
    """Mode B. Accumulate `recognized` events, stop on session_stopped or canceled.

    Returns one payload per utterance; merging them is `_merge_overall` further down.

    This is the only recognition path that can be stopped part-way. `cancel_event` is
    polled while waiting rather than waited on directly, because the SDK signals
    completion through `done` and the caller signals cancellation through `cancel_event`:
    with two independent events there is nothing to block on but both in turn.
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

    def on_recognized(evt: Any) -> None:
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            try:
                payloads.append(_raw_json(evt.result))
            except Exception as exc:  # noqa: BLE001 — a malformed utterance must not kill the session
                logger.warning("Skipped an unparseable utterance: %s", utils.redact(str(exc)))

    def on_canceled(evt: Any) -> None:
        # EndOfStream is the normal way a file-backed session finishes, not a failure.
        if evt.reason != speechsdk.CancellationReason.EndOfStream:
            failure.append(classify_cancellation(evt))
        done.set()

    def on_stopped(_evt: Any) -> None:
        done.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_stopped)

    recognizer.start_continuous_recognition_async().get()
    try:
        deadline = time.monotonic() + CONTINUOUS_TIMEOUT_SECONDS
        while not done.wait(timeout=CANCEL_POLL_SECONDS):
            if cancel_event is not None and cancel_event.is_set():
                # Audio has already been streamed to Azure by this point, so the caller is
                # told the attempt reached it — even though nothing is recorded or metered.
                raise Cancelled("Assessment stopped before Azure finished.", reached_azure=True)
            if time.monotonic() >= deadline:
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
    """Replay a committed payload. The true zero-cost path — no network call at all.

    `OFFLINE_FIXTURE` names a different file in `tests/fixtures/` to replay instead of the
    per-mode default. A development setting, and the reason it exists is narrow: the
    captured recordings came back clean on Break and Intonation, so without it there is no
    way to see the delivery coaching in the running app at all — only in the test suite.

    The name is resolved inside `FIXTURE_DIR` and refused if it escapes: this selects one
    of the committed payloads, and it is not a way to point the app at an arbitrary file
    on the machine.
    """
    override = (utils.get("OFFLINE_FIXTURE") or "").strip()
    name = override or FIXTURES.get(mode) or FIXTURES[Mode.DRILL]
    path = (FIXTURE_DIR / name).resolve()
    if not path.is_relative_to(FIXTURE_DIR.resolve()):
        raise AssessmentError(
            f"OFFLINE_FIXTURE={name!r} points outside {FIXTURE_DIR.name}/. Name one of the "
            f"committed fixtures, not a path."
        )
    if not path.exists():
        raise AssessmentError(
            f"OFFLINE_MODE is on but the fixture {path.name} is missing, so there is "
            f"nothing to replay."
        )
    if override:
        logger.info("OFFLINE_FIXTURE: replaying %s instead of the %s default", name, mode.value)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else [data]


def recognise(
    wav_path: str,
    reference_text: str,
    mode: Mode,
    *,
    cancel_event: threading.Event | None = None,
    on_attempt: Callable[[int], None] | None = None,
) -> tuple[list[dict[str, Any]], bool, int]:
    """Run the assessment for `mode`.

    Returns (verbatim payloads, came_from_fixture, attempts_that_reached_azure).

    `cancel_event` is checked before anything is dispatched and, in continuous mode, while
    waiting for Azure to finish. `on_attempt` fires before each attempt reaches Azure, so a
    caller can tell "nothing was sent" from "something was sent and then abandoned" —
    `retry_transient` already needs the same hook to meter retries.
    """
    if cancel_event is not None and cancel_event.is_set():
        raise Cancelled("Assessment cancelled before it started.", reached_azure=False)

    if utils.offline_mode():
        logger.info("OFFLINE_MODE: replaying the %s fixture instead of calling Azure", mode.value)
        return _load_fixture(mode), True, 0

    if mode is Mode.UNSCRIPTED:
        raise AssessmentError(
            "Unscripted mode is not implemented yet — it needs Azure's content assessment, "
            "which is a separate chunk of work."
        )

    made = 0

    def call() -> list[dict[str, Any]]:
        if mode is Mode.DRILL:
            # Single-shot has no cancellation point of its own: it is one blocking round
            # trip. A stop clicked during it takes effect when the call returns.
            return _assess_single_shot(wav_path, reference_text)
        return _assess_continuous(wav_path, reference_text, cancel_event)

    def count(attempt: int) -> None:
        nonlocal made
        made = attempt
        if on_attempt is not None:
            on_attempt(attempt)

    payloads = utils.retry_transient(call, on_attempt=count)
    if made > 1:
        logger.warning("Assessment took %d attempts; all of them may have cost quota.", made)
    return payloads, False, made


# --- Normalisation ------------------------------------------------------------------------
# Azure returns scores in two shapes depending on the path: the SDK's JSON result nests
# them under a "PronunciationAssessment" object at every level, while the REST short-audio
# response flattens them straight onto the node. Reading both costs one helper and makes
# the parser survive an SDK change that silently switches shapes.


def _scores(node: dict[str, Any]) -> dict[str, Any]:
    nested = node.get("PronunciationAssessment")
    return nested if isinstance(nested, dict) else node


def _score(node: dict[str, Any], key: str) -> float | None:
    value = _scores(node).get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _best(payload: dict[str, Any]) -> dict[str, Any]:
    """The top recognition hypothesis, or an empty dict if there is none."""
    nbest = payload.get("NBest") or []
    return nbest[0] if nbest and isinstance(nbest[0], dict) else {}


def _display_text(payload: dict[str, Any]) -> str:
    return (payload.get("DisplayText") or _best(payload).get("Display") or "").strip()


def _delivery_error_types(word: dict[str, Any]) -> list[str]:
    """Pull UnexpectedBreak / MissingBreak / Monotone out of the prosody feedback block.

    Master plan §5 expects these as word-level `ErrorType` values. In the payload Azure
    actually returns (verified against tests/fixtures/) they live under `Feedback.Prosody`,
    in `Break.ErrorTypes` and `Intonation.ErrorTypes`, while `ErrorType` carries only the
    miscue kinds (None / Mispronunciation / Omission / Insertion). Both `Feedback` and
    `ErrorType` sit *inside* the word's `PronunciationAssessment` object in the SDK shape,
    not at the word's top level — hence `_scores()` rather than `word.get()`. Both places
    are read, because the flat REST shape does put them at the top level.
    """
    found: list[str] = []
    scores = _scores(word)

    top_level = scores.get("ErrorType")
    if top_level and top_level not in {"None", "Mispronunciation", "Omission", "Insertion"}:
        found.append(top_level)

    prosody = (scores.get("Feedback") or word.get("Feedback") or {}).get("Prosody") or {}
    for section in ("Break", "Intonation"):
        for error_type in (prosody.get(section) or {}).get("ErrorTypes", []) or []:
            if error_type and error_type != "None":
                found.append(error_type)

    # Order-preserving dedupe: one word can be flagged twice for the same thing.
    return list(dict.fromkeys(found))


def _prosody_detail(word: dict[str, Any]) -> dict[str, float | None]:
    """The numbers Azure reports beside the delivery faults, from the same block.

    `_delivery_error_types` above says *which* fault; this says what was measured where it
    happened: `Break.BreakLength`, and `Intonation.Monotone.SyllablePitchDeltaConfidence`.

    Kept even when the word carries no fault, because Azure sends them regardless. The
    committed capture reports a break length of 200 ms on "thursday" with
    `Break.ErrorTypes: ["None"]` — an ordinary pause at a sentence boundary — and the same
    pitch-delta confidence on every word in the recording. Filtering those down to the
    words actually flagged is `delivery_faults`' job, not the parser's.

    **`BreakLength` is in 100-ns ticks**, and is converted here. SDK 1.51.1 never mentions
    the field — not in its Python layer, not in the strings of its native libraries
    (checked, not assumed) — so the unit is derived from the committed payload instead:
    the values are 0, 200000 and 2000000, in a response whose `Offset` and `Duration` are
    ticks and whose utterance is 9.79 s long. Read as milliseconds the largest would be a
    2000-second pause inside a ten-second recording; read as ticks it is 200 ms, and the
    same divisor gives the word durations their sane 0.27-0.41 s. Ticks is the only
    self-consistent reading, so the value is exposed as milliseconds and can be said out
    loud to a learner.
    """
    scores = _scores(word)
    prosody = (scores.get("Feedback") or word.get("Feedback") or {}).get("Prosody") or {}
    break_length = (prosody.get("Break") or {}).get("BreakLength")
    monotone = (prosody.get("Intonation") or {}).get("Monotone") or {}
    confidence = monotone.get("SyllablePitchDeltaConfidence")
    return {
        "break_length_ms": (
            float(break_length) / TICKS_PER_MS if isinstance(break_length, (int, float)) else None
        ),
        "monotone_confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
    }


def _timing(node: dict[str, Any]) -> dict[str, Any]:
    """Where a word, syllable or phoneme sits in time. Azure sends this at all three levels.

    `Offset` and `Duration` are in 100-ns ticks and are carried through unconverted, because
    they are exact integers and every derived figure below is not. `start_s`/`end_s` are the
    convenience floats: no consumer should have to remember the divisor.

    Two things about these numbers that are not obvious and cost real work to establish:

    **The offsets are ticks from the start of the AUDIO STREAM, not from the start of the
    file.** In the committed drill fixture the whole response carries `Offset: 16900000` and
    the first word begins at exactly that tick — 1.69 s in, with 1.69 s of something before
    it. Nothing here depends on that, since nothing in this project slices audio yet. The
    chunk that does must read the payload's own top-level `Offset` first rather than treating
    a word offset as a file position.

    **There is a systematic 10 ms seam between consecutive segments.** Within a word the first
    phoneme starts exactly at the word's `Offset` and the last ends exactly at
    `Offset + Duration` (20 of 20 words in the drill fixture), yet every consecutive phoneme
    pair is separated by exactly one `FRAME_TICKS` gap (62 of 62; syllables likewise, 9 of 9).
    So `sum(phoneme durations) + 10 ms x (n-1) == word duration`, and the self-consistent
    reading is that Azure reports `Duration` as `(frames - 1) * 10 ms` — each segment's true
    extent being one frame longer than stated.

    `Duration` is nonetheless carried through **raw**. The +1-frame correction is an inference
    from the arithmetic; the reported value is what Azure states. The choice is not free —
    applied to `rhythm.npvi` it moves the fixture's score from 55.72 to roughly 50.3, because
    a 10 ms shortfall costs a 40 ms vowel 25% and a 320 ms vowel 3%, so short intervals shrink
    further and the variability index rises. That bias is one more reason a published nPVI
    band is not a comparison this data can support. It cancels entirely against the TTS
    baseline, which is measured through this identical code.
    """
    offset = node.get("Offset")
    duration = node.get("Duration")
    offset_ticks = int(offset) if isinstance(offset, (int, float)) else None
    duration_ticks = int(duration) if isinstance(duration, (int, float)) else None
    return {
        "offset_ticks": offset_ticks,
        "duration_ticks": duration_ticks,
        "start_s": offset_ticks / TICKS_PER_SECOND if offset_ticks is not None else None,
        "end_s": (
            (offset_ticks + duration_ticks) / TICKS_PER_SECOND
            if offset_ticks is not None and duration_ticks is not None
            else None
        ),
    }


# The timing keys as a word that was never spoken carries them. Present and None rather than
# absent, so a consumer reading `word["start_s"]` needs no guard for one construction path and
# not the other — the same contract `prosody_detail` already holds in `_omission`.
NO_TIMING: dict[str, Any] = {
    "offset_ticks": None,
    "duration_ticks": None,
    "start_s": None,
    "end_s": None,
}


def _normalise_word(word: dict[str, Any]) -> dict[str, Any]:
    accuracy = _score(word, "AccuracyScore")
    phonemes = []
    for phoneme in word.get("Phonemes") or []:
        score = _score(phoneme, "AccuracyScore")
        nbest = [
            {"phoneme": alt.get("Phoneme"), "score": float(alt.get("Score", 0.0))}
            for alt in (_scores(phoneme).get("NBestPhonemes") or [])
            if isinstance(alt, dict)
        ]
        phonemes.append(
            {
                "phoneme": phoneme.get("Phoneme"),
                "score": score,
                "is_mispronounced": score is not None and score < utils.PHONEME_RED,
                # What was ACTUALLY said. Without this the report can only say a score.
                "nbest": nbest,
                # Where it sits in time. `rhythm.py` reads exactly this and nothing else.
                **_timing(phoneme),
            }
        )

    return {
        "word": word.get("Word"),
        "accuracy": accuracy,
        "error_type": _scores(word).get("ErrorType") or "None",
        "error_source": "azure",
        "delivery_error_types": _delivery_error_types(word),
        "prosody_detail": _prosody_detail(word),
        "syllables": [
            {
                "syllable": s.get("Syllable"),
                "score": _score(s, "AccuracyScore"),
                **_timing(s),
            }
            for s in word.get("Syllables") or []
        ],
        "phonemes": phonemes,
        **_timing(word),
    }


def _diff_miscue(reference_text: str, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive Omission and Insertion by diffing reference against recognised words.

    Needed because `enableMiscue` is ignored in continuous mode, which is the only way to
    assess a paragraph. Entries produced here are marked `error_source: "local_diff"` so
    the UI never presents our guess as Azure's judgement.
    """
    import difflib

    reference = utils.normalise_words(reference_text)
    if not reference:
        return words

    # Tokenise per word and keep a token -> word index map. Joining the words into one
    # string and re-splitting it would desynchronise the diff indices from `words` the
    # moment a word does not yield exactly one token: "well-known" yields two and shifts
    # every later index, while a punctuation-only word yields none and silently drops a
    # real word from the result.
    heard: list[str] = []
    index_of_token: list[int] = []
    for position, word in enumerate(words):
        tokens = utils.normalise_words(str(word.get("word") or ""))
        if not tokens:
            # Nothing alphanumeric to align on. A token that cannot match anything in the
            # reference keeps the mapping intact and lands the word as an Insertion.
            tokens = [f"\x00unmatchable{position}"]
        for token in tokens:
            heard.append(token)
            index_of_token.append(position)

    def words_for(j1: int, j2: int) -> list[dict[str, Any]]:
        """The distinct words covering tokens j1..j2, in order.

        Deduplicated because a multi-token word contributes several tokens and must still
        appear once in the result.
        """
        positions = dict.fromkeys(index_of_token[j1:j2])
        return [words[position] for position in positions]

    result: list[dict[str, Any]] = []
    matcher = difflib.SequenceMatcher(a=reference, b=heard, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"equal", "replace"}:
            result.extend(words_for(j1, j2))
            if tag == "replace":
                # Said something, just not the reference word: an omission of the target.
                result.extend(_omission(word) for word in reference[i1:i2])
        elif tag == "delete":
            result.extend(_omission(word) for word in reference[i1:i2])
        elif tag == "insert":
            for word in words_for(j1, j2):
                inserted = dict(word)
                inserted["error_type"] = "Insertion"
                inserted["error_source"] = "local_diff"
                result.append(inserted)
    return result


def _omission(word: str) -> dict[str, Any]:
    """A reference word that was never heard. It has no scores — it was not spoken."""
    return {
        "word": word,
        "accuracy": None,
        "error_type": "Omission",
        "error_source": "local_diff",
        "delivery_error_types": [],
        # Present and empty rather than absent: every normalised word has the key, so no
        # consumer needs a guard for one construction path and not the other.
        "prosody_detail": {"break_length_ms": None, "monotone_confidence": None},
        "syllables": [],
        "phonemes": [],
        # A word that was never spoken has no extent. None, never 0.0 — a zero-length word at
        # the start of the recording is a very different claim from an absent one, and
        # `rhythm.vocalic_intervals` walks this list in time order.
        **NO_TIMING,
    }


def _weighted(pairs: Sequence[tuple[float | None, float]]) -> float | None:
    """Duration-weighted mean of (score, weight). Falls back to a plain mean if unweighted."""
    scored = [(value, weight) for value, weight in pairs if value is not None]
    if not scored:
        return None
    total_weight = sum(weight for _, weight in scored)
    if total_weight <= 0:
        logger.warning("Utterances carried no usable Duration; falling back to equal weighting.")
        return sum(value for value, _ in scored) / len(scored)
    return sum(value * weight for value, weight in scored) / total_weight


def _merge_overall(
    payloads: list[dict[str, Any]], reference_text: str, words: list[dict[str, Any]]
) -> dict[str, Any]:
    """Combine per-utterance scores into one set.

    Duration-weighted, never a naive mean: a 2-second utterance and a 40-second one do not
    carry equal evidence. Two departures from simply averaging:

    - `completeness` is recomputed globally from the omissions. Azure scores each utterance
      against the *whole* reference text, so per-utterance completeness is meaningless to
      average — a five-utterance paragraph would report ~20% complete.
    - `pron_score` as a weighted average is an approximation. Azure's composite weighting
      is not published, so it cannot be recomputed exactly from the parts.
    """
    weights = [float(p.get("Duration") or 0.0) for p in payloads]
    bests = [_best(p) for p in payloads]

    merged: dict[str, Any] = {}
    for out_key, in_key in (
        ("pron_score", "PronScore"),
        ("accuracy", "AccuracyScore"),
        ("fluency", "FluencyScore"),
        ("prosody", "ProsodyScore"),
    ):
        merged[out_key] = _weighted(
            [(_score(best, in_key), weight) for best, weight in zip(bests, weights)]
        )

    reference_words = utils.normalise_words(reference_text)
    if reference_words:
        omitted = sum(1 for w in words if w.get("error_type") == "Omission")
        merged["completeness"] = round(
            100.0 * max(0, len(reference_words) - omitted) / len(reference_words), 1
        )
    else:
        merged["completeness"] = _weighted(
            [(_score(best, "CompletenessScore"), weight) for best, weight in zip(bests, weights)]
        )

    merged.update(_snr(payloads))
    return merged


def _snr(payloads: list[dict[str, Any]]) -> dict[str, float | None]:
    """Signal-to-noise ratio in dB, from the payload's own top-level `SNR`.

    Not a pronunciation score — a statement about whether the recording was clean enough for
    the pronunciation scores to mean anything. Later accent work gates measurement quality on
    it, which is the whole reason it is carried: a rhythm figure from a noisy recording is a
    measurement of the room.

    Two numbers, because continuous mode returns **one SNR per utterance, not one per
    recording** — the seven-utterance capture in tests/fixtures/bad_delivery_capture.json
    carries seven, spanning 20.6 to 23.2 dB:

    - `snr_db` is duration-weighted through `_weighted`, the same helper every other merged
      score goes through, so a two-second utterance does not outvote a forty-second one.
    - `snr_db_min` is the worst utterance. Quality gating is governed by the worst segment and
      not by the average: an otherwise clean read with one utterance recorded into a fan is
      not a clean read, and averaging hides exactly that.

    None, never 0.0, when Azure sent no SNR at all — 0 dB is signal at the noise floor, which
    is a real and very bad measurement rather than a missing one.
    """
    values = [(p.get("SNR"), float(p.get("Duration") or 0.0)) for p in payloads]
    usable = [(float(snr), weight) for snr, weight in values if isinstance(snr, (int, float))]
    if not usable:
        return {"snr_db": None, "snr_db_min": None}
    if len(usable) == 1:
        # One utterance's SNR is that utterance's SNR. Short-circuited rather than routed
        # through `_weighted`, which computes `v * w / w` and returns 25.035731999999996 for
        # an input of 25.035732 — a float artefact that would then be asserted against the
        # fixture, stored, and charted as though it were a measurement.
        return {"snr_db": usable[0][0], "snr_db_min": usable[0][0]}
    return {
        "snr_db": _weighted(usable),
        "snr_db_min": min(snr for snr, _ in usable),
    }


def normalise(
    payloads: list[dict[str, Any]], reference_text: str, mode: Mode
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Turn verbatim payloads into the shape master plan §4 specifies.

    Returns (overall_scores, recognised_text, words).
    """
    if not payloads:
        raise AssessmentError("Azure returned no assessable utterances.")

    words: list[dict[str, Any]] = []
    for payload in payloads:
        words.extend(_normalise_word(w) for w in _best(payload).get("Words") or [])

    # Single-shot had enableMiscue on, so Azure already labelled omissions and insertions.
    if mode is not Mode.DRILL and reference_text:
        words = _diff_miscue(reference_text, words)

    recognised_text = " ".join(t for t in (_display_text(p) for p in payloads) if t)

    # Drill is the only path where Azure's own overall scores can be trusted as-is: it is
    # single-shot, so enableMiscue was honoured and CompletenessScore reflects the whole
    # attempt. Continuous mode goes through the merge even for one utterance, because its
    # completeness has to be recomputed from the local diff either way.
    if mode is Mode.DRILL and len(payloads) == 1:
        best = _best(payloads[0])
        overall = {
            "pron_score": _score(best, "PronScore"),
            "accuracy": _score(best, "AccuracyScore"),
            "fluency": _score(best, "FluencyScore"),
            "completeness": _score(best, "CompletenessScore"),
            # None, never 0.0 — a missing prosody score and a prosody score of zero are
            # very different things, and the UI renders the first as "—".
            "prosody": _score(best, "ProsodyScore"),
            # One payload, so the weighting and the minimum both collapse to its own SNR.
            # Routed through the same helper anyway, so the two branches cannot drift.
            **_snr(payloads[:1]),
        }
    else:
        overall = _merge_overall(payloads, reference_text, words)

    return overall, recognised_text, words


def analyse(
    wav_path: str,
    reference_text: str,
    mode: Mode,
    *,
    cancel_event: threading.Event | None = None,
    on_attempt: Callable[[int], None] | None = None,
) -> Assessment:
    """Assess one recording end to end: recognise, then normalise. No storage, no UI."""
    payloads, offline, attempts = recognise(
        wav_path, reference_text, mode, cancel_event=cancel_event, on_attempt=on_attempt
    )
    overall, recognised_text, words = normalise(payloads, reference_text, mode)
    return Assessment(
        raw=payloads,
        overall_scores=overall,
        recognised_text=recognised_text,
        words=words,
        offline=offline,
        attempts=attempts,
    )


# --- Reading the normalised shape ------------------------------------------------------
# Pure readers of the structure `normalise` produces: no Streamlit, no network, no SDK.
# They live here rather than in `app.py` because the coaching layer needs them too and
# cannot import a module that pulls in Streamlit. One definition of "what you actually
# produced", so the word card and the coaching report can never disagree about it.


def is_flagged(word: dict[str, Any]) -> bool:
    """Whether a word is worth showing a card for, and worth coaching on.

    One predicate for both, so the coaching report can never discuss a word the UI has
    not flagged, or stay silent about one it has.
    """
    accuracy = word.get("accuracy")
    return bool(
        (word.get("error_type") or "None") != "None"
        or (isinstance(accuracy, (int, float)) and accuracy < utils.WORD_AMBER)
        or word.get("delivery_error_types")
    )


def phoneme_pairs(word: dict[str, Any]) -> list[tuple[str | None, str | None, float | None]]:
    """(expected IPA, produced IPA, score) for each phoneme in a word.

    The produced phoneme is the highest-scoring nbest alternate that differs from the
    target, or None when Azure's best guess agrees with it. This is the whole point of the
    tool: "you produced /d/ where /ð/ was expected" is actionable, "your /ð/ scored 80" is
    not. Takes the maximum rather than trusting nbest to arrive sorted.
    """
    pairs: list[tuple[str | None, str | None, float | None]] = []
    for phoneme in word.get("phonemes") or []:
        expected = phoneme.get("phoneme")
        alternates = [a for a in (phoneme.get("nbest") or []) if a.get("phoneme")]
        produced = None
        if alternates:
            best = max(alternates, key=lambda a: a.get("score") or 0.0)
            if best.get("phoneme") != expected:
                produced = best.get("phoneme")
        pairs.append((expected, produced, phoneme.get("score")))
    return pairs


def mispronounced_words(words: list[dict[str, Any]]) -> list[str]:
    """Words Azure flagged `ErrorType: Mispronunciation`, in reading order.

    A separate count from `is_flagged`/`delivery_summary`: those cover every reason a word
    might be worth a card (low accuracy, omission, a delivery fault), while this is the one
    specific miscue kind the headline "Mispronunciations" count in #10/#12 asks for.
    """
    return [str(w.get("word") or "") for w in words if w.get("error_type") == "Mispronunciation"]


def delivery_summary(words: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Which words carry each delivery fault, in reading order.

    These are not `ErrorType` values — they live under `Feedback.Prosody` in the payload
    (see `_delivery_error_types` above). Aggregating them into counts plus the
    specific words involved is what §5 asks for, and it is what turns a pronunciation
    scorer into something that also fixes speaking flow.
    """
    summary: dict[str, list[str]] = {}
    for word in words:
        for fault in word.get("delivery_error_types") or []:
            summary.setdefault(fault, []).append(str(word.get("word") or ""))
    return summary


# Which delivery fault to put in front of which, when two damaged the same number of words.
# A pause dropped into the middle of a phrase breaks the phrase a listener is assembling;
# two phrases run together makes them assemble the wrong one; a flat phrase is still
# understood. Ordering by the *measurements* instead would be the obvious thing and is
# deliberately not done — see `_prosody_detail` on why no meaning is attached to their
# magnitude.
FAULT_PRECEDENCE: tuple[str, ...] = ("UnexpectedBreak", "MissingBreak", "Monotone")

# Which measurement belongs to which fault. `BreakLength` is reported on the Break block,
# so it says nothing about a Monotone span, and quoting it there would be noise dressed as
# evidence.
_BREAK_FAULTS = frozenset({"UnexpectedBreak", "MissingBreak"})


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _runs(span: list[str], positions: list[int]) -> list[list[str]]:
    """Cut a fault's span into stretches of consecutive words, in reading order.

    Two words are in the same stretch when they sat next to each other in the recording.
    A gap means the fault stopped and started again, which is a different thing to practise
    — and joining the two would quote a phrase the speaker never said.
    """
    runs: list[list[str]] = []
    previous: int | None = None
    for word, position in zip(span, positions):
        if previous is not None and position == previous + 1:
            runs[-1].append(word)
        else:
            runs.append([word])
        previous = position
    return runs


def delivery_faults(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per delivery fault present: the span, and what Azure measured on it.

    `delivery_summary` answers "which words"; this answers "which words, and what was
    measured there", which is the form the coaching payload needs — a fault the coach can
    only name is a fault the learner can only be told about.

    Ordered by how many words the fault damaged, then by `FAULT_PRECEDENCE`. A total
    order, because a report that reshuffles between two runs on identical input is not
    deterministic, and this one is rendered straight onto the page.

    The measurements are averaged over **the words carrying that fault only**. Azure
    reports `SyllablePitchDeltaConfidence` on clean words too, so averaging across the
    whole attempt would hand every reader a monotone number whether or not anything was
    flagged.

    `runs` is the same span cut into **contiguous stretches**, and it is what the coaching
    actually names. A real Monotone comes back as a long unbroken passage — the captured
    bad reading flagged 30 words — and the flat word list alone is unusable advice: its
    first few entries are whichever function words happened to start the span, scattered
    across sentences. A stretch can be quoted back as the phrase it is.
    """
    summary = delivery_summary(words)
    carriers: dict[str, list[dict[str, Any]]] = {}
    positions: dict[str, list[int]] = {}
    for index, word in enumerate(words):
        for fault in word.get("delivery_error_types") or []:
            carriers.setdefault(fault, []).append(word)
            positions.setdefault(fault, []).append(index)

    faults: list[dict[str, Any]] = []
    for fault, span in summary.items():
        detail = [(w.get("prosody_detail") or {}) for w in carriers.get(fault, [])]
        breaks = [d["break_length_ms"] for d in detail if d.get("break_length_ms") is not None]
        pitches = [
            d["monotone_confidence"] for d in detail if d.get("monotone_confidence") is not None
        ]
        entry: dict[str, Any] = {
            "fault": fault,
            "words": span,
            "runs": _runs(span, positions.get(fault, [])),
            "break_length_ms_max": None,
            "break_length_ms_mean": None,
            "monotone_confidence_mean": None,
        }
        if fault in _BREAK_FAULTS and breaks:
            entry["break_length_ms_max"] = round(max(breaks), 1)
            entry["break_length_ms_mean"] = round(sum(breaks) / len(breaks), 1)
        if fault == "Monotone" and pitches:
            entry["monotone_confidence_mean"] = round(sum(pitches) / len(pitches), 3)
        faults.append(entry)

    def rank(entry: dict[str, Any]) -> tuple[int, int, str]:
        fault = entry["fault"]
        precedence = (
            FAULT_PRECEDENCE.index(fault) if fault in FAULT_PRECEDENCE else len(FAULT_PRECEDENCE)
        )
        # The name is the last term so an unrecognised fault Azure adds later still sorts
        # somewhere fixed rather than wherever the dict happened to put it.
        return (-len(entry["words"]), precedence, fault)

    return sorted(faults, key=rank)
