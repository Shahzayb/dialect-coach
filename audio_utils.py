"""Audio conversion, duration validation, and temp-file lifecycle.

Azure's `AudioConfig(filename=...)` needs a real path on disk, which is the only reason a
temp file exists anywhere in this project. It is deleted in a `finally` block so it goes
even when the call it was created for raises mid-flight.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
from contextlib import contextmanager
from typing import Iterator

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
            f"That recording is {seconds:.1f}s — too short to assess. "
            f"Record at least {minimum:g}s."
        )
    if seconds > maximum:
        raise AudioError(
            f"That recording is {seconds:.1f}s, over the {maximum:g}s limit for "
            f"{mode.value} mode. Shorten it, or switch mode."
        )


@contextmanager
def temp_wav(wav_bytes: bytes) -> Iterator[str]:
    """Write `wav_bytes` to a temp file, yield its path, and always delete it.

    The `finally` is the point: Azure raising mid-call must not leave audio on disk, since
    "no persistent audio storage" is a project constraint, not a nicety.
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
