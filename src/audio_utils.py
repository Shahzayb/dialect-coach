"""Audio conversion, duration validation, temp-file lifecycle, and kept recordings.

Azure's `AudioConfig(filename=...)` needs a real path on disk, which is why the temp file in
`temp_wav` exists. It is deleted in a `finally` block so it goes even when the call it was
created for raises mid-flight.

**`keep` is a separate thing and a newer one.** Until v0.10.0 this module deleted every
recording and said so in a comment that called it a project constraint. That stopped being
true on 2026-08-19, when the "no stored audio" rule was lifted: recordings may be kept
locally, never committed, with the path and hash in the database. The accent measurement is
the first feature that needs it — not to defer the measurement, which still runs inside the
assessment request while the audio is in memory, but so that a changed normalisation scheme
or reference table is a re-derivation over stored audio rather than a request that the
calibration passage be read again.

`temp_wav` still deletes, because a temp file is still a temp file.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from pydub import AudioSegment

import utils
from utils import Mode

logger = logging.getLogger(__name__)

# What Azure pronunciation assessment expects: 16 kHz, 16-bit, mono PCM.
TARGET_SAMPLE_RATE = 16_000
TARGET_SAMPLE_WIDTH = 2  # bytes, i.e. 16-bit
TARGET_CHANNELS = 1

SUPPORTED_UPLOAD_TYPES = ("wav", "mp3", "m4a", "webm", "ogg")


class AudioError(ValueError):
    """The supplied audio cannot be used. Message is safe to show in the UI."""


def to_pcm_wav(data: bytes) -> bytes:
    """Decode any ffmpeg-readable audio and re-encode as 16 kHz/16-bit/mono PCM WAV.

    Format is sniffed by ffmpeg rather than trusted from a file extension, so a
    mislabelled upload still works.
    """
    if not data:
        raise AudioError("The recording is empty. Try recording again.")

    try:
        segment = AudioSegment.from_file(io.BytesIO(data))
    except Exception as exc:
        # pydub surfaces ffmpeg's stderr, which is noise to a user.
        logger.debug("pydub failed to decode audio", exc_info=True)
        raise AudioError(
            "That audio could not be decoded. Supported formats: "
            + ", ".join(SUPPORTED_UPLOAD_TYPES)
            + "."
        ) from exc

    segment = (
        segment.set_frame_rate(TARGET_SAMPLE_RATE)
        .set_sample_width(TARGET_SAMPLE_WIDTH)
        .set_channels(TARGET_CHANNELS)
    )
    buffer = io.BytesIO()
    segment.export(buffer, format="wav")
    return buffer.getvalue()


def duration_seconds(wav_bytes: bytes) -> float:
    """Length of already-converted PCM WAV audio, in seconds."""
    try:
        segment = AudioSegment.from_file(io.BytesIO(wav_bytes), format="wav")
    except Exception as exc:
        raise AudioError("Could not read the converted audio.") from exc
    return len(segment) / 1000.0


def validate_duration(seconds: float, mode: Mode) -> None:
    """Reject audio that is too short or too long for `mode`, before any API call.

    Azure returns confusing errors on near-silent or sub-second audio, and the per-mode
    maximum is the client-side half of the quota guard: seconds sent are seconds spent.
    """
    minimum = utils.get_float("MIN_DURATION_SECONDS")
    maximum = utils.max_duration_seconds(mode)

    if seconds < minimum:
        raise AudioError(
            f"That recording is {seconds:.1f}s — too short to assess. Record at least {minimum:g}s."
        )
    if seconds > maximum:
        raise AudioError(
            f"That recording is {seconds:.1f}s, over the {maximum:g}s limit for "
            f"{mode.value} mode. Shorten it, or switch mode."
        )


@contextmanager
def temp_wav(wav_bytes: bytes) -> Iterator[str]:
    """Write `wav_bytes` to a temp file, yield its path, and always delete it.

    The `finally` is the point: a temp file is scratch space for one Azure call, and Azure
    raising mid-call must not leave a stray copy in the system temp directory. This is no
    longer about a no-stored-audio rule — that was lifted on 2026-08-19 and `keep` below is
    what acts on it — it is simply that scratch space gets cleaned up.
    """
    handle, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(handle, "wb") as f:
            f.write(wav_bytes)
        yield path
    finally:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove temp audio file", exc_info=True)


def prepare(data: bytes, mode: Mode) -> tuple[bytes, float]:
    """Convert, measure, and validate in one step. Returns (wav_bytes, seconds)."""
    wav_bytes = to_pcm_wav(data)
    seconds = duration_seconds(wav_bytes)
    validate_duration(seconds, mode)
    return wav_bytes, seconds


def echo_track(clips: Sequence[bytes], *, tail_ms: int = 0) -> bytes:
    """Concatenate synthesised clips, each followed by a silence as long as itself.

    The shadowing warm-up: the model says a phrase, then leaves exactly enough room to say it
    back. The gap is derived from the clip rather than fixed, because a fixed pause is either
    too short for the long sentences or dead air after the short ones — and a gap that runs
    out mid-phrase teaches the reader to rush, which is the opposite of the point.

    Every clip is resampled to the assessment format on the way in. Azure's synthesiser
    returns 24 kHz mono PCM while `to_pcm_wav` targets 16 kHz, and `AudioSegment` concatenation
    silently keeps the *first* segment's frame rate — so a mismatch would not raise, it would
    play the rest of the track at the wrong pitch.
    """
    if not clips:
        raise AudioError("There is nothing to build an echo track from.")

    track = AudioSegment.empty()
    for index, clip in enumerate(clips):
        try:
            segment = AudioSegment.from_file(io.BytesIO(clip))
        except Exception as exc:
            logger.debug("pydub failed to decode echo clip %d", index, exc_info=True)
            raise AudioError("One of the model clips could not be decoded.") from exc
        segment = (
            segment.set_frame_rate(TARGET_SAMPLE_RATE)
            .set_sample_width(TARGET_SAMPLE_WIDTH)
            .set_channels(TARGET_CHANNELS)
        )
        track += segment + AudioSegment.silent(
            duration=len(segment) + tail_ms, frame_rate=TARGET_SAMPLE_RATE
        )

    buffer = io.BytesIO()
    track.export(buffer, format="wav")
    return buffer.getvalue()


def kept_path(sha256: str) -> Path:
    """Where the recording with this digest lives. Content-addressed, so repeats share a file.

    `attempts.audio_sha256` already holds the digest, so the path needs no separate identity
    and re-reading the same passage with byte-identical audio cannot write it twice.
    """
    return Path(utils.get("AUDIO_DIR") or "./audio/attempts") / f"{sha256}.wav"


def keep(wav_bytes: bytes, sha256: str) -> Path | None:
    """Write a recording to the kept-audio directory. None when keeping is switched off.

    Never raises into the caller: a full disk must fail the recording's *storage*, not the
    assessment the user just paid Azure for. A failure is logged and reported as None, and
    the attempt is still recorded — it simply cannot be re-derived later.

    The directory is gitignored (`audio/`). Nothing here ever leaves the machine.
    """
    if not utils.get_bool("KEEP_AUDIO"):
        return None
    path = kept_path(sha256)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(wav_bytes)
        return path
    except OSError:
        logger.warning("Could not keep the recording at %s", path, exc_info=True)
        return None
