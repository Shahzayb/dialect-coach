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


class QuotaExhausted(AssessmentError):
    """Azure itself reported the monthly allowance as gone (403). Authoritative."""


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


DEFAULT_BAD_REQUEST_HINT = "Check the reference text and the audio format."


def classify_cancellation(details, *, bad_request_hint: str = DEFAULT_BAD_REQUEST_HINT) -> Exception:
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
        raise classify_cancellation(result.cancellation_details)
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
            failure.append(classify_cancellation(evt))
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


def recognise(
    wav_path: str, reference_text: str, mode: Mode
) -> tuple[list[dict[str, Any]], bool, int]:
    """Run the assessment for `mode`.

    Returns (verbatim payloads, came_from_fixture, attempts_that_reached_azure).
    """
    if utils.offline_mode():
        logger.info("OFFLINE_MODE: replaying the %s fixture instead of calling Azure", mode.value)
        return _load_fixture(mode), True, 0

    if mode is Mode.UNSCRIPTED:
        raise AssessmentError(
            "Unscripted mode is not implemented yet — it needs Azure's content assessment, "
            "which is a separate chunk of work."
        )

    call = _assess_single_shot if mode is Mode.DRILL else _assess_continuous
    made = 0

    def count(attempt: int) -> None:
        nonlocal made
        made = attempt

    payloads = utils.retry_transient(
        lambda: call(wav_path, reference_text), on_attempt=count
    )
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

    prosody = ((scores.get("Feedback") or word.get("Feedback") or {}).get("Prosody") or {})
    for section in ("Break", "Intonation"):
        for error_type in (prosody.get(section) or {}).get("ErrorTypes", []) or []:
            if error_type and error_type != "None":
                found.append(error_type)

    # Order-preserving dedupe: one word can be flagged twice for the same thing.
    return list(dict.fromkeys(found))


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
            }
        )

    return {
        "word": word.get("Word"),
        "accuracy": accuracy,
        "error_type": _scores(word).get("ErrorType") or "None",
        "error_source": "azure",
        "delivery_error_types": _delivery_error_types(word),
        "syllables": [
            {"syllable": s.get("Syllable"), "score": _score(s, "AccuracyScore")}
            for s in word.get("Syllables") or []
        ],
        "phonemes": phonemes,
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
        "syllables": [],
        "phonemes": [],
    }


def _weighted(pairs: list[tuple[float, float]]) -> float | None:
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

    return merged


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
        }
    else:
        overall = _merge_overall(payloads, reference_text, words)

    return overall, recognised_text, words


def analyse(wav_path: str, reference_text: str, mode: Mode) -> Assessment:
    """Assess one recording end to end: recognise, then normalise. No storage, no UI."""
    payloads, offline, attempts = recognise(wav_path, reference_text, mode)
    overall, recognised_text, words = normalise(payloads, reference_text, mode)
    return Assessment(
        raw=payloads,
        overall_scores=overall,
        recognised_text=recognised_text,
        words=words,
        offline=offline,
        attempts=attempts,
    )
